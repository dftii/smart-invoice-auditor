import { useMemo } from 'react'
import type { VatBreakdown } from '../../types/api'
import { centsToDecimal, formatEur, formatPercent, toCents, toNumber } from '../../lib/format'
import { ChartCard } from './ChartCard'

const STANDARD_RATES = [21, 10, 4, 0]

export function VatBreakdownTable({ breakdown }: { breakdown: VatBreakdown[] }) {
  const rows = useMemo(() => {
    const byRate = new Map(breakdown.map((item) => [toNumber(item.vat_rate), item]))
    const rates = [...new Set([...STANDARD_RATES, ...byRate.keys()])].sort((a, b) => b - a)
    return rates.map((rate) => ({
      rate,
      base: byRate.get(rate)?.tax_base ?? '0',
      vat: byRate.get(rate)?.vat_amount ?? '0',
    }))
  }, [breakdown])

  const totalBase = centsToDecimal(rows.reduce((sum, row) => sum + toCents(row.base), 0))
  const totalVat = centsToDecimal(rows.reduce((sum, row) => sum + toCents(row.vat), 0))

  return (
    <ChartCard title="IVA por tipo impositivo" description="Suma de las líneas de todas las facturas">
      <table className="w-full text-sm">
        <caption className="sr-only">Base imponible e IVA por tipo impositivo</caption>
        <thead>
          <tr className="border-b border-slate-200 text-xs text-slate-500">
            <th scope="col" className="pb-2 text-left font-medium">Tipo</th>
            <th scope="col" className="pb-2 text-right font-medium">Base imponible</th>
            <th scope="col" className="pb-2 text-right font-medium">Cuota IVA</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((row) => (
            <tr key={row.rate} className={toCents(row.base) === 0 ? 'text-slate-400' : 'text-slate-700'}>
              <th scope="row" className="py-2 text-left font-medium">{formatPercent(row.rate)}</th>
              <td className="py-2 text-right">{formatEur(row.base)}</td>
              <td className="py-2 text-right">{formatEur(row.vat)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t border-slate-200 font-semibold text-slate-900">
            <th scope="row" className="pt-2 text-left">Total</th>
            <td className="pt-2 text-right">{formatEur(totalBase)}</td>
            <td className="pt-2 text-right">{formatEur(totalVat)}</td>
          </tr>
        </tfoot>
      </table>
    </ChartCard>
  )
}
