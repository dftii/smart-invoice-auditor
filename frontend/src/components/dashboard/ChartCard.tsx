import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

interface ChartCardProps {
  title: string
  description?: string
  className?: string
  children: ReactNode
}

export function ChartCard({ title, description, className, children }: ChartCardProps) {
  return (
    <section className={cn('rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-900/5', className)}>
      <header className="mb-4">
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
        {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
      </header>
      {children}
    </section>
  )
}

export function EmptyChart({ message }: { message: string }) {
  return (
    <div className="flex min-h-32 items-center justify-center rounded-xl border border-dashed border-slate-200 px-4 py-8 text-center text-sm text-slate-500">
      {message}
    </div>
  )
}
