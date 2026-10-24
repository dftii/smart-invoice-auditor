import { Calculator, Trash2 } from 'lucide-react'
import { formatEur } from '../../lib/format'
import { centsToDecimal } from '../../lib/decimal'
import { Button } from '../ui/Button'
import type { LineDraft } from './draft'
import { SelectInput, TextInput } from './Field'
import { analyzeLine, IRPF_RATE_OPTIONS, recalculateAmounts, sameDecimal, VAT_RATE_OPTIONS } from './reconcile'

interface LineEditorProps {
  index: number
  line: LineDraft
  disabled: boolean
  onChange: (line: LineDraft) => void
  onRemove: () => void
}

function rateOptions(current: string, allowed: readonly number[]) {
  const options = allowed.map((rate) => ({ value: String(rate), label: `${rate} %` }))
  const match = options.find((option) => sameDecimal(option.value, current))
  if (match) return { options, value: match.value }
  return { options: [...options, { value: current, label: `${current} % (no admitido)` }], value: current }
}

function Expected({ show, cents }: { show: boolean; cents: number }) {
  if (!show) return null
  return <span className="text-red-600">Esperado {formatEur(centsToDecimal(cents))}</span>
}

export function LineEditor({ index, line, disabled, onChange, onRemove }: LineEditorProps) {
  const analysis = analyzeLine(line)
  const set = (patch: Partial<LineDraft>) => onChange({ ...line, ...patch })
  const vat = rateOptions(line.vat_rate, VAT_RATE_OPTIONS)
  const irpf = rateOptions(line.irpf_rate, IRPF_RATE_OPTIONS)

  function recalculate() {
    set(recalculateAmounts([line]).lines[0])
  }

  return (
    <li className="rounded-xl bg-slate-50 p-3 ring-1 ring-slate-900/5">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-sm font-semibold text-slate-900">Línea {index + 1}</p>
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            disabled={disabled}
            icon={<Calculator aria-hidden className="size-4" />}
            onClick={recalculate}
            aria-label={`Recalcular importes de la línea ${index + 1}`}
          >
            Recalcular
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={disabled}
            onClick={onRemove}
            aria-label={`Eliminar línea ${index + 1}`}
            className="text-red-600 hover:bg-red-50 hover:text-red-700"
            icon={<Trash2 aria-hidden className="size-4" />}
          />
        </div>
      </div>
      <TextInput
        label="Concepto"
        value={line.concept}
        disabled={disabled}
        invalid={!line.concept.trim()}
        maxLength={500}
        onChange={(concept) => set({ concept })}
      />
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <TextInput label="Cantidad" numeric value={line.quantity} disabled={disabled} onChange={(quantity) => set({ quantity })} />
        <TextInput
          label="Precio unitario (€)"
          numeric
          value={line.unit_price}
          disabled={disabled}
          onChange={(unit_price) => set({ unit_price })}
        />
        <TextInput
          label="Descuento (%)"
          numeric
          value={line.discount_rate}
          disabled={disabled}
          onChange={(discount_rate) => set({ discount_rate })}
        />
        <SelectInput
          label="Tipo de IVA"
          value={vat.value}
          options={vat.options}
          disabled={disabled}
          invalid={!analysis.vatRateValid}
          onChange={(vat_rate) => set({ vat_rate })}
        />
        <SelectInput
          label="Tipo de IRPF"
          value={irpf.value}
          options={irpf.options}
          disabled={disabled}
          invalid={!analysis.irpfRateValid}
          onChange={(irpf_rate) => set({ irpf_rate })}
        />
        <TextInput
          label="Base (€)"
          numeric
          value={line.line_base}
          disabled={disabled}
          invalid={!analysis.baseOk}
          hint={<Expected show={!analysis.baseOk} cents={analysis.expectedBase} />}
          onChange={(line_base) => set({ line_base })}
        />
        <TextInput
          label="Cuota IVA (€)"
          numeric
          value={line.vat_amount}
          disabled={disabled}
          invalid={!analysis.vatOk}
          hint={<Expected show={!analysis.vatOk} cents={analysis.expectedVat} />}
          onChange={(vat_amount) => set({ vat_amount })}
        />
        <TextInput
          label="Retención IRPF (€)"
          numeric
          value={line.irpf_amount}
          disabled={disabled}
          invalid={!analysis.irpfOk}
          hint={<Expected show={!analysis.irpfOk} cents={analysis.expectedIrpf} />}
          onChange={(irpf_amount) => set({ irpf_amount })}
        />
      </div>
    </li>
  )
}
