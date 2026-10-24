import type { MonthlyTotal } from '../../types/api'
import { centsToDecimal } from '../../lib/decimal'

const compactEur = new Intl.NumberFormat('es-ES', {
  style: 'currency',
  currency: 'EUR',
  notation: 'compact',
  maximumFractionDigits: 1,
})

export function formatCompactEur(value: number): string {
  return compactEur.format(value)
}

export interface Ticks {
  values: number[]
  top: number
}

/** "Nice" axis ticks from 0 up to the first round value at or above `max`. */
export function niceTicks(max: number, target = 4): Ticks {
  const safeMax = max > 0 ? max : 1000
  const raw = safeMax / target
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const normalized = raw / magnitude
  const factor = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10
  const step = factor * magnitude
  const count = Math.ceil(safeMax / step - 1e-9)
  return { values: Array.from({ length: count + 1 }, (_, index) => index * step), top: count * step }
}

function monthKey(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, '0')}`
}

/** The last `count` calendar months ending at the latest month with data (or the current month), zero-filled. */
export function lastMonths(monthly: MonthlyTotal[], count = 12, now = new Date()): MonthlyTotal[] {
  const latest = monthly.reduce((acc, item) => (item.month > acc ? item.month : acc), '')
  const [endYear, endMonth] = latest
    ? latest.split('-').map(Number)
    : [now.getFullYear(), now.getMonth() + 1]
  const byMonth = new Map(monthly.map((item) => [item.month, item]))
  const result: MonthlyTotal[] = []
  for (let offset = count - 1; offset >= 0; offset--) {
    const index = endYear * 12 + (endMonth - 1) - offset
    const key = monthKey(Math.floor(index / 12), (index % 12) + 1)
    result.push(
      byMonth.get(key) ?? {
        month: key,
        invoice_count: 0,
        tax_base: centsToDecimal(0),
        total_vat: centsToDecimal(0),
        total_irpf: centsToDecimal(0),
        total_amount: centsToDecimal(0),
      },
    )
  }
  return result
}
