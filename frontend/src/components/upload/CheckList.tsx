import { CircleCheck, CircleX, TriangleAlert } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { cn } from '../../lib/cn'
import { ruleLabel } from '../../lib/format'
import type { AuditCheck } from '../../types/api'
import { countChecks, groupChecks, toneOf, type Tone } from './checks'

const TONE_STYLES: Record<Tone, { icon: typeof CircleCheck; className: string; label: string }> = {
  pass: { icon: CircleCheck, className: 'text-emerald-500', label: 'Correcto' },
  warning: { icon: TriangleAlert, className: 'text-amber-500', label: 'Aviso' },
  error: { icon: CircleX, className: 'text-red-500', label: 'Error' },
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`
}

export function CheckCounter({ checks, live }: { checks: AuditCheck[]; live: boolean }) {
  const { total, errors, warnings } = countChecks(checks)
  return (
    <p
      role="status"
      aria-live={live ? 'off' : 'polite'}
      className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs font-medium tabular-nums text-slate-600"
    >
      <span>{plural(total, 'comprobación', 'comprobaciones')}</span>
      <span aria-hidden className="text-slate-300">
        ·
      </span>
      <span className={cn(errors > 0 ? 'text-red-600' : 'text-slate-500')}>{plural(errors, 'error', 'errores')}</span>
      <span aria-hidden className="text-slate-300">
        ·
      </span>
      <span className={cn(warnings > 0 ? 'text-amber-600' : 'text-slate-500')}>{plural(warnings, 'aviso', 'avisos')}</span>
    </p>
  )
}

interface CheckListProps {
  /** Checks already revealed, in `orderChecks` order. */
  checks: AuditCheck[]
  /** While true the list follows the newest row. */
  live: boolean
}

export function CheckList({ checks, live }: CheckListProps) {
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const element = scroller.current
    if (live && element) element.scrollTop = element.scrollHeight
  }, [checks.length, live])

  return (
    <div ref={scroller} className="max-h-64 space-y-2.5 overflow-y-auto rounded-xl bg-slate-50 p-3 ring-1 ring-slate-900/5">
      {groupChecks(checks).map((group) => {
        const failures = group.checks.filter((check) => !check.passed).length
        return (
          <section key={group.ruleCode} aria-label={ruleLabel(group.ruleCode)}>
            <h4 className="mb-1 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              {ruleLabel(group.ruleCode)}
              {failures > 0 && <span className="rounded-full bg-slate-200 px-1.5 text-[10px] text-slate-600">{failures}</span>}
            </h4>
            <ul className="space-y-1">
              {group.checks.map((check, index) => {
                const tone = TONE_STYLES[toneOf(check)]
                const Icon = tone.icon
                return (
                  <li key={index} className="flex animate-fade-in items-start gap-2 text-sm leading-snug">
                    <Icon aria-hidden className={cn('mt-0.5 size-4 shrink-0', tone.className)} />
                    <span className="sr-only">{tone.label}: </span>
                    <span className="min-w-0 flex-1 break-words text-slate-700">{check.message}</span>
                    {check.line_number !== null && (
                      <span className="shrink-0 rounded bg-white px-1.5 py-0.5 text-[11px] font-medium text-slate-500 ring-1 ring-slate-200">
                        Línea {check.line_number}
                      </span>
                    )}
                  </li>
                )
              })}
            </ul>
          </section>
        )
      })}
    </div>
  )
}
