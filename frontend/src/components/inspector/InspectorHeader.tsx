import { useState, type ReactNode } from 'react'
import { CircleAlert, CircleCheck, ExternalLink, ShieldCheck, ShieldX, X } from 'lucide-react'
import { api } from '../../services/api'
import { formatDate, formatDateTime } from '../../lib/format'
import type { InvoiceDetail } from '../../types/api'
import { StatusBadge } from '../ui/StatusBadge'
import { fullNumber } from './draft'

interface TopBarProps {
  invoice: InvoiceDetail | null
  onClose: () => void
}

/** Fixed title bar: always visible, even while the body scrolls. */
export function InspectorTopBar({ invoice, onClose }: TopBarProps) {
  return (
    <div className="flex items-start gap-3 border-b border-slate-200 bg-white px-4 py-3 sm:px-6">
      <div className="min-w-0 flex-1">
        <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">Inspección de Auditoría</p>
        <h2 id="inspector-title" className="mt-0.5 truncate text-lg font-semibold text-slate-900">
          {invoice ? `Factura ${fullNumber(invoice)}` : 'Cargando factura…'}
        </h2>
      </div>
      {invoice && <StatusBadge status={invoice.audit_status} exportedAt={invoice.facturae_exported_at} size="md" />}
      <button
        type="button"
        onClick={onClose}
        data-autofocus
        aria-label="Cerrar inspección"
        className="-mr-1 rounded-lg p-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-900"
      >
        <X aria-hidden className="size-5" />
      </button>
    </div>
  )
}

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm font-medium text-slate-900">{children}</dd>
    </div>
  )
}

/** Supplier, dates and audit summary shown at the top of the scrolling body. */
export function InvoiceMeta({ invoice }: { invoice: InvoiceDetail }) {
  const [expanded, setExpanded] = useState(false)
  const { supplier } = invoice
  const checked = invoice.last_audited_at !== null
  return (
    <section aria-label="Datos de la factura" className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-900/5 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-base font-semibold text-slate-900">{supplier.name}</p>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-slate-600">
            <span className="tabular-nums">{invoice.supplier_tax_id}</span>
            {supplier.tax_id_valid ? (
              <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700">
                <ShieldCheck aria-hidden className="size-4" />
                {supplier.tax_id_type} válido
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 text-xs font-medium text-red-700">
                <ShieldX aria-hidden className="size-4" />
                Identificador no válido
              </span>
            )}
          </p>
        </div>
        {invoice.source_filename && (
          <a
            href={api.originalFileUrl(invoice.id)}
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-white px-2.5 text-xs font-semibold text-slate-700 shadow-sm ring-1 ring-inset ring-slate-300 hover:bg-slate-50"
          >
            <ExternalLink aria-hidden className="size-4" />
            Ver documento original
          </a>
        )}
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
        <Meta label="Expedición">{formatDate(invoice.issue_date)}</Meta>
        <Meta label="Operación">{formatDate(invoice.operation_date)}</Meta>
        <Meta label="Vencimiento">{formatDate(invoice.due_date)}</Meta>
        <Meta label="Última auditoría">{formatDateTime(invoice.last_audited_at)}</Meta>
      </dl>

      <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
        <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-1 font-medium text-red-700 ring-1 ring-inset ring-red-600/20">
          <CircleAlert aria-hidden className="size-3.5" />
          {invoice.error_count} {invoice.error_count === 1 ? 'error' : 'errores'}
        </span>
        <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-1 font-medium text-amber-800 ring-1 ring-inset ring-amber-600/20">
          {invoice.warning_count} {invoice.warning_count === 1 ? 'aviso' : 'avisos'}
        </span>
        {invoice.facturae_exported_at && (
          <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-1 font-medium text-blue-700 ring-1 ring-inset ring-blue-600/20">
            <CircleCheck aria-hidden className="size-3.5" />
            XML generado el {formatDateTime(invoice.facturae_exported_at)}
          </span>
        )}
      </div>

      {checked && invoice.audit_summary && (
        <div className="mt-3">
          <p className={`break-words text-sm text-slate-600 ${expanded ? '' : 'line-clamp-2'}`}>{invoice.audit_summary}</p>
          {invoice.audit_summary.length > 140 && (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
              className="mt-1 text-xs font-medium text-indigo-600 hover:text-indigo-500"
            >
              {expanded ? 'Ver menos' : 'Ver resumen completo'}
            </button>
          )}
        </div>
      )}
    </section>
  )
}
