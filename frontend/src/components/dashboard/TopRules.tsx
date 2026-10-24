import type { RuleFailureCount } from '../../types/api'
import { ruleLabel } from '../../lib/format'
import { ChartCard, EmptyChart } from './ChartCard'

export function TopRules({ rules }: { rules: RuleFailureCount[] }) {
  const max = Math.max(1, ...rules.map((rule) => rule.count))
  return (
    <ChartCard title="Reglas que más fallan" description="Controles no superados en la última auditoría de cada factura">
      {rules.length === 0 ? (
        <EmptyChart message="Ninguna regla ha fallado. Todo cuadra." />
      ) : (
        <ol className="space-y-3">
          {rules.map((rule) => (
            <li key={rule.rule_code}>
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="min-w-0 truncate text-slate-700">{ruleLabel(rule.rule_code)}</span>
                <span className="shrink-0 font-semibold text-slate-900">{rule.count}</span>
              </div>
              <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-slate-100">
                <div
                  className="h-full rounded-full bg-red-400"
                  style={{ width: `${(rule.count / max) * 100}%` }}
                  role="presentation"
                />
              </div>
            </li>
          ))}
        </ol>
      )}
    </ChartCard>
  )
}
