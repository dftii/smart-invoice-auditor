import { useState } from 'react'
import { Check, Copy, RefreshCw, ServerOff, WifiOff } from 'lucide-react'
import { API_BASE, isHttpTransport } from '../../services/api'
import { Button } from '../ui/Button'

/** Port of the configured API URL (`http://localhost:8030` -> 8030); the backend's default otherwise. */
function localPort(): string {
  return /:(\d+)(?:\/|$)/.exec(API_BASE)?.[1] ?? '8000'
}

function startCommand(): string {
  return `cd backend && python -m uvicorn app.main:app --reload --port ${localPort()}`
}

interface ApiOfflineBannerProps {
  onRetry: () => void
  retrying: boolean
  /** true = the page has nothing to show without the API; false = the page keeps working with its last data. */
  blocking: boolean
}

export function ApiOfflineBanner({ onRetry, retrying, blocking }: ApiOfflineBannerProps) {
  const [copied, setCopied] = useState(false)
  const http = isHttpTransport()
  const target = API_BASE || window.location.origin

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(startCommand())
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      setCopied(false)
    }
  }

  if (!blocking) {
    return (
      <div
        role="alert"
        className="animate-fade-in flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950 shadow-sm"
      >
        <WifiOff aria-hidden className="size-4 shrink-0 text-amber-700" />
        <p className="min-w-0 flex-1 basis-64">
          <strong className="font-semibold">Sin conexión con la API.</strong> Los datos mostrados pueden estar
          desactualizados; se reintentará automáticamente.
        </p>
        <Button size="sm" variant="secondary" loading={retrying} icon={<RefreshCw aria-hidden className="size-4" />} onClick={onRetry}>
          Reintentar
        </Button>
      </div>
    )
  }

  return (
    <div role="alert" className="animate-fade-in rounded-2xl border border-amber-200 bg-amber-50 p-5 shadow-sm sm:p-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-amber-100 text-amber-700">
          <ServerOff aria-hidden className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold text-amber-950">No se puede conectar con la API</h2>
          {http ? (
            <>
              <p className="mt-1 break-words text-sm text-amber-900/80">
                El backend no responde en <code className="font-mono">{target}</code>. Arráncalo en local con este comando y
                pulsa «Reintentar»:
              </p>
              <div className="mt-3 flex items-center gap-2 rounded-lg bg-slate-900 py-2 pl-3 pr-2 text-slate-100">
                <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap font-mono text-xs sm:text-sm">
                  {startCommand()}
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
            </>
          ) : (
            <p className="mt-1 text-sm text-amber-900/80">
              El motor de la demo no responde. Recarga la página o pulsa «Reintentar».
            </p>
          )}
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
