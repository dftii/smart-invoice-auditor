import type { InvoiceDetail, InvoiceSummary } from '../types/api'

/** Display number, exactly as the backend prints it (`Invoice.full_number`): "ARQ-26/0033" or just the number. */
export function invoiceLabel(invoice: Pick<InvoiceSummary | InvoiceDetail, 'invoice_series' | 'invoice_number'>): string {
  const { invoice_series: series, invoice_number: number } = invoice
  return series ? `${series}-${number}` : number
}
