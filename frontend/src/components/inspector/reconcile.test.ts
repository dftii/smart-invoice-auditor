import { describe, expect, it } from 'vitest'
import {
  analyzeLine,
  expectedLineBaseCents,
  fixForCheck,
  parseExpectedAmount,
  percentOfCents,
  reconcile,
  recalculateAmounts,
  sameDecimal,
  type LineLike,
  type TotalsLike,
} from './reconcile'

function line(overrides: Partial<LineLike> = {}): LineLike {
  return {
    quantity: '1',
    unit_price: '100.00',
    discount_rate: '0',
    vat_rate: '21',
    irpf_rate: '0',
    line_base: '100.00',
    vat_amount: '21.00',
    irpf_amount: '0.00',
    ...overrides,
  }
}

function invoice(lines: LineLike[], totals: Partial<Omit<TotalsLike, 'lines'>> = {}): TotalsLike {
  return { tax_base: '100.00', total_vat: '21.00', total_irpf: '0.00', total_amount: '121.00', ...totals, lines }
}

describe('line arithmetic (backend: quantity x price x (100 - discount) / 100, ROUND_HALF_UP)', () => {
  it('rounds the .005 boundary up', () => {
    expect(expectedLineBaseCents(line({ quantity: '3', unit_price: '0.335' }))).toBe(101) // 1.005
    expect(expectedLineBaseCents(line({ quantity: '3', unit_price: '0.334' }))).toBe(100) // 1.002
    expect(percentOfCents(5, '10')).toBe(1) // 0.005 -> 0.01
    expect(percentOfCents(4, '10')).toBe(0)
  })

  it('applies discounts', () => {
    expect(expectedLineBaseCents(line({ quantity: '2', unit_price: '10.50', discount_rate: '10' }))).toBe(1890)
    expect(expectedLineBaseCents(line({ quantity: '1', unit_price: '99.99', discount_rate: '100' }))).toBe(0)
    expect(expectedLineBaseCents(line({ quantity: '3', unit_price: '10', discount_rate: '33.33' }))).toBe(2000)
  })

  it('quantizes quantity (4 decimals), unit price (6) and rates (2) like the database', () => {
    expect(expectedLineBaseCents(line({ quantity: '1.00005', unit_price: '10000' }))).toBe(1000100)
    expect(expectedLineBaseCents(line({ quantity: '1000000', unit_price: '0.0000005' }))).toBe(100)
    expect(expectedLineBaseCents(line({ quantity: '1', unit_price: '100', discount_rate: '10.004' }))).toBe(9000)
    expect(analyzeLine(line({ vat_rate: '21.004' })).vatRateValid).toBe(true)
    expect(analyzeLine(line({ vat_rate: '21.005' })).vatRateValid).toBe(false)
  })

  it('flags wrong base, VAT and IRPF per line', () => {
    const wrong = analyzeLine(line({ line_base: '100.01', vat_amount: '21.01', irpf_rate: '15', irpf_amount: '14.99' }))
    expect(wrong.baseOk).toBe(false)
    expect(wrong.vatOk).toBe(false)
    expect(wrong.irpfOk).toBe(false)
    expect(analyzeLine(line()).baseOk).toBe(true)
  })

  it('validates VAT (21/10/4/0) and IRPF (0/1/2/7/15/19) rates', () => {
    for (const rate of ['21', '10', '4', '0', '21.00']) expect(analyzeLine(line({ vat_rate: rate })).vatRateValid).toBe(true)
    for (const rate of ['19', '5', '', 'abc']) expect(analyzeLine(line({ vat_rate: rate })).vatRateValid).toBe(false)
    for (const rate of ['0', '1', '2', '7', '15', '19']) expect(analyzeLine(line({ irpf_rate: rate })).irpfRateValid).toBe(true)
    for (const rate of ['21', '5', '10']) expect(analyzeLine(line({ irpf_rate: rate })).irpfRateValid).toBe(false)
    expect(analyzeLine(line({ vat_rate: '0' })).vatRateIsZero).toBe(true)
  })
})

describe('reconcile', () => {
  it('balances a clean invoice', () => {
    const result = reconcile(invoice([line()]))
    expect(result.fullyBalanced).toBe(true)
    expect(result.totalBalanced).toBe(true)
    expect(result.lineIssues).toEqual([])
    expect(result.calculatedTotal).toBe(12100)
  })

  it('accepts IRPF retention and exempt (0 %) lines', () => {
    const withIrpf = reconcile(
      invoice([line({ irpf_rate: '15', irpf_amount: '15.00' })], { total_irpf: '15.00', total_amount: '106.00' }),
    )
    expect(withIrpf.fullyBalanced).toBe(true)
    const exempt = reconcile(
      invoice([line({ vat_rate: '0', vat_amount: '0.00' })], { total_vat: '0.00', total_amount: '100.00' }),
    )
    expect(exempt.fullyBalanced).toBe(true)
  })

  it('reports a one-cent difference in the document total', () => {
    const result = reconcile(invoice([line()], { total_amount: '121.01' }))
    expect(result.totalBalanced).toBe(false)
    expect(result.fullyBalanced).toBe(false)
    expect(result.totalDiff).toBe(1)
  })

  it('accepts both per-rate-group and per-line VAT rounding (backend _check_tax_total)', () => {
    const lines = [
      line({ unit_price: '0.05', line_base: '0.05', vat_rate: '10', vat_amount: '0.01' }),
      line({ unit_price: '0.05', line_base: '0.05', vat_rate: '10', vat_amount: '0.01' }),
    ]
    const byGroup = reconcile(invoice(lines, { tax_base: '0.10', total_vat: '0.01', total_amount: '0.11' }))
    expect(byGroup.vat.byGroup).toBe(1)
    expect(byGroup.vat.byLine).toBe(2)
    expect(byGroup.vat.used).toBe('group')
    expect(byGroup.fullyBalanced).toBe(true)

    const byLine = reconcile(invoice(lines, { tax_base: '0.10', total_vat: '0.02', total_amount: '0.12' }))
    expect(byLine.vat.used).toBe('line')
    expect(byLine.fullyBalanced).toBe(true)

    const neither = reconcile(invoice(lines, { tax_base: '0.10', total_vat: '0.03', total_amount: '0.13' }))
    expect(neither.vat.matches).toBe(false)
    expect(neither.fullyBalanced).toBe(false)
  })

  it('groups equal rates written differently ("21" and "21.00")', () => {
    const result = reconcile(
      invoice([line(), line({ vat_rate: '21.00' })], { tax_base: '200.00', total_vat: '42.00', total_amount: '242.00' }),
    )
    expect(result.vat.groups).toHaveLength(1)
    expect(result.fullyBalanced).toBe(true)
  })

  it('is NOT "fully balanced" when totals add up but a line amount is wrong', () => {
    const result = reconcile(invoice([line({ vat_amount: '20.00' })]))
    expect(result.totalsBalanced).toBe(true)
    expect(result.lineIssues).toEqual([1])
    expect(result.fullyBalanced).toBe(false)
  })

  it('is NOT "fully balanced" when a line base disagrees with quantity x price', () => {
    const result = reconcile(invoice([line({ line_base: '99.99', vat_amount: '21.00' })]))
    expect(result.lineIssues).toEqual([1])
    expect(result.fullyBalanced).toBe(false)
  })

  it('is NOT "fully balanced" with an invalid VAT or IRPF rate even if the arithmetic is consistent', () => {
    const vat = reconcile(
      invoice([line({ vat_rate: '19', vat_amount: '19.00' })], { total_vat: '19.00', total_amount: '119.00' }),
    )
    expect(vat.totalsBalanced).toBe(true)
    expect(vat.lineIssues).toEqual([1])
    expect(vat.fullyBalanced).toBe(false)

    const irpf = reconcile(
      invoice([line({ irpf_rate: '5', irpf_amount: '5.00' })], { total_irpf: '5.00', total_amount: '116.00' }),
    )
    expect(irpf.totalsBalanced).toBe(true)
    expect(irpf.fullyBalanced).toBe(false)
  })

  it('lists every offending line with 1-based numbers', () => {
    const result = reconcile(
      invoice([line(), line({ vat_amount: '1.00' }), line(), line({ irpf_rate: '3' })], {
        tax_base: '400.00',
        total_vat: '84.00',
        total_amount: '484.00',
      }),
    )
    expect(result.lineIssues).toEqual([2, 4])
  })

  it('has nothing to balance without lines', () => {
    const result = reconcile(invoice([], { tax_base: '0.00', total_vat: '0.00', total_amount: '0.00' }))
    expect(result.hasLines).toBe(false)
  })
})

describe('recalculateAmounts', () => {
  it('produces cent-exact lines and per-group totals', () => {
    const result = recalculateAmounts([
      line({ quantity: '3', unit_price: '0.335', line_base: '', vat_amount: '', irpf_rate: '15' }),
      line({ quantity: '2', unit_price: '10.50', discount_rate: '10', vat_rate: '10' }),
    ])
    expect(result.lines[0]).toEqual({ line_base: '1.01', vat_amount: '0.21', irpf_amount: '0.15' })
    expect(result.lines[1]).toEqual({ line_base: '18.90', vat_amount: '1.89', irpf_amount: '0.00' })
    expect(result.tax_base).toBe('19.91')
    expect(result.total_vat).toBe('2.10')
    expect(result.total_irpf).toBe('0.15')
    expect(result.total_amount).toBe('21.86')
  })
})

describe('expected values from audit messages', () => {
  it('parses Spanish and plain amounts', () => {
    expect(parseExpectedAmount('150.00')).toBe('150.00')
    expect(parseExpectedAmount('150,00 €')).toBe('150.00')
    expect(parseExpectedAmount('1.234,56')).toBe('1234.56')
    expect(parseExpectedAmount('1,234.56')).toBe('1234.56')
    expect(parseExpectedAmount('1234.5')).toBe('1234.50')
    expect(parseExpectedAmount('0.005')).toBe('0.01')
  })

  it('rejects lists and non-numbers', () => {
    expect(parseExpectedAmount('21, 10, 4, 0')).toBeNull()
    expect(parseExpectedAmount('<= 2024-01-01')).toBeNull()
    expect(parseExpectedAmount('')).toBeNull()
    expect(parseExpectedAmount(null)).toBeNull()
  })

  it('maps failed checks to form fields', () => {
    const base = { passed: false, expected_value: '121.00' }
    expect(fixForCheck({ ...base, field: 'total_amount', line_number: null })?.target).toEqual({
      scope: 'header',
      field: 'total_amount',
    })
    expect(fixForCheck({ ...base, field: 'vat_amount', line_number: 2 })?.target).toEqual({
      scope: 'line',
      lineNumber: 2,
      field: 'vat_amount',
    })
    expect(fixForCheck({ ...base, field: 'vat_rate', line_number: 2 })).toBeNull()
    expect(fixForCheck({ ...base, passed: true, field: 'total_amount', line_number: null })).toBeNull()
  })

  it('sameDecimal ignores formatting', () => {
    expect(sameDecimal('21', '21.00')).toBe(true)
    expect(sameDecimal('21,0', '21.00')).toBe(true)
    expect(sameDecimal('21', '21.01')).toBe(false)
    expect(sameDecimal('abc', ' abc ')).toBe(true)
  })
})
