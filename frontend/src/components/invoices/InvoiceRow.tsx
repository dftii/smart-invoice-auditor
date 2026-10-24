import type { InvoiceSummary } from '../../types/api'
import { formatDate, formatEur } from '../../lib/format'
import { cn } from '../../lib/cn'
import { StatusBadge } from '../ui/StatusBadge'
import { IssueCounters } from './IssueCounters'
import { SyntheticChip } from '../ui/SyntheticChip'
import { OriginChip } from './OriginChip'
import { invoiceLabel } from '../../lib/invoice'

interface InvoiceRowProps {
  invoice: InvoiceSummary
  selected: boolean
  onSelect: (id: number) => void
}

export function InvoiceRow({ invoice, selected, onSelect }: InvoiceRowProps) {
  const hasErrors = invoice.error_count > 0
  const label = invoiceLabel(invoice)

  return (
    <tr
      // Mouse convenience only: keyboard and assistive tech use the button in the first cell (one tab stop per row).
      onClick={() => onSelect(invoice.id)}
      className={cn('group cursor-pointer transition-colors hover:bg-slate-50 focus-within:bg-indigo-50/50', selected && 'bg-indigo-50/60')}
    >
      <td className={cn('whitespace-nowrap border-l-[3px] py-3 pl-4 pr-3', hasErrors ? 'border-red-400' : 'border-transparent')}>
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation()
            onSelect(invoice.id)
          }}
          aria-label={`Inspeccionar factura ${label}`}
          aria-current={selected || undefined}
          className="rounded font-mono text-[13px] font-medium text-slate-900 underline-offset-2 hover:text-indigo-700 hover:underline"
        >
          {label}
        </button>
      </td>
      <td className="max-w-[16rem] px-3 py-3">
        <p className="truncate font-medium text-slate-900" title={invoice.supplier_name}>
          {invoice.supplier_name}
        </p>
        <p className="font-mono text-xs text-slate-500">{invoice.supplier_tax_id}</p>
      </td>
      <td className="whitespace-nowrap px-3 py-3 text-slate-600">{formatDate(invoice.issue_date)}</td>
      <td className="hidden whitespace-nowrap px-3 py-3 text-right text-slate-600 lg:table-cell">{formatEur(invoice.tax_base)}</td>
      <td className="hidden whitespace-nowrap px-3 py-3 text-right text-slate-600 lg:table-cell">{formatEur(invoice.total_vat)}</td>
      <td className="whitespace-nowrap px-3 py-3 text-right font-semibold text-slate-900">{formatEur(invoice.total_amount)}</td>
      <td className="px-3 py-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <StatusBadge status={invoice.audit_status} exportedAt={invoice.facturae_exported_at} />
          <IssueCounters errors={invoice.error_count} warnings={invoice.warning_count} />
          {invoice.is_synthetic && <SyntheticChip />}
        </div>
      </td>
      <td className="hidden px-3 py-3 lg:table-cell">
        <OriginChip method={invoice.extraction_method} />
      </td>
    </tr>
  )
}
