import { FlaskConical, PenLine, Sparkles } from 'lucide-react'
import type { ExtractionMethod } from '../../types/api'
import { cn } from '../../lib/cn'

const ORIGINS: Record<ExtractionMethod, { label: string; title: string; className: string; Icon: typeof Sparkles }> = {
  AI: { label: 'IA', title: 'Extraída con inteligencia artificial', className: 'bg-indigo-50 text-indigo-700 ring-indigo-600/20', Icon: Sparkles },
  DEMO: { label: 'Demo', title: 'Datos de demostración', className: 'bg-amber-50 text-amber-800 ring-amber-600/20', Icon: FlaskConical },
  MANUAL: { label: 'Manual', title: 'Introducida o corregida a mano', className: 'bg-slate-100 text-slate-700 ring-slate-500/20', Icon: PenLine },
}

export function OriginChip({ method }: { method: ExtractionMethod }) {
  const { label, title, className, Icon } = ORIGINS[method]
  return (
    <span
      title={title}
      className={cn('inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset', className)}
    >
      <Icon aria-hidden className="size-3" />
      {label}
    </span>
  )
}
