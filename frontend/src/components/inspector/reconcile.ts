// Pure invoice arithmetic for the inspector. Mirrors backend/app/services/auditor.py:
// every amount is computed in integer cents with ROUND_HALF_UP (ties away from zero), no floats involved.
import { centsToDecimal } from '../../lib/format'

export const VAT_RATE_OPTIONS = [21, 10, 4, 0] as const
export const IRPF_RATE_OPTIONS = [0, 1, 2, 7, 15, 19] as const

/** Minimal shape needed to reason about one line; both API lines and form drafts satisfy it. */
export interface LineLike {
  quantity: string
  unit_price: string
  discount_rate: string
  vat_rate: string
  irpf_rate: string
  line_base: string
  vat_amount: string
  irpf_amount: string
}

export interface TotalsLike {
  tax_base: string
  total_vat: string
  total_irpf: string
  total_amount: string
  lines: readonly LineLike[]
}

// ----------------------------------------------------------------------------------------- decimals

/** Exact decimal: value = n / 10^s. */
interface Dec {
  n: bigint
  s: number
}

const ZERO: Dec = { n: 0n, s: 0 }
const DECIMAL_PATTERN = /^([+-])?(\d*)(?:\.(\d*))?$/

function pow10(exponent: number): bigint {
  return 10n ** BigInt(exponent)
}

/** Parses "12.5", "12,5" or a number; null when the text is not a plain decimal. */
export function parseDec(value: string | number | null | undefined): Dec | null {
  if (value === null || value === undefined) return null
  const text = (typeof value === 'number' ? value.toFixed(8) : value).trim().replace(',', '.')
  const match = DECIMAL_PATTERN.exec(text)
  if (!match) return null
  const [, sign, integer = '', fraction = ''] = match
  if (integer === '' && fraction === '') return null
  const n = BigInt(`${integer || '0'}${fraction}`)
  return { n: sign === '-' ? -n : n, s: fraction.length }
}

function decOrZero(value: string | number | null | undefined): Dec {
  return parseDec(value) ?? ZERO
}

export function isValidDecimal(value: string): boolean {
  return parseDec(value) !== null
}

/** Rounds to `scale` decimals, ROUND_HALF_UP (ties away from zero) like Python's Decimal. */
function roundToScale(value: Dec, scale: number): bigint {
  if (value.s <= scale) return value.n * pow10(scale - value.s)
  const divisor = pow10(value.s - scale)
  const magnitude = value.n < 0n ? -value.n : value.n
  let quotient = magnitude / divisor
  if ((magnitude % divisor) * 2n >= divisor) quotient += 1n
  return value.n < 0n ? -quotient : quotient
}

/** A decimal string as integer cents (the backend's `_round`). Invalid text counts as 0. */
export function parseCents(value: string | number | null | undefined): number {
  return Number(roundToScale(decOrZero(value), 2))
}

function toNumberValue(value: Dec): number {
  return Number(value.n) / 10 ** value.s
}

/** Canonical grouping key so that "21", "21.0" and "21.00" fall in the same rate group. */
function rateKey(value: Dec): string {
  let { n, s } = value
  while (s > 0 && n % 10n === 0n) {
    n /= 10n
    s -= 1
  }
  return `${n}/${s}`
}

export function rateValue(text: string): number {
  return toNumberValue(decOrZero(text))
}

/** `cents * rate / 100`, rounded to a whole number of cents. */
export function percentOfCents(cents: number, rate: string | number): number {
  const parsed = decOrZero(rate)
  return Number(roundToScale({ n: BigInt(Math.round(cents)) * parsed.n, s: parsed.s + 2 }, 0))
}

/** quantity × price × (1 − discount / 100), rounded to cents. */
export function expectedLineBaseCents(line: Pick<LineLike, 'quantity' | 'unit_price' | 'discount_rate'>): number {
  const quantity = decOrZero(line.quantity)
  const price = decOrZero(line.unit_price)
  const discount = decOrZero(line.discount_rate)
  const factor = 100n * pow10(discount.s) - discount.n
  const product: Dec = { n: quantity.n * price.n * factor, s: quantity.s + price.s + discount.s + 2 }
  return Number(roundToScale(product, 2))
}

// -------------------------------------------------------------------------------------------- lines

export interface LineAnalysis {
  /** Amounts exactly as the backend audits them: VAT and IRPF are derived from the declared base. */
  declaredBase: number
  expectedBase: number
  declaredVat: number
  expectedVat: number
  declaredIrpf: number
  expectedIrpf: number
  baseOk: boolean
  vatOk: boolean
  irpfOk: boolean
  vatRateValid: boolean
  vatRateIsZero: boolean
  irpfRateValid: boolean
}

function inOptions(value: string, options: readonly number[]): boolean {
  const parsed = parseDec(value)
  return parsed !== null && options.includes(toNumberValue(parsed))
}

export function analyzeLine(line: LineLike): LineAnalysis {
  const declaredBase = parseCents(line.line_base)
  const expectedBase = expectedLineBaseCents(line)
  const declaredVat = parseCents(line.vat_amount)
  const expectedVat = percentOfCents(declaredBase, line.vat_rate)
  const declaredIrpf = parseCents(line.irpf_amount)
  const expectedIrpf = percentOfCents(declaredBase, line.irpf_rate)
  const vatRateValid = inOptions(line.vat_rate, VAT_RATE_OPTIONS)
  return {
    declaredBase,
    expectedBase,
    declaredVat,
    expectedVat,
    declaredIrpf,
    expectedIrpf,
    baseOk: declaredBase === expectedBase,
    vatOk: declaredVat === expectedVat,
    irpfOk: declaredIrpf === expectedIrpf,
    vatRateValid,
    vatRateIsZero: vatRateValid && rateValue(line.vat_rate) === 0,
    irpfRateValid: inOptions(line.irpf_rate, IRPF_RATE_OPTIONS),
  }
}

// ----------------------------------------------------------------------------------- reconciliation

export interface TaxGroup {
  rate: number
  base: number
  amount: number
}

export type Rounding = 'group' | 'line'

export interface TaxTotal {
  /** Total computed from the recalculated lines with the rounding that matches the document (group by default). */
  calculated: number
  declared: number
  used: Rounding
  /** True when the declared total equals the group or the per-line total. */
  matches: boolean
  byGroup: number
  byLine: number
  groups: TaxGroup[]
}

export interface Reconciliation {
  lines: LineAnalysis[]
  base: { calculated: number; declared: number; declaredLines: number }
  vat: TaxTotal
  irpf: TaxTotal
  calculatedTotal: number
  declaredTotal: number
  /** declared base + declared VAT − declared IRPF: what the document total should be given its own header. */
  headerTotal: number
  /** declaredTotal − calculatedTotal. */
  totalDiff: number
  /** The document total matches the recomputed one to the cent. */
  totalBalanced: boolean
  /** Every component (base, VAT, IRPF, total) matches the recomputed value. */
  fullyBalanced: boolean
  hasLines: boolean
}

function taxTotal(
  entries: { base: number; rate: string; amount: number }[],
  declared: number,
): TaxTotal {
  const groups = new Map<string, TaxGroup & { lineSum: number }>()
  for (const entry of entries) {
    const parsed = decOrZero(entry.rate)
    const key = rateKey(parsed)
    const group = groups.get(key) ?? { rate: toNumberValue(parsed), base: 0, amount: 0, lineSum: 0 }
    group.base += entry.base
    group.lineSum += entry.amount
    groups.set(key, group)
  }
  const list: (TaxGroup & { lineSum: number })[] = [...groups.values()].sort((a, b) => b.rate - a.rate)
  for (const group of list) group.amount = percentOfCents(group.base, group.rate)
  const byGroup = list.reduce((sum, group) => sum + group.amount, 0)
  const byLine = list.reduce((sum, group) => sum + group.lineSum, 0)
  const used: Rounding = declared !== byGroup && declared === byLine ? 'line' : 'group'
  const calculated = used === 'line' ? byLine : byGroup
  return {
    calculated,
    declared,
    used,
    matches: declared === byGroup || declared === byLine,
    byGroup,
    byLine,
    groups: list.map(({ rate, base, amount }) => ({ rate, base, amount })),
  }
}

/** Recomputes the whole invoice from quantities, prices and rates and compares it with what the document declares. */
export function reconcile(source: TotalsLike): Reconciliation {
  const lines = source.lines.map(analyzeLine)
  const baseCalculated = lines.reduce((sum, line) => sum + line.expectedBase, 0)
  const declaredBase = parseCents(source.tax_base)
  const declaredVat = parseCents(source.total_vat)
  const declaredIrpf = parseCents(source.total_irpf)
  const declaredTotal = parseCents(source.total_amount)

  const vat = taxTotal(
    source.lines.map((line, index) => ({
      base: lines[index].expectedBase,
      rate: line.vat_rate,
      amount: percentOfCents(lines[index].expectedBase, line.vat_rate),
    })),
    declaredVat,
  )
  const irpf = taxTotal(
    source.lines.map((line, index) => ({
      base: lines[index].expectedBase,
      rate: line.irpf_rate,
      amount: percentOfCents(lines[index].expectedBase, line.irpf_rate),
    })),
    declaredIrpf,
  )

  const calculatedTotal = baseCalculated + vat.calculated - irpf.calculated
  const totalDiff = declaredTotal - calculatedTotal
  const totalBalanced = totalDiff === 0
  return {
    lines,
    base: {
      calculated: baseCalculated,
      declared: declaredBase,
      declaredLines: lines.reduce((sum, line) => sum + line.declaredBase, 0),
    },
    vat,
    irpf,
    calculatedTotal,
    declaredTotal,
    headerTotal: declaredBase + declaredVat - declaredIrpf,
    totalDiff,
    totalBalanced,
    fullyBalanced:
      totalBalanced &&
      declaredBase === baseCalculated &&
      declaredVat === vat.calculated &&
      declaredIrpf === irpf.calculated,
    hasLines: source.lines.length > 0,
  }
}

// --------------------------------------------------------------------------------------- recalculation

export interface RecalculatedAmounts {
  lines: { line_base: string; vat_amount: string; irpf_amount: string }[]
  tax_base: string
  total_vat: string
  total_irpf: string
  total_amount: string
}

/** Every line amount and every total, cent-exact; header taxes use per-rate-group rounding (accepted by the auditor and the Facturae exporter). */
export function recalculateAmounts(lines: readonly LineLike[]): RecalculatedAmounts {
  const computed = lines.map((line) => {
    const base = expectedLineBaseCents(line)
    return {
      base,
      vat: percentOfCents(base, line.vat_rate),
      irpf: percentOfCents(base, line.irpf_rate),
    }
  })
  const vat = taxTotal(
    lines.map((line, index) => ({ base: computed[index].base, rate: line.vat_rate, amount: computed[index].vat })),
    0,
  )
  const irpf = taxTotal(
    lines.map((line, index) => ({ base: computed[index].base, rate: line.irpf_rate, amount: computed[index].irpf })),
    0,
  )
  const base = computed.reduce((sum, line) => sum + line.base, 0)
  return {
    lines: computed.map((line) => ({
      line_base: centsToDecimal(line.base),
      vat_amount: centsToDecimal(line.vat),
      irpf_amount: centsToDecimal(line.irpf),
    })),
    tax_base: centsToDecimal(base),
    total_vat: centsToDecimal(vat.byGroup),
    total_irpf: centsToDecimal(irpf.byGroup),
    total_amount: centsToDecimal(base + vat.byGroup - irpf.byGroup),
  }
}

// ------------------------------------------------------------------------------------ expected values

export type HeaderMoneyField = 'tax_base' | 'total_vat' | 'total_irpf' | 'total_amount'
export type LineMoneyField = 'line_base' | 'vat_amount' | 'irpf_amount'

export type FixTarget =
  | { scope: 'header'; field: HeaderMoneyField }
  | { scope: 'line'; lineNumber: number; field: LineMoneyField }

const HEADER_FIELDS: readonly string[] = ['tax_base', 'total_vat', 'total_irpf', 'total_amount']
const LINE_FIELDS: readonly string[] = ['line_base', 'vat_amount', 'irpf_amount']

/** Parses "150.00", "150,00 €", "1.234,56" or "1,234.56" into a plain dot-decimal string with 2 decimals. */
export function parseExpectedAmount(text: string | null | undefined): string | null {
  if (!text) return null
  let cleaned = text.replace(/[€\s ]/g, '')
  if (!/^[+-]?[\d.,]+$/.test(cleaned)) return null
  const lastDot = cleaned.lastIndexOf('.')
  const lastComma = cleaned.lastIndexOf(',')
  if (lastDot !== -1 && lastComma !== -1) {
    const decimalSeparator = lastDot > lastComma ? '.' : ','
    const thousandsSeparator = decimalSeparator === '.' ? ',' : '.'
    cleaned = cleaned.split(thousandsSeparator).join('').replace(decimalSeparator, '.')
  } else if (lastComma !== -1) {
    cleaned = cleaned.split(',').length > 2 ? cleaned.split(',').join('') : cleaned.replace(',', '.')
  } else if (cleaned.split('.').length > 2) {
    cleaned = cleaned.split('.').join('')
  }
  const parsed = parseDec(cleaned)
  return parsed === null ? null : centsToDecimal(Number(roundToScale(parsed, 2)))
}

interface CheckLike {
  passed: boolean
  field: string | null
  line_number: number | null
  expected_value: string | null
}

/** Which form field a failed check can fix and with which value, or null when it has no numeric target. */
export function fixForCheck(check: CheckLike): { target: FixTarget; value: string } | null {
  if (check.passed || !check.field) return null
  const value = parseExpectedAmount(check.expected_value)
  if (value === null) return null
  if (HEADER_FIELDS.includes(check.field) && check.line_number === null) {
    return { target: { scope: 'header', field: check.field as HeaderMoneyField }, value }
  }
  if (LINE_FIELDS.includes(check.field) && check.line_number !== null) {
    return { target: { scope: 'line', lineNumber: check.line_number, field: check.field as LineMoneyField }, value }
  }
  return null
}

/** True when two decimal strings are the same number (ignores formatting such as "21" vs "21.00"). */
export function sameDecimal(a: string, b: string): boolean {
  const left = parseDec(a)
  const right = parseDec(b)
  if (left === null || right === null) return a.trim() === b.trim()
  return rateKey(left) === rateKey(right)
}
