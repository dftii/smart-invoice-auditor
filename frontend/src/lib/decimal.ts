// The one decimal parser and the exact-arithmetic helpers of the app (BigInt based, no floats involved).
//
// Parsing rules (`parseDecimal`):
//   - Whitespace (including NBSP and thin spaces), "€" and "EUR" are ignored; a leading "+", "-" or "−" is the sign.
//   - Only digits, "." and "," are allowed in the number itself.
//   - Both separators present:  the LAST one is the decimal separator, the other one groups thousands.
//         "1.234,56" -> 1234.56      "1,234.56" -> 1234.56
//     The thousands separator must form proper groups (1-3 digits, then groups of exactly 3) or the text is rejected.
//   - The same separator repeated ("1.234.567", "1,234,567"): thousands grouping, no fraction.
//   - A single comma is ALWAYS the decimal separator in Spanish text: "1234,56" -> 1234.56, "1,234" -> 1.234.
//   - A single dot:
//       * 'es' mode (text typed by a person, filters, expected values in messages):
//             followed by exactly 3 digits (and a valid leading group) -> thousands:  "1.500" -> 1500, "12.345" -> 12345
//             followed by 1-2 digits or 4+ digits -> decimal separator: "1234.56", "0.500" (a leading 0 is never a
//             thousands group), "1.5", "2.12345".
//       * 'plain' mode (machine strings from the API and the numeric fields of the correction form): the dot is
//             always the decimal separator, so "2.125" stays 2.125 (quantities have 4 decimals, prices up to 6).
//   - At least one digit is required; anything else yields null.
//
// Rounding is ROUND_HALF_UP (ties away from zero) like Python's Decimal in the backend.

/** Exact decimal: value = n / 10^s. */
export interface Dec {
  n: bigint
  s: number
}

export type ParseMode = 'es' | 'plain'

export const ZERO: Dec = { n: 0n, s: 0 }

const IGNORED = /[\s  €]|eur/gi
const SIGN = /^([+\-−])?(.*)$/
const FIRST_GROUP = /^[1-9]\d{0,2}$/
const NEXT_GROUP = /^\d{3}$/

function pow10(exponent: number): bigint {
  return 10n ** BigInt(exponent)
}

function countOf(text: string, char: string): number {
  return text.split(char).length - 1
}

/** "1.234.567" / "1,234,567" style: 1-3 digits (no leading zero) followed by groups of exactly 3. */
function isGrouped(text: string, separator: string): boolean {
  const parts = text.split(separator)
  return FIRST_GROUP.test(parts[0]) && parts.slice(1).every((part) => NEXT_GROUP.test(part))
}

interface Split {
  integer: string
  fraction: string
}

function splitNumber(body: string, mode: ParseMode): Split | null {
  const dots = countOf(body, '.')
  const commas = countOf(body, ',')

  if (dots > 0 && commas > 0) {
    const decimalSeparator = body.lastIndexOf('.') > body.lastIndexOf(',') ? '.' : ','
    const thousandsSeparator = decimalSeparator === '.' ? ',' : '.'
    if (countOf(body, decimalSeparator) !== 1) return null
    const [integerPart, fraction] = body.split(decimalSeparator)
    if (!isGrouped(integerPart, thousandsSeparator) || !/^\d*$/.test(fraction)) return null
    return { integer: integerPart.split(thousandsSeparator).join(''), fraction }
  }

  const separator = dots > 0 ? '.' : commas > 0 ? ',' : null
  if (separator === null) return { integer: body, fraction: '' }

  if (countOf(body, separator) > 1) {
    return isGrouped(body, separator) ? { integer: body.split(separator).join(''), fraction: '' } : null
  }

  const [integer, fraction] = body.split(separator)
  if (separator === '.' && mode === 'es' && fraction.length === 3 && FIRST_GROUP.test(integer)) {
    return { integer: integer + fraction, fraction: '' }
  }
  return { integer, fraction }
}

/** Parses a decimal typed by a person or received from the API; null when the text is not a number. */
export function parseDecimal(value: string | number | null | undefined, mode: ParseMode = 'es'): Dec | null {
  if (value === null || value === undefined) return null
  const text = typeof value === 'number' ? (Number.isFinite(value) ? value.toFixed(8) : '') : value
  const cleaned = text.replace(IGNORED, '')
  const [, sign, body = ''] = SIGN.exec(cleaned) ?? []
  if (!/^[\d.,]+$/.test(body) || !/\d/.test(body)) return null
  const parts = splitNumber(body, typeof value === 'number' ? 'plain' : mode)
  if (parts === null) return null
  const magnitude = BigInt(`${parts.integer || '0'}${parts.fraction}`)
  return { n: sign === '-' || sign === '−' ? -magnitude : magnitude, s: parts.fraction.length }
}

/** Rounds to `scale` decimals, ROUND_HALF_UP (ties away from zero); the result is the integer value at that scale. */
export function roundDec(value: Dec, scale: number): bigint {
  if (value.s <= scale) return value.n * pow10(scale - value.s)
  const divisor = pow10(value.s - scale)
  const magnitude = value.n < 0n ? -value.n : value.n
  let quotient = magnitude / divisor
  if ((magnitude % divisor) * 2n >= divisor) quotient += 1n
  return value.n < 0n ? -quotient : quotient
}

/** The same decimal rounded to `scale` decimals (what the backend stores in a NUMERIC(_, scale) column). */
export function quantize(value: Dec, scale: number): Dec {
  return { n: roundDec(value, scale), s: scale }
}

export function decToNumber(value: Dec): number {
  return Number(value.n) / 10 ** value.s
}

/** Canonical dot-decimal text without padding: { n: 1250, s: 2 } -> "12.5"; parseable by every consumer. */
export function decToString(value: Dec): string {
  const negative = value.n < 0n
  const digits = (negative ? -value.n : value.n).toString().padStart(value.s + 1, '0')
  const integer = digits.slice(0, digits.length - value.s)
  const fraction = digits.slice(digits.length - value.s)
  return `${negative ? '-' : ''}${integer}${fraction ? `.${fraction}` : ''}`
}

/** Canonical key so that "21", "21.0" and "21.00" compare equal. */
export function decKey(value: Dec): string {
  let { n, s } = value
  while (s > 0 && n % 10n === 0n) {
    n /= 10n
    s -= 1
  }
  return `${n}/${s}`
}

export function decEquals(a: Dec, b: Dec): boolean {
  return decKey(a) === decKey(b)
}

/** Float view for display and charts only. Invalid or empty text is 0. API strings are always dot-decimal. */
export function toNumber(value: string | number | null | undefined, mode: ParseMode = 'plain'): number {
  const parsed = parseDecimal(value, mode)
  return parsed === null ? 0 : decToNumber(parsed)
}

/** Integer cents (ROUND_HALF_UP). Invalid or empty text is 0. */
export function toCents(value: string | number | null | undefined, mode: ParseMode = 'plain'): number {
  const parsed = parseDecimal(value, mode)
  return parsed === null ? 0 : Number(roundDec(parsed, 2))
}

/** Integer cents as a plain "1234.50" string. */
export function centsToDecimal(cents: number): string {
  const sign = cents < 0 ? '-' : ''
  const absolute = Math.abs(cents)
  return `${sign}${Math.floor(absolute / 100)}.${String(absolute % 100).padStart(2, '0')}`
}
