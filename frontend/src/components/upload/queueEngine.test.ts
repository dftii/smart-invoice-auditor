import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setTransport, type TransportRequest } from '../../services/api'
import type { UploadResponse } from '../../types/api'
import { createQueueEngine, type TextRecognizer } from './queueEngine'
import type { UploadItem, UploadPhase } from './types'

const RESPONSE = {
  invoice: { id: 7 },
  audit: { checks: [], status: 'VALIDATED' },
  extraction_method: 'DEMO',
  extraction_warnings: [],
} as unknown as UploadResponse

interface Harness {
  engine: ReturnType<typeof createQueueEngine>
  phases: UploadPhase['kind'][]
  latest: () => UploadItem | undefined
  requests: TransportRequest[]
}

function setup(recognizeText: TextRecognizer, ocrEnabled: boolean): Harness {
  const phases: UploadPhase['kind'][] = []
  let items: UploadItem[] = []
  const requests: TransportRequest[] = []
  setTransport(async (request) => {
    requests.push(request)
    return { status: 201, headers: { 'content-type': 'application/json' }, body: RESPONSE as never }
  })
  const engine = createQueueEngine({
    publish: (next) => {
      items = next
      const kind = next[0]?.phase.kind
      if (kind && phases[phases.length - 1] !== kind) phases.push(kind)
    },
    recognizeText,
  })
  engine.mount()
  engine.setOcrEnabled(ocrEnabled)
  return { engine, phases, latest: () => items[0], requests }
}

function image(): File {
  return new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'foto.png', { type: 'image/png' })
}

function formField(request: TransportRequest, name: string): FormDataEntryValue | null {
  return (request.body as FormData).get(name)
}

beforeEach(() => {
  // Reduced motion: results are shown at once, without the reveal animation.
  vi.stubGlobal('window', {
    setTimeout: globalThis.setTimeout.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
    matchMedia: () => ({ matches: true }),
  })
})

afterEach(() => {
  setTransport(null)
  vi.unstubAllGlobals()
})

describe('OCR before the upload', () => {
  it('reads the image, reports progress and sends the text with the file', async () => {
    const recognize = vi.fn<TextRecognizer>(async (_file, onProgress) => {
      onProgress({ stage: 'Reconociendo el texto de la imagen', progress: 0.5 })
      return 'FACTURA Nº 1'
    })
    const { engine, phases, latest, requests } = setup(recognize, true)
    engine.addFiles([image()])

    await vi.waitFor(() => expect(latest()?.phase.kind).toBe('done'), { timeout: 5000 })
    expect(recognize).toHaveBeenCalledTimes(1)
    expect(phases).toEqual(['queued', 'reading', 'processing', 'done'])
    expect(requests).toHaveLength(1)
    expect(formField(requests[0], 'ocr_text')).toBe('FACTURA Nº 1')
    engine.dispose()
  })

  it('uploads without OCR when it is disabled (Modo IA)', async () => {
    const recognize = vi.fn<TextRecognizer>()
    const { engine, phases, latest, requests } = setup(recognize, false)
    engine.addFiles([image()])

    await vi.waitFor(() => expect(latest()?.phase.kind).toBe('done'), { timeout: 5000 })
    expect(recognize).not.toHaveBeenCalled()
    expect(phases[1]).toBe('uploading')
    expect(formField(requests[0], 'ocr_text')).toBeNull()
    engine.dispose()
  })

  it('sends no OCR text when the PDF already has text', async () => {
    const recognize = vi.fn<TextRecognizer>(async () => null)
    const { engine, latest, requests } = setup(recognize, true)
    engine.addFiles([new File(['%PDF-1.4'], 'factura.pdf', { type: 'application/pdf' })])

    await vi.waitFor(() => expect(latest()?.phase.kind).toBe('done'), { timeout: 5000 })
    expect(recognize).toHaveBeenCalledTimes(1)
    expect(formField(requests[0], 'ocr_text')).toBeNull()
    engine.dispose()
  })

  it('shows an OCR failure as a retryable error and does not upload', async () => {
    const recognize = vi.fn<TextRecognizer>(async () => {
      throw new Error('No se pudo leer el texto del documento con OCR.')
    })
    const { engine, latest, requests } = setup(recognize, true)
    engine.addFiles([image()])

    await vi.waitFor(() => expect(latest()?.phase.kind).toBe('error'), { timeout: 5000 })
    const phase = latest()?.phase
    expect(phase).toMatchObject({ kind: 'error', retryable: true, failedStep: 1 })
    expect(phase?.kind === 'error' && phase.message).toContain('OCR')
    expect(requests).toHaveLength(0)
    engine.dispose()
  })

  it('cancelling during OCR aborts the recognizer and never uploads', async () => {
    let seenSignal: AbortSignal | null = null
    const recognize = vi.fn<TextRecognizer>(
      (_file, _onProgress, signal) =>
        new Promise((_resolve, reject) => {
          seenSignal = signal
          signal.addEventListener('abort', () => reject(new DOMException('Lectura cancelada', 'AbortError')))
        }),
    )
    const { engine, latest, requests } = setup(recognize, true)
    engine.addFiles([image()])
    await vi.waitFor(() => expect(latest()?.phase.kind).toBe('reading'))

    engine.cancel(latest()!.id)
    await vi.waitFor(() => expect(latest()?.phase.kind).toBe('cancelled'))
    expect(seenSignal!.aborted).toBe(true)
    expect(latest()?.phase).toMatchObject({ kind: 'cancelled', maybeCreated: false })
    expect(requests).toHaveLength(0)
    engine.dispose()
  })
})
