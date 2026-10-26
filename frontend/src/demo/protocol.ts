// Messages exchanged between the main thread (transport.ts) and the backend worker (worker.ts).

/** Boot steps shown on the boot screen, in order. The worker reports `step` (1-based) as each one starts. */
export const BOOT_STAGES = [
  'Descargando Python (WebAssembly)…',
  'Instalando dependencias…',
  'Cargando backend FastAPI…',
] as const

export interface BootProgress {
  step: number
  total: number
  stage: string
}

export interface WorkerRequest {
  id: number
  method: string
  /** Server path including the API prefix, e.g. `/api/v1/invoices/3`. */
  path: string
  /** Raw query string without the leading `?`. */
  query: string
  headers: Record<string, string>
  body: ArrayBuffer | null
}

export type WorkerInbound = { type: 'init' } | WorkerRequest

export interface WorkerResponse {
  id: number
  status: number
  headers: [string, string][]
  body: ArrayBuffer
}

export type WorkerOutbound =
  | ({ type: 'progress' } & BootProgress)
  | { type: 'ready'; timings: Record<string, number> }
  | { type: 'error'; message: string; stack?: string }
  | { type: 'log'; line: string }
  | WorkerResponse
