import type { KeyboardEvent } from 'react'
import { Eye } from 'lucide-react'
import type { InvoiceSummary } from '../../types/api'
import { formatDate, formatEur } from '../../lib/format'
import { cn } from '../../lib/cn'
import { StatusBadge } from '../ui/StatusBadge'
import { IssueCounters } from './IssueCounters'
import { OriginChip } from './OriginChip'
import { invoiceLabel } from './invoiceDisplay'

interface InvoiceRowProps {
  invoice: InvoiceSummary
  selected: boolean
  onSelect: (id: number) => void
}

export function InvoiceRow({ invoice, selected, onSelect }: InvoiceRowProps) {
  const hasErrors = invoice.error_count > 0
  const label = invoiceLabel(invoice)

  const onKeyDown = (event: KeyboardEvent<HTMLTableRowElement>) => {
    if (event.target !== event.currentTarget) return
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onSelect(invoice.id)
    }
  }

  return (
    <tr
      tabIndex={0}
      aria-selected={selected || undefined}
      aria-label={`Factura ${label} de ${invoice.supplier_name}. Pulsa Intro para inspeccionar`}
      onClick={() => onSelect(invoice.id)}
      onKeyDown={onKeyDown}
      className={cn(
        'group cursor-pointer transition-colors hover:bg-slate-50 focus-visible:bg-indigo-50/50 focus-visible:outline-offset-[-2px]',
        selected && 'bg-indigo-50/60',
      )}
    >
      <td className={cn('whitespace-nowrap border-l-[3px] py-3 pl-4 pr-3', hasErrors ? 'border-red-400' : 'border-transparent')}>
        <span className="font-mono text-[13px] font-medium text-slate-900">{label}</span>
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
        </div>
      </td>
      <td className="hidden px-3 py-3 lg:table-cell">
        <OriginChip method={invoice.extraction_method} />
      </td>
      <td className="py-3 pl-3 pr-4 text-right">
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation()
            onSelect(invoice.id)
          }}
          aria-label={`Inspeccionar factura ${label}`}
          className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs font-semibold text-indigo-700 ring-1 ring-inset ring-indigo-200 transition-colors hover:bg-indigo-50"
        >
          <Eye aria-hidden className="size-4" />
          <span className="hidden xl:inline">Inspeccionar</span>
        </button>
      </td>
    </tr>
  )
}
