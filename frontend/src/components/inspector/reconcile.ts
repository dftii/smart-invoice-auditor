// Pure invoice arithmetic for the inspector. Mirrors backend/app/services/auditor.py:
// every amount is computed in integer cents with ROUND_HALF_UP (ties away from zero), no floats involved.
// Inputs are first quantized like the database columns (quantity 4 decimals, unit price 6, rates 2).
import {
  centsToDecimal,
  decEquals,
  decKey,
  decToNumber,
  parseDecimal,
  quantize,
  roundDec,
  ZERO,
  type Dec,
} from '../../lib/decimal'

export const VAT_RATE_OPTIONS = [21, 10, 4, 0] as const
export const IRPF_RATE_OPTIONS = [0, 1, 2, 7, 15, 19] as const

/** Decimal places the backend stores (models.py: QUANTITY / UNIT_PRICE / RATE). */
const QUANTITY_SCALE = 4
const UNIT_PRICE_SCALE = 6
const RATE_SCALE = 2

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

/** API strings and form fields are dot-decimal ('plain' mode): "2.125" is a quantity, not 2125. */
function parseDec(value: string | number | null | undefined): Dec | null {
  return parseDecimal(value, 'plain')
}

function decOrZero(value: string | number | null | undefined): Dec {
  return parseDec(value) ?? ZERO
}

/** A rate as the database keeps it: 2 decimals. */
function rateDec(value: string | number | null | undefined): Dec {
  return quantize(decOrZero(value), RATE_SCALE)
}

/** A decimal string as integer cents (the backend's `_round`). Invalid text counts as 0. */
export function parseCents(value: string | number | null | undefined): number {
  return Number(roundDec(decOrZero(value), 2))
}

export function rateValue(text: string): number {
  return decToNumber(rateDec(text))
}

/** `cents * rate / 100`, rounded to a whole number of cents. */
export function percentOfCents(cents: number, rate: string | number): number {
  const parsed = rateDec(rate)
  return Number(roundDec({ n: BigInt(Math.round(cents)) * parsed.n, s: parsed.s + 2 }, 0))
}

/** quantity × price × (1 − discount / 100), rounded to cents. */
export function expectedLineBaseCents(line: Pick<LineLike, 'quantity' | 'unit_price' | 'discount_rate'>): number {
  const quantity = quantize(decOrZero(line.quantity), QUANTITY_SCALE)
  const price = quantize(decOrZero(line.unit_price), UNIT_PRICE_SCALE)
  const discount = rateDec(line.discount_rate)
  const factor = 100n * 10n ** BigInt(discount.s) - discount.n
  const product: Dec = { n: quantity.n * price.n * factor, s: quantity.s + price.s + discount.s + 2 }
  return Number(roundDec(product, 2))
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
  return parseDec(value) !== null && options.includes(rateValue(value))
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
  /** Every header component (base, VAT, IRPF, total) matches the recomputed value. */
  totalsBalanced: boolean
  /** 1-based numbers of the lines whose base, VAT or IRPF amount is wrong, or whose VAT/IRPF rate is not allowed. */
  lineIssues: number[]
  /** Header totals AND every line are consistent: the only state shown as "Cuadra al céntimo". */
  fullyBalanced: boolean
  hasLines: boolean
}

function taxTotal(
  entries: { base: number; rate: string; amount: number }[],
  declared: number,
): TaxTotal {
  const groups = new Map<string, TaxGroup & { lineSum: number }>()
  for (const entry of entries) {
    const parsed = rateDec(entry.rate)
    const key = decKey(parsed)
    const group = groups.get(key) ?? { rate: decToNumber(parsed), base: 0, amount: 0, lineSum: 0 }
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
  const totalsBalanced =
    totalBalanced &&
    declaredBase === baseCalculated &&
    declaredVat === vat.calculated &&
    declaredIrpf === irpf.calculated
  const lineIssues = lines.flatMap((line, index) =>
    line.baseOk && line.vatOk && line.irpfOk && line.vatRateValid && line.irpfRateValid ? [] : [index + 1],
  )
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
    totalsBalanced,
    lineIssues,
    fullyBalanced: totalsBalanced && lineIssues.length === 0,
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

/** Parses "150.00", "150,00 €", "1.234,56" or "1,234.56" (see lib/decimal.ts) into a dot-decimal string with 2 decimals. */
export function parseExpectedAmount(text: string | null | undefined): string | null {
  if (!text) return null
  const parsed = parseDecimal(text, 'es')
  return parsed === null ? null : centsToDecimal(Number(roundDec(parsed, 2)))
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
  return decEquals(left, right)
}
