import { CircleAlert, CircleCheck, Clock, Copy, FileCode2 } from 'lucide-react'
import type { AuditStatus } from '../../types/api'
import { STATUS_LABELS } from '../../lib/format'
import { cn } from '../../lib/cn'
import { displayStatus, type DisplayStatus } from '../../lib/status'

const STYLES: Record<DisplayStatus, { label: string; className: string; Icon: typeof CircleCheck }> = {
  VALIDATED: {
    label: STATUS_LABELS.VALIDATED,
    className: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
    Icon: CircleCheck,
  },
  DISCREPANCY: {
    label: STATUS_LABELS.DISCREPANCY,
    className: 'bg-red-50 text-red-700 ring-red-600/20',
    Icon: CircleAlert,
  },
  DUPLICATE: {
    label: STATUS_LABELS.DUPLICATE,
    className: 'bg-amber-50 text-amber-800 ring-amber-600/20',
    Icon: Copy,
  },
  PENDING: {
    label: STATUS_LABELS.PENDING,
    className: 'bg-slate-100 text-slate-700 ring-slate-500/20',
    Icon: Clock,
  },
  EXPORTED: {
    label: 'XML FACe generado',
    className: 'bg-blue-50 text-blue-700 ring-blue-600/20',
    Icon: FileCode2,
  },
}

interface StatusBadgeProps {
  status: AuditStatus
  exportedAt?: string | null
  size?: 'sm' | 'md'
  className?: string
}

export function StatusBadge({ status, exportedAt, size = 'sm', className }: StatusBadgeProps) {
  const style = STYLES[displayStatus(status, exportedAt)]
  const { Icon } = style
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 whitespace-nowrap rounded-full font-medium ring-1 ring-inset',
        size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-sm',
        style.className,
        className,
      )}
    >
      <Icon aria-hidden className={size === 'sm' ? 'size-3.5' : 'size-4'} />
      {style.label}
    </span>
  )
}
