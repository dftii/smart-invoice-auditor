import { ChevronRight } from 'lucide-react'
import type { InvoiceSummary } from '../../types/api'
import { formatDate, formatEur } from '../../lib/format'
import { cn } from '../../lib/cn'
import { StatusBadge } from '../ui/StatusBadge'
import { IssueCounters } from './IssueCounters'
import { SyntheticChip } from '../ui/SyntheticChip'
import { OriginChip } from './OriginChip'
import { invoiceLabel } from '../../lib/invoice'

interface InvoiceCardProps {
  invoice: InvoiceSummary
  onSelect: (id: number) => void
}

/** Stacked representation of a table row for narrow screens. */
export function InvoiceCard({ invoice, onSelect }: InvoiceCardProps) {
  const label = invoiceLabel(invoice)
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(invoice.id)}
        aria-label={`Inspeccionar factura ${label} de ${invoice.supplier_name}`}
        className={cn(
          'flex w-full flex-col gap-2 border-l-[3px] px-4 py-3.5 text-left transition-colors hover:bg-slate-50',
          invoice.error_count > 0 ? 'border-red-400' : 'border-transparent',
        )}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-mono text-[13px] font-semibold text-slate-900">{label}</p>
            <p className="truncate text-sm text-slate-700">{invoice.supplier_name}</p>
            <p className="font-mono text-xs text-slate-500">{invoice.supplier_tax_id}</p>
          </div>
          <div className="shrink-0 text-right">
            <p className="text-base font-semibold text-slate-900">{formatEur(invoice.total_amount)}</p>
            <p className="text-xs text-slate-500">{formatDate(invoice.issue_date)}</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <StatusBadge status={invoice.audit_status} exportedAt={invoice.facturae_exported_at} />
          <IssueCounters errors={invoice.error_count} warnings={invoice.warning_count} />
          <OriginChip method={invoice.extraction_method} />
          {invoice.is_synthetic && <SyntheticChip />}
          <ChevronRight aria-hidden className="ml-auto size-4 text-slate-400" />
        </div>
      </button>
    </li>
  )
}
