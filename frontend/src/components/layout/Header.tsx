import { ExternalLink, FileCheck2, FlaskConical, Sparkles } from 'lucide-react'
import type { ExtractionMode } from '../../types/api'
import { cn } from '../../lib/cn'
import type { HealthStatus } from '../../hooks/useHealth'
import { api } from '../../services/api'

const HEALTH_DOT: Record<HealthStatus, { dot: string; label: string }> = {
  checking: { dot: 'bg-slate-300 animate-pulse', label: 'Comprobando API…' },
  online: { dot: 'bg-emerald-500', label: 'API conectada' },
  offline: { dot: 'bg-red-500', label: 'API sin conexión' },
}

interface HeaderProps {
  health: HealthStatus
  mode: ExtractionMode | null
}

export function Header({ health, mode }: HeaderProps) {
  const dot = HEALTH_DOT[health]
  const docsUrl = api.docsUrl()
  return (
    <header className="sticky top-0 z-30 border-b border-slate-200/80 bg-white/85 backdrop-blur">
      <div className="mx-auto flex max-w-[1400px] items-center gap-3 px-4 py-3 sm:px-6 lg:px-8">
        <span
          aria-hidden
          className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-indigo-700 text-white shadow-sm shadow-indigo-600/30"
        >
          <FileCheck2 className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-base font-semibold leading-tight text-slate-900 sm:text-lg">
            SmartInvoice-FACe Auditor
          </h1>
          <p className="truncate text-xs text-slate-500">Auditoría fiscal y Facturae 3.2.2</p>
        </div>

        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          <span
            className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-600"
            role="status"
            aria-label={dot.label}
            title={dot.label}
          >
            <span aria-hidden className={cn('size-2.5 rounded-full', dot.dot)} />
            <span className="hidden md:inline">{dot.label}</span>
          </span>

          {mode && (
            <span
              className={cn(
                'inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset',
                mode === 'AI'
                  ? 'bg-indigo-50 text-indigo-700 ring-indigo-600/20'
                  : 'bg-amber-50 text-amber-800 ring-amber-600/25',
              )}
              title={
                mode === 'AI'
                  ? 'Extracción con inteligencia artificial activa'
                  : 'Sin clave de IA: se usan datos de demostración al subir archivos'
              }
            >
              {mode === 'AI' ? (
                <Sparkles aria-hidden className="size-3.5" />
              ) : (
                <FlaskConical aria-hidden className="size-3.5" />
              )}
              <span className="hidden sm:inline">Modo </span>
              {mode === 'AI' ? 'IA' : 'Demo'}
            </span>
          )}

          {docsUrl && (
            <a
              href={docsUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2 text-xs font-semibold text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900"
            >
              <span className="hidden sm:inline">API docs</span>
              <span className="sr-only sm:hidden">API docs</span>
              <ExternalLink aria-hidden className="size-4" />
            </a>
          )}
        </div>
      </div>
    </header>
  )
}
