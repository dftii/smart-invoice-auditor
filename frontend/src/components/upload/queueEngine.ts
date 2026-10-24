import { ApiError, uploadInvoice } from '../../services/api'
import type { UploadResponse } from '../../types/api'
import { activeStepIndex, isTerminal, type ProcessingStep, type UploadItem, type UploadPhase } from './types'
import { validateFile } from './validation'

/** The server does extraction + audit in one request, so the scan/extract/audit steps are timed. */
const STEP_DELAY_MS: Record<Exclude<ProcessingStep, 'scanning'>, number> = { extracting: 900, auditing: 1800 }
/** Minimum time the scan animation plays after the upload finished, even if the response is faster. */
const MIN_PROCESSING_MS = 2400

interface Run {
  controller: AbortController
  timers: Set<number>
  processStart: number | null
  skip: boolean
}

export interface QueueEngineDeps {
  publish: (items: UploadItem[]) => void
}

export interface QueueEngine {
  mount: () => void
  setOnUploaded: (listener: (result: UploadResponse) => void) => void
  dispose: () => void
  addFiles: (files: File[]) => void
  cancel: (id: number) => void
  retry: (id: number) => void
  remove: (id: number) => void
  skip: (id: number) => void
  skipAll: () => void
  clearFinished: () => void
}

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

  const beginProcessing = (id: number, run: Run) => {
    if (run.processStart !== null) return
    run.processStart = Date.now()
    setPhase(id, { kind: 'processing', step: 'scanning', result: null })
    schedule(run, () => setStep(id, 'extracting'), STEP_DELAY_MS.extracting)
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
      setPhase(
        id,
        aborted ? { kind: 'cancelled', failedStep } : { kind: 'error', message: errorMessage(error), failedStep, retryable: true },
      )
    }
    pump()
  }

  function start(item: UploadItem) {
    busy = true
    const run: Run = {
      controller: new AbortController(),
      timers: new Set(),
      processStart: null,
      skip: skipWhenQueued.delete(item.id),
    }
    runs.set(item.id, run)
    setPhase(item.id, { kind: 'uploading', loaded: 0, total: item.file.size })
    uploadInvoice(
      item.file,
      ({ loaded, total }) => {
        if (!alive || phaseOf(item.id)?.kind !== 'uploading') return
        if (loaded >= total) beginProcessing(item.id, run)
        else setPhase(item.id, { kind: 'uploading', loaded, total })
      },
      run.controller.signal,
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
        setPhase(id, { kind: 'cancelled', failedStep: 0 })
        return
      }
      if (phase?.kind === 'uploading' || (phase?.kind === 'processing' && !phase.result)) runs.get(id)?.controller.abort()
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
