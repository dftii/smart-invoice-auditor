import { CircleAlert, CircleCheck, Scale } from 'lucide-react'
import { cn } from '../../lib/cn'
import { centsToDecimal, formatEur, formatPercent } from '../../lib/format'
import type { Reconciliation, TaxTotal } from './reconcile'

interface ReconciliationCardProps {
  data: Reconciliation
  /** True while the card reflects unsaved form values. */
  preview?: boolean
}

const eur = (cents: number) => formatEur(centsToDecimal(cents))

function Term({ label, value, sign }: { label: string; value: number; sign?: '+' | '−' }) {
  return (
    <div className="min-w-0 flex-1 basis-28 rounded-xl bg-slate-50 px-3 py-2 ring-1 ring-slate-900/5">
      <p className="truncate text-xs font-medium text-slate-500">
        {sign && <span aria-hidden>{sign} </span>}
        {label}
      </p>
      <p className="mt-0.5 text-base font-semibold tabular-nums text-slate-900">{eur(value)}</p>
    </div>
  )
}

function DiffCell({ diff }: { diff: number }) {
  if (diff === 0) return <span className="text-emerald-700">0,00 €</span>
  return <span className="font-semibold text-red-700">{`${diff > 0 ? '+' : '−'}${eur(Math.abs(diff))}`}</span>
}

function roundingNote(label: string, tax: TaxTotal): string | null {
  if (tax.byGroup === tax.byLine) return null
  if (!tax.matches) {
    return `${label}: ${eur(tax.byGroup)} redondeando por tipo impositivo o ${eur(tax.byLine)} sumando las líneas.`
  }
  return tax.used === 'line'
    ? `${label} del documento coincide con la suma redondeada línea a línea (${eur(tax.byLine)}).`
    : `${label} del documento coincide con el redondeo por tipo impositivo (${eur(tax.byGroup)}).`
}

export function ReconciliationCard({ data, preview = false }: ReconciliationCardProps) {
  const diff = data.totalDiff
  const partialMismatch = data.totalBalanced && !data.fullyBalanced
  const rows = [
    { label: 'Base imponible', calculated: data.base.calculated, declared: data.base.declared },
    { label: 'IVA', calculated: data.vat.calculated, declared: data.vat.declared },
    { label: 'IRPF (retención)', calculated: data.irpf.calculated, declared: data.irpf.declared },
    { label: 'Total', calculated: data.calculatedTotal, declared: data.declaredTotal },
  ]
  const notes = [roundingNote('El IVA', data.vat), roundingNote('El IRPF', data.irpf)].filter(Boolean)
  const vatGroups = data.vat.groups.filter((group) => group.rate > 0 && group.base !== 0)

  const tone = !data.hasLines ? 'amber' : data.fullyBalanced ? 'green' : partialMismatch ? 'amber' : 'red'

  return (
    <section
      aria-labelledby="reconcile-title"
      className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-900/5 sm:p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="reconcile-title" className="flex items-center gap-2 text-sm font-semibold text-slate-900">
          <Scale aria-hidden className="size-4 text-slate-500" />
          Cuadre de la factura
          {preview && (
            <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-inset ring-indigo-600/20">
              Vista previa
            </span>
          )}
        </h3>
        <p
          key={`${data.totalDiff}-${data.fullyBalanced}`}
          role="status"
          className={cn(
            'inline-flex animate-pop-in items-center gap-1.5 rounded-full px-3 py-1 text-sm font-semibold ring-1 ring-inset',
            tone === 'green' && 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
            tone === 'red' && 'bg-red-50 text-red-700 ring-red-600/20',
            tone === 'amber' && 'bg-amber-50 text-amber-800 ring-amber-600/20',
          )}
        >
          {tone === 'green' ? (
            <CircleCheck aria-hidden className="size-4" />
          ) : (
            <CircleAlert aria-hidden className="size-4" />
          )}
          {!data.hasLines
            ? 'Sin líneas que comprobar'
            : tone === 'green'
              ? 'Cuadra al céntimo'
              : partialMismatch
                ? 'El total cuadra, pero hay importes parciales distintos'
                : `Descuadre: ${eur(Math.abs(diff))}`}
        </p>
      </div>

      <div
        className="mt-4 flex flex-wrap items-center gap-2"
        role="group"
        aria-label="Base imponible más IVA menos IRPF igual al total calculado"
      >
        <Term label="Base imponible" value={data.base.calculated} />
        <Term label="IVA" value={data.vat.calculated} sign="+" />
        <Term label="IRPF" value={data.irpf.calculated} sign="−" />
        <div
          className={cn(
            'min-w-0 flex-1 basis-32 rounded-xl px-3 py-2 ring-1',
            data.totalBalanced ? 'bg-emerald-50 ring-emerald-600/20' : 'bg-slate-100 ring-slate-900/10',
          )}
        >
          <p className="truncate text-xs font-medium text-slate-500">= Total calculado</p>
          <p className="mt-0.5 text-base font-bold tabular-nums text-slate-900">{eur(data.calculatedTotal)}</p>
        </div>
      </div>

      <div
        className={cn(
          'mt-2 flex flex-wrap items-center justify-between gap-2 rounded-xl px-3 py-2 ring-1',
          data.totalBalanced ? 'bg-emerald-50 ring-emerald-600/20' : 'bg-red-50 ring-red-600/20',
        )}
      >
        <p className="text-xs font-medium text-slate-600">Total del documento</p>
        <p className={cn('text-base font-bold tabular-nums', data.totalBalanced ? 'text-emerald-700' : 'text-red-700')}>
          {eur(data.declaredTotal)}
          {!data.totalBalanced && (
            <span className="ml-2 text-sm font-semibold">
              ({diff > 0 ? '+' : '−'}
              {eur(Math.abs(diff))})
            </span>
          )}
        </p>
      </div>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[320px] text-sm">
          <caption className="sr-only">Importes calculados a partir de las líneas frente a los del documento</caption>
          <thead>
            <tr className="text-left text-xs font-medium text-slate-500">
              <th scope="col" className="py-1.5 pr-3 font-medium">
                Concepto
              </th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">
                Calculado
              </th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">
                Documento
              </th>
              <th scope="col" className="py-1.5 pl-3 text-right font-medium">
                Diferencia
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((row, index) => (
              <tr key={row.label} className={cn(index === rows.length - 1 && 'font-semibold')}>
                <th scope="row" className="py-1.5 pr-3 text-left font-normal text-slate-600">
                  {row.label}
                </th>
                <td className="px-3 py-1.5 text-right tabular-nums text-slate-900">{eur(row.calculated)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums text-slate-900">{eur(row.declared)}</td>
                <td className="py-1.5 pl-3 text-right tabular-nums">
                  <DiffCell diff={row.declared - row.calculated} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.hasLines && data.base.declaredLines !== data.base.calculated && (
        <p className="mt-3 text-xs text-slate-500">
          Las bases escritas en las líneas suman {eur(data.base.declaredLines)}; recalculadas con cantidad × precio −
          descuento suman {eur(data.base.calculated)}.
        </p>
      )}
      {data.headerTotal !== data.declaredTotal && (
        <p className="mt-2 text-xs text-slate-500">
          Con los importes de la cabecera del documento (base + IVA − IRPF) el total sería {eur(data.headerTotal)}.
        </p>
      )}
      {vatGroups.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-1.5" aria-label="IVA por tipo impositivo">
          {vatGroups.map((group) => (
            <li key={group.rate} className="rounded-md bg-slate-100 px-2 py-1 text-xs tabular-nums text-slate-600">
              IVA {formatPercent(group.rate)}: {eur(group.base)} → {eur(group.amount)}
            </li>
          ))}
        </ul>
      )}
      {notes.map((note) => (
        <p key={note} className="mt-2 text-xs text-slate-500">
          {note}
        </p>
      ))}
    </section>
  )
}
