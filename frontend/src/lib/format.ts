import type { AuditStatus, RuleCode } from '../types/api'
import { toNumber } from './decimal'

const eurFormatter = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' })
const numberFormatter = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 4 })
const percentFormatter = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 2 })
const dateFormatter = new Intl.DateTimeFormat('es-ES', { day: '2-digit', month: '2-digit', year: 'numeric' })
const dateTimeFormatter = new Intl.DateTimeFormat('es-ES', { dateStyle: 'short', timeStyle: 'short' })
const monthFormatter = new Intl.DateTimeFormat('es-ES', { month: 'short', year: '2-digit' })

export function formatEur(value: string | number | null | undefined): string {
  return eurFormatter.format(toNumber(value))
}

export function formatNumber(value: string | number | null | undefined): string {
  return numberFormatter.format(toNumber(value))
}

export function formatPercent(value: string | number | null | undefined): string {
  return `${percentFormatter.format(toNumber(value))} %`
}

/** Dates from the API are plain YYYY-MM-DD; build them in local time so they never shift a day. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const [year, month, day] = value.slice(0, 10).split('-').map(Number)
  if (!year || !month || !day) return value
  return dateFormatter.format(new Date(year, month - 1, day))
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : dateTimeFormatter.format(date)
}

export function formatMonth(value: string): string {
  const [year, month] = value.split('-').map(Number)
  if (!year || !month) return value
  return monthFormatter.format(new Date(year, month - 1, 1))
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1).replace('.', ',')} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1).replace('.', ',')} MB`
}

export const STATUS_LABELS: Record<AuditStatus, string> = {
  PENDING: 'Pendiente',
  VALIDATED: 'Cuadra',
  DISCREPANCY: 'Discrepancia',
  DUPLICATE: 'Duplicada',
}

export const RULE_LABELS: Record<RuleCode, string> = {
  SUPPLIER_TAX_ID: 'NIF/CIF del emisor',
  BUYER_TAX_ID: 'NIF/CIF del receptor',
  DATES: 'Fechas',
  HAS_LINES: 'Líneas de factura',
  LINE_BASE: 'Base de cada línea',
  VAT_RATE: 'Tipo de IVA',
  LINE_VAT: 'IVA de cada línea',
  IRPF_RATE: 'Tipo de IRPF',
  LINE_IRPF: 'IRPF de cada línea',
  TAX_BASE_SUM: 'Suma de bases',
  VAT_TOTAL: 'IVA total',
  IRPF_TOTAL: 'Retención IRPF total',
  GRAND_TOTAL: 'Total de la factura',
  DUPLICATE: 'Duplicados',
  CURRENCY: 'Moneda',
  FACE_DIR3: 'Códigos DIR3 (FACe)',
  FACE_PARTY_DATA: 'Datos de las partes (Facturae)',
}

export function ruleLabel(code: string): string {
  return RULE_LABELS[code as RuleCode] ?? code
}
