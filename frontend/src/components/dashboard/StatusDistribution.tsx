import { useMemo } from 'react'
import type { DashboardStats } from '../../types/api'
import type { StatusPreset } from '../invoices/filters'
import { formatEur, formatPercent } from '../../lib/format'
import { ChartCard, EmptyChart } from './ChartCard'

interface Segment {
  preset: StatusPreset
  label: string
  count: number
  /** Total of the invoices in this status; null when the API does not provide it. */
  amount: string | null
  bar: string
  dot: string
}

interface StatusDistributionProps {
  stats: DashboardStats
  onFilter: (preset: StatusPreset) => void
}

export function StatusDistribution({ stats, onFilter }: StatusDistributionProps) {
  const segments = useMemo<Segment[]>(() => {
    const find = (status: string) => stats.by_status.find((item) => item.status === status)
    const status = (preset: StatusPreset, label: string, key: string, color: string): Segment => ({
      preset,
      label,
      count: find(key)?.count ?? 0,
      amount: find(key)?.total_amount ?? '0',
      bar: color,
      dot: color,
    })
    return [
      status('VALIDATED', 'Cuadra', 'VALIDATED', 'bg-emerald-500'),
      status('DISCREPANCY', 'Discrepancia', 'DISCREPANCY', 'bg-red-500'),
      status('DUPLICATE', 'Duplicada', 'DUPLICATE', 'bg-amber-500'),
      status('PENDING', 'Pendiente', 'PENDING', 'bg-slate-400'),
    ]
  }, [stats])

  // The bar is a partition by audit status. "XML FACe generado" counts invoices whatever their current status
  // (exactly the KPI's `exported_to_face`), so it is listed apart and is not part of the bar.
  const exported: Segment = {
    preset: 'EXPORTED',
    label: 'XML FACe generado',
    count: stats.exported_to_face,
    amount: null,
    bar: 'bg-blue-500',
    dot: 'bg-blue-500',
  }
  const rows = [...segments, exported]

  const total = segments.reduce((sum, segment) => sum + segment.count, 0)

  return (
    <ChartCard title="Estado de las facturas" description="Resultado de la auditoría fiscal">
      {total === 0 ? (
        <EmptyChart message="Aún no hay facturas auditadas." />
      ) : (
        <>
          <div
            role="img"
            aria-label={`Distribución por estado: ${segments.map((s) => `${s.label} ${s.count}`).join(', ')}`}
            className="flex h-4 w-full gap-0.5 overflow-hidden rounded-full bg-slate-100"
          >
            {segments
              .filter((segment) => segment.count > 0)
              .map((segment) => (
                <div
                  key={segment.preset}
                  className={segment.bar}
                  style={{ width: `${(segment.count / total) * 100}%` }}
                  title={`${segment.label}: ${segment.count}`}
                />
              ))}
          </div>

          <ul className="mt-5 space-y-1">
            {rows.map((segment) => (
              <li key={segment.preset} className={segment === exported ? 'mt-2 border-t border-slate-100 pt-2' : undefined}>
                <button
                  type="button"
                  disabled={segment.count === 0 || segment.preset === 'PENDING'}
                  onClick={() => onFilter(segment.preset)}
                  className="flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left text-sm transition-colors enabled:hover:bg-slate-50 disabled:cursor-default"
                >
                  <span aria-hidden className={`size-2.5 shrink-0 rounded-full ${segment.dot}`} />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="text-slate-700">{segment.label}</span>
                    <span className="text-xs text-slate-400">
                      {segment.amount === null ? 'Con XML generado, sea cual sea su estado actual' : formatEur(segment.amount)}
                    </span>
                  </span>
                  <span className="w-8 text-right font-semibold text-slate-900">{segment.count}</span>
                  <span className="w-14 text-right text-xs text-slate-500">
                    {formatPercent((segment.count / total) * 100)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </ChartCard>
  )
}
