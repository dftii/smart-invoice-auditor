import { FileImage, FileText, LoaderCircle } from 'lucide-react'
import { useState } from 'react'
import { useAsync } from '../../hooks/useAsync'
import { cn } from '../../lib/cn'
import { api } from '../../services/api'
import type { SampleInvoice } from '../../types/api'
import { useToast } from '../ui/toastContext'

interface SampleInvoicesProps {
  /** Receives the downloaded sample, which then goes through the normal upload queue. */
  onFiles: (files: File[]) => void
}

/** "¿No tienes una factura a mano?": one-click sample invoices served by the backend (GET /api/v1/samples). */
export function SampleInvoices({ onFiles }: SampleInvoicesProps) {
  const samples = useAsync(api.listSamples)
  const toast = useToast()
  const [loading, setLoading] = useState<string | null>(null)

  if (!samples.data || samples.data.length === 0) return null

  async function trySample(sample: SampleInvoice) {
    setLoading(sample.filename)
    try {
      onFiles([await api.sampleFile(sample)])
    } catch (reason) {
      toast.error('No se pudo cargar el ejemplo', reason instanceof Error ? reason.message : undefined)
    } finally {
      setLoading(null)
    }
  }

  return (
    <div className="space-y-2.5 rounded-2xl border border-slate-200 bg-white p-4">
      <p className="text-sm text-slate-600">
        <span className="font-semibold text-slate-900">¿No tienes una factura a mano?</span> Prueba con un ejemplo: se
        sube y se audita como si fuera tuya.
      </p>
      <ul className="flex flex-wrap gap-2" aria-label="Facturas de ejemplo">
        {samples.data.map((sample) => {
          const busy = loading === sample.filename
          const hasErrors = sample.expected_status !== 'VALIDATED'
          const Icon = sample.media_type.startsWith('image/') ? FileImage : FileText
          return (
            <li key={sample.filename}>
              <button
                type="button"
                disabled={loading !== null}
                onClick={() => void trySample(sample)}
                title={sample.description}
                aria-label={`Probar con el ejemplo «${sample.title}»: ${sample.description}`}
                className="inline-flex h-8 items-center gap-1.5 rounded-full bg-white px-3 text-xs font-medium text-slate-700 ring-1 ring-inset ring-slate-300 transition-colors hover:bg-indigo-50 hover:text-indigo-700 hover:ring-indigo-300 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {busy ? (
                  <LoaderCircle aria-hidden className="size-3.5 animate-spin" />
                ) : (
                  <Icon aria-hidden className="size-3.5 text-slate-400" />
                )}
                {sample.title}
                <span
                  aria-hidden
                  className={cn('size-1.5 rounded-full', hasErrors ? 'bg-red-500' : 'bg-emerald-500')}
                />
              </button>
            </li>
          )
        })}
      </ul>
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="size-1.5 rounded-full bg-emerald-500" /> cuadra
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="size-1.5 rounded-full bg-red-500" /> con errores a propósito
        </span>
      </p>
    </div>
  )
}
