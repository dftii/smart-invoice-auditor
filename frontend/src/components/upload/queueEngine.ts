import { ApiError, isHttpTransport, uploadInvoice } from '../../services/api'
import type { UploadResponse } from '../../types/api'
import { activeStepIndex, isTerminal, type ProcessingStep, type UploadItem, type UploadPhase } from './types'
import { validateFile } from './validation'
import { isOcrCandidate, recognizeInvoiceText, type OcrProgressListener } from '../../lib/ocr'

/** Recognises the text of a file without a text layer; null when OCR is not needed. See lib/ocr.ts. */
export type TextRecognizer = (file: File, onProgress: OcrProgressListener, signal: AbortSignal) => Promise<string | null>

/** The server does extraction + audit in one request, so the scan/extract/audit steps are timed. */
const STEP_DELAY_MS: Record<Exclude<ProcessingStep, 'scanning'>, number> = { extracting: 900, auditing: 1800 }
/** Minimum time the scan animation plays after the upload finished, even if the response is faster. */
const MIN_PROCESSING_MS = 2400

interface Run {
  controller: AbortController
  timers: Set<number>
  processStart: number | null
  /** The whole request body has been sent: from now on the server may create the invoice even if we give up. */
  uploadSent: boolean
  skip: boolean
}

export interface QueueEngineDeps {
  publish: (items: UploadItem[]) => void
  /** Injected in tests; defaults to the in-browser Tesseract OCR. */
  recognizeText?: TextRecognizer
}

export interface QueueEngine {
  mount: () => void
  setOnUploaded: (listener: (result: UploadResponse) => void) => void
  /** OCR before uploading images and scanned PDFs: on in Modo Demo; the AI reads documents by itself. */
  setOcrEnabled: (enabled: boolean) => void
  /** Called when a request was abandoned after it had been sent: the invoice list may have changed. */
  setOnMaybeCreated: (listener: () => void) => void
  dispose: () => void
  addFiles: (files: File[]) => void
  cancel: (id: number) => void
  retry: (id: number) => void
  remove: (id: number) => void
  skip: (id: number) => void
  skipAll: () => void
  clearFinished: () => void
}

const MAYBE_CREATED_HINT = 'La factura puede haberse registrado en el servidor: compruébalo en la tabla antes de reintentar.'

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function revealDelay(index: number): number {
  return 60 + ((index * 37) % 31)
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error && error.message) return error.message
  return 'Error inesperado al subir la factura.'
}

/**
 * Sequential upload queue as a plain state machine (outside React so timers and requests have one owner).
 * Only one request is in flight at a time; the reveal animation of a finished file overlaps the next upload.
 */
export function createQueueEngine(deps: QueueEngineDeps): QueueEngine {
  let items: UploadItem[] = []
  let alive = true
  let busy = false
  let nextId = 1
  let notify: (result: UploadResponse) => void = () => {}
  let notifyMaybeCreated: () => void = () => {}
  let ocrEnabled = false
  const recognizeText = deps.recognizeText ?? recognizeInvoiceText
  const runs = new Map<number, Run>()
  const skipWhenQueued = new Set<number>()

  const publish = () => deps.publish(items)

  const phaseOf = (id: number): UploadPhase | undefined => items.find((item) => item.id === id)?.phase

  const setPhase = (id: number, phase: UploadPhase) => {
    if (!items.some((item) => item.id === id)) return
    items = items.map((item) => (item.id === id ? { ...item, phase } : item))
    publish()
  }

  const clearTimers = (run: Run) => {
    run.timers.forEach((timer) => window.clearTimeout(timer))
    run.timers.clear()
  }

  const schedule = (run: Run, callback: () => void, delay: number) => {
    const timer = window.setTimeout(() => {
      run.timers.delete(timer)
      if (alive) callback()
    }, delay)
    run.timers.add(timer)
  }

  const finish = (id: number, run: Run, result: UploadResponse) => {
    clearTimers(run)
    runs.delete(id)
    setPhase(id, { kind: 'done', result })
  }

  const beginReveal = (id: number, run: Run, result: UploadResponse) => {
    clearTimers(run)
    const total = result.audit.checks.length
    if (run.skip || total === 0 || prefersReducedMotion()) {
      finish(id, run, result)
      return
    }
    let revealed = 0
    const tick = () => {
      revealed += 1
      if (revealed >= total) {
        finish(id, run, result)
        return
      }
      setPhase(id, { kind: 'revealing', result, revealed })
      schedule(run, tick, revealDelay(revealed))
    }
    setPhase(id, { kind: 'revealing', result, revealed: 0 })
    schedule(run, tick, revealDelay(0))
  }

  const setStep = (id: number, step: ProcessingStep) => {
    const phase = phaseOf(id)
    if (phase?.kind === 'processing') setPhase(id, { ...phase, step })
  }

  /** `firstStep` is 'extracting' when the text was already recognised in the browser (the scan really happened). */
  const beginProcessing = (id: number, run: Run, firstStep: ProcessingStep = 'scanning') => {
    if (run.processStart !== null) return
    run.processStart = Date.now()
    setPhase(id, { kind: 'processing', step: firstStep, result: null })
    if (firstStep === 'scanning') schedule(run, () => setStep(id, 'extracting'), STEP_DELAY_MS.extracting)
    schedule(run, () => setStep(id, 'auditing'), STEP_DELAY_MS.auditing)
  }

  function pump() {
    if (busy || !alive) return
    const next = items.find((item) => item.phase.kind === 'queued')
    if (next) start(next)
  }

  const onResponse = (id: number, run: Run, result: UploadResponse) => {
    busy = false
    if (!alive) return
    if (!items.some((item) => item.id === id)) {
      runs.delete(id)
      pump()
      return
    }
    notify(result)
    run.uploadSent = true
    beginProcessing(id, run)
    const phase = phaseOf(id)
    if (phase?.kind === 'processing') setPhase(id, { ...phase, result })
    const elapsed = Date.now() - (run.processStart ?? Date.now())
    const remaining = run.skip ? 0 : Math.max(0, MIN_PROCESSING_MS - elapsed)
    if (remaining === 0) beginReveal(id, run, result)
    else schedule(run, () => beginReveal(id, run, result), remaining)
    pump()
  }

  const onFailure = (id: number, run: Run, error: unknown) => {
    busy = false
    clearTimers(run)
    runs.delete(id)
    if (!alive) return
    const phase = phaseOf(id)
    if (phase) {
      const failedStep = Math.max(0, activeStepIndex(phase))
      const aborted = error instanceof DOMException && error.name === 'AbortError'
      // Once the body is sent the server may finish (and store) the invoice whatever happens to the response here.
      const gatewayGaveUp = error instanceof ApiError && (error.status === 0 || error.status === 502 || error.status === 504)
      const maybeCreated = run.uploadSent && (aborted || gatewayGaveUp)
      if (aborted) {
        setPhase(id, { kind: 'cancelled', failedStep, maybeCreated })
      } else {
        const message = errorMessage(error)
        setPhase(id, {
          kind: 'error',
          message: maybeCreated ? `${message} ${MAYBE_CREATED_HINT}` : message,
          failedStep,
          retryable: true,
          maybeCreated,
        })
      }
      if (maybeCreated) notifyMaybeCreated()
    }
    pump()
  }

  function start(item: UploadItem) {
    busy = true
    const run: Run = {
      controller: new AbortController(),
      timers: new Set(),
      processStart: null,
      uploadSent: false, // set by send(): nothing reaches the backend while OCR runs
      skip: skipWhenQueued.delete(item.id),
    }
    runs.set(item.id, run)
    if (!ocrEnabled || !isOcrCandidate(item.file)) {
      send(item, run, null, false)
      return
    }
    setPhase(item.id, { kind: 'reading', stage: 'Preparando el motor OCR', progress: 0 })
    recognizeText(
      item.file,
      ({ stage, progress }) => {
        if (alive && phaseOf(item.id)?.kind === 'reading') setPhase(item.id, { kind: 'reading', stage, progress })
      },
      run.controller.signal,
    ).then(
      (text) => {
        if (run.controller.signal.aborted) onFailure(item.id, run, new DOMException('Subida cancelada', 'AbortError'))
        else send(item, run, text, true)
      },
      (error: unknown) => onFailure(item.id, run, error),
    )
  }

  /**
   * After OCR the item goes straight to "processing": the upload itself is instant in the demo and a few ms on a
   * local backend, and showing an upload bar after the scan step would move the stepper backwards.
   */
  function send(item: UploadItem, run: Run, ocrText: string | null, afterReading: boolean) {
    const firstStep: ProcessingStep = ocrText ? 'extracting' : 'scanning'
    // Without a network upload (in-process transport) the work starts immediately: nothing is "not yet sent".
    run.uploadSent = !isHttpTransport()
    if (afterReading) beginProcessing(item.id, run, firstStep)
    else setPhase(item.id, { kind: 'uploading', loaded: 0, total: item.file.size })
    uploadInvoice(
      item.file,
      ({ loaded, total }) => {
        if (!alive) return
        if (loaded >= total) {
          run.uploadSent = true
          beginProcessing(item.id, run, firstStep)
        } else if (phaseOf(item.id)?.kind === 'uploading') {
          setPhase(item.id, { kind: 'uploading', loaded, total })
        }
      },
      run.controller.signal,
      ocrText,
    ).then(
      (result) => onResponse(item.id, run, result),
      (error: unknown) => onFailure(item.id, run, error),
    )
  }

  const revoke = (item: UploadItem) => {
    if (item.previewUrl) URL.revokeObjectURL(item.previewUrl)
  }

  const dropItems = (ids: Set<number>) => {
    for (const item of items) {
      if (!ids.has(item.id)) continue
      const run = runs.get(item.id)
      if (run) {
        clearTimers(run)
        run.controller.abort()
        runs.delete(item.id)
      }
      skipWhenQueued.delete(item.id)
      revoke(item)
    }
    items = items.filter((item) => !ids.has(item.id))
    publish()
  }

  const skip = (id: number) => {
    const phase = phaseOf(id)
    if (!phase) return
    const run = runs.get(id)
    if (!run) {
      if (phase.kind === 'queued') skipWhenQueued.add(id)
      return
    }
    run.skip = true
    if (phase.kind === 'processing' && phase.result) finish(id, run, phase.result)
    else if (phase.kind === 'revealing') finish(id, run, phase.result)
  }

  return {
    mount() {
      alive = true
    },

    setOnUploaded(listener) {
      notify = listener
    },

    setOnMaybeCreated(listener) {
      notifyMaybeCreated = listener
    },

    setOcrEnabled(enabled) {
      ocrEnabled = enabled
    },

    dispose() {
      alive = false
      busy = false
      for (const run of runs.values()) {
        clearTimers(run)
        run.controller.abort()
      }
      runs.clear()
      skipWhenQueued.clear()
      items.forEach(revoke)
      items = []
    },

    addFiles(files) {
      if (files.length === 0) return
      const added = files.map<UploadItem>((file) => {
        const error = validateFile(file)
        return {
          id: nextId++,
          file,
          previewUrl: file.type.startsWith('image/') ? URL.createObjectURL(file) : null,
          phase: error ? { kind: 'error', message: error, failedStep: 0, retryable: false } : { kind: 'queued' },
        }
      })
      items = [...items, ...added]
      publish()
      pump()
    },

    cancel(id) {
      const phase = phaseOf(id)
      if (phase?.kind === 'queued') {
        skipWhenQueued.delete(id)
        setPhase(id, { kind: 'cancelled', failedStep: 0, maybeCreated: false })
        return
      }
      if (phase?.kind === 'reading' || phase?.kind === 'uploading' || (phase?.kind === 'processing' && !phase.result)) {
        runs.get(id)?.controller.abort()
      }
    },

    retry(id) {
      const phase = phaseOf(id)
      if (phase?.kind === 'cancelled' || (phase?.kind === 'error' && phase.retryable)) {
        setPhase(id, { kind: 'queued' })
        pump()
      }
    },

    remove(id) {
      dropItems(new Set([id]))
    },

    skip,

    skipAll() {
      items.forEach((item) => skip(item.id))
    },

    clearFinished() {
      dropItems(new Set(items.filter((item) => isTerminal(item.phase)).map((item) => item.id)))
    },
  }
}
