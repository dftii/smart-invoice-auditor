import { useCallback, useMemo, useState } from 'react'
import { Download, FileSearch, FileUp, SearchX } from 'lucide-react'
import type { InvoiceFilters, SortField } from '../../types/api'
import { api } from '../../services/api'
import { useAsync } from '../../hooks/useAsync'
import { useDebouncedValue } from '../../hooks/useDebouncedValue'
import { Button } from '../ui/Button'
import { useToast } from '../ui/Toast'
import { ErrorState } from '../layout/ErrorState'
import { Skeleton } from '../layout/Skeleton'
import { InvoiceCard } from './InvoiceCard'
import { InvoiceFiltersBar } from './InvoiceFiltersBar'
import { InvoiceRow } from './InvoiceRow'
import { Pagination } from './Pagination'
import { SortHeader } from './SortHeader'
import {
  DEFAULT_SORT,
  PAGE_SIZES,
  EMPTY_FILTERS,
  hasActiveFilters,
  toApiFilters,
  type FilterValues,
  type SortState,
  type StatusPreset,
} from './filters'

/** A request from outside the table (KPI card, chart legend) to show one status; `id` makes repeats distinct. */
export interface PresetRequest {
  id: number
  preset: StatusPreset
}

interface InvoiceTableProps {
  selectedId: number | null
  refreshKey: number
  presetRequest: PresetRequest | null
  onSelect: (id: number) => void
}

const SKELETON_ROWS = 6
const DEFAULT_FIRST_SORT: Partial<Record<SortField, 'asc' | 'desc'>> = { invoice_number: 'asc', audit_status: 'asc' }

function TableSkeleton() {
  return (
    <div aria-hidden className="divide-y divide-slate-100">
      {Array.from({ length: SKELETON_ROWS }, (_, index) => (
        <div key={index} className="flex items-center gap-4 px-4 py-4">
          <Skeleton className="h-4 w-24" />
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-4 w-48 max-w-full" />
            <Skeleton className="h-3 w-24" />
          </div>
          <Skeleton className="hidden h-4 w-20 sm:block" />
          <Skeleton className="h-4 w-20" />
          <Skeleton className="hidden h-6 w-28 rounded-full md:block" />
        </div>
      ))}
    </div>
  )
}

interface EmptyProps {
  filtered: boolean
  onReset: () => void
}

function EmptyState({ filtered, onReset }: EmptyProps) {
  if (filtered) {
    return (
      <div className="flex flex-col items-center gap-3 px-6 py-14 text-center">
        <span className="flex size-12 items-center justify-center rounded-full bg-slate-100 text-slate-500">
          <SearchX aria-hidden className="size-6" />
        </span>
        <div>
          <p className="text-sm font-semibold text-slate-900">Ninguna factura coincide con los filtros</p>
          <p className="mt-1 text-sm text-slate-500">Prueba con otra búsqueda o amplía el rango de fechas e importes.</p>
        </div>
        <Button variant="secondary" size="sm" onClick={onReset}>
          Quitar filtros
        </Button>
      </div>
    )
  }
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-14 text-center">
      <span className="flex size-12 items-center justify-center rounded-full bg-indigo-50 text-indigo-600">
        <FileSearch aria-hidden className="size-6" />
      </span>
      <div>
        <p className="text-sm font-semibold text-slate-900">Todavía no hay facturas</p>
        <p className="mt-1 max-w-md text-sm text-slate-500">
          <FileUp aria-hidden className="mr-1 inline size-4 align-text-bottom" />
          Sube un PDF o una imagen en la zona de arriba, o carga datos de ejemplo ejecutando{' '}
          <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-800">python scripts/seed.py</code>{' '}
          desde la carpeta <code className="font-mono text-xs">backend</code>.
        </p>
      </div>
    </div>
  )
}

export function InvoiceTable({ selectedId, refreshKey, presetRequest, onSelect }: InvoiceTableProps) {
  const toast = useToast()
  const [values, setValues] = useState<FilterValues>(EMPTY_FILTERS)
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(PAGE_SIZES[0])
  const [handledRequest, setHandledRequest] = useState<number | null>(null)
  const [exporting, setExporting] = useState(false)

  // Apply an external preset request during render so the very first fetch already uses it.
  if (presetRequest && presetRequest.id !== handledRequest) {
    setHandledRequest(presetRequest.id)
    setValues({ ...EMPTY_FILTERS, preset: presetRequest.preset })
  }

  // Text inputs hit the server only after the user pauses typing.
  const typed = useMemo(
    () => ({ search: values.search, minTotal: values.minTotal, maxTotal: values.maxTotal }),
    [values.search, values.minTotal, values.maxTotal],
  )
  const settled = useDebouncedValue(typed, 300)
  const filtersKey = JSON.stringify(toApiFilters({ ...values, ...settled }, sort))
  // Keyed by content so typing that has not reached the server yet does not trigger a refetch.
  const apiFilters = useMemo<InvoiceFilters>(() => JSON.parse(filtersKey), [filtersKey])

  // Any change to what is being queried goes back to the first page (adjusted during render, not in an effect).
  const queryKey = `${filtersKey}|${pageSize}`
  const [lastQueryKey, setLastQueryKey] = useState(queryKey)
  if (queryKey !== lastQueryKey) {
    setLastQueryKey(queryKey)
    setPage(1)
  }

  const fetchPage = useCallback(() => api.listInvoices(apiFilters, page, pageSize), [apiFilters, page, pageSize])
  const { data, error, loading, reload } = useAsync(fetchPage, refreshKey)

  // After deletions the current page can fall past the end.
  if (data && data.pages > 0 && page > data.pages) setPage(data.pages)

  const patchValues = (patch: Partial<FilterValues>) => setValues((current) => ({ ...current, ...patch }))
  const reset = () => setValues(EMPTY_FILTERS)

  const toggleSort = (field: SortField) =>
    setSort((current) =>
      current.by === field
        ? { by: field, dir: current.dir === 'asc' ? 'desc' : 'asc' }
        : { by: field, dir: DEFAULT_FIRST_SORT[field] ?? 'desc' },
    )

  const exportCsv = async () => {
    setExporting(true)
    try {
      const filename = await api.downloadCsv(apiFilters)
      toast.success('CSV exportado', filename)
    } catch (reason) {
      toast.error('No se pudo exportar el CSV', reason instanceof Error ? reason.message : undefined)
    } finally {
      setExporting(false)
    }
  }

  const items = data?.items ?? []
  const filtered = hasActiveFilters(values)
  const initialLoad = loading && !data
  const refreshing = loading && !!data

  return (
    <section
      id="facturas"
      aria-labelledby="invoices-title"
      className="scroll-mt-20 overflow-hidden rounded-2xl bg-white shadow-sm ring-1 ring-slate-900/5"
    >
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-4 sm:px-5">
        <div>
          <h2 id="invoices-title" className="text-base font-semibold text-slate-900">
            Facturas
          </h2>
          <p className="text-xs text-slate-500">Haz clic en una fila para ver la auditoría y exportar a Facturae.</p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          loading={exporting}
          disabled={!data || data.total === 0}
          onClick={exportCsv}
          icon={<Download aria-hidden className="size-4" />}
        >
          Exportar CSV
        </Button>
      </header>

      <InvoiceFiltersBar values={values} sort={sort} onChange={patchValues} onSortChange={setSort} onReset={reset} />

      <div aria-busy={loading} className="relative">
        {refreshing && <div aria-hidden className="absolute inset-x-0 top-0 z-10 h-0.5 animate-pulse bg-indigo-500" />}

        {error && !data ? (
          <ErrorState title="No se pudieron cargar las facturas" message={error.message} onRetry={reload} />
        ) : initialLoad ? (
          <TableSkeleton />
        ) : items.length === 0 ? (
          <EmptyState filtered={filtered} onReset={reset} />
        ) : (
          <div className={refreshing ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
            {error && (
              <p role="alert" className="border-b border-red-100 bg-red-50 px-4 py-2 text-sm text-red-700">
                No se pudo actualizar la lista: {error.message}{' '}
                <button type="button" onClick={reload} className="font-semibold underline">
                  Reintentar
                </button>
              </p>
            )}

            <div className="hidden overflow-x-auto md:block">
              <table className="w-full text-sm">
                <caption className="sr-only">Listado de facturas</caption>
                <thead className="border-b border-slate-200 bg-slate-50/70 text-xs">
                  <tr>
                    <SortHeader field="invoice_number" label="Nº factura" sort={sort} onSort={toggleSort} className="pl-4" />
                    <th scope="col" className="px-3 py-2.5 text-left font-medium text-slate-500">Proveedor</th>
                    <SortHeader field="issue_date" label="Fecha" sort={sort} onSort={toggleSort} />
                    <th scope="col" className="hidden px-3 py-2.5 text-right font-medium text-slate-500 lg:table-cell">Base</th>
                    <th scope="col" className="hidden px-3 py-2.5 text-right font-medium text-slate-500 lg:table-cell">IVA</th>
                    <SortHeader field="total_amount" label="Total" sort={sort} onSort={toggleSort} align="right" />
                    <SortHeader field="audit_status" label="Estado" sort={sort} onSort={toggleSort} />
                    <th scope="col" className="hidden px-3 py-2.5 text-left font-medium text-slate-500 lg:table-cell">Origen</th>
                    <th scope="col" className="py-2.5 pl-3 pr-4">
                      <span className="sr-only">Acciones</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {items.map((invoice) => (
                    <InvoiceRow key={invoice.id} invoice={invoice} selected={invoice.id === selectedId} onSelect={onSelect} />
                  ))}
                </tbody>
              </table>
            </div>

            <ul className="divide-y divide-slate-100 md:hidden">
              {items.map((invoice) => (
                <InvoiceCard key={invoice.id} invoice={invoice} onSelect={onSelect} />
              ))}
            </ul>
          </div>
        )}
      </div>

      {data && data.total > 0 && (
        <Pagination
          page={data.page}
          pages={data.pages}
          pageSize={pageSize}
          total={data.total}
          onPage={setPage}
          onPageSize={setPageSize}
        />
      )}
    </section>
  )
}
