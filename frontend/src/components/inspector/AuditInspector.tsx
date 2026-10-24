import { useRef, useState, type KeyboardEvent } from 'react'
import { ArrowRight, CircleAlert, LoaderCircle, X } from 'lucide-react'
import { cn } from '../../lib/cn'
import { invoiceLabel } from '../../lib/invoice'
import { Button } from '../ui/Button'
import { StatusBadge } from '../ui/StatusBadge'
import { ActionsFooter } from './ActionsFooter'
import { FailedChecks, PassedChecks } from './ChecksList'
import { ConfirmDialog } from './ConfirmDialog'
import { EditForm } from './EditForm'
import { HistoryTab } from './HistoryTab'
import { InspectorTopBar, InvoiceMeta } from './InspectorHeader'
import { LinesTable } from './LinesTable'
import { PartiesCard } from './PartiesCard'
import { ReconciliationCard } from './ReconciliationCard'
import { trapTab, useBodyScrollLock, useDialogFocus } from './useDialog'
import { useInspectorActions, type TabId, type Transition } from './useInspectorActions'

export interface AuditInspectorProps {
  /** null = closed */
  invoiceId: number | null
  onClose: () => void
  /** Called after PATCH / revalidate / XML export / delete. */
  onChanged: () => void
  /** Called after the invoice was deleted, once the drawer is closed. */
  onDeleted?: () => void
}

export function AuditInspector({ invoiceId, onClose, onChanged, onDeleted }: AuditInspectorProps) {
  if (invoiceId === null) return null
  return (
    <InspectorDrawer key={invoiceId} invoiceId={invoiceId} onClose={onClose} onChanged={onChanged} onDeleted={onDeleted} />
  )
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

const TAB_IDS: readonly TabId[] = ['audit', 'history']

interface DrawerProps {
  invoiceId: number
  onClose: () => void
  onChanged: () => void
  onDeleted?: () => void
}

function InspectorDrawer({ invoiceId, onClose, onChanged, onDeleted }: DrawerProps) {
  const panelRef = useRef<HTMLElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const tabRefs = useRef<Record<TabId, HTMLButtonElement | null>>({ audit: null, history: null })
  useDialogFocus(panelRef)
  useBodyScrollLock()

  const [tab, setTab] = useState<TabId>('audit')
  const scrollToTop = () => bodyRef.current?.scrollTo({ top: 0, behavior: 'smooth' })

  const actions = useInspectorActions({ invoiceId, onClose, onChanged, onDeleted, setTab, scrollToTop })
  const { invoice, reconciliation, editing, draft, busy, confirm } = actions

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (confirm) return
    if (event.key === 'Escape') {
      event.stopPropagation()
      actions.requestClose()
      return
    }
    trapTab(event, panelRef.current)
  }

  /** WAI-ARIA tabs: arrows move (and activate) between tabs, Home/End jump to the ends. */
  function handleTabKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const index = TAB_IDS.indexOf(tab)
    let next = index
    if (event.key === 'ArrowRight') next = (index + 1) % TAB_IDS.length
    else if (event.key === 'ArrowLeft') next = (index - 1 + TAB_IDS.length) % TAB_IDS.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = TAB_IDS.length - 1
    else return
    event.preventDefault()
    setTab(TAB_IDS[next])
    tabRefs.current[TAB_IDS[next]]?.focus()
  }

  const hasLogs = (invoice?.validation_logs.length ?? 0) > 0
  const tabLabels: Record<TabId, string> = { audit: editing ? 'Corrección' : 'Auditoría', history: 'Historial' }

  return (
    <div className="fixed inset-0 z-50" onKeyDown={handleKeyDown}>
      <div className="absolute inset-0 animate-fade-in bg-slate-900/40" aria-hidden onClick={actions.requestClose} />
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="inspector-title"
        tabIndex={-1}
        className="absolute inset-y-0 right-0 flex w-[min(760px,100vw)] animate-slide-in-right flex-col bg-slate-50 shadow-2xl focus:outline-none"
      >
        <InspectorTopBar invoice={invoice} onClose={actions.requestClose} />

        <div role="tablist" aria-label="Secciones de la inspección" className="flex gap-1 border-b border-slate-200 bg-white px-4 sm:px-6">
          {TAB_IDS.map((id) => (
            <button
              key={id}
              ref={(element) => {
                tabRefs.current[id] = element
              }}
              type="button"
              role="tab"
              id={`tab-${id}`}
              aria-selected={tab === id}
              aria-controls={`panel-${id}`}
              tabIndex={tab === id ? 0 : -1}
              onClick={() => setTab(id)}
              onKeyDown={handleTabKeyDown}
              className={cn(
                '-mb-px border-b-2 px-3 py-2.5 text-sm font-medium transition-colors',
                tab === id
                  ? 'border-indigo-600 text-indigo-700'
                  : 'border-transparent text-slate-500 hover:text-slate-800',
              )}
            >
              {tabLabels[id]}
            </button>
          ))}
        </div>

        <div ref={bodyRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4 sm:px-6">
          <div role="tabpanel" id="panel-history" aria-labelledby="tab-history" hidden={tab !== 'history'}>
            {tab === 'history' && invoice && <HistoryTab invoiceId={invoice.id} refreshToken={invoice.last_audit_run_id} />}
          </div>
          <div role="tabpanel" id="panel-audit" aria-labelledby="tab-audit" hidden={tab !== 'audit'}>
            {tab === 'audit' &&
              (actions.loadError ? (
                <div role="alert" className="rounded-2xl bg-red-50 p-5 ring-1 ring-red-600/20">
                  <p className="flex items-center gap-2 text-sm font-semibold text-red-800">
                    <CircleAlert aria-hidden className="size-4" />
                    No se pudo cargar la factura
                  </p>
                  <p className="mt-1 text-sm text-red-700">{actions.loadError}</p>
                  <Button className="mt-3" variant="secondary" size="sm" onClick={actions.retryLoad}>
                    Reintentar
                  </Button>
                </div>
              ) : !invoice || !reconciliation ? (
                <p role="status" className="flex items-center justify-center gap-2 py-20 text-sm text-slate-500">
                  <LoaderCircle aria-hidden className="size-5 animate-spin" />
                  Cargando inspección…
                </p>
              ) : (
                <div className="space-y-4 pb-2">
                  {actions.problems.length > 0 && (
                    <div role="alert" className="rounded-2xl bg-red-50 p-4 ring-1 ring-red-600/20">
                      <p className="flex items-center gap-2 text-sm font-semibold text-red-800">
                        <CircleAlert aria-hidden className="size-4" />
                        No se puede guardar todavía
                      </p>
                      <ul className="mt-2 list-disc space-y-1 pl-6 text-sm text-red-700">
                        {actions.problems.map((problem) => (
                          <li key={problem}>{problem}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {actions.transition && !editing && (
                    <StatusTransition transition={actions.transition} onDismiss={actions.dismissTransition} />
                  )}

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
                      <FailedChecks checks={invoice.validation_logs} draft={draft} onApplyFix={actions.applyCheckFix} />
                    ) : (
                      <p className="rounded-2xl bg-white p-4 text-sm text-slate-600 ring-1 ring-slate-900/5">
                        Esta factura todavía no tiene resultados de auditoría. Pulse «Re-validar» para ejecutarla.
                      </p>
                    )}
                  </section>

                  {editing && draft ? (
                    <EditForm draft={draft} reconciliation={reconciliation} disabled={busy !== null} onChange={actions.setDraft} />
                  ) : (
                    <>
                      <LinesTable lines={invoice.lines} />
                      <PartiesCard invoice={invoice} />
                      {hasLogs && <PassedChecks checks={invoice.validation_logs} />}
                    </>
                  )}
                </div>
              ))}
          </div>
        </div>

        {invoice && (
          <ActionsFooter
            invoice={invoice}
            editing={editing}
            dirty={actions.dirty}
            busy={busy}
            exportError={actions.exportError}
            onStartEdit={() => actions.startEdit()}
            onDiscard={actions.requestDiscard}
            onSave={() => void actions.save()}
            onRevalidate={() => void actions.revalidate()}
            onExport={() => void actions.exportXml()}
            onDelete={() => actions.setConfirm('delete')}
          />
        )}
      </aside>

      {confirm === 'delete' && invoice && (
        <ConfirmDialog
          danger
          title="¿Eliminar esta factura?"
          confirmLabel="Eliminar factura"
          loading={busy === 'delete'}
          onCancel={() => actions.setConfirm(null)}
          onConfirm={() => void actions.remove()}
        >
          Se eliminará la factura <strong>{invoiceLabel(invoice)}</strong> de {invoice.supplier.name}, con su historial de
          auditorías y el archivo original. Esta acción no se puede deshacer.
        </ConfirmDialog>
      )}
      {(confirm === 'discard' || confirm === 'close') && (
        <ConfirmDialog
          title="¿Descartar los cambios?"
          confirmLabel="Descartar cambios"
          cancelLabel="Seguir editando"
          onCancel={() => actions.setConfirm(null)}
          onConfirm={actions.confirmDiscard}
        >
          Hay modificaciones sin guardar en el formulario. Si continúa, se perderán.
        </ConfirmDialog>
      )}
    </div>
  )
}
