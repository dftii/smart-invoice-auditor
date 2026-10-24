import { useState } from 'react'
import { ChevronDown, CircleAlert, CircleCheck, TriangleAlert, Wand2 } from 'lucide-react'
import { cn } from '../../lib/cn'
import { formatEur, ruleLabel } from '../../lib/format'
import type { AuditCheck } from '../../types/api'
import { Button } from '../ui/Button'
import { currentFixValue, type InvoiceDraft } from './draft'
import { fixForCheck, sameDecimal, type FixTarget } from './reconcile'

export type CheckFix = { target: FixTarget; value: string }

interface FailedChecksProps {
  checks: AuditCheck[]
  /** Draft being edited, to tell which expected values were already applied. */
  draft: InvoiceDraft | null
  onApplyFix: (fix: CheckFix) => void
}

function Chip({ label, value, tone }: { label: string; value: string; tone: 'expected' | 'found' }) {
  return (
    <span
      className={cn(
        'inline-flex max-w-full items-center gap-1 rounded-md px-2 py-1 text-xs ring-1 ring-inset',
        tone === 'expected' ? 'bg-emerald-50 text-emerald-800 ring-emerald-600/20' : 'bg-white text-red-700 ring-red-600/25',
      )}
    >
      <span className="font-medium">{label}</span>
      <span className="break-all font-semibold tabular-nums">{value}</span>
    </span>
  )
}

function CheckCard({ check, draft, onApplyFix }: { check: AuditCheck } & Omit<FailedChecksProps, 'checks'>) {
  const isError = check.severity === 'ERROR'
  const fix = fixForCheck(check)
  const monetary = fix !== null
  const show = (value: string) => (monetary ? formatEur(value) : value)
  const applied = fix !== null && draft !== null && (() => {
    const current = currentFixValue(draft, fix.target)
    return current !== null && sameDecimal(current, fix.value)
  })()

  return (
    <li
      className={cn(
        'rounded-xl p-3 ring-1 sm:p-4',
        isError ? 'bg-red-50/60 ring-red-600/20' : 'bg-amber-50/60 ring-amber-600/20',
      )}
    >
      <div className="flex items-start gap-3">
        {isError ? (
          <CircleAlert aria-hidden className="mt-0.5 size-5 shrink-0 text-red-600" />
        ) : (
          <TriangleAlert aria-hidden className="mt-0.5 size-5 shrink-0 text-amber-600" />
        )}
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-semibold text-slate-900">
            {ruleLabel(check.rule_code)}
            {check.line_number !== null && (
              <span className="rounded-full bg-white px-2 py-0.5 text-xs font-medium text-slate-600 ring-1 ring-slate-900/10">
                Línea {check.line_number}
              </span>
            )}
          </p>
          <p className="mt-1 break-words text-sm text-slate-700">{check.message}</p>
          {(check.expected_value || check.actual_value) && (
            <div className="mt-2 flex flex-wrap gap-2">
              {check.expected_value && <Chip tone="expected" label="Esperado" value={show(check.expected_value)} />}
              {check.actual_value && <Chip tone="found" label="Encontrado" value={show(check.actual_value)} />}
            </div>
          )}
          {fix && (
            <div className="mt-3">
              {applied ? (
                <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-700">
                  <CircleCheck aria-hidden className="size-4" />
                  Valor esperado aplicado en el formulario
                </span>
              ) : (
                <Button size="sm" variant="secondary" icon={<Wand2 aria-hidden className="size-4" />} onClick={() => onApplyFix(fix)}>
                  Aplicar valor esperado
                </Button>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  )
}

function Group({ title, tone, checks, draft, onApplyFix }: { title: string; tone: 'red' | 'amber' } & FailedChecksProps) {
  if (checks.length === 0) return null
  return (
    <section aria-label={title} className="space-y-2">
      <h4
        className={cn(
          'flex items-center gap-2 text-xs font-semibold uppercase tracking-wide',
          tone === 'red' ? 'text-red-700' : 'text-amber-800',
        )}
      >
        {title}
        <span
          className={cn(
            'rounded-full px-2 py-0.5 text-xs normal-case',
            tone === 'red' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-800',
          )}
        >
          {checks.length}
        </span>
      </h4>
      <ul className="space-y-2">
        {checks.map((check, index) => (
          <CheckCard key={`${check.rule_code}-${check.line_number ?? 'x'}-${index}`} check={check} draft={draft} onApplyFix={onApplyFix} />
        ))}
      </ul>
    </section>
  )
}

export function FailedChecks({ checks, draft, onApplyFix }: FailedChecksProps) {
  const errors = checks.filter((check) => !check.passed && check.severity === 'ERROR')
  const warnings = checks.filter((check) => !check.passed && check.severity !== 'ERROR')
  if (errors.length === 0 && warnings.length === 0) {
    return (
      <div role="status" className="flex items-center gap-3 rounded-2xl bg-emerald-50 p-4 text-sm font-medium text-emerald-800 ring-1 ring-emerald-600/20">
        <CircleCheck aria-hidden className="size-5 shrink-0" />
        No hay errores ni avisos en la última auditoría.
      </div>
    )
  }
  return (
    <div className="space-y-4">
      <Group title="Errores" tone="red" checks={errors} draft={draft} onApplyFix={onApplyFix} />
      <Group title="Avisos" tone="amber" checks={warnings} draft={draft} onApplyFix={onApplyFix} />
    </div>
  )
}

export function PassedChecks({ checks }: { checks: AuditCheck[] }) {
  const [open, setOpen] = useState(false)
  const passed = checks.filter((check) => check.passed)
  if (passed.length === 0) return null
  return (
    <section className="rounded-2xl bg-white shadow-sm ring-1 ring-slate-900/5">
      <h4>
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          aria-controls="passed-checks"
          className="flex w-full items-center gap-2 rounded-2xl px-4 py-3 text-left text-sm font-semibold text-emerald-700 hover:bg-emerald-50/60 sm:px-5"
        >
          <CircleCheck aria-hidden className="size-4" />
          Comprobaciones superadas ({passed.length})
          <ChevronDown aria-hidden className={cn('ml-auto size-4 text-slate-400 transition-transform', open && 'rotate-180')} />
        </button>
      </h4>
      {open && (
        <ul id="passed-checks" className="animate-fade-in space-y-1 px-4 pb-4 sm:px-5">
          {passed.map((check, index) => (
            <li key={`${check.rule_code}-${index}`} className="flex items-start gap-2 rounded-lg px-2 py-1.5 text-sm">
              <CircleCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-emerald-600" />
              <span className="min-w-0">
                <span className="font-medium text-slate-800">{ruleLabel(check.rule_code)}</span>
                <span className="text-slate-600"> — {check.message}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
