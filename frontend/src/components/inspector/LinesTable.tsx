import { ListChecks } from 'lucide-react'
import { cn } from '../../lib/cn'
import { centsToDecimal, formatEur, formatNumber, formatPercent } from '../../lib/format'
import type { InvoiceLine } from '../../types/api'
import { analyzeLine, IRPF_RATE_OPTIONS, VAT_RATE_OPTIONS } from './reconcile'

const eur = (cents: number) => formatEur(centsToDecimal(cents))

interface AmountCellProps {
  declared: number
  expected: number
  ok: boolean
}

function AmountCell({ declared, expected, ok }: AmountCellProps) {
  return (
    <td className="px-3 py-2 text-right align-top tabular-nums">
      <span className={cn(ok ? 'text-slate-900' : 'rounded bg-red-50 px-1 font-semibold text-red-700 ring-1 ring-red-600/20')}>
        {eur(declared)}
      </span>
      {!ok && <span className="mt-0.5 block text-xs text-red-600">Esperado {eur(expected)}</span>}
    </td>
  )
}

interface RateCellProps {
  value: string
  valid: boolean
  warning?: string
  allowed: readonly number[]
}

function RateCell({ value, valid, warning, allowed }: RateCellProps) {
  return (
    <td className="px-3 py-2 text-right align-top tabular-nums">
      <span className={cn(valid ? 'text-slate-900' : 'rounded bg-red-50 px-1 font-semibold text-red-700 ring-1 ring-red-600/20')}>
        {formatPercent(value)}
      </span>
      {!valid && <span className="mt-0.5 block text-xs text-red-600">Admitidos: {allowed.join(', ')}</span>}
      {valid && warning && <span className="mt-0.5 block text-xs text-amber-700">{warning}</span>}
    </td>
  )
}

export function LinesTable({ lines }: { lines: InvoiceLine[] }) {
  return (
    <section aria-labelledby="lines-title" className="overflow-hidden rounded-2xl bg-white shadow-sm ring-1 ring-slate-900/5">
      <h3 id="lines-title" className="flex items-center gap-2 px-4 pt-4 text-sm font-semibold text-slate-900 sm:px-5">
        <ListChecks aria-hidden className="size-4 text-slate-500" />
        Líneas de la factura
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">{lines.length}</span>
      </h3>
      {lines.length === 0 ? (
        <p className="px-4 py-6 text-sm text-slate-500 sm:px-5">La factura no tiene líneas de detalle.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[820px] text-sm">
            <caption className="sr-only">
              Detalle de líneas: los importes en rojo no coinciden con el cálculo y debajo se muestra el valor esperado
            </caption>
            <thead className="bg-slate-50 text-xs text-slate-500">
              <tr>
                <th scope="col" className="px-3 py-2 text-left font-medium">Concepto</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Cant.</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Precio</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Dto.</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Base</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">IVA %</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Cuota IVA</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">IRPF %</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Retención</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {lines.map((line) => {
                const analysis = analyzeLine(line)
                const hasIssue = !analysis.baseOk || !analysis.vatOk || !analysis.irpfOk || !analysis.vatRateValid || !analysis.irpfRateValid
                return (
                  <tr key={line.id} className={cn(hasIssue && 'bg-red-50/40')}>
                    <td className="max-w-[220px] px-3 py-2 align-top">
                      <span className="mr-1.5 text-xs text-slate-400">{line.line_number}.</span>
                      <span className="break-words text-slate-900">{line.concept}</span>
                    </td>
                    <td className="px-3 py-2 text-right align-top tabular-nums text-slate-700">{formatNumber(line.quantity)}</td>
                    <td className="px-3 py-2 text-right align-top tabular-nums text-slate-700">{formatEur(line.unit_price)}</td>
                    <td className="px-3 py-2 text-right align-top tabular-nums text-slate-700">
                      {Number(line.discount_rate) ? formatPercent(line.discount_rate) : '—'}
                    </td>
                    <AmountCell declared={analysis.declaredBase} expected={analysis.expectedBase} ok={analysis.baseOk} />
                    <RateCell
                      value={line.vat_rate}
                      valid={analysis.vatRateValid}
                      warning={analysis.vatRateIsZero ? 'Exenta / no sujeta' : undefined}
                      allowed={VAT_RATE_OPTIONS}
                    />
                    <AmountCell declared={analysis.declaredVat} expected={analysis.expectedVat} ok={analysis.vatOk} />
                    <RateCell value={line.irpf_rate} valid={analysis.irpfRateValid} allowed={IRPF_RATE_OPTIONS} />
                    <AmountCell declared={analysis.declaredIrpf} expected={analysis.expectedIrpf} ok={analysis.irpfOk} />
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
