import type { UploadResponse } from '../../types/api'

export type ProcessingStep = 'scanning' | 'extracting' | 'auditing'

/** Every state a queued file can be in; `kind` is the discriminant. */
export type UploadPhase =
  | { kind: 'queued' }
  /** OCR in the browser (images and scanned PDFs, Modo Demo only), before the upload. `progress` goes from 0 to 1. */
  | { kind: 'reading'; stage: string; progress: number }
  | { kind: 'uploading'; loaded: number; total: number }
  /** Server is extracting + auditing. `result` is set once the response arrived but the minimum scan animation is still playing. */
  | { kind: 'processing'; step: ProcessingStep; result: UploadResponse | null }
  | { kind: 'revealing'; result: UploadResponse; revealed: number }
  | { kind: 'done'; result: UploadResponse }
  /** `maybeCreated`: the request had been fully sent, so the server may have stored the invoice anyway. */
  | { kind: 'error'; message: string; failedStep: number; retryable: boolean; maybeCreated?: boolean }
  /** `maybeCreated`: cancelled while the server was processing, so it may have stored the invoice anyway. */
  | { kind: 'cancelled'; failedStep: number; maybeCreated: boolean }

export interface UploadItem {
  id: number
  file: File
  /** Object URL for image thumbnails; revoked by the queue engine. */
  previewUrl: string | null
  phase: UploadPhase
}

export type StepState = 'complete' | 'active' | 'pending' | 'error'

export const PIPELINE_STEPS = ['Subiendo', 'Escaneando', 'Extrayendo', 'Auditando', 'Resultado'] as const

const PROCESSING_INDEX: Record<ProcessingStep, number> = { scanning: 1, extracting: 2, auditing: 3 }

/** Index of the pipeline step a phase is currently in (-1 = not started, 5 = everything finished). */
export function activeStepIndex(phase: UploadPhase): number {
  switch (phase.kind) {
    case 'queued':
      return -1
    case 'uploading':
      return 0
    case 'reading':
      return 1
    case 'processing':
      return PROCESSING_INDEX[phase.step]
    case 'revealing':
      return 3
    case 'done':
      return PIPELINE_STEPS.length
    case 'error':
    case 'cancelled':
      return phase.failedStep
  }
}

export function stepStates(phase: UploadPhase): StepState[] {
  const current = activeStepIndex(phase)
  const failed = phase.kind === 'error' || phase.kind === 'cancelled'
  return PIPELINE_STEPS.map((_, index) => {
    if (index < current) return 'complete'
    if (index === current) return failed ? 'error' : 'active'
    return 'pending'
  })
}

export function isTerminal(phase: UploadPhase): boolean {
  return phase.kind === 'done' || phase.kind === 'error' || phase.kind === 'cancelled'
}
