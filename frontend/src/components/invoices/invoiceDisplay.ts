import type { InvoiceSummary } from '../../types/api'

/** "A-0012" style label; avoids repeating the series when the number already carries it. */
export function invoiceLabel(invoice: Pick<InvoiceSummary, 'invoice_series' | 'invoice_number'>): string {
  const { invoice_series: series, invoice_number: number } = invoice
  return series && !number.startsWith(series) ? `${series}-${number}` : number
}
