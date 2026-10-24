import type { DashboardStats } from '../../types/api'
import type { StatusPreset } from '../invoices/filters'
import { cn } from '../../lib/cn'
import { Skeleton } from '../layout/Skeleton'
import { MonthlyChart } from './MonthlyChart'
import { StatusDistribution } from './StatusDistribution'
import { TopRules } from './TopRules'
import { VatBreakdownTable } from './VatBreakdownTable'

interface AnalyticsRowProps {
  stats: DashboardStats | null
  onFilter: (preset: StatusPreset) => void
}

function ChartSkeleton({ className }: { className?: string }) {
  return (
    <div aria-hidden className={cn('rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-900/5', className)}>
      <Skeleton className="h-4 w-40" />
      <Skeleton className="mt-2 h-3 w-56" />
      <Skeleton className="mt-6 h-40 w-full" />
    </div>
  )
}

export function AnalyticsRow({ stats, onFilter }: AnalyticsRowProps) {
  if (!stats) {
    return (
      <div className="grid gap-4 lg:grid-cols-3" aria-busy="true" aria-label="Cargando gráficos">
        <ChartSkeleton className="lg:col-span-2" />
        <ChartSkeleton />
      </div>
    )
  }

  return (
    <div className="grid gap-4 lg:grid-cols-3 lg:items-start">
      <div className="flex min-w-0 flex-col gap-4 lg:col-span-2">
        <MonthlyChart monthly={stats.monthly} />
        <VatBreakdownTable breakdown={stats.vat_breakdown} />
      </div>
      <div className="flex min-w-0 flex-col gap-4">
        <StatusDistribution stats={stats} onFilter={onFilter} />
        <TopRules rules={stats.top_failed_rules} />
      </div>
    </div>
  )
}
