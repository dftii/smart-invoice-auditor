import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { ArrowRight, CircleAlert, LoaderCircle, X } from 'lucide-react'
import { api } from '../../services/api'
import { cn } from '../../lib/cn'
import type { AuditStatus, InvoiceDetail } from '../../types/api'
import { Button } from '../ui/Button'
import { StatusBadge } from '../ui/StatusBadge'
import { useToast } from '../ui/Toast'
import { ActionsFooter, type BusyAction } from './ActionsFooter'
import { FailedChecks, PassedChecks, type CheckFix } from './ChecksList'
import { ConfirmDialog } from './ConfirmDialog'
import {
  applyFix,
  buildUpdate,
  draftFromInvoice,
  fullNumber,
  isDirty,
  validateDraft,
  type InvoiceDraft,
} from './draft'
import { EditForm } from './EditForm'
import { HistoryTab } from './HistoryTab'
import { InspectorTopBar, InvoiceMeta } from './InspectorHeader'
import { LinesTable } from './LinesTable'
import { PartiesCard } from './PartiesCard'
import { reconcile } from './reconcile'
import { ReconciliationCard } from './ReconciliationCard'
import { trapTab, useBodyScrollLock, useDialogFocus } from './useDialog'

export interface AuditInspectorProps {
  /** null = closed */
  invoiceId: number | null
  onClose: () => void
  /** Called after PATCH / revalidate / XML export / delete. */
  onChanged: () => void
}

type TabId = 'audit' | 'history'
type PendingConfirm = 'delete' | 'discard' | 'close' | null

interface Transition {
  from: AuditStatus
  to: AuditStatus
  errors: number
  warnings: number
  id: number
}

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error && reason.message ? reason.message : fallback
}

export function AuditInspector({ invoiceId, onClose, onChanged }: AuditInspectorProps) {
  if (invoiceId === null) return null
  return <InspectorDrawer key={invoiceId} invoiceId={invoiceId} onClose={onClose} onChanged={onChanged} />
}

function StatusTransition({ transition, onDismiss }: { transition: Transition; onDismiss: () => void }) {
  const changed = transition.from !== transition.to
  const resolved = transition.to === 'VALIDATED'
  const text = resolved
    ? changed
      ? 'La factura ya cuadra: la auditoría no encuentra errores.'
      : 'La factura sigue cuadrando tras la nueva auditoría.'
    : `La nueva auditoría encuentra ${transition.errors} ${transition.errors === 1 ? 'error' : 'errores'} y ${transition.warnings} ${transition.warnings === 1 ? 'aviso' : 'avisos'}.`
  return (
    <div
      key={transition.id}
      role="status"
      className={cn(
        'flex animate-pop-in flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl p-4 ring-1',
        resolved ? 'bg-emerald-50 ring-emerald-600/20' : 'bg-white ring-slate-900/10',
      )}
    >
      <div className="flex items-center gap-2">
        <StatusBadge status={transition.from} size="md" />
        <ArrowRight aria-hidden className="size-4 text-slate-400" />
        <StatusBadge status={transition.to} size="md" className="animate-pop-in" />
      </div>
      <p className="min-w-0 flex-1 basis-48 text-sm font-medium text-slate-800">{text}</p>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Cerrar aviso de re-auditoría"
        className="rounded-md p-1 text-slate-400 hover:bg-white/60 hover:text-slate-700"
      >
        <X aria-hidden className="size-4" />
      </button>
    </div>
  )
}

interface DrawerProps {
  invoiceId: number
  onClose: () => void
  onChanged: () => void
}

function InspectorDrawer({ invoiceId, onClose, onChanged }: DrawerProps) {
  const toast = useToast()
  const panelRef = useRef<HTMLElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  useDialogFocus(panelRef)
  useBodyScrollLock()

  const [invoice, setInvoice] = useState<InvoiceDetail | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [tab, setTab] = useState<TabId>('audit')

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

  const retry = () => {
    setLoadError(null)
    setAttempt((value) => value + 1)
  }

  const dirty = editing && original !== null && draft !== null && isDirty(original, draft)
  const clientProblems = useMemo(() => (attempted && draft ? validateDraft(draft) : []), [attempted, draft])
  const problems = [...clientProblems, ...apiProblems]

  const source = editing && draft ? draft : invoice
  const reconciliation = useMemo(() => (source ? reconcile(source) : null), [source])

  const scrollToTop = () => bodyRef.current?.scrollTo({ top: 0, behavior: 'smooth' })

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
    [invoice],
  )

  function leaveEdit() {
    setEditing(false)
    setDraft(null)
    setOriginal(null)
    setAttempted(false)
    setApiProblems([])
  }

  function handleApplyFix(fix: CheckFix) {
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
      setApiProblems(errorMessage(reason, 'No se pudieron guardar los cambios').split(' · '))
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
      const message = errorMessage(reason, 'No se pudo generar el XML')
      setExportError(message)
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
      toast.success('Factura eliminada', fullNumber(invoice))
      onChanged()
      onClose()
    } catch (reason) {
      setConfirm(null)
      toast.error('No se pudo eliminar la factura', errorMessage(reason, 'Error desconocido'))
      setBusy(null)
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (confirm) return
    if (event.key === 'Escape') {
      event.stopPropagation()
      requestClose()
      return
    }
    trapTab(event, panelRef.current)
  }

  const hasLogs = (invoice?.validation_logs.length ?? 0) > 0

  return (
    <div className="fixed inset-0 z-50" onKeyDown={handleKeyDown}>
      <div className="absolute inset-0 animate-fade-in bg-slate-900/40" aria-hidden onClick={requestClose} />
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="inspector-title"
        tabIndex={-1}
        className="absolute inset-y-0 right-0 flex w-[min(760px,100vw)] animate-slide-in-right flex-col bg-slate-50 shadow-2xl focus:outline-none"
      >
        <InspectorTopBar invoice={invoice} onClose={requestClose} />

        <div role="tablist" aria-label="Secciones de la inspección" className="flex gap-1 border-b border-slate-200 bg-white px-4 sm:px-6">
          {(
            [
              ['audit', editing ? 'Corrección' : 'Auditoría'],
              ['history', 'Historial'],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              id={`tab-${id}`}
              aria-selected={tab === id}
              aria-controls={`panel-${id}`}
              onClick={() => setTab(id)}
              className={cn(
                '-mb-px border-b-2 px-3 py-2.5 text-sm font-medium transition-colors',
                tab === id
                  ? 'border-indigo-600 text-indigo-700'
                  : 'border-transparent text-slate-500 hover:text-slate-800',
              )}
            >
              {label}
            </button>
          ))}
        </div>

        <div ref={bodyRef} role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4 sm:px-6">
          {loadError ? (
            <div role="alert" className="rounded-2xl bg-red-50 p-5 ring-1 ring-red-600/20">
              <p className="flex items-center gap-2 text-sm font-semibold text-red-800">
                <CircleAlert aria-hidden className="size-4" />
                No se pudo cargar la factura
              </p>
              <p className="mt-1 text-sm text-red-700">{loadError}</p>
              <Button className="mt-3" variant="secondary" size="sm" onClick={retry}>
                Reintentar
              </Button>
            </div>
          ) : !invoice || !reconciliation ? (
            <p role="status" className="flex items-center justify-center gap-2 py-20 text-sm text-slate-500">
              <LoaderCircle aria-hidden className="size-5 animate-spin" />
              Cargando inspección…
            </p>
          ) : tab === 'history' ? (
            <HistoryTab invoiceId={invoice.id} refreshToken={invoice.last_audit_run_id} />
          ) : (
            <div className="space-y-4 pb-2">
              {problems.length > 0 && (
                <div role="alert" className="rounded-2xl bg-red-50 p-4 ring-1 ring-red-600/20">
                  <p className="flex items-center gap-2 text-sm font-semibold text-red-800">
                    <CircleAlert aria-hidden className="size-4" />
                    No se puede guardar todavía
                  </p>
                  <ul className="mt-2 list-disc space-y-1 pl-6 text-sm text-red-700">
                    {problems.map((problem) => (
                      <li key={problem}>{problem}</li>
                    ))}
                  </ul>
                </div>
              )}

              {transition && !editing && <StatusTransition transition={transition} onDismiss={() => setTransition(null)} />}

              {!editing && <InvoiceMeta invoice={invoice} />}

              <ReconciliationCard data={reconciliation} preview={editing} />

              <section aria-labelledby="checks-title" className="space-y-3">
                <h3 id="checks-title" className="text-sm font-semibold text-slate-900">
                  {editing ? 'Resultado de la última auditoría' : 'Discrepancias y avisos'}
                </h3>
                {editing && (
                  <p className="-mt-1 text-xs text-slate-500">
                    Se actualizará al guardar. Use «Aplicar valor esperado» para copiar el importe correcto al formulario.
                  </p>
                )}
                {hasLogs ? (
                  <FailedChecks checks={invoice.validation_logs} draft={draft} onApplyFix={handleApplyFix} />
                ) : (
                  <p className="rounded-2xl bg-white p-4 text-sm text-slate-600 ring-1 ring-slate-900/5">
                    Esta factura todavía no tiene resultados de auditoría. Pulse «Re-validar» para ejecutarla.
                  </p>
                )}
              </section>

              {editing && draft ? (
                <EditForm draft={draft} reconciliation={reconciliation} disabled={busy !== null} onChange={setDraft} />
              ) : (
                <>
                  <LinesTable lines={invoice.lines} />
                  <PartiesCard invoice={invoice} />
                  {hasLogs && <PassedChecks checks={invoice.validation_logs} />}
                </>
              )}
            </div>
          )}
        </div>

        {invoice && (
          <ActionsFooter
            invoice={invoice}
            editing={editing}
            dirty={dirty}
            busy={busy}
            exportError={exportError}
            onStartEdit={() => startEdit()}
            onDiscard={requestDiscard}
            onSave={() => void save()}
            onRevalidate={() => void revalidate()}
            onExport={() => void exportXml()}
            onDelete={() => setConfirm('delete')}
          />
        )}
      </aside>

      {confirm === 'delete' && invoice && (
        <ConfirmDialog
          danger
          title="¿Eliminar esta factura?"
          confirmLabel="Eliminar factura"
          loading={busy === 'delete'}
          onCancel={() => setConfirm(null)}
          onConfirm={() => void remove()}
        >
          Se eliminará la factura <strong>{fullNumber(invoice)}</strong> de {invoice.supplier.name}, con su historial de
          auditorías y el archivo original. Esta acción no se puede deshacer.
        </ConfirmDialog>
      )}
      {(confirm === 'discard' || confirm === 'close') && (
        <ConfirmDialog
          title="¿Descartar los cambios?"
          confirmLabel="Descartar cambios"
          cancelLabel="Seguir editando"
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            const closing = confirm === 'close'
            setConfirm(null)
            leaveEdit()
            if (closing) onClose()
          }}
        >
          Hay modificaciones sin guardar en el formulario. Si continúa, se perderán.
        </ConfirmDialog>
      )}
    </div>
  )
}
