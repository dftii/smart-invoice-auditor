// Transport that sends every API request to the in-browser backend (Pyodide in a Web Worker) instead of HTTP.

import { buildQuery, type JsonValue, type Transport, type TransportRequest, type TransportResponse } from '../services/api'
import type { BootProgress, WorkerOutbound, WorkerRequest, WorkerResponse } from './protocol'

export type { BootProgress } from './protocol'

const BACKEND_UNAVAILABLE = 'El backend del navegador no está disponible. Recarga la página para reiniciarlo.'

interface Pending {
  resolve: (response: WorkerResponse) => void
  reject: (error: Error) => void
}

interface Session {
  worker: Worker
  pending: Map<number, Pending>
  ready: Promise<void>
  listeners: Set<(progress: BootProgress) => void>
  nextId: number
  /** Last boot progress, replayed to listeners that subscribe after the boot started. */
  lastProgress: BootProgress | null
}

let session: Session | null = null

function createSession(): Session {
  const worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module', name: 'smartinvoice-backend' })
  const pending = new Map<number, Pending>()
  const listeners = new Set<(progress: BootProgress) => void>()
  let resolveReady!: () => void
  let rejectReady!: (error: Error) => void
  const ready = new Promise<void>((resolve, reject) => {
    resolveReady = resolve
    rejectReady = reject
  })
  ready.catch(() => undefined) // consumers handle it; avoid an "unhandled rejection" if nobody awaits yet
  const created: Session = { worker, pending, ready, listeners, nextId: 1, lastProgress: null }

  const failAll = (error: Error) => {
    rejectReady(error)
    for (const request of pending.values()) request.reject(error)
    pending.clear()
  }

  worker.addEventListener('message', (event: MessageEvent<WorkerOutbound>) => {
    const message = event.data
    if ('type' in message) {
      switch (message.type) {
        case 'progress':
          created.lastProgress = { step: message.step, total: message.total, stage: message.stage }
          listeners.forEach((listener) => listener(created.lastProgress!))
          break
        case 'ready':
          console.info('[demo] backend listo', message.timings)
          resolveReady()
          break
        case 'error':
          console.error('[demo] error de arranque:', message.stack ?? message.message)
          failAll(new Error(message.message))
          break
        case 'log':
          console.debug('[python]', message.line)
          break
      }
      return
    }
    const request = pending.get(message.id)
    if (!request) return // aborted meanwhile
    pending.delete(message.id)
    request.resolve(message)
  })
  worker.addEventListener('error', (event) => failAll(new Error(event.message || BACKEND_UNAVAILABLE)))
  worker.addEventListener('messageerror', () => failAll(new Error(BACKEND_UNAVAILABLE)))
  worker.postMessage({ type: 'init' })
  return created
}

/**
 * Boots the in-browser backend (idempotent) and resolves when it is ready to serve requests. After a failure the
 * next call starts from scratch (retry).
 */
export function startDemo(onProgress?: (progress: BootProgress) => void): Promise<void> {
  if (!session) session = createSession()
  const current = session
  if (onProgress) {
    current.listeners.add(onProgress)
    if (current.lastProgress) onProgress(current.lastProgress)
  }
  current.ready.catch(() => {
    if (session === current) {
      current.worker.terminate()
      session = null
    }
  })
  return current.ready.finally(() => {
    if (onProgress) current.listeners.delete(onProgress)
  })
}

// ------------------------------------------------------------------------------------------ requests

interface EncodedBody {
  bytes: ArrayBuffer | null
  contentType: string | null
}

/** JSON objects are serialized; FormData is turned into multipart bytes (the browser picks the boundary). */
async function encodeBody(body: TransportRequest['body']): Promise<EncodedBody> {
  if (body === undefined) return { bytes: null, contentType: null }
  if (body instanceof FormData) {
    const multipart = new Response(body)
    return { bytes: await multipart.arrayBuffer(), contentType: multipart.headers.get('content-type') }
  }
  const text = new TextEncoder().encode(JSON.stringify(body))
  return { bytes: text.buffer as ArrayBuffer, contentType: 'application/json' }
}

export function decodeResponse(response: WorkerResponse): TransportResponse {
  const headers: Record<string, string> = {}
  for (const [name, value] of response.headers) headers[name.toLowerCase()] = value
  const contentType = headers['content-type'] ?? ''
  const empty = response.body.byteLength === 0 || [204, 205, 304].includes(response.status)
  let body: TransportResponse['body'] = null
  if (!empty) {
    const blob = new Blob([response.body], { type: contentType })
    body = blob
    if (/json/i.test(contentType)) {
      try {
        body = JSON.parse(new TextDecoder().decode(response.body)) as JsonValue
      } catch {
        body = blob
      }
    }
  }
  return { status: response.status, headers, body }
}

function abortError(): DOMException {
  return new DOMException('Petición cancelada', 'AbortError')
}

/** Reports a plausible 0 -> 100 % progress: the data never leaves the browser, so it is only a visual cue. */
function simulateUpload(total: number, report: NonNullable<TransportRequest['onUploadProgress']>) {
  const steps = [0.2, 0.45, 0.7, 0.9]
  let index = 0
  report({ loaded: 0, total })
  const timer = window.setInterval(() => {
    if (index >= steps.length) return
    report({ loaded: Math.round(total * steps[index++]), total })
  }, 120)
  return () => {
    window.clearInterval(timer)
    report({ loaded: total, total })
  }
}

export const demoTransport: Transport = async (request) => {
  const { signal } = request
  if (signal?.aborted) throw abortError()
  await startDemo()
  const { bytes, contentType } = await encodeBody(request.body)
  if (signal?.aborted) throw abortError()

  const active = session
  if (!active) throw new Error(BACKEND_UNAVAILABLE)
  const id = active.nextId++
  const message: WorkerRequest = {
    id,
    method: request.method,
    path: request.path,
    query: buildQuery(request.query).replace(/^\?/, ''),
    headers: { accept: 'application/json, */*', ...(contentType ? { 'content-type': contentType } : {}) },
    body: bytes,
  }
  const finishProgress =
    request.onUploadProgress && bytes ? simulateUpload(bytes.byteLength, request.onUploadProgress) : null

  return new Promise<TransportResponse>((resolve, reject) => {
    const onAbort = () => {
      active.pending.delete(id) // the worker still finishes the call, its answer is dropped
      cleanup()
      finishProgress?.()
      reject(abortError())
    }
    const cleanup = () => signal?.removeEventListener('abort', onAbort)
    signal?.addEventListener('abort', onAbort, { once: true })
    active.pending.set(id, {
      resolve: (response) => {
        cleanup()
        finishProgress?.()
        resolve(decodeResponse(response))
      },
      reject: (error) => {
        cleanup()
        finishProgress?.()
        reject(error)
      },
    })
    active.worker.postMessage(message, bytes ? [bytes] : [])
  })
}
