import { FileCheck2, Receipt, ShieldAlert, Wallet } from 'lucide-react'
import type { DashboardStats } from '../../types/api'
import type { StatusPreset } from '../invoices/filters'
import { formatEur, formatPercent } from '../../lib/format'
import { Skeleton } from '../layout/Skeleton'
import { ErrorState } from '../layout/ErrorState'
import { KpiCard } from './KpiCard'

interface KpiCardsProps {
  stats: DashboardStats | null
  error: Error | null
  onRetry: () => void
  onFilter: (preset: StatusPreset) => void
}

function KpiSkeleton() {
  return (
    <div aria-hidden className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-900/5">
      <div className="flex items-start justify-between">
        <Skeleton className="h-4 w-28" />
        <Skeleton className="size-9 rounded-xl" />
      </div>
      <Skeleton className="mt-4 h-8 w-36" />
      <Skeleton className="mt-3 h-3 w-32" />
    </div>
  )
}

export function KpiCards({ stats, error, onRetry, onFilter }: KpiCardsProps) {
  if (!stats && error) {
    return (
      <div className="rounded-2xl bg-white shadow-sm ring-1 ring-slate-900/5">
        <ErrorState title="No se pudieron cargar los indicadores" message={error.message} onRetry={onRetry} />
      </div>
    )
  }

  if (!stats) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-busy="true" aria-label="Cargando indicadores">
        {Array.from({ length: 4 }, (_, index) => (
          <KpiSkeleton key={index} />
        ))}
      </div>
    )
  }

  const errorShare = stats.total_invoices > 0 ? (stats.invoices_with_errors / stats.total_invoices) * 100 : 0

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <KpiCard
        label="Total facturado"
        value={formatEur(stats.total_amount)}
        subline={`Base imponible ${formatEur(stats.total_tax_base)}`}
        icon={<Wallet className="size-5" />}
        accent="indigo"
      />
      <KpiCard
        label="IVA soportado"
        value={formatEur(stats.total_vat)}
        subline={`IRPF retenido ${formatEur(stats.total_irpf)}`}
        icon={<Receipt className="size-5" />}
        accent="emerald"
      />
      <KpiCard
        label="Facturas con errores"
        value={String(stats.invoices_with_errors)}
        subline={`${formatPercent(errorShare)} del total · discrepancias y duplicadas`}
        icon={<ShieldAlert className="size-5" />}
        accent="red"
        onClick={() => onFilter('ERRORS')}
        actionLabel="Filtrar la tabla por facturas con errores"
      />
      <KpiCard
        label="Exportadas a FACe"
        value={String(stats.exported_to_face)}
        subline={`${stats.ready_for_face} ${stats.ready_for_face === 1 ? 'lista' : 'listas'} para exportar`}
        icon={<FileCheck2 className="size-5" />}
        accent="blue"
        onClick={() => onFilter('EXPORTED')}
        actionLabel="Filtrar la tabla por facturas exportadas"
      />
    </div>
  )
}
