import type { AuditCheck } from '../../types/api'

export type Tone = 'pass' | 'warning' | 'error'

export function toneOf(check: AuditCheck): Tone {
  if (check.passed) return 'pass'
  return check.severity === 'ERROR' ? 'error' : 'warning'
}

export interface CheckGroup {
  ruleCode: string
  checks: AuditCheck[]
}

/** Groups checks by rule, keeping the order in which each rule first appears. */
export function groupChecks(checks: AuditCheck[]): CheckGroup[] {
  const groups = new Map<string, CheckGroup>()
  for (const check of checks) {
    const group = groups.get(check.rule_code)
    if (group) group.checks.push(check)
    else groups.set(check.rule_code, { ruleCode: check.rule_code, checks: [check] })
  }
  return [...groups.values()]
}

/** Reveal order: the same sequence the grouped list renders, so a prefix is always a valid partial list. */
export function orderChecks(checks: AuditCheck[]): AuditCheck[] {
  return groupChecks(checks).flatMap((group) => group.checks)
}

export function countChecks(checks: AuditCheck[]): { total: number; errors: number; warnings: number } {
  let errors = 0
  let warnings = 0
  for (const check of checks) {
    const tone = toneOf(check)
    if (tone === 'error') errors += 1
    else if (tone === 'warning') warnings += 1
  }
  return { total: checks.length, errors, warnings }
}
