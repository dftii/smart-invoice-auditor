// In-browser OCR (Tesseract.js, Spanish LSTM model) for invoices without a text layer: photos, screenshots and
// scanned PDFs. It runs in Web Workers on the visitor's machine, from the app's own origin (public/ocr, copied by
// scripts/copy-ocr-assets.mjs): the document is never sent anywhere to be read. The recognised text travels with the
// upload (`ocr_text`) and the backend's demo parser reads it like the text of a PDF.
//
// Tesseract (~6 MB the first time, then cached) and pdf.js are imported lazily: they only load when a file needs OCR.

import type { LoggerMessage, Worker as TesseractWorker } from 'tesseract.js'

/** Pages read from a scanned PDF (OCR takes about a second per page). */
export const MAX_OCR_PDF_PAGES = 5
/** A PDF with fewer visible characters than this has no usable text layer: it is a scan. */
export const MIN_PDF_TEXT_CHARS = 40
/** Same limit as the backend (MAX_OCR_TEXT_CHARS). */
export const MAX_OCR_TEXT_CHARS = 60_000
/** 72 dpi × 3 ≈ 216 dpi: enough for Tesseract on A4 invoices without huge canvases. */
const PDF_RENDER_SCALE = 3
/** Share of the progress bar used by loading the engine and the model before the first page. */
const SETUP_SHARE = 0.15

export interface OcrProgress {
  /** Spanish description of what is happening. */
  stage: string
  /** 0 to 1. */
  progress: number
}

export type OcrProgressListener = (progress: OcrProgress) => void

const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp']
const IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'webp']

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot < 0 ? '' : name.slice(dot + 1).toLowerCase()
}

export function isImageFile(file: File): boolean {
  return IMAGE_TYPES.includes(file.type) || (file.type === '' && IMAGE_EXTENSIONS.includes(extensionOf(file.name)))
}

export function isPdfFile(file: File): boolean {
  return file.type === 'application/pdf' || extensionOf(file.name) === 'pdf'
}

/** Images always need OCR; PDFs only when they turn out to have no text layer. */
export function isOcrCandidate(file: File): boolean {
  return isImageFile(file) || isPdfFile(file)
}

const STAGE_LABELS: Record<string, string> = {
  'loading tesseract core': 'Cargando el motor OCR',
  'initializing tesseract': 'Preparando el motor OCR',
  'initialized tesseract': 'Preparando el motor OCR',
  'loading language traineddata': 'Descargando el modelo de español',
  'loading language traineddata (from cache)': 'Cargando el modelo de español',
  'loaded language traineddata': 'Cargando el modelo de español',
  'initializing api': 'Preparando el motor OCR',
  'initialized api': 'Preparando el motor OCR',
}

/** Spanish label for a Tesseract logger status (unknown statuses keep a generic label). */
export function stageLabel(status: string): string {
  return STAGE_LABELS[status] ?? 'Preparando el motor OCR'
}

/** Overall progress while page `index` (0-based) of `total` is at `fraction` (0-1), after the setup share. */
export function pageProgress(index: number, total: number, fraction: number): number {
  const share = (1 - SETUP_SHARE) / Math.max(1, total)
  return Math.min(1, SETUP_SHARE + share * (index + Math.min(1, Math.max(0, fraction))))
}

/** Count of non-blank characters, used to tell a text PDF from a scanned one. */
export function visibleCharCount(text: string): number {
  return text.replace(/\s+/g, '').length
}

function abortError(): DOMException {
  return new DOMException('Lectura cancelada', 'AbortError')
}

function throwIfAborted(signal: AbortSignal | undefined) {
  if (signal?.aborted) throw abortError()
}

/** Resolves like `promise`, or rejects with AbortError as soon as `signal` aborts (running OCR is stopped apart). */
function abortable<T>(promise: Promise<T>, signal: AbortSignal | undefined): Promise<T> {
  if (!signal) return promise
  if (signal.aborted) return Promise.reject(abortError())
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(abortError())
    signal.addEventListener('abort', onAbort, { once: true })
    promise.then(
      (value) => {
        signal.removeEventListener('abort', onAbort)
        resolve(value)
      },
      (error: unknown) => {
        signal.removeEventListener('abort', onAbort)
        reject(error)
      },
    )
  })
}

function assetUrl(path: string): string {
  return new URL(`${import.meta.env.BASE_URL}ocr/${path}`, window.location.origin).href
}

// ------------------------------------------------------------------------------------------ Tesseract

let workerPromise: Promise<TesseractWorker> | null = null
/** Receives the logger messages of the job in progress (one job at a time: the upload queue is sequential). */
let activeLogger: ((message: LoggerMessage) => void) | null = null

function getWorker(): Promise<TesseractWorker> {
  if (!workerPromise) {
    const created = (async () => {
      const { createWorker, OEM, PSM } = await import('tesseract.js')
      const worker = await createWorker('spa', OEM.LSTM_ONLY, {
        workerPath: assetUrl('tesseract/worker.min.js'),
        corePath: assetUrl('tesseract/core/'),
        langPath: assetUrl('tesseract/lang'),
        // A same-origin worker file instead of a blob: URL, so a strict CSP (worker-src 'self') allows it.
        workerBlobURL: false,
        logger: (message) => activeLogger?.(message),
      })
      await worker.setParameters({
        // Keep the column gaps of tables: the parser splits rows on runs of spaces.
        preserve_interword_spaces: '1',
        tessedit_pageseg_mode: PSM.AUTO,
        // Tesseract's informational messages ("Estimating resolution as 245") would show up as console errors.
        debug_file: '/dev/null',
      })
      return worker
    })()
    workerPromise = created
    created.catch(() => {
      if (workerPromise === created) workerPromise = null
    })
  }
  return workerPromise
}

/** Stops the worker (the only way to interrupt a recognition); the next job starts a fresh one. */
function discardWorker() {
  const current = workerPromise
  workerPromise = null
  current?.then((worker) => worker.terminate()).catch(() => undefined)
}

async function recognizeImage(
  image: File | HTMLCanvasElement,
  onPage: (progress: number) => void,
  onSetup: (stage: string) => void,
  signal: AbortSignal | undefined,
): Promise<string> {
  activeLogger = (message) => {
    if (message.status === 'recognizing text') onPage(message.progress)
    else onSetup(stageLabel(message.status))
  }
  try {
    const worker = await abortable(getWorker(), signal)
    const { data } = await abortable(worker.recognize(image), signal)
    return data.text
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') discardWorker()
    throw error
  } finally {
    activeLogger = null
  }
}

// ------------------------------------------------------------------------------------------ pdf.js

type PdfJs = typeof import('pdfjs-dist')
let pdfJsPromise: Promise<PdfJs> | null = null

function loadPdfJs(): Promise<PdfJs> {
  if (!pdfJsPromise) {
    const loading = import('pdfjs-dist').then((pdfjs) => {
      pdfjs.GlobalWorkerOptions.workerPort = new Worker(new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url), {
        type: 'module',
      })
      return pdfjs
    })
    pdfJsPromise = loading
    loading.catch(() => {
      if (pdfJsPromise === loading) pdfJsPromise = null
    })
  }
  return pdfJsPromise
}

// ------------------------------------------------------------------------------------------ public API

export class OcrError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options)
    this.name = 'OcrError'
  }
}

/**
 * Text of an invoice that has no text layer, recognised in the browser.
 * Returns null when OCR is not needed (a PDF that already contains text: the backend reads it directly).
 * Throws OcrError when the engine cannot run, AbortError when `signal` aborts.
 */
export async function recognizeInvoiceText(
  file: File,
  onProgress: OcrProgressListener,
  signal?: AbortSignal,
): Promise<string | null> {
  try {
    if (isImageFile(file)) {
      onProgress({ stage: 'Preparando el motor OCR', progress: 0 })
      const text = await recognizeImage(
        file,
        (progress) => onProgress({ stage: 'Reconociendo el texto de la imagen', progress: pageProgress(0, 1, progress) }),
        (stage) => onProgress({ stage, progress: 0.05 }),
        signal,
      )
      return text.trim().slice(0, MAX_OCR_TEXT_CHARS)
    }
    if (isPdfFile(file)) return await recognizeScannedPdf(file, onProgress, signal)
    return null
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    if (error instanceof OcrError) throw error
    throw new OcrError('No se pudo leer el texto del documento con OCR. Recarga la página e inténtalo de nuevo.', {
      cause: error,
    })
  }
}

async function recognizeScannedPdf(
  file: File,
  onProgress: OcrProgressListener,
  signal: AbortSignal | undefined,
): Promise<string | null> {
  onProgress({ stage: 'Analizando el PDF', progress: 0 })
  const pdfjs = await abortable(loadPdfJs(), signal)
  const data = new Uint8Array(await abortable(file.arrayBuffer(), signal))
  const loadingTask = pdfjs.getDocument({ data, wasmUrl: assetUrl('pdfjs/wasm/') })
  let document: Awaited<typeof loadingTask.promise>
  try {
    document = await abortable(loadingTask.promise, signal)
  } catch (error) {
    void loadingTask.destroy()
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    return null // encrypted or damaged: the backend reports it with its own message
  }
  try {
    const pages = Math.min(document.numPages, MAX_OCR_PDF_PAGES)
    let visible = 0
    for (let number = 1; number <= pages; number += 1) {
      const page = await abortable(document.getPage(number), signal)
      const content = await abortable(page.getTextContent(), signal)
      visible += visibleCharCount(content.items.map((item) => ('str' in item ? item.str : '')).join(''))
    }
    if (visible >= MIN_PDF_TEXT_CHARS) return null

    const texts: string[] = []
    for (let index = 0; index < pages; index += 1) {
      throwIfAborted(signal)
      const label = pages > 1 ? `Reconociendo el texto (página ${index + 1} de ${pages})` : 'Reconociendo el texto del PDF'
      onProgress({ stage: label, progress: pageProgress(index, pages, 0) })
      const page = await abortable(document.getPage(index + 1), signal)
      const viewport = page.getViewport({ scale: PDF_RENDER_SCALE })
      const canvas = window.document.createElement('canvas')
      canvas.width = Math.ceil(viewport.width)
      canvas.height = Math.ceil(viewport.height)
      // intent 'print': renders in one go instead of pacing with requestAnimationFrame, which browsers pause in
      // background tabs (switching tabs during the OCR would otherwise freeze it).
      await abortable(page.render({ canvas, viewport, background: '#ffffff', intent: 'print' }).promise, signal)
      texts.push(
        await recognizeImage(
          canvas,
          (progress) => onProgress({ stage: label, progress: pageProgress(index, pages, progress) }),
          (stage) => onProgress({ stage, progress: pageProgress(index, pages, 0) }),
          signal,
        ),
      )
      canvas.width = 0 // release the bitmap right away
      canvas.height = 0
    }
    return texts.join('\n\n').trim().slice(0, MAX_OCR_TEXT_CHARS)
  } finally {
    void loadingTask.destroy()
  }
}
