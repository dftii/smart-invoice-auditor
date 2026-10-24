import type { ReactNode } from 'react'
import { RotateCcw, Search, X } from 'lucide-react'
import { Button } from '../ui/Button'
import { cn } from '../../lib/cn'
import {
  SORT_OPTIONS,
  STATUS_PRESETS,
  hasActiveFilters,
  type FilterValues,
  type SortState,
  type StatusPreset,
} from './filters'

interface InvoiceFiltersBarProps {
  values: FilterValues
  sort: SortState
  onChange: (patch: Partial<FilterValues>) => void
  onSortChange: (sort: SortState) => void
  onReset: () => void
}

const inputClass =
  'h-9 w-full rounded-lg border-0 bg-white px-3 text-sm text-slate-900 shadow-sm ring-1 ring-inset ring-slate-300 placeholder:text-slate-400 focus:ring-2 focus:ring-indigo-500'

function Field({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <label className={cn('flex flex-col gap-1 text-xs font-medium text-slate-500', className)}>
      {label}
      {children}
    </label>
  )
}

export function InvoiceFiltersBar({ values, sort, onChange, onSortChange, onReset }: InvoiceFiltersBarProps) {
  const active = hasActiveFilters(values)
  const sortValue = `${sort.by}:${sort.dir}`

  return (
    <div className="space-y-3 border-b border-slate-200 p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="relative flex-1">
          <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
          <input
            type="search"
            value={values.search}
            onChange={(event) => onChange({ search: event.target.value })}
            placeholder="Buscar por nº de factura, proveedor o NIF…"
            aria-label="Buscar facturas"
            className={cn(inputClass, 'pl-9 pr-9')}
          />
          {values.search && (
            <button
              type="button"
              onClick={() => onChange({ search: '' })}
              aria-label="Borrar búsqueda"
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-slate-400 hover:text-slate-600"
            >
              <X aria-hidden className="size-4" />
            </button>
          )}
        </div>

        <div
          role="group"
          aria-label="Filtrar por estado"
          className="-mx-1 flex gap-1 overflow-x-auto rounded-xl bg-slate-100 p-1 lg:mx-0"
        >
          {STATUS_PRESETS.map((option) => {
            const selected = values.preset === option.value
            return (
              <button
                key={option.value}
                type="button"
                aria-pressed={selected}
                onClick={() => onChange({ preset: option.value as StatusPreset })}
                className={cn(
                  'shrink-0 whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors',
                  selected ? 'bg-white text-slate-900 shadow-sm ring-1 ring-slate-900/5' : 'text-slate-600 hover:text-slate-900',
                )}
              >
                {option.label}
              </button>
            )
          })}
        </div>
      </div>

      <div className="grid grid-cols-2 items-end gap-3 sm:grid-cols-4 lg:grid-cols-[repeat(4,minmax(0,1fr))_auto]">
        <Field label="Desde">
          <input
            type="date"
            value={values.dateFrom}
            max={values.dateTo || undefined}
            onChange={(event) => onChange({ dateFrom: event.target.value })}
            className={inputClass}
          />
        </Field>
        <Field label="Hasta">
          <input
            type="date"
            value={values.dateTo}
            min={values.dateFrom || undefined}
            onChange={(event) => onChange({ dateTo: event.target.value })}
            className={inputClass}
          />
        </Field>
        <Field label="Total mínimo (€)">
          <input
            type="text"
            inputMode="decimal"
            value={values.minTotal}
            placeholder="0,00"
            onChange={(event) => onChange({ minTotal: event.target.value })}
            className={inputClass}
          />
        </Field>
        <Field label="Total máximo (€)">
          <input
            type="text"
            inputMode="decimal"
            value={values.maxTotal}
            placeholder="Sin límite"
            onChange={(event) => onChange({ maxTotal: event.target.value })}
            className={inputClass}
          />
        </Field>
        <Button
          variant="ghost"
          size="sm"
          className="col-span-2 h-9 sm:col-span-4 lg:col-span-1"
          disabled={!active}
          onClick={onReset}
          icon={<RotateCcw aria-hidden className="size-4" />}
        >
          Restablecer
        </Button>
      </div>

      <Field label="Ordenar por" className="md:hidden">
        <select
          value={sortValue}
          onChange={(event) => {
            const option = SORT_OPTIONS.find((item) => `${item.by}:${item.dir}` === event.target.value)
            if (option) onSortChange({ by: option.by, dir: option.dir })
          }}
          className={inputClass}
        >
          {SORT_OPTIONS.map((option) => (
            <option key={`${option.by}:${option.dir}`} value={`${option.by}:${option.dir}`}>
              {option.label}
            </option>
          ))}
        </select>
      </Field>
    </div>
  )
}
