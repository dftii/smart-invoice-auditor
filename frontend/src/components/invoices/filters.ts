import type { AuditStatus, InvoiceFilters, SortDir, SortField } from '../../types/api'

export type StatusPreset = 'ALL' | AuditStatus | 'ERRORS' | 'EXPORTED'

export const STATUS_PRESETS: { value: StatusPreset; label: string }[] = [
  { value: 'ALL', label: 'Todas' },
  { value: 'VALIDATED', label: 'Cuadra' },
  { value: 'DISCREPANCY', label: 'Discrepancia' },
  { value: 'DUPLICATE', label: 'Duplicada' },
  { value: 'ERRORS', label: 'Con errores' },
  { value: 'EXPORTED', label: 'XML FACe generado' },
]

export interface FilterValues {
  search: string
  preset: StatusPreset
  dateFrom: string
  dateTo: string
  minTotal: string
  maxTotal: string
}

export const EMPTY_FILTERS: FilterValues = {
  search: '',
  preset: 'ALL',
  dateFrom: '',
  dateTo: '',
  minTotal: '',
  maxTotal: '',
}

export interface SortState {
  by: SortField
  dir: SortDir
}

export const DEFAULT_SORT: SortState = { by: 'issue_date', dir: 'desc' }

export const SORT_OPTIONS: { by: SortField; dir: SortDir; label: string }[] = [
  { by: 'issue_date', dir: 'desc', label: 'Fecha: más recientes' },
  { by: 'issue_date', dir: 'asc', label: 'Fecha: más antiguas' },
  { by: 'total_amount', dir: 'desc', label: 'Total: mayor a menor' },
  { by: 'total_amount', dir: 'asc', label: 'Total: menor a mayor' },
  { by: 'invoice_number', dir: 'asc', label: 'Nº factura: A-Z' },
  { by: 'audit_status', dir: 'asc', label: 'Estado' },
]

/** Accepts "1.234,5" / "1234.50" while typing; returns a plain decimal string or '' when unusable. */
export function normalizeAmount(raw: string): string {
  const text = raw.trim().replace(/\s/g, '')
  if (!text) return ''
  const normalized = text.includes(',') ? text.replace(/\./g, '').replace(',', '.') : text
  return /^\d+(\.\d+)?$/.test(normalized) ? normalized : ''
}

export function toApiFilters(values: FilterValues, sort: SortState): InvoiceFilters {
  const filters: InvoiceFilters = { sort_by: sort.by, sort_dir: sort.dir }
  const search = values.search.trim()
  if (search) filters.search = search
  if (values.preset === 'EXPORTED') {
    filters.exported = true
  } else if (values.preset === 'ERRORS') {
    filters.has_errors = true
  } else if (values.preset !== 'ALL') {
    filters.status = values.preset
  }
  if (values.dateFrom) filters.date_from = values.dateFrom
  if (values.dateTo) filters.date_to = values.dateTo
  const min = normalizeAmount(values.minTotal)
  const max = normalizeAmount(values.maxTotal)
  if (min) filters.min_total = min
  if (max) filters.max_total = max
  return filters
}

export function hasActiveFilters(values: FilterValues): boolean {
  return (Object.keys(EMPTY_FILTERS) as (keyof FilterValues)[]).some((key) => values[key] !== EMPTY_FILTERS[key])
}

export const PAGE_SIZES = [10, 25, 50]
