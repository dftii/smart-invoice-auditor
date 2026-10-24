import { useState } from 'react'
import { Check, Copy, RefreshCw, ServerOff } from 'lucide-react'
import { Button } from '../ui/Button'

const START_COMMAND = 'cd backend && python -m uvicorn app.main:app --reload --port 8000'

interface ApiOfflineBannerProps {
  onRetry: () => void
  retrying: boolean
}

export function ApiOfflineBanner({ onRetry, retrying }: ApiOfflineBannerProps) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(START_COMMAND)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div role="alert" className="animate-fade-in rounded-2xl border border-amber-200 bg-amber-50 p-5 shadow-sm sm:p-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-amber-100 text-amber-700">
          <ServerOff aria-hidden className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold text-amber-950">No se puede conectar con la API</h2>
          <p className="mt-1 text-sm text-amber-900/80">
            El backend no responde en el puerto 8000. Arráncalo con este comando y pulsa «Reintentar»:
          </p>
          <div className="mt-3 flex items-center gap-2 rounded-lg bg-slate-900 py-2 pl-3 pr-2 text-slate-100">
            <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap font-mono text-xs sm:text-sm">
              {START_COMMAND}
            </code>
            <button
              type="button"
              onClick={copy}
              aria-label={copied ? 'Comando copiado' : 'Copiar comando'}
              className="shrink-0 rounded-md p-1.5 text-slate-300 transition-colors hover:bg-white/10 hover:text-white"
            >
              {copied ? (
                <Check aria-hidden className="size-4 text-emerald-400" />
              ) : (
                <Copy aria-hidden className="size-4" />
              )}
            </button>
          </div>
          <p className="mt-2 text-xs text-amber-900/70">
            Con el entorno virtual activado. Para cargar datos de ejemplo:{' '}
            <code className="font-mono">python scripts/seed.py</code>
          </p>
        </div>
        <Button
          variant="primary"
          loading={retrying}
          icon={<RefreshCw aria-hidden className="size-4" />}
          onClick={onRetry}
          className="self-start"
        >
          Reintentar
        </Button>
      </div>
    </div>
  )
}
