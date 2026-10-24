import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { AlertTriangle, Check, FileCheck2, ExternalLink, Loader2, RotateCw, X } from 'lucide-react'
import { setTransport } from '../services/api'
import { BOOT_STAGES } from './protocol'
import { demoTransport, startDemo, type BootProgress } from './transport'

const REPO_URL = 'https://github.com/dftii/smart-invoice-auditor'

type Phase = { kind: 'booting' } | { kind: 'ready' } | { kind: 'error'; message: string }

function useElapsedSeconds(running: boolean): number {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    if (!running) return
    const startedAt = performance.now()
    const timer = window.setInterval(() => setSeconds(Math.floor((performance.now() - startedAt) / 1000)), 250)
    return () => window.clearInterval(timer)
  }, [running])
  return seconds
}

/** Boots the in-browser backend, then swaps the app's transport and renders the app (with a slim demo banner). */
export function DemoGate({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>({ kind: 'booting' })
  const [progress, setProgress] = useState<BootProgress | null>(null)
  const [attempt, setAttempt] = useState(0)
  const elapsed = useElapsedSeconds(phase.kind === 'booting')

  useEffect(() => {
    let cancelled = false
    startDemo((next) => {
      if (!cancelled) setProgress(next)
    }).then(
      () => {
        if (cancelled) return
        setTransport(demoTransport)
        setPhase({ kind: 'ready' })
      },
      (error: unknown) => {
        if (!cancelled) setPhase({ kind: 'error', message: error instanceof Error ? error.message : String(error) })
      },
    )
    return () => {
      cancelled = true
    }
  }, [attempt])

  const retry = useCallback(() => {
    setProgress(null)
    setPhase({ kind: 'booting' })
    setAttempt((value) => value + 1)
  }, [])

  if (phase.kind === 'ready') {
    return (
      <>
        <DemoBanner />
        {children}
      </>
    )
  }
  return <BootScreen phase={phase} progress={progress} elapsed={elapsed} onRetry={retry} />
}

export default DemoGate

function BootScreen({
  phase,
  progress,
  elapsed,
  onRetry,
}: {
  phase: Exclude<Phase, { kind: 'ready' }>
  progress: BootProgress | null
  elapsed: number
  onRetry: () => void
}) {
  const current = progress?.step ?? 0
  const completed = Math.max(current - 1, 0)
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-10">
      <div className="w-full max-w-lg rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
        <div className="flex items-center gap-3">
          <span
            aria-hidden
            className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-indigo-700 text-white shadow-sm shadow-indigo-600/30"
          >
            <FileCheck2 className="size-5" />
          </span>
          <div className="min-w-0">
            <h1 className="text-lg font-semibold leading-tight text-slate-900">SmartInvoice-FACe Auditor</h1>
            <p className="text-xs text-slate-500">Demo en el navegador</p>
          </div>
        </div>

        <p className="mt-5 text-sm leading-relaxed text-slate-600">
          El backend real en Python (FastAPI + SQLAlchemy + auditor fiscal) se está ejecutando dentro de tu navegador con
          WebAssembly. Primera carga ~10 s; los datos viven en memoria y se reinician al recargar.
        </p>

        {phase.kind === 'error' ? (
          <div role="alert" className="mt-6 rounded-xl border border-red-200 bg-red-50 p-4">
            <p className="flex items-center gap-2 text-sm font-semibold text-red-800">
              <AlertTriangle className="size-4 shrink-0" aria-hidden />
              No se ha podido arrancar el backend en el navegador
            </p>
            <p className="mt-1 break-words text-xs text-red-700">{phase.message}</p>
            <p className="mt-2 text-xs text-red-700">
              Necesita conexión (Python se descarga desde un CDN) y un navegador reciente con WebAssembly.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={onRetry}
                className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
              >
                <RotateCw className="size-4" aria-hidden />
                Reintentar
              </button>
              <a
                href={REPO_URL}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                <ExternalLink className="size-4" aria-hidden />
                Ver el repositorio
              </a>
            </div>
          </div>
        ) : (
          <>
            <ol className="mt-6 space-y-2.5" aria-label="Progreso del arranque">
              {BOOT_STAGES.map((label, index) => {
                const step = index + 1
                const done = step < current
                const active = step === current || (current === 0 && step === 1)
                const tone = done ? 'text-slate-500' : active ? 'font-medium text-slate-900' : 'text-slate-400'
                return (
                  <li key={label} className={`flex items-center gap-2.5 text-sm ${tone}`} aria-current={active ? 'step' : undefined}>
                    <span className="flex size-5 shrink-0 items-center justify-center" aria-hidden>
                      {done ? (
                        <Check className="size-4 text-emerald-600" />
                      ) : active ? (
                        <Loader2 className="size-4 text-indigo-600 motion-safe:animate-spin" />
                      ) : (
                        <span className="size-1.5 rounded-full bg-slate-300" />
                      )}
                    </span>
                    {label}
                  </li>
                )
              })}
            </ol>
            <div
              className="mt-5 h-1.5 overflow-hidden rounded-full bg-slate-100"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={BOOT_STAGES.length}
              aria-valuenow={completed}
              aria-label="Arranque del backend"
            >
              <div
                className="h-full rounded-full bg-indigo-500 transition-[width] duration-500"
                style={{ width: `${(completed / BOOT_STAGES.length) * 100 + (current > 0 ? 8 : 2)}%` }}
              />
            </div>
            <p className="mt-2 text-xs tabular-nums text-slate-500" role="status">
              {elapsed} s
            </p>
          </>
        )}
      </div>
    </main>
  )
}

function DemoBanner() {
  const [visible, setVisible] = useState(true)
  if (!visible) return null
  return (
    <div className="border-b border-indigo-100 bg-indigo-50 text-indigo-900" role="note">
      <div className="mx-auto flex max-w-[1400px] items-center gap-3 px-4 py-1.5 text-xs sm:px-6 lg:px-8">
        <p className="min-w-0 flex-1">
          <span className="font-semibold">Demo en el navegador</span>
          <span aria-hidden> · </span>Backend Python en WebAssembly<span aria-hidden> · </span>Datos en memoria
        </p>
        <a
          href={REPO_URL}
          target="_blank"
          rel="noreferrer"
          className="inline-flex shrink-0 items-center gap-1 font-medium underline-offset-2 hover:underline"
        >
          <ExternalLink className="size-3.5" aria-hidden />
          <span className="hidden sm:inline">Código en GitHub</span>
          <span className="sm:hidden">GitHub</span>
        </a>
        <button
          type="button"
          onClick={() => setVisible(false)}
          aria-label="Cerrar aviso de demo"
          className="shrink-0 rounded p-0.5 text-indigo-700 hover:bg-indigo-100 focus-visible:outline-2 focus-visible:outline-indigo-600"
        >
          <X className="size-3.5" aria-hidden />
        </button>
      </div>
    </div>
  )
}
