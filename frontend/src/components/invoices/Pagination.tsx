import { ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '../../lib/cn'
import { PAGE_SIZES } from './filters'

interface PaginationProps {
  page: number
  pages: number
  pageSize: number
  total: number
  onPage: (page: number) => void
  onPageSize: (size: number) => void
}

/** Page numbers to render: first, last, and a window around the current page, with null as an ellipsis. */
function pageWindow(page: number, pages: number): (number | null)[] {
  const wanted = new Set([1, pages, page - 1, page, page + 1])
  const sorted = [...wanted].filter((value) => value >= 1 && value <= pages).sort((a, b) => a - b)
  const result: (number | null)[] = []
  sorted.forEach((value, index) => {
    if (index > 0 && value - sorted[index - 1] > 1) result.push(null)
    result.push(value)
  })
  return result
}

const pageButton =
  'inline-flex size-8 items-center justify-center rounded-lg text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:text-slate-300'

export function Pagination({ page, pages, pageSize, total, onPage, onPageSize }: PaginationProps) {
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1
  const to = Math.min(total, page * pageSize)

  return (
    <div className="flex flex-col gap-3 border-t border-slate-200 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-slate-600">
        <p aria-live="polite">
          Mostrando <span className="font-medium text-slate-900">{from}–{to}</span> de{' '}
          <span className="font-medium text-slate-900">{total}</span>
        </p>
        <label className="flex items-center gap-2 text-xs text-slate-500">
          Filas por página
          <select
            value={pageSize}
            onChange={(event) => onPageSize(Number(event.target.value))}
            className="h-8 rounded-lg border-0 bg-white py-0 pl-2 pr-7 text-sm text-slate-700 shadow-sm ring-1 ring-inset ring-slate-300 focus:ring-2 focus:ring-indigo-500"
          >
            {PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </label>
      </div>

      {pages > 1 && (
        <nav aria-label="Paginación" className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => onPage(page - 1)}
            disabled={page <= 1}
            aria-label="Página anterior"
            className={cn(pageButton, 'text-slate-600 hover:bg-slate-100')}
          >
            <ChevronLeft aria-hidden className="size-4" />
          </button>
          {pageWindow(page, pages).map((item, index) =>
            item === null ? (
              <span key={`gap-${index}`} aria-hidden className="px-1 text-slate-400">
                …
              </span>
            ) : (
              <button
                key={item}
                type="button"
                onClick={() => onPage(item)}
                aria-label={`Página ${item}`}
                aria-current={item === page ? 'page' : undefined}
                className={cn(
                  pageButton,
                  item === page ? 'bg-indigo-600 text-white shadow-sm' : 'text-slate-600 hover:bg-slate-100',
                )}
              >
                {item}
              </button>
            ),
          )}
          <button
            type="button"
            onClick={() => onPage(page + 1)}
            disabled={page >= pages}
            aria-label="Página siguiente"
            className={cn(pageButton, 'text-slate-600 hover:bg-slate-100')}
          >
            <ChevronRight aria-hidden className="size-4" />
          </button>
        </nav>
      )}
    </div>
  )
}
