import { Building2 } from 'lucide-react'
import type { InvoiceDetail } from '../../types/api'

function Item({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className={value ? 'mt-0.5 break-words text-sm text-slate-900' : 'mt-0.5 text-sm italic text-amber-700'}>
        {value || 'Sin indicar'}
      </dd>
    </div>
  )
}

/** Read-only receiver data and DIR3 codes, the fields FACe needs. */
export function PartiesCard({ invoice }: { invoice: InvoiceDetail }) {
  const place = [invoice.buyer_postal_code, invoice.buyer_city, invoice.buyer_province].filter(Boolean).join(' · ')
  return (
    <section aria-labelledby="parties-title" className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-900/5 sm:p-5">
      <h3 id="parties-title" className="flex items-center gap-2 text-sm font-semibold text-slate-900">
        <Building2 aria-hidden className="size-4 text-slate-500" />
        Receptor y códigos DIR3
      </h3>
      <dl className="mt-3 grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
        <Item label="Nombre" value={invoice.buyer_name} />
        <Item label="NIF/CIF" value={invoice.buyer_tax_id} />
        <Item label="Dirección" value={invoice.buyer_address} />
        <Item label="CP · Población · Provincia" value={place || null} />
        <Item label="Oficina contable (DIR3)" value={invoice.dir3_accounting_office} />
        <Item label="Órgano gestor (DIR3)" value={invoice.dir3_managing_body} />
        <Item label="Unidad tramitadora (DIR3)" value={invoice.dir3_processing_unit} />
        <Item label="IBAN de pago" value={invoice.payment_iban ?? null} />
      </dl>
    </section>
  )
}
