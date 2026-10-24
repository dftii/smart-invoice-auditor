import { CircleAlert, TriangleAlert } from 'lucide-react'

interface IssueCountersProps {
  errors: number
  warnings: number
}

export function IssueCounters({ errors, warnings }: IssueCountersProps) {
  if (errors === 0 && warnings === 0) return null
  return (
    <span className="inline-flex items-center gap-1">
      {errors > 0 && (
        <span
          title={`${errors} ${errors === 1 ? 'error' : 'errores'}`}
          className="inline-flex items-center gap-0.5 rounded-full bg-red-50 px-1.5 py-0.5 text-xs font-semibold text-red-700 ring-1 ring-inset ring-red-600/20"
        >
          <CircleAlert aria-hidden className="size-3" />
          {errors}
          <span className="sr-only">{errors === 1 ? 'error' : 'errores'}</span>
        </span>
      )}
      {warnings > 0 && (
        <span
          title={`${warnings} ${warnings === 1 ? 'aviso' : 'avisos'}`}
          className="inline-flex items-center gap-0.5 rounded-full bg-amber-50 px-1.5 py-0.5 text-xs font-semibold text-amber-800 ring-1 ring-inset ring-amber-600/20"
        >
          <TriangleAlert aria-hidden className="size-3" />
          {warnings}
          <span className="sr-only">{warnings === 1 ? 'aviso' : 'avisos'}</span>
        </span>
      )}
    </span>
  )
}
