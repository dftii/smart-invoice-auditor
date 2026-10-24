import { Bot, Copy, Cpu, Eye, PenLine, TriangleAlert } from 'lucide-react'
import { cn } from '../../lib/cn'
import { formatDate, formatEur, formatPercent } from '../../lib/format'
import { invoiceLabel } from '../../lib/invoice'
import { StatusBadge } from '../ui/StatusBadge'
import { Button } from '../ui/Button'
import { SyntheticChip } from '../ui/SyntheticChip'
import type { ExtractionMethod, UploadResponse } from '../../types/api'

const METHOD_CHIPS: Record<ExtractionMethod, { label: string; icon: typeof Bot; className: string }> = {
  AI: { label: 'IA · Claude', icon: Bot, className: 'bg-violet-50 text-violet-700 ring-violet-600/20' },
  DEMO: { label: 'Parser demo', icon: Cpu, className: 'bg-slate-100 text-slate-700 ring-slate-500/20' },
  MANUAL: { label: 'Manual', icon: PenLine, className: 'bg-slate-100 text-slate-700 ring-slate-500/20' },
}

interface ResultCardProps {
  result: UploadResponse
  onInspect: (invoiceId: number) => void
}

export function ResultCard({ result, onInspect }: ResultCardProps) {
  const { invoice, audit, extraction_method: method, extraction_warnings: warnings } = result
  const chip = METHOD_CHIPS[method]
  const ChipIcon = chip.icon
  const confidence = invoice.extraction_confidence
  const label = invoiceLabel(invoice)

  return (
    <div className="animate-pop-in space-y-3 rounded-xl bg-white p-4 ring-1 ring-slate-900/10">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={audit.status} exportedAt={invoice.facturae_exported_at} size="md" />
        <span
          className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset', chip.className)}
        >
          <ChipIcon aria-hidden className="size-3.5" />
          {chip.label}
          {confidence !== null && <span className="tabular-nums">· {formatPercent(Math.round(confidence * 100))} confianza</span>}
        </span>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
        <div className="min-w-0">
          <dt className="text-xs text-slate-500">Factura</dt>
          <dd className="truncate font-semibold text-slate-900" title={label}>
            {label}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-slate-500">Proveedor</dt>
          <dd className="truncate font-semibold text-slate-900" title={invoice.supplier.name}>
            {invoice.supplier.name}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-slate-500">Fecha</dt>
          <dd className="font-semibold text-slate-900">{formatDate(invoice.issue_date)}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-slate-500">Total</dt>
          <dd className="font-semibold tabular-nums text-slate-900">{formatEur(invoice.total_amount)}</dd>
        </div>
      </dl>

      {invoice.is_synthetic && (
        <p className="flex items-start gap-2 rounded-lg bg-amber-50 p-2.5 text-sm text-amber-900 ring-1 ring-amber-600/20">
          <SyntheticChip />
          <span>Contiene datos sintéticos: se puede auditar y corregir, pero la exportación a FACe está bloqueada.</span>
        </p>
      )}

      {audit.summary && <p className="text-sm text-slate-600">{audit.summary}</p>}

      {audit.status === 'DUPLICATE' && audit.duplicate_of.length > 0 && (
        <p className="flex items-start gap-2 rounded-lg bg-amber-50 p-2.5 text-sm text-amber-900 ring-1 ring-amber-600/20">
          <Copy aria-hidden className="mt-0.5 size-4 shrink-0" />
          <span>
            Duplicada de {audit.duplicate_of.length === 1 ? 'la factura' : 'las facturas'}{' '}
            {audit.duplicate_of.map((id) => `#${id}`).join(', ')}.
          </span>
        </p>
      )}

      {warnings.length > 0 && (
        <ul className="space-y-1 rounded-lg bg-amber-50 p-2.5 ring-1 ring-amber-600/20">
          {warnings.map((warning, index) => (
            <li key={index} className="flex items-start gap-2 text-sm text-amber-900">
              <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-amber-600" />
              <span className="min-w-0 break-words">{warning}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="flex justify-end">
        <Button size="sm" icon={<Eye aria-hidden className="size-4" />} onClick={() => onInspect(invoice.id)}>
          Ver auditoría
        </Button>
      </div>
    </div>
  )
}
