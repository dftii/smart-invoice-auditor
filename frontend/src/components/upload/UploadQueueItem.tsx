import { Ban, ChevronDown, CircleAlert, FastForward, FileText, RotateCw, TriangleAlert, X } from 'lucide-react'
import { useState } from 'react'
import { cn } from '../../lib/cn'
import { formatFileSize } from '../../lib/format'
import { Button } from '../ui/Button'
import { CheckCounter, CheckList } from './CheckList'
import { orderChecks } from './checks'
import { PipelineStepper } from './PipelineStepper'
import { ResultCard } from './ResultCard'
import { ScanVisual } from './ScanVisual'
import { isPdf } from './validation'
import type { ProcessingStep, UploadItem } from './types'

const STEP_TEXT: Record<ProcessingStep, { title: string; detail: string }> = {
  scanning: { title: 'Escaneando documento', detail: 'Detectando texto, tablas e importes del documento.' },
  extracting: { title: 'Extrayendo datos', detail: 'Identificando emisor, receptor, fechas, líneas e impuestos.' },
  auditing: { title: 'Auditando', detail: 'Aplicando las reglas fiscales: NIF/CIF, IVA, IRPF y totales.' },
}

interface UploadQueueItemProps {
  item: UploadItem
  onCancel: (id: number) => void
  onRetry: (id: number) => void
  onRemove: (id: number) => void
  onSkip: (id: number) => void
  onInspect: (invoiceId: number) => void
}

function Thumbnail({ item }: { item: UploadItem }) {
  if (item.previewUrl) {
    return <img src={item.previewUrl} alt="" className="size-12 shrink-0 rounded-lg object-cover ring-1 ring-slate-900/10" />
  }
  return (
    <span
      aria-hidden
      className={cn(
        'flex size-12 shrink-0 items-center justify-center rounded-lg ring-1 ring-inset',
        isPdf(item.file) ? 'bg-red-50 text-red-500 ring-red-500/20' : 'bg-slate-100 text-slate-500 ring-slate-500/20',
      )}
    >
      <FileText className="size-6" />
    </span>
  )
}

export function UploadQueueItem({ item, onCancel, onRetry, onRemove, onSkip, onInspect }: UploadQueueItemProps) {
  const { phase, file } = item
  const [expanded, setExpanded] = useState<boolean | null>(null)
  const [confirmingRetry, setConfirmingRetry] = useState(false)

  const checksVisible = expanded ?? phase.kind === 'revealing'
  const canCancel = phase.kind === 'reading' || phase.kind === 'uploading' || (phase.kind === 'processing' && !phase.result)
  const canSkip = (phase.kind === 'processing' && phase.result !== null) || phase.kind === 'revealing'
  const canRetry = phase.kind === 'cancelled' || (phase.kind === 'error' && phase.retryable)
  // The server may already hold this invoice; retrying then needs an explicit "yes, duplicate it".
  const retryNeedsConfirm = (phase.kind === 'cancelled' || phase.kind === 'error') && phase.maybeCreated === true
  const canRemove = phase.kind === 'queued' || phase.kind === 'done' || phase.kind === 'error' || phase.kind === 'cancelled'

  const statusText = (() => {
    switch (phase.kind) {
      case 'queued':
        return 'En cola'
      case 'reading':
        return 'Leyendo el texto (OCR)…'
      case 'uploading':
        return 'Subiendo…'
      case 'processing':
        return STEP_TEXT[phase.step].title
      case 'revealing':
        return 'Auditando'
      case 'done':
        return 'Completada'
      case 'error':
        return 'Error'
      case 'cancelled':
        return 'Cancelada'
    }
  })()

  const orderedChecks = phase.kind === 'revealing' || phase.kind === 'done' ? orderChecks(phase.result.audit.checks) : []
  const visibleChecks = phase.kind === 'revealing' ? orderedChecks.slice(0, phase.revealed) : orderedChecks

  return (
    <li
      className={cn(
        'animate-fade-in space-y-4 rounded-2xl bg-white p-4 shadow-sm ring-1',
        phase.kind === 'error' ? 'ring-red-500/30' : 'ring-slate-900/5',
      )}
    >
      <div className="flex items-center gap-3">
        <Thumbnail item={item} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-slate-900" title={file.name}>
            {file.name}
          </p>
          <p className="text-xs text-slate-500">
            {formatFileSize(file.size)} · <span className={cn(phase.kind === 'error' && 'text-red-600')}>{statusText}</span>
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {canCancel && (
            <Button variant="ghost" size="sm" icon={<Ban aria-hidden className="size-4" />} onClick={() => onCancel(item.id)}>
              <span className="hidden sm:inline">Cancelar</span>
              <span className="sr-only sm:hidden">Cancelar subida de {file.name}</span>
            </Button>
          )}
          {canSkip && (
            <Button variant="ghost" size="sm" icon={<FastForward aria-hidden className="size-4" />} onClick={() => onSkip(item.id)}>
              <span className="hidden sm:inline">Omitir animación</span>
              <span className="sr-only sm:hidden">Omitir animación de {file.name}</span>
            </Button>
          )}
          {canRetry && (
            <Button variant="secondary" size="sm" icon={<RotateCw aria-hidden className="size-4" />} onClick={() => (retryNeedsConfirm ? setConfirmingRetry(true) : onRetry(item.id))}
            >
              Reintentar
            </Button>
          )}
          {canRemove && (
            <Button
              variant="ghost"
              size="sm"
              className="px-1.5"
              aria-label={`Quitar ${file.name} de la lista`}
              onClick={() => onRemove(item.id)}
            >
              <X aria-hidden className="size-4" />
            </Button>
          )}
        </div>
      </div>

      {phase.kind !== 'queued' && <PipelineStepper phase={phase} />}

      {phase.kind === 'reading' && <OcrProgress stage={phase.stage} progress={phase.progress} />}

      {phase.kind === 'uploading' && <UploadProgress loaded={phase.loaded} total={phase.total} />}

      {phase.kind === 'processing' && (
        <div className="flex items-center gap-4 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-900/5">
          <ScanVisual step={phase.step} />
          <div className="min-w-0 space-y-1" role="status">
            <p className="text-sm font-semibold text-indigo-700">{STEP_TEXT[phase.step].title}…</p>
            <p className="text-sm text-slate-600">{STEP_TEXT[phase.step].detail}</p>
            <p className="text-xs text-slate-400">
              {phase.result ? 'Respuesta recibida.' : 'Esperando la respuesta del servidor.'}
            </p>
          </div>
        </div>
      )}

      {phase.kind === 'done' && <ResultCard result={phase.result} onInspect={onInspect} />}

      {(phase.kind === 'revealing' || phase.kind === 'done') && orderedChecks.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3">
            <CheckCounter checks={visibleChecks} live={phase.kind === 'revealing'} />
            {phase.kind === 'done' && (
              <button
                type="button"
                aria-expanded={checksVisible}
                onClick={() => setExpanded(!checksVisible)}
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs font-medium text-indigo-600 hover:bg-indigo-50"
              >
                {checksVisible ? 'Ocultar comprobaciones' : 'Ver comprobaciones'}
                <ChevronDown aria-hidden className={cn('size-3.5 transition-transform', checksVisible && 'rotate-180')} />
              </button>
            )}
          </div>
          {checksVisible && <CheckList checks={visibleChecks} live={phase.kind === 'revealing'} />}
        </div>
      )}

      {phase.kind === 'error' && (
        <p role="alert" className="flex items-start gap-2 rounded-lg bg-red-50 p-3 text-sm text-red-800 ring-1 ring-red-600/20">
          <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
          <span className="min-w-0 break-words">{phase.message}</span>
        </p>
      )}

      {phase.kind === 'cancelled' &&
        (phase.maybeCreated ? (
          <p role="status" className="flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900 ring-1 ring-amber-600/20">
            <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-amber-600" />
            <span className="min-w-0">
              Cancelada en el navegador; el servidor puede haberla registrado. Revisa la tabla de facturas antes de
              volver a subirla.
            </span>
          </p>
        ) : (
          <p className="text-sm text-slate-500">La subida se canceló antes de terminar.</p>
        ))}

      {confirmingRetry && retryNeedsConfirm && (
        <div role="alert" className="space-y-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900 ring-1 ring-amber-600/20">
          <p>
            <strong className="font-semibold">Posible duplicado.</strong> El servidor ya pudo guardar esta factura; si la
            vuelves a subir, quedará registrada dos veces (la auditoría la marcará como duplicada).
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setConfirmingRetry(false)
                onRetry(item.id)
              }}
            >
              Reintentar igualmente
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmingRetry(false)}>
              No reintentar
            </Button>
          </div>
        </div>
      )}
    </li>
  )
}

function OcrProgress({ stage, progress }: { stage: string; progress: number }) {
  const percent = Math.min(100, Math.max(0, Math.round(progress * 100)))
  return (
    <div className="flex items-center gap-4 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-900/5">
      <ScanVisual step="scanning" />
      <div className="min-w-0 flex-1 space-y-2" role="status">
        <div className="space-y-1">
          <p className="text-sm font-semibold text-indigo-700">{stage}…</p>
          <p className="text-sm text-slate-600">
            OCR en tu navegador: el documento no sale de tu equipo para leerlo. La primera vez se descarga el modelo
            de español (unos 6 MB).
          </p>
        </div>
        <div
          role="progressbar"
          aria-label="Progreso del OCR"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          className="h-2 overflow-hidden rounded-full bg-slate-200"
        >
          <div className="h-full rounded-full bg-indigo-500 transition-[width] duration-200" style={{ width: `${percent}%` }} />
        </div>
        <p className="text-xs tabular-nums text-slate-500">{percent} %</p>
      </div>
    </div>
  )
}

function UploadProgress({ loaded, total }: { loaded: number; total: number }) {
  const percent = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0
  return (
    <div className="space-y-1.5">
      <div
        role="progressbar"
        aria-label="Progreso de subida"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className="h-2 overflow-hidden rounded-full bg-slate-100"
      >
        <div className="h-full rounded-full bg-indigo-500 transition-[width] duration-150" style={{ width: `${percent}%` }} />
      </div>
      <p className="flex justify-between text-xs tabular-nums text-slate-500">
        <span>{percent} %</span>
        <span>
          {formatFileSize(loaded)} de {formatFileSize(total)}
        </span>
      </p>
    </div>
  )
}
