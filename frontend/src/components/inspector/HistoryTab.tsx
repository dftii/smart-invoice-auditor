import { useEffect, useMemo, useState } from 'react'
import { ChevronDown, CircleAlert, CircleCheck, History, LoaderCircle, TriangleAlert } from 'lucide-react'
import { api } from '../../services/api'
import { cn } from '../../lib/cn'
import { formatDateTime, ruleLabel } from '../../lib/format'
import type { ValidationLog } from '../../types/api'
import { Button } from '../ui/Button'

interface Run {
  runId: string
  at: string
  logs: ValidationLog[]
  passed: number
  errors: number
  warnings: number
}

function groupRuns(logs: ValidationLog[]): Run[] {
  const byRun = new Map<string, ValidationLog[]>()
  for (const log of logs) byRun.set(log.run_id, [...(byRun.get(log.run_id) ?? []), log])
  const runs: (Run & { lastId: number })[] = [...byRun.entries()].map(([runId, items]) => ({
    runId,
    at: items.reduce((latest, item) => (item.created_at > latest ? item.created_at : latest), items[0].created_at),
    lastId: Math.max(...items.map((item) => item.id)),
    logs: items,
    passed: items.filter((item) => item.passed).length,
    errors: items.filter((item) => !item.passed && item.severity === 'ERROR').length,
    warnings: items.filter((item) => !item.passed && item.severity !== 'ERROR').length,
  }))
  return runs.sort((a, b) => b.lastId - a.lastId)
}

function RunCard({ run, latest, defaultOpen }: { run: Run; latest: boolean; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const failed = run.logs.filter((log) => !log.passed)
  const regionId = `run-${run.runId}`
  return (
    <li className="rounded-2xl bg-white shadow-sm ring-1 ring-slate-900/5">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={regionId}
        className="flex w-full flex-wrap items-center gap-x-3 gap-y-1.5 rounded-2xl px-4 py-3 text-left hover:bg-slate-50 sm:px-5"
      >
        <span
          className={cn(
            'flex size-8 shrink-0 items-center justify-center rounded-full',
            run.errors > 0 ? 'bg-red-50 text-red-600' : 'bg-emerald-50 text-emerald-600',
          )}
        >
          {run.errors > 0 ? <CircleAlert aria-hidden className="size-4" /> : <CircleCheck aria-hidden className="size-4" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-slate-900">
            {formatDateTime(run.at)}
            {latest && (
              <span className="ml-2 rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-inset ring-indigo-600/20">
                Última
              </span>
            )}
          </span>
          <span className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-slate-500">
            <span className="text-emerald-700">{run.passed} superadas</span>
            <span className={run.errors > 0 ? 'font-medium text-red-700' : undefined}>{run.errors} {run.errors === 1 ? 'error' : 'errores'}</span>
            <span className={run.warnings > 0 ? 'font-medium text-amber-700' : undefined}>{run.warnings} {run.warnings === 1 ? 'aviso' : 'avisos'}</span>
          </span>
        </span>
        <ChevronDown aria-hidden className={cn('size-4 text-slate-400 transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div id={regionId} className="animate-fade-in border-t border-slate-100 px-4 py-3 sm:px-5">
          {failed.length === 0 ? (
            <p className="text-sm text-emerald-700">Todas las comprobaciones se superaron.</p>
          ) : (
            <ul className="space-y-2">
              {failed.map((log) => (
                <li key={log.id} className="flex items-start gap-2 text-sm">
                  {log.severity === 'ERROR' ? (
                    <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-red-600" />
                  ) : (
                    <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-amber-600" />
                  )}
                  <span className="min-w-0">
                    <span className="font-medium text-slate-900">{ruleLabel(log.rule_code)}</span>
                    {log.line_number !== null && <span className="text-slate-500"> · línea {log.line_number}</span>}
                    <span className="block break-words text-slate-600">{log.message}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  )
}

/** `refreshToken` changes whenever a new audit run exists, which reloads the list. */
export function HistoryTab({ invoiceId, refreshToken }: { invoiceId: number; refreshToken: string | null }) {
  const [logs, setLogs] = useState<ValidationLog[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    api
      .getLogs(invoiceId, true)
      .then((result) => {
        if (cancelled) return
        setLogs(result)
        setError(null)
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'No se pudo cargar el historial')
      })
    return () => {
      cancelled = true
    }
  }, [invoiceId, refreshToken, attempt])

  const runs = useMemo(() => (logs ? groupRuns(logs) : []), [logs])

  if (error) {
    return (
      <div role="alert" className="rounded-2xl bg-red-50 p-4 text-sm text-red-800 ring-1 ring-red-600/20">
        <p>{error}</p>
        <Button className="mt-3" size="sm" variant="secondary" onClick={() => setAttempt((value) => value + 1)}>
          Reintentar
        </Button>
      </div>
    )
  }
  if (!logs) {
    return (
      <p role="status" className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500">
        <LoaderCircle aria-hidden className="size-4 animate-spin" />
        Cargando historial…
      </p>
    )
  }
  if (runs.length === 0) {
    return (
      <p className="flex flex-col items-center gap-2 py-10 text-sm text-slate-500">
        <History aria-hidden className="size-6 text-slate-400" />
        Esta factura todavía no se ha auditado.
      </p>
    )
  }
  return (
    <div>
      <p className="mb-3 text-sm text-slate-600">
        {runs.length} {runs.length === 1 ? 'auditoría registrada' : 'auditorías registradas'}, de la más reciente a la más antigua.
      </p>
      <ul className="space-y-2">
        {runs.map((run, index) => (
          <RunCard key={run.runId} run={run} latest={index === 0} defaultOpen={index === 0} />
        ))}
      </ul>
    </div>
  )
}
