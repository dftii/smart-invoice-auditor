import { describe, expect, it } from 'vitest'
import { centsToDecimal, decToString, parseDecimal, quantize, roundDec, toCents, toNumber } from './decimal'

const text = (value: string, mode: 'es' | 'plain' = 'es') => {
  const parsed = parseDecimal(value, mode)
  return parsed === null ? null : decToString(parsed)
}

describe('parseDecimal (Spanish-aware)', () => {
  it.each([
    ['1.234,56', '1234.56'],
    ['1234,56', '1234.56'],
    ['1234.56', '1234.56'],
    ['1,234.56', '1234.56'],
    ['1.500', '1500'],
    ['12.345', '12345'],
    ['1.234.567', '1234567'],
    ['1,234,567', '1234567'],
    ['0.500', '0.500'],
    ['1.5', '1.5'],
    ['2.12345', '2.12345'],
    ['1,234', '1.234'],
    ['0,5', '0.5'],
    ['.5', '0.5'],
    ['-1.234,50', '-1234.50'],
    ['−12,5', '-12.5'],
    ['+7', '7'],
    ['1 234,50 €', '1234.50'],
    ['1 234,50 EUR', '1234.50'],
    ['42', '42'],
  ])('es: %s -> %s', (input, expected) => {
    expect(text(input)).toBe(expected)
  })

  it.each(['', '  ', 'abc', '1.2.3', '1,2,3', '1.23,4', '12.34.567', '1.234,56,7', '--1', '1e5', '€', '.', ','])(
    'rejects %j',
    (input) => {
      expect(parseDecimal(input)).toBeNull()
    },
  )

  it('plain mode keeps a lone dot as the decimal separator', () => {
    expect(text('1.500', 'plain')).toBe('1.500')
    expect(text('2.125', 'plain')).toBe('2.125')
    expect(text('1.234,56', 'plain')).toBe('1234.56')
    expect(text('1,5', 'plain')).toBe('1.5')
  })

  it('numbers and nullish values', () => {
    expect(decToString(parseDecimal(21)!)).toBe('21.00000000')
    expect(parseDecimal(null)).toBeNull()
    expect(parseDecimal(undefined)).toBeNull()
    expect(parseDecimal(Number.NaN)).toBeNull()
  })
})

describe('rounding (ROUND_HALF_UP, ties away from zero)', () => {
  it.each([
    ['0.005', 1],
    ['0.004', 0],
    ['0.0049999', 0],
    ['1.005', 101],
    ['2.675', 268],
    ['-0.005', -1],
    ['-1.005', -101],
    ['0.015', 2],
    ['0.025', 3],
    ['10.5', 1050],
    ['1234.564', 123456],
    ['1234.565', 123457],
  ])('toCents(%s) = %i', (input, cents) => {
    expect(toCents(input)).toBe(cents)
  })

  it('quantize keeps the target scale', () => {
    expect(quantize(parseDecimal('1.00005', 'plain')!, 4)).toEqual({ n: 10001n, s: 4 })
    expect(roundDec({ n: 5n, s: 1 }, 0)).toBe(1n)
    expect(roundDec({ n: -5n, s: 1 }, 0)).toBe(-1n)
  })

  it('toNumber / toCents tolerate invalid input', () => {
    expect(toNumber('abc')).toBe(0)
    expect(toNumber(null)).toBe(0)
    expect(toNumber('12.5')).toBe(12.5)
    expect(toCents(undefined)).toBe(0)
  })

  it('centsToDecimal', () => {
    expect(centsToDecimal(0)).toBe('0.00')
    expect(centsToDecimal(5)).toBe('0.05')
    expect(centsToDecimal(123456)).toBe('1234.56')
    expect(centsToDecimal(-105)).toBe('-1.05')
  })
})
