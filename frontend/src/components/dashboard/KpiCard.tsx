import type { ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'
import { cn } from '../../lib/cn'

export type KpiAccent = 'indigo' | 'emerald' | 'red' | 'blue'

const ACCENTS: Record<KpiAccent, { tile: string; value: string; hover: string }> = {
  indigo: { tile: 'bg-indigo-50 text-indigo-600', value: 'text-slate-900', hover: 'hover:ring-indigo-300' },
  emerald: { tile: 'bg-emerald-50 text-emerald-600', value: 'text-slate-900', hover: 'hover:ring-emerald-300' },
  red: { tile: 'bg-red-50 text-red-600', value: 'text-red-700', hover: 'hover:ring-red-300' },
  blue: { tile: 'bg-blue-50 text-blue-600', value: 'text-blue-700', hover: 'hover:ring-blue-300' },
}

interface KpiCardProps {
  label: string
  value: string
  subline: string
  icon: ReactNode
  accent: KpiAccent
  onClick?: () => void
  actionLabel?: string
}

export function KpiCard({ label, value, subline, icon, accent, onClick, actionLabel }: KpiCardProps) {
  const style = ACCENTS[accent]
  const content = (
    <>
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-slate-500">{label}</p>
        <span aria-hidden className={cn('flex size-9 shrink-0 items-center justify-center rounded-xl', style.tile)}>
          {icon}
        </span>
      </div>
      <p className={cn('mt-3 truncate text-2xl font-semibold tracking-tight sm:text-[1.75rem]', style.value)}>{value}</p>
      <p className="mt-1 flex items-center gap-1 text-xs text-slate-500">
        <span className="truncate">{subline}</span>
        {onClick && <ChevronRight aria-hidden className="ml-auto size-4 shrink-0 text-slate-400" />}
      </p>
    </>
  )

  const base = 'block w-full rounded-2xl bg-white p-5 text-left shadow-sm ring-1 ring-slate-900/5'

  if (!onClick) return <div className={base}>{content}</div>
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`${label}: ${value}. ${subline}. ${actionLabel ?? 'Filtrar la tabla'}`}
      className={cn(base, 'transition-all hover:-translate-y-0.5 hover:shadow-md', style.hover)}
    >
      {content}
    </button>
  )
}
