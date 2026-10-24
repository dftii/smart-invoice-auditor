// Editable form model for the inspector: string fields, diffing against the loaded invoice, and PATCH payload building.
import type { InvoiceDetail, InvoiceLineInput, InvoiceUpdate } from '../../types/api'
import { decToNumber, decToString, parseDecimal } from '../../lib/decimal'
import {
  recalculateAmounts,
  sameDecimal,
  type FixTarget,
  type HeaderMoneyField,
  type LineLike,
} from './reconcile'

export interface LineDraft extends LineLike {
  /** Local React key. */
  key: string
  /** Original line number when the line comes from the API; null for lines added in the form. */
  source: number | null
  concept: string
  unit_of_measure: string
}

export interface InvoiceDraft {
  supplier_tax_id: string
  invoice_number: string
  invoice_series: string
  issue_date: string
  operation_date: string
  due_date: string
  buyer_name: string
  buyer_tax_id: string
  buyer_address: string
  buyer_postal_code: string
  buyer_city: string
  buyer_province: string
  buyer_country_code: string
  dir3_accounting_office: string
  dir3_managing_body: string
  dir3_processing_unit: string
  payment_iban: string
  tax_base: string
  total_vat: string
  total_irpf: string
  total_amount: string
  lines: LineDraft[]
}

export function normalizeIban(value: string): string {
  return value.replace(/\s+/g, '').toUpperCase()
}

/** Shape of an IBAN: 2 letters, 2 check digits and up to 30 alphanumeric characters (ES: 24 characters in total). */
const IBAN_FORMAT = /^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$/

export function isIbanFormatValid(value: string): boolean {
  const iban = normalizeIban(value)
  return iban === '' || (IBAN_FORMAT.test(iban) && (!iban.startsWith('ES') || iban.length === 24))
}

/** ISO 13616 mod-97 check; used as a hint only, so test IBANs never block saving. */
export function isIbanChecksumValid(value: string): boolean {
  const iban = normalizeIban(value)
  if (!IBAN_FORMAT.test(iban)) return false
  const rearranged = iban.slice(4) + iban.slice(0, 4)
  const digits = [...rearranged].map((char) => (/\d/.test(char) ? char : String(char.charCodeAt(0) - 55))).join('')
  return BigInt(digits) % 97n === 1n
}

type TextField = Exclude<keyof InvoiceDraft, 'lines' | HeaderMoneyField | 'supplier_tax_id' | 'invoice_number' | 'issue_date' | 'buyer_country_code'>

const NULLABLE_TEXT_FIELDS: readonly TextField[] = [
  'invoice_series',
  'operation_date',
  'due_date',
  'buyer_name',
  'buyer_tax_id',
  'buyer_address',
  'buyer_postal_code',
  'buyer_city',
  'buyer_province',
  'dir3_accounting_office',
  'dir3_managing_body',
  'dir3_processing_unit',
  'payment_iban',
]

const MONEY_FIELDS: readonly HeaderMoneyField[] = ['tax_base', 'total_vat', 'total_irpf', 'total_amount']

/** Numeric form fields are dot-decimal text ('plain' mode): see lib/decimal.ts. */
function parseDec(value: string) {
  return parseDecimal(value, 'plain')
}

function isValidDecimal(value: string): boolean {
  return parseDec(value) !== null
}

let keyCounter = 0
function nextKey(): string {
  keyCounter += 1
  return `line-${keyCounter}`
}

/** Keeps at least two decimals but drops the storage padding ("10.000000" -> "10.00"). */
function tidy(value: string): string {
  if (!value.includes('.')) return value
  const [integer, fraction] = value.split('.')
  const trimmed = fraction.replace(/0+$/, '')
  return `${integer}.${trimmed.padEnd(2, '0')}`
}

export function blankLine(): LineDraft {
  return {
    key: nextKey(),
    source: null,
    concept: '',
    unit_of_measure: '01',
    quantity: '1',
    unit_price: '0.00',
    discount_rate: '0.00',
    vat_rate: '21.00',
    irpf_rate: '0.00',
    line_base: '0.00',
    vat_amount: '0.00',
    irpf_amount: '0.00',
  }
}

export function draftFromInvoice(invoice: InvoiceDetail): InvoiceDraft {
  return {
    supplier_tax_id: invoice.supplier_tax_id,
    invoice_number: invoice.invoice_number,
    invoice_series: invoice.invoice_series ?? '',
    issue_date: invoice.issue_date,
    operation_date: invoice.operation_date ?? '',
    due_date: invoice.due_date ?? '',
    buyer_name: invoice.buyer_name ?? '',
    buyer_tax_id: invoice.buyer_tax_id ?? '',
    buyer_address: invoice.buyer_address ?? '',
    buyer_postal_code: invoice.buyer_postal_code ?? '',
    buyer_city: invoice.buyer_city ?? '',
    buyer_province: invoice.buyer_province ?? '',
    buyer_country_code: invoice.buyer_country_code,
    dir3_accounting_office: invoice.dir3_accounting_office ?? '',
    dir3_managing_body: invoice.dir3_managing_body ?? '',
    dir3_processing_unit: invoice.dir3_processing_unit ?? '',
    payment_iban: invoice.payment_iban ?? '',
    tax_base: invoice.tax_base,
    total_vat: invoice.total_vat,
    total_irpf: invoice.total_irpf,
    total_amount: invoice.total_amount,
    lines: invoice.lines.map((line) => ({
      key: nextKey(),
      source: line.line_number,
      concept: line.concept,
      unit_of_measure: line.unit_of_measure,
      quantity: tidy(line.quantity),
      unit_price: tidy(line.unit_price),
      discount_rate: tidy(line.discount_rate),
      vat_rate: tidy(line.vat_rate),
      irpf_rate: tidy(line.irpf_rate),
      line_base: line.line_base,
      vat_amount: line.vat_amount,
      irpf_amount: line.irpf_amount,
    })),
  }
}

// ----------------------------------------------------------------------------------------- mutations

/** Sets one corrected amount and returns the new draft (the check's target line is found by its original number). */
export function applyFix(draft: InvoiceDraft, target: FixTarget, value: string): InvoiceDraft {
  if (target.scope === 'header') return { ...draft, [target.field]: value }
  return {
    ...draft,
    lines: draft.lines.map((line) => (line.source === target.lineNumber ? { ...line, [target.field]: value } : line)),
  }
}

export function currentFixValue(draft: InvoiceDraft, target: FixTarget): string | null {
  if (target.scope === 'header') return draft[target.field]
  return draft.lines.find((line) => line.source === target.lineNumber)?.[target.field] ?? null
}

/** Recomputes every line amount and every header total from quantities, prices and rates. */
export function recalculateDraft(draft: InvoiceDraft): InvoiceDraft {
  const result = recalculateAmounts(draft.lines)
  return {
    ...draft,
    tax_base: result.tax_base,
    total_vat: result.total_vat,
    total_irpf: result.total_irpf,
    total_amount: result.total_amount,
    lines: draft.lines.map((line, index) => ({ ...line, ...result.lines[index] })),
  }
}

// ---------------------------------------------------------------------------------------------- diff

const LINE_NUMERIC_FIELDS = [
  'quantity',
  'unit_price',
  'discount_rate',
  'vat_rate',
  'irpf_rate',
  'line_base',
  'vat_amount',
  'irpf_amount',
] as const

function linesChanged(original: LineDraft[], current: LineDraft[]): boolean {
  if (original.length !== current.length) return true
  return current.some((line, index) => {
    const before = original[index]
    if (line.source !== before.source) return true
    if (line.concept.trim() !== before.concept.trim() || line.unit_of_measure !== before.unit_of_measure) return true
    return LINE_NUMERIC_FIELDS.some((field) => !sameDecimal(line[field], before[field]))
  })
}

export function isDirty(original: InvoiceDraft, current: InvoiceDraft): boolean {
  return Object.keys(buildUpdate(original, current).payload).length > 0
}

/** IBANs are compared and sent without spaces and in upper case; other text fields are only trimmed. */
function normalizeText(field: TextField, value: string): string {
  return field === 'payment_iban' ? normalizeIban(value) : value.trim()
}

function normalizeNumber(value: string): string {
  const parsed = parseDec(value)
  return parsed === null ? value.trim() : decToString(parsed)
}

/** Only the fields that differ from the loaded invoice; `lines` is the full list and only present when something changed in it. */
export function buildUpdate(original: InvoiceDraft, current: InvoiceDraft): { payload: InvoiceUpdate } {
  const payload: InvoiceUpdate = {}

  if (current.supplier_tax_id.trim() !== original.supplier_tax_id.trim()) {
    payload.supplier_tax_id = current.supplier_tax_id.trim()
  }
  if (current.invoice_number.trim() !== original.invoice_number.trim()) {
    payload.invoice_number = current.invoice_number.trim()
  }
  if (current.issue_date !== original.issue_date) payload.issue_date = current.issue_date
  if (current.buyer_country_code.trim().toUpperCase() !== original.buyer_country_code.trim().toUpperCase()) {
    payload.buyer_country_code = current.buyer_country_code.trim().toUpperCase()
  }
  for (const field of NULLABLE_TEXT_FIELDS) {
    const before = normalizeText(field, original[field])
    const after = normalizeText(field, current[field])
    if (after !== before) payload[field] = after || null
  }
  for (const field of MONEY_FIELDS) {
    if (!sameDecimal(current[field], original[field])) payload[field] = normalizeNumber(current[field])
  }
  if (linesChanged(original.lines, current.lines)) {
    payload.lines = current.lines.map(
      (line, index): InvoiceLineInput => ({
        line_number: index + 1,
        concept: line.concept.trim(),
        quantity: normalizeNumber(line.quantity),
        unit_of_measure: line.unit_of_measure || '01',
        unit_price: normalizeNumber(line.unit_price),
        discount_rate: normalizeNumber(line.discount_rate),
        vat_rate: normalizeNumber(line.vat_rate),
        irpf_rate: normalizeNumber(line.irpf_rate),
        line_base: normalizeNumber(line.line_base),
        vat_amount: normalizeNumber(line.vat_amount),
        irpf_amount: normalizeNumber(line.irpf_amount),
      }),
    )
  }
  return { payload }
}

// ----------------------------------------------------------------------------------------- validation

/** Client-side checks that mirror the API constraints, so most mistakes are caught before the round trip. */
export function validateDraft(draft: InvoiceDraft): string[] {
  const problems: string[] = []
  if (!draft.invoice_number.trim()) problems.push('El número de factura es obligatorio.')
  if (!draft.issue_date) problems.push('La fecha de expedición es obligatoria.')
  if (!draft.supplier_tax_id.trim()) problems.push('El NIF/CIF del emisor es obligatorio.')
  if (!isIbanFormatValid(draft.payment_iban)) {
    problems.push('El IBAN no tiene un formato válido (por ejemplo ES91 2100 0418 4502 0005 1332).')
  }
  if (!/^[A-Za-z]{3}$/.test(draft.buyer_country_code.trim())) {
    problems.push('El país del receptor debe ser un código de 3 letras (por ejemplo ESP).')
  }
  for (const field of MONEY_FIELDS) {
    if (!isValidDecimal(draft[field])) problems.push(`El importe «${MONEY_LABELS[field]}» no es un número válido.`)
  }
  draft.lines.forEach((line, index) => {
    const label = `Línea ${index + 1}`
    if (!line.concept.trim()) problems.push(`${label}: el concepto es obligatorio.`)
    const quantity = parseDec(line.quantity)
    if (quantity === null || quantity.n <= 0n) problems.push(`${label}: la cantidad debe ser mayor que 0.`)
    for (const field of ['unit_price', 'line_base', 'vat_amount', 'irpf_amount'] as const) {
      if (!isValidDecimal(line[field])) problems.push(`${label}: «${LINE_LABELS[field]}» no es un número válido.`)
    }
    for (const field of ['discount_rate', 'vat_rate', 'irpf_rate'] as const) {
      const value = parseDec(line[field])
      if (value === null || value.n < 0n || decToNumber(value) > 100) {
        problems.push(`${label}: «${LINE_LABELS[field]}» debe estar entre 0 y 100.`)
      }
    }
  })
  return problems
}

const MONEY_LABELS: Record<HeaderMoneyField, string> = {
  tax_base: 'Base imponible',
  total_vat: 'IVA total',
  total_irpf: 'IRPF total',
  total_amount: 'Total',
}

const LINE_LABELS = {
  unit_price: 'Precio unitario',
  line_base: 'Base',
  vat_amount: 'Cuota de IVA',
  irpf_amount: 'Retención de IRPF',
  discount_rate: 'Descuento',
  vat_rate: 'Tipo de IVA',
  irpf_rate: 'Tipo de IRPF',
} as const
