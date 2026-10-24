import { ArrowDown, ArrowUp, ChevronsUpDown } from 'lucide-react'
import type { SortField } from '../../types/api'
import { cn } from '../../lib/cn'
import type { SortState } from './filters'

interface SortHeaderProps {
  field: SortField
  label: string
  sort: SortState
  onSort: (field: SortField) => void
  align?: 'left' | 'right'
  className?: string
}

export function SortHeader({ field, label, sort, onSort, align = 'left', className }: SortHeaderProps) {
  const active = sort.by === field
  const Icon = !active ? ChevronsUpDown : sort.dir === 'asc' ? ArrowUp : ArrowDown
  return (
    <th
      scope="col"
      aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
      className={cn('whitespace-nowrap px-3 py-2.5 font-medium', align === 'right' ? 'text-right' : 'text-left', className)}
    >
      <button
        type="button"
        onClick={() => onSort(field)}
        className={cn(
          '-mx-1 inline-flex items-center gap-1 rounded px-1 py-0.5 transition-colors hover:text-slate-900',
          active ? 'text-slate-900' : 'text-slate-500',
          align === 'right' && 'flex-row-reverse',
        )}
      >
        {label}
        <Icon aria-hidden className={cn('size-3.5', !active && 'opacity-50')} />
      </button>
    </th>
  )
}
