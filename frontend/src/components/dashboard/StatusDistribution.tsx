import { useMemo } from 'react'
import type { DashboardStats } from '../../types/api'
import type { StatusPreset } from '../invoices/filters'
import { formatEur, formatPercent } from '../../lib/format'
import { ChartCard, EmptyChart } from './ChartCard'

interface Segment {
  preset: StatusPreset
  label: string
  count: number
  amount: string
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
    const validated = find('VALIDATED')
    const validatedCount = validated?.count ?? 0
    const exported = Math.min(stats.exported_to_face, validatedCount)
    const validatedAmount = Number(validated?.total_amount ?? 0)
    // Exported invoices are a subset of the validated ones; split the amount proportionally for display only.
    const exportedAmount = validatedCount > 0 ? (validatedAmount * exported) / validatedCount : 0
    return [
      {
        preset: 'VALIDATED',
        label: 'Cuadra',
        count: validatedCount - exported,
        amount: String(validatedAmount - exportedAmount),
        bar: 'bg-emerald-500',
        dot: 'bg-emerald-500',
      },
      {
        preset: 'EXPORTED',
        label: 'XML FACe generado',
        count: exported,
        amount: String(exportedAmount),
        bar: 'bg-blue-500',
        dot: 'bg-blue-500',
      },
      {
        preset: 'DISCREPANCY',
        label: 'Discrepancia',
        count: find('DISCREPANCY')?.count ?? 0,
        amount: find('DISCREPANCY')?.total_amount ?? '0',
        bar: 'bg-red-500',
        dot: 'bg-red-500',
      },
      {
        preset: 'DUPLICATE',
        label: 'Duplicada',
        count: find('DUPLICATE')?.count ?? 0,
        amount: find('DUPLICATE')?.total_amount ?? '0',
        bar: 'bg-amber-500',
        dot: 'bg-amber-500',
      },
      {
        preset: 'PENDING',
        label: 'Pendiente',
        count: find('PENDING')?.count ?? 0,
        amount: find('PENDING')?.total_amount ?? '0',
        bar: 'bg-slate-400',
        dot: 'bg-slate-400',
      },
    ]
  }, [stats])

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
            {segments.map((segment) => (
              <li key={segment.preset}>
                <button
                  type="button"
                  disabled={segment.count === 0 || segment.preset === 'PENDING'}
                  onClick={() => onFilter(segment.preset)}
                  className="flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left text-sm transition-colors enabled:hover:bg-slate-50 disabled:cursor-default"
                >
                  <span aria-hidden className={`size-2.5 shrink-0 rounded-full ${segment.dot}`} />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="text-slate-700">{segment.label}</span>
                    <span className="text-xs text-slate-400">{formatEur(segment.amount)}</span>
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
