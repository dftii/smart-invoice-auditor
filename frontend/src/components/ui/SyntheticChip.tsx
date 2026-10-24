import { FlaskConical } from 'lucide-react'
import { cn } from '../../lib/cn'

/** Amber marker for demo/synthetic invoices: they can be audited and edited, but never exported to FACe. */
export function SyntheticChip({ className }: { className?: string }) {
  return (
    <span
      title="Datos sintéticos: no se pueden exportar a FACe"
      className={cn(
        'inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-600/25',
        className,
      )}
    >
      <FlaskConical aria-hidden className="size-3" />
      Datos sintéticos
    </span>
  )
}
