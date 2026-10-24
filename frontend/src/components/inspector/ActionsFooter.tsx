import { FileCode2, Pencil, RefreshCw, RotateCcw, Save, Trash2, TriangleAlert } from 'lucide-react'
import type { InvoiceDetail } from '../../types/api'
import { Button } from '../ui/Button'

export type BusyAction = 'save' | 'revalidate' | 'export' | 'delete' | null

interface ActionsFooterProps {
  invoice: InvoiceDetail
  editing: boolean
  dirty: boolean
  busy: BusyAction
  exportError: string | null
  onStartEdit: () => void
  onDiscard: () => void
  onSave: () => void
  onRevalidate: () => void
  onExport: () => void
  onDelete: () => void
}

export function ActionsFooter({
  invoice,
  editing,
  dirty,
  busy,
  exportError,
  onStartEdit,
  onDiscard,
  onSave,
  onRevalidate,
  onExport,
  onDelete,
}: ActionsFooterProps) {
  const synthetic = invoice.is_synthetic === true
  const exportable = invoice.audit_status === 'VALIDATED' && !synthetic
  const exportBlockedReason = synthetic
    ? 'Esta factura contiene datos sintéticos (de demostración) y no se puede exportar a FACe.'
    : exportable
      ? null
      : 'Solo se puede exportar una factura en estado «Cuadra». Corrija las discrepancias y vuelva a auditarla.'
  const working = busy !== null

  return (
    <footer className="border-t border-slate-200 bg-white px-4 py-3 sm:px-6">
      {exportError && (
        <div role="alert" className="mb-3 flex items-start gap-3 rounded-xl bg-red-50 p-3 ring-1 ring-red-600/20">
          <TriangleAlert aria-hidden className="mt-0.5 size-5 shrink-0 text-red-600" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-red-800">No se pudo generar el XML de FACe</p>
            <p className="mt-0.5 break-words text-sm text-red-700">{exportError}</p>
            {!editing && (
              <Button className="mt-2" size="sm" variant="secondary" icon={<Pencil aria-hidden className="size-4" />} onClick={onStartEdit}>
                Corregir datos
              </Button>
            )}
          </div>
        </div>
      )}

      {editing ? (
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-end">
          <Button variant="secondary" disabled={working} icon={<RotateCcw aria-hidden className="size-4" />} onClick={onDiscard}>
            Descartar cambios
          </Button>
          <Button
            loading={busy === 'save'}
            disabled={working || !dirty}
            icon={<Save aria-hidden className="size-4" />}
            onClick={onSave}
            title={dirty ? undefined : 'No hay cambios que guardar'}
          >
            Guardar y re-auditar
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" disabled={working} icon={<Pencil aria-hidden className="size-4" />} onClick={onStartEdit}>
            Corregir
          </Button>
          <Button
            variant="secondary"
            loading={busy === 'revalidate'}
            disabled={working}
            icon={<RefreshCw aria-hidden className="size-4" />}
            onClick={onRevalidate}
          >
            Re-validar
          </Button>
          <Button
            variant="ghost"
            disabled={working}
            icon={<Trash2 aria-hidden className="size-4" />}
            onClick={onDelete}
            className="text-red-600 hover:bg-red-50 hover:text-red-700"
          >
            Eliminar
          </Button>
          <span className="ml-auto" title={exportBlockedReason ?? undefined}>
            <Button
              variant="face"
              loading={busy === 'export'}
              disabled={working || !exportable}
              icon={<FileCode2 aria-hidden className="size-4" />}
              onClick={onExport}
              aria-describedby={exportBlockedReason ? 'export-hint' : undefined}
            >
              Exportar XML FACe
            </Button>
          </span>
          {exportBlockedReason && (
            <p id="export-hint" className="basis-full text-right text-xs text-slate-500">
              {exportBlockedReason}
            </p>
          )}
        </div>
      )}
    </footer>
  )
}
