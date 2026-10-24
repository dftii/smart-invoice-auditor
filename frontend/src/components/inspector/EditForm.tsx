import type { ReactNode } from 'react'
import { Calculator, Info, Plus } from 'lucide-react'
import { centsToDecimal, formatEur } from '../../lib/format'
import { Button } from '../ui/Button'
import { blankLine, recalculateDraft, type InvoiceDraft } from './draft'
import { TextInput } from './Field'
import { LineEditor } from './LineEditor'
import { parseCents, type Reconciliation } from './reconcile'

interface EditFormProps {
  draft: InvoiceDraft
  reconciliation: Reconciliation
  disabled: boolean
  onChange: (draft: InvoiceDraft) => void
}

function Section({ title, description, action, children }: { title: string; description?: ReactNode; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-900/5 sm:p-5">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
          {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

export function EditForm({ draft, reconciliation, disabled, onChange }: EditFormProps) {
  const set = <K extends keyof InvoiceDraft>(key: K, value: InvoiceDraft[K]) => onChange({ ...draft, [key]: value })

  function suggestion(field: 'tax_base' | 'total_vat' | 'total_irpf' | 'total_amount', cents: number): ReactNode {
    if (!reconciliation.hasLines || parseCents(draft[field]) === cents) return null
    const value = centsToDecimal(cents)
    return (
      <button
        type="button"
        disabled={disabled}
        onClick={() => set(field, value)}
        className="text-left font-medium text-indigo-600 underline decoration-dotted underline-offset-2 hover:text-indigo-500"
      >
        Calculado: {formatEur(value)} · usar
      </button>
    )
  }

  return (
    <div className="space-y-4">
      <Section
        title="Totales de la factura"
        description="Importes de la cabecera del documento."
        action={
          <Button
            size="sm"
            variant="secondary"
            disabled={disabled}
            icon={<Calculator aria-hidden className="size-4" />}
            onClick={() => onChange(recalculateDraft(draft))}
          >
            Recalcular importes
          </Button>
        }
      >
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <TextInput
            label="Base imponible (€)"
            numeric
            value={draft.tax_base}
            disabled={disabled}
            onChange={(value) => set('tax_base', value)}
            hint={suggestion('tax_base', reconciliation.base.calculated)}
          />
          <TextInput
            label="IVA total (€)"
            numeric
            value={draft.total_vat}
            disabled={disabled}
            onChange={(value) => set('total_vat', value)}
            hint={suggestion('total_vat', reconciliation.vat.calculated)}
          />
          <TextInput
            label="IRPF total (€)"
            numeric
            value={draft.total_irpf}
            disabled={disabled}
            onChange={(value) => set('total_irpf', value)}
            hint={suggestion('total_irpf', reconciliation.irpf.calculated)}
          />
          <TextInput
            label="Total (€)"
            numeric
            value={draft.total_amount}
            disabled={disabled}
            onChange={(value) => set('total_amount', value)}
            hint={suggestion('total_amount', reconciliation.calculatedTotal)}
          />
        </div>
      </Section>

      <Section title="Datos de la factura">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <TextInput label="Número" value={draft.invoice_number} disabled={disabled} invalid={!draft.invoice_number.trim()} maxLength={50} onChange={(value) => set('invoice_number', value)} />
          <TextInput label="Serie" value={draft.invoice_series} disabled={disabled} maxLength={20} onChange={(value) => set('invoice_series', value)} />
          <TextInput
            label="NIF/CIF del emisor"
            value={draft.supplier_tax_id}
            disabled={disabled}
            invalid={!draft.supplier_tax_id.trim()}
            maxLength={20}
            onChange={(value) => set('supplier_tax_id', value)}
          />
          <TextInput label="Fecha de expedición" type="date" value={draft.issue_date} disabled={disabled} invalid={!draft.issue_date} onChange={(value) => set('issue_date', value)} />
          <TextInput label="Fecha de operación" type="date" value={draft.operation_date} disabled={disabled} onChange={(value) => set('operation_date', value)} />
          <TextInput label="Vencimiento" type="date" value={draft.due_date} disabled={disabled} onChange={(value) => set('due_date', value)} />
        </div>
      </Section>

      <Section title="Receptor" description="Datos de la entidad a la que se factura; Facturae exige dirección completa.">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <TextInput label="Nombre o razón social" value={draft.buyer_name} disabled={disabled} maxLength={255} onChange={(value) => set('buyer_name', value)} />
          <TextInput label="NIF/CIF del receptor" value={draft.buyer_tax_id} disabled={disabled} maxLength={20} onChange={(value) => set('buyer_tax_id', value)} />
          <TextInput className="sm:col-span-2" label="Dirección" value={draft.buyer_address} disabled={disabled} maxLength={255} onChange={(value) => set('buyer_address', value)} />
          <div className="grid grid-cols-2 gap-3 sm:col-span-2 sm:grid-cols-4">
            <TextInput label="Código postal" value={draft.buyer_postal_code} disabled={disabled} maxLength={10} onChange={(value) => set('buyer_postal_code', value)} />
            <TextInput label="Población" value={draft.buyer_city} disabled={disabled} maxLength={100} onChange={(value) => set('buyer_city', value)} />
            <TextInput label="Provincia" value={draft.buyer_province} disabled={disabled} maxLength={100} onChange={(value) => set('buyer_province', value)} />
            <TextInput
              label="País (ISO 3)"
              value={draft.buyer_country_code}
              disabled={disabled}
              invalid={!/^[A-Za-z]{3}$/.test(draft.buyer_country_code.trim())}
              maxLength={3}
              onChange={(value) => set('buyer_country_code', value.toUpperCase())}
            />
          </div>
        </div>
      </Section>

      <Section
        title="Códigos DIR3 para FACe"
        description={
          <span className="inline-flex items-start gap-1.5">
            <Info aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            <span>
              FACe identifica al organismo público destinatario con tres códigos DIR3: oficina contable, órgano gestor
              y unidad tramitadora. Sin ellos la factura no se puede presentar en FACe.
            </span>
          </span>
        }
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <TextInput label="Oficina contable" value={draft.dir3_accounting_office} disabled={disabled} maxLength={20} placeholder="p. ej. L01280796" onChange={(value) => set('dir3_accounting_office', value)} />
          <TextInput label="Órgano gestor" value={draft.dir3_managing_body} disabled={disabled} maxLength={20} placeholder="p. ej. L01280796" onChange={(value) => set('dir3_managing_body', value)} />
          <TextInput label="Unidad tramitadora" value={draft.dir3_processing_unit} disabled={disabled} maxLength={20} placeholder="p. ej. L01280796" onChange={(value) => set('dir3_processing_unit', value)} />
        </div>
      </Section>

      <Section
        title={`Líneas (${draft.lines.length})`}
        description="Los importes se comprueban en vivo: en rojo, los que no coinciden con cantidad × precio y tipos."
        action={
          <Button
            size="sm"
            variant="secondary"
            disabled={disabled}
            icon={<Plus aria-hidden className="size-4" />}
            onClick={() => onChange({ ...draft, lines: [...draft.lines, blankLine()] })}
          >
            Añadir línea
          </Button>
        }
      >
        {draft.lines.length === 0 ? (
          <p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800 ring-1 ring-amber-600/20">
            La factura no tiene líneas. Añada al menos una para poder validarla.
          </p>
        ) : (
          <ul className="space-y-3">
            {draft.lines.map((line, index) => (
              <LineEditor
                key={line.key}
                index={index}
                line={line}
                disabled={disabled}
                onChange={(next) => set('lines', draft.lines.map((item) => (item.key === line.key ? next : item)))}
                onRemove={() => set('lines', draft.lines.filter((item) => item.key !== line.key))}
              />
            ))}
          </ul>
        )}
      </Section>
    </div>
  )
}
