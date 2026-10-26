// Web Worker (ES module) that runs the real FastAPI backend inside Pyodide (CPython compiled to WebAssembly).
//
// Protocol (see transport.ts):
//   main -> worker  { type: 'init' }                                              start booting (idempotent)
//                   { id, method, path, query, headers, body: ArrayBuffer|null }  one HTTP request
//   worker -> main  { type: 'progress', step, total, stage }
//                   { type: 'ready', timings }  |  { type: 'error', message, stack }  |  { type: 'log', line }
//                   { id, status, headers: [name, value][], body: ArrayBuffer }
// Requests that arrive before the runtime is ready wait for the boot to finish.

import { BOOT_STAGES, type WorkerInbound, type WorkerOutbound } from './protocol'

const PYODIDE_VERSION = '0.29.5' // pin: lockfile, wasm and Pyodide-built wheels must come from the same release
const PYODIDE_BASE = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`

// Pyodide-built packages (compiled extensions: they cannot come from PyPI). fastapi/starlette are NOT taken from
// Pyodide (too old); the pure-Python wheels listed in demo/manifest.json are served from this same origin.
const PYODIDE_PACKAGES = ['sqlite3', 'sqlalchemy', 'pydantic', 'pydantic_core', 'lxml', 'tzdata', 'anyio', 'typing-extensions']

interface Manifest {
  backend: { file: string; sha256: string }
  wheels: { file: string; name: string; version: string }[]
}

interface PyodideModule {
  FS: { writeFile(path: string, data: Uint8Array): void }
  loadPackage(names: string[]): Promise<unknown>
  unpackArchive(data: Uint8Array, format: string, options?: { extractDir?: string }): void
  runPython(code: string): unknown
  pyimport(name: string): PyRuntime
}

interface PyRuntime {
  boot_js(): Promise<string>
  handle_js(
    method: string,
    path: string,
    query: string,
    headers: Record<string, string>,
    body: Uint8Array | null,
  ): Promise<{ status: number; headers: [string, string][]; body: Uint8Array }>
}

interface WorkerScope {
  postMessage(message: WorkerOutbound, transfer?: Transferable[]): void
  addEventListener(type: 'message', listener: (event: MessageEvent<WorkerInbound>) => void): void
  location: { href: string }
}
const ctx = self as unknown as WorkerScope

const assetUrl = (file: string): string => new URL(`${import.meta.env.BASE_URL}demo/${file}`, ctx.location.href).href

async function fetchBytes(url: string): Promise<Uint8Array> {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`No se pudo descargar ${url} (HTTP ${response.status})`)
  return new Uint8Array(await response.arrayBuffer())
}

let runtime: PyRuntime | null = null
let bootPromise: Promise<void> | null = null

async function boot(): Promise<void> {
  const t0 = performance.now()
  const timings: Record<string, number> = {}
  let last = t0
  const lap = (key: string) => {
    const now = performance.now()
    timings[key] = Math.round(now - last)
    last = now
  }
  const progress = (step: number) =>
    ctx.postMessage({ type: 'progress', step, total: BOOT_STAGES.length, stage: BOOT_STAGES[step - 1] })

  // 1. Python runtime. Our own assets download in parallel with the (much larger) Pyodide core.
  progress(1)
  // GitHub Pages lets browsers reuse files for 10 minutes; revalidating the (tiny) manifest makes a new deploy load the
  // matching backend.zip at once (its URL carries the hash) instead of the previous one from the cache.
  const manifest = (await (await fetch(assetUrl('manifest.json'), { cache: 'no-cache' })).json()) as Manifest
  const backendZip = fetchBytes(`${assetUrl(manifest.backend.file)}?v=${manifest.backend.sha256.slice(0, 12)}`)
  const wheels = Promise.all(manifest.wheels.map((wheel) => fetchBytes(assetUrl(wheel.file))))
  // Keep a rejection from a never-awaited promise from surfacing as "unhandled" while Pyodide is still loading.
  backendZip.catch(() => undefined)
  wheels.catch(() => undefined)

  const pyodideModule = (await import(/* @vite-ignore */ `${PYODIDE_BASE}pyodide.mjs`)) as {
    loadPyodide(options: Record<string, unknown>): Promise<PyodideModule>
  }
  const log = (line: string) => ctx.postMessage({ type: 'log', line })
  const py = await pyodideModule.loadPyodide({ indexURL: PYODIDE_BASE, stdout: log, stderr: log })
  lap('pyodide-core')

  // 2. Dependencies: Pyodide built-ins + same-origin pure wheels (no PyPI at runtime, no micropip).
  progress(2)
  const [, wheelBytes] = await Promise.all([py.loadPackage(PYODIDE_PACKAGES), wheels])
  for (const bytes of wheelBytes) py.unpackArchive(bytes, 'wheel')
  py.runPython('import importlib; importlib.invalidate_caches()')
  lap('dependencies')

  // 3. The backend itself: backend/app + runtime.py (ASGI bridge) -> /app. The database starts empty.
  progress(3)
  py.unpackArchive(await backendZip, 'zip', { extractDir: '/app' })
  py.runPython('import sys\nif "/app" not in sys.path:\n    sys.path.insert(0, "/app")')
  runtime = py.pyimport('runtime')
  await runtime.boot_js() // env, shims, import app.main, ASGI lifespan startup (creates the tables)
  lap('backend')

  timings.total = Math.round(performance.now() - t0)
  ctx.postMessage({ type: 'ready', timings })
}

function startBoot(): Promise<void> {
  if (!bootPromise) {
    bootPromise = boot()
    bootPromise.catch((error: unknown) => {
      const err = error instanceof Error ? error : new Error(String(error))
      ctx.postMessage({ type: 'error', message: err.message, stack: err.stack })
    })
  }
  return bootPromise
}

function jsonResponse(id: number, status: number, payload: unknown): void {
  const bytes = new TextEncoder().encode(JSON.stringify(payload))
  ctx.postMessage({ id, status, headers: [['content-type', 'application/json']], body: bytes.buffer as ArrayBuffer }, [
    bytes.buffer as ArrayBuffer,
  ])
}

async function handleRequest(request: Extract<WorkerInbound, { id: number }>): Promise<void> {
  const { id } = request
  try {
    await startBoot()
    const body = request.body ? new Uint8Array(request.body) : null
    const result = await runtime!.handle_js(request.method, request.path, request.query, request.headers, body)
    const bytes = result.body.slice() // copy out of the wasm heap before transferring
    ctx.postMessage({ id, status: result.status, headers: result.headers, body: bytes.buffer as ArrayBuffer }, [
      bytes.buffer as ArrayBuffer,
    ])
  } catch (error) {
    jsonResponse(id, 500, { detail: `Error del backend en el navegador: ${error instanceof Error ? error.message : String(error)}` })
  }
}

ctx.addEventListener('message', ({ data }) => {
  if (!data) return
  if ('type' in data) {
    void startBoot().catch(() => undefined) // failure is reported through the 'error' message
    return
  }
  void handleRequest(data)
})
