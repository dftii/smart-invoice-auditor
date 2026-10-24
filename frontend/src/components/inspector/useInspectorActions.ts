import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { invoiceLabel } from '../../lib/invoice'
import { ApiError, api } from '../../services/api'
import type { AuditStatus, InvoiceDetail } from '../../types/api'
import { useToast } from '../ui/toastContext'
import type { BusyAction } from './ActionsFooter'
import type { CheckFix } from './ChecksList'
import { applyFix, buildUpdate, draftFromInvoice, isDirty, validateDraft, type InvoiceDraft } from './draft'
import { reconcile } from './reconcile'

export type TabId = 'audit' | 'history'
export type PendingConfirm = 'delete' | 'discard' | 'close' | null

export interface Transition {
  from: AuditStatus
  to: AuditStatus
  errors: number
  warnings: number
  id: number
}

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error && reason.message ? reason.message : fallback
}

interface Options {
  invoiceId: number
  onClose: () => void
  /** Called after PATCH / revalidate / XML export / delete. */
  onChanged: () => void
  /** Called after the invoice has been deleted (its row is gone: the page restores focus). */
  onDeleted?: () => void
  /** Switches the visible tab (starting an edit shows the correction tab). */
  setTab: (tab: TabId) => void
  scrollToTop: () => void
}

/** State and operations of the inspector drawer: loading, editing (draft/diff/save), re-audit, export and delete. */
export function useInspectorActions({ invoiceId, onClose, onChanged, onDeleted, setTab, scrollToTop }: Options) {
  const toast = useToast()
  const [invoice, setInvoice] = useState<InvoiceDetail | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  const [editing, setEditing] = useState(false)
  const [original, setOriginal] = useState<InvoiceDraft | null>(null)
  const [draft, setDraft] = useState<InvoiceDraft | null>(null)
  const [attempted, setAttempted] = useState(false)
  const [apiProblems, setApiProblems] = useState<string[]>([])

  const [busy, setBusy] = useState<BusyAction>(null)
  const [exportError, setExportError] = useState<string | null>(null)
  const [transition, setTransition] = useState<Transition | null>(null)
  const [confirm, setConfirm] = useState<PendingConfirm>(null)
  const transitionCounter = useRef(0)

  useEffect(() => {
    let cancelled = false
    api
      .getInvoice(invoiceId)
      .then((result) => {
        if (!cancelled) setInvoice(result)
      })
      .catch((reason: unknown) => {
        if (!cancelled) setLoadError(errorMessage(reason, 'No se pudo cargar la factura'))
      })
    return () => {
      cancelled = true
    }
  }, [invoiceId, attempt])

  const retryLoad = () => {
    setLoadError(null)
    setAttempt((value) => value + 1)
  }

  const dirty = editing && original !== null && draft !== null && isDirty(original, draft)
  const clientProblems = useMemo(() => (attempted && draft ? validateDraft(draft) : []), [attempted, draft])
  const problems = [...clientProblems, ...apiProblems]

  const source = editing && draft ? draft : invoice
  const reconciliation = useMemo(() => (source ? reconcile(source) : null), [source])

  function showTransition(before: AuditStatus, after: InvoiceDetail) {
    transitionCounter.current += 1
    setTransition({
      from: before,
      to: after.audit_status,
      errors: after.error_count,
      warnings: after.warning_count,
      id: transitionCounter.current,
    })
  }

  const startEdit = useCallback(
    (fix?: CheckFix) => {
      if (!invoice) return
      const base = draftFromInvoice(invoice)
      setOriginal(base)
      setDraft(fix ? applyFix(base, fix.target, fix.value) : base)
      setAttempted(false)
      setApiProblems([])
      setTransition(null)
      setEditing(true)
      setTab('audit')
    },
    [invoice, setTab],
  )

  function leaveEdit() {
    setEditing(false)
    setDraft(null)
    setOriginal(null)
    setAttempted(false)
    setApiProblems([])
  }

  function applyCheckFix(fix: CheckFix) {
    if (editing) {
      setDraft((current) => (current ? applyFix(current, fix.target, fix.value) : current))
    } else {
      startEdit(fix)
      toast.info('Valor esperado aplicado', 'Revise el formulario y pulse «Guardar y re-auditar».')
    }
  }

  function requestClose() {
    if (dirty) setConfirm('close')
    else onClose()
  }

  function requestDiscard() {
    if (dirty) setConfirm('discard')
    else leaveEdit()
  }

  /** Confirmed "discard changes": leaves the form and, when the drawer was being closed, closes it. */
  function confirmDiscard() {
    const closing = confirm === 'close'
    setConfirm(null)
    leaveEdit()
    if (closing) onClose()
  }

  async function save() {
    if (!invoice || !original || !draft) return
    setAttempted(true)
    setApiProblems([])
    if (validateDraft(draft).length > 0) {
      scrollToTop()
      return
    }
    const { payload } = buildUpdate(original, draft)
    if (Object.keys(payload).length === 0) return
    setBusy('save')
    try {
      const updated = await api.updateInvoice(invoice.id, payload)
      showTransition(invoice.audit_status, updated)
      setInvoice(updated)
      setExportError(null)
      leaveEdit()
      scrollToTop()
      toast.success(
        updated.audit_status === 'VALIDATED' ? 'Factura corregida: cuadra' : 'Cambios guardados y factura re-auditada',
        updated.audit_status === 'VALIDATED' ? undefined : `${updated.error_count} errores · ${updated.warning_count} avisos`,
      )
      onChanged()
    } catch (reason) {
      setApiProblems(
        reason instanceof ApiError ? reason.problems : [errorMessage(reason, 'No se pudieron guardar los cambios')],
      )
      scrollToTop()
      toast.error('No se pudieron guardar los cambios')
    } finally {
      setBusy(null)
    }
  }

  async function revalidate() {
    if (!invoice) return
    setBusy('revalidate')
    try {
      await api.revalidate(invoice.id)
      const refreshed = await api.getInvoice(invoice.id)
      showTransition(invoice.audit_status, refreshed)
      setInvoice(refreshed)
      setExportError(null)
      toast.success('Factura re-validada', `${refreshed.error_count} errores · ${refreshed.warning_count} avisos`)
      onChanged()
    } catch (reason) {
      toast.error('No se pudo re-validar', errorMessage(reason, 'Error desconocido'))
    } finally {
      setBusy(null)
    }
  }

  async function exportXml() {
    if (!invoice) return
    setBusy('export')
    setExportError(null)
    try {
      const filename = await api.downloadFacturae(invoice.id)
      toast.success('XML FACe generado', filename)
      onChanged()
      try {
        setInvoice(await api.getInvoice(invoice.id))
      } catch {
        // The export itself succeeded; the next refresh will show the new state.
      }
    } catch (reason) {
      setExportError(errorMessage(reason, 'No se pudo generar el XML'))
      toast.error('No se pudo exportar a FACe')
    } finally {
      setBusy(null)
    }
  }

  async function remove() {
    if (!invoice) return
    setBusy('delete')
    try {
      await api.deleteInvoice(invoice.id)
      toast.success('Factura eliminada', invoiceLabel(invoice))
      onChanged()
      onClose()
      onDeleted?.()
    } catch (reason) {
      setConfirm(null)
      toast.error('No se pudo eliminar la factura', errorMessage(reason, 'Error desconocido'))
      setBusy(null)
    }
  }

  return {
    invoice,
    loadError,
    retryLoad,
    editing,
    draft,
    setDraft,
    dirty,
    problems,
    reconciliation,
    busy,
    exportError,
    transition,
    dismissTransition: () => setTransition(null),
    confirm,
    setConfirm,
    startEdit,
    applyCheckFix,
    requestClose,
    requestDiscard,
    confirmDiscard,
    save,
    revalidate,
    exportXml,
    remove,
  }
}
