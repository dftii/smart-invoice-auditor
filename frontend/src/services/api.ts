import { getMaxUploadMb } from '../lib/config'
import type {
  ApiErrorBody,
  AuditReport,
  DashboardStats,
  HealthResponse,
  InvoiceDetail,
  InvoiceFilters,
  InvoiceSummary,
  InvoiceUpdate,
  Page,
  UploadResponse,
  ValidationIssue,
  ValidationLog,
} from '../types/api'

// ------------------------------------------------------------------------------------------ transport

export type HttpMethod = 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'

export interface UploadProgress {
  loaded: number
  total: number
}

export type QueryParams = Record<string, string | number | boolean | undefined | null>

/**
 * One request to the backend API. `path` is the server path including the API prefix (`/api/v1/invoices/3`,
 * `/health`) so any transport (HTTP, a Web Worker running the backend, a test double) can route it as is.
 */
export interface TransportRequest {
  method: HttpMethod
  path: string
  query?: QueryParams
  /** JSON-serializable object or multipart form data (file upload). */
  body?: object | FormData
  /** Aborting must reject the transport promise with a `DOMException` named `AbortError`. */
  signal?: AbortSignal
  /** Optional upload progress (only meaningful for FormData bodies). */
  onUploadProgress?: (progress: UploadProgress) => void
}

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue }

export interface TransportResponse {
  status: number
  /** Header names are matched case-insensitively. */
  headers: Record<string, string>
  /** Parsed JSON for JSON responses, a Blob for files (XML, CSV, PDF, images), null/undefined when empty. */
  body: Blob | JsonValue | null | undefined
}

/** Every request of the app goes through this one function. Reject (any Error) only for network-level failures. */
export type Transport = (request: TransportRequest) => Promise<TransportResponse>

export const REQUEST_TIMEOUT_MS = 180_000

const API_PREFIX = '/api/v1'

/**
 * Where the browser reaches the API. `VITE_API_BASE_URL` wins; otherwise same origin under the app's base path, so a
 * build served from `/smart-invoice-auditor/` never calls the root-absolute `/api/v1`.
 */
export function resolveApiBase(configured: string | undefined, baseUrl: string | undefined): string {
  const explicit = (configured ?? '').trim().replace(/\/+$/, '')
  if (explicit) return explicit
  const base = (baseUrl ?? '/').trim()
  return base.startsWith('/') ? base.replace(/\/+$/, '') : ''
}

export const API_BASE = resolveApiBase(import.meta.env.VITE_API_BASE_URL, import.meta.env.BASE_URL)

export function buildQuery(params: QueryParams | undefined): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value === undefined || value === null || value === '') continue
    query.set(key, String(value))
  }
  const text = query.toString()
  return text ? `?${text}` : ''
}

export class ApiError extends Error {
  readonly status: number
  /** Individual problems when the server reported several (validation errors); otherwise just `[message]`. */
  readonly problems: string[]

  constructor(status: number, message: string, problems?: string[]) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.problems = problems && problems.length > 0 ? problems : [message]
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function abortError(): DOMException {
  return new DOMException('Petición cancelada', 'AbortError')
}

const CONNECTION_MESSAGE = 'No se puede conectar con el servidor. Comprueba tu conexión y que la API esté en marcha.'
const TIMEOUT_MESSAGE = 'El servidor ha tardado demasiado en responder. Inténtalo de nuevo en unos instantes.'

function parseResponseHeaders(raw: string): Record<string, string> {
  const headers: Record<string, string> = {}
  for (const line of raw.trim().split(/[\r\n]+/)) {
    const colon = line.indexOf(':')
    if (colon > 0) headers[line.slice(0, colon).trim().toLowerCase()] = line.slice(colon + 1).trim()
  }
  return headers
}

function headerValue(headers: Record<string, string>, name: string): string | null {
  const wanted = name.toLowerCase()
  for (const [key, value] of Object.entries(headers)) {
    if (key.toLowerCase() === wanted) return value
  }
  return null
}

/** JSON responses are parsed; anything else (and empty bodies) is handed over as a Blob / null. */
async function decodeBody(blob: Blob | null, contentType: string | null): Promise<TransportResponse['body']> {
  if (!blob || blob.size === 0) return null
  if (contentType && /json/i.test(contentType)) {
    try {
      return JSON.parse(await blob.text()) as JsonValue
    } catch {
      return blob
    }
  }
  return blob
}

/** Optional API key for deployments that protect the API. Sent as `X-API-Key`; never logged. */
function apiKeyHeaders(): Record<string, string> {
  const key = (import.meta.env.VITE_API_KEY ?? '').trim()
  return key ? { 'X-API-Key': key } : {}
}

/** Default transport: XMLHttpRequest (real upload progress, 180 s timeout, abort) against `API_BASE`. */
export const httpTransport: Transport = (request) =>
  new Promise((resolve, reject) => {
    const { signal } = request
    if (signal?.aborted) {
      reject(abortError())
      return
    }
    const xhr = new XMLHttpRequest()
    xhr.open(request.method, `${API_BASE}${request.path}${buildQuery(request.query)}`)
    xhr.responseType = 'blob'
    xhr.timeout = REQUEST_TIMEOUT_MS
    xhr.setRequestHeader('Accept', 'application/json, */*')
    for (const [name, value] of Object.entries(apiKeyHeaders())) xhr.setRequestHeader(name, value)

    const onAbort = () => xhr.abort()
    signal?.addEventListener('abort', onAbort, { once: true })
    const cleanup = () => signal?.removeEventListener('abort', onAbort)

    if (request.onUploadProgress) {
      const report = request.onUploadProgress
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) report({ loaded: event.loaded, total: event.total })
      }
    }
    xhr.onload = () => {
      cleanup()
      const headers = parseResponseHeaders(xhr.getAllResponseHeaders())
      decodeBody(xhr.response as Blob | null, headers['content-type'] ?? null).then(
        (body) => resolve({ status: xhr.status, headers, body }),
        reject,
      )
    }
    xhr.onerror = () => {
      cleanup()
      reject(new ApiError(0, CONNECTION_MESSAGE))
    }
    xhr.ontimeout = () => {
      cleanup()
      reject(new ApiError(0, TIMEOUT_MESSAGE))
    }
    xhr.onabort = () => {
      cleanup()
      reject(abortError())
    }

    if (request.body instanceof FormData) {
      xhr.send(request.body)
    } else if (request.body !== undefined) {
      xhr.setRequestHeader('Content-Type', 'application/json')
      xhr.send(JSON.stringify(request.body))
    } else {
      xhr.send()
    }
  })

let activeTransport: Transport = httpTransport

/** Replaces the transport for the whole app (call before rendering). `null` restores the HTTP default. */
export function setTransport(transport: Transport | null): void {
  activeTransport = transport ?? httpTransport
}

/** True while requests go over HTTP to a real server (direct links, API docs and local-start hints make sense). */
export function isHttpTransport(): boolean {
  return activeTransport === httpTransport
}

// ------------------------------------------------------------------------------------ error messages

const STATUS_MESSAGES: Record<number, string> = {
  0: CONNECTION_MESSAGE,
  401: 'Falta la clave de API o no es válida. Configúrala (VITE_API_KEY) y vuelve a intentarlo.',
  403: 'No tienes permiso para realizar esta acción.',
  404: 'No se ha encontrado el recurso solicitado.',
  408: TIMEOUT_MESSAGE,
  409: 'La operación entra en conflicto con el estado actual de los datos.',
  415: 'Formato de archivo no admitido. Sube un PDF o una imagen PNG, JPG o WEBP.',
  422: 'Los datos enviados no son válidos.',
  429: 'Demasiadas peticiones seguidas. Espera unos segundos e inténtalo de nuevo.',
  502: 'El servidor no responde. Inténtalo de nuevo en unos instantes.',
  503: 'El servidor no responde. Inténtalo de nuevo en unos instantes.',
  504: 'El servidor no responde. Inténtalo de nuevo en unos instantes.',
}

/** Generic Spanish message for an HTTP status (used when the body carries no usable explanation). */
export function messageForStatus(status: number): string {
  if (status === 413) return `El archivo supera el tamaño máximo permitido (${getMaxUploadMb()} MB).`
  const known = STATUS_MESSAGES[status]
  if (known) return known
  if (status >= 500) return `Error interno del servidor (HTTP ${status}). Inténtalo de nuevo más tarde.`
  return `Error inesperado (HTTP ${status}).`
}

/** Statuses whose server text (often a proxy's HTML or an English default) is replaced by our own message. */
const ALWAYS_MAPPED = new Set([401, 502, 504])
const ENGLISH_DEFAULTS =
  /^(not found|method not allowed|not authenticated|unauthorized|forbidden|internal server error|bad gateway|service unavailable|gateway timeout|request entity too large|payload too large|unsupported media type|bad request|too many requests)\.?$/i

const FIELD_LABELS: Record<string, string> = {
  concept: 'concepto',
  quantity: 'cantidad',
  unit_of_measure: 'unidad de medida',
  unit_price: 'precio unitario',
  discount_rate: 'descuento',
  vat_rate: 'tipo de IVA',
  irpf_rate: 'tipo de IRPF',
  line_number: 'número de línea',
  line_base: 'base',
  vat_amount: 'cuota de IVA',
  irpf_amount: 'retención de IRPF',
  lines: 'líneas',
  invoice_number: 'número de factura',
  invoice_series: 'serie',
  issue_date: 'fecha de expedición',
  operation_date: 'fecha de operación',
  due_date: 'fecha de vencimiento',
  supplier_tax_id: 'NIF/CIF del emisor',
  buyer_name: 'nombre del receptor',
  buyer_tax_id: 'NIF/CIF del receptor',
  buyer_address: 'dirección del receptor',
  buyer_postal_code: 'código postal del receptor',
  buyer_city: 'población del receptor',
  buyer_province: 'provincia del receptor',
  buyer_country_code: 'país del receptor',
  dir3_accounting_office: 'oficina contable (DIR3)',
  dir3_managing_body: 'órgano gestor (DIR3)',
  dir3_processing_unit: 'unidad tramitadora (DIR3)',
  payment_iban: 'IBAN',
  tax_base: 'base imponible',
  total_vat: 'IVA total',
  total_irpf: 'IRPF total',
  total_amount: 'total',
  notes: 'notas',
  file: 'archivo',
  search: 'búsqueda',
  page: 'página',
  page_size: 'tamaño de página',
  status: 'estado',
  supplier_id: 'proveedor',
  tax_id: 'NIF/CIF',
  date_from: 'fecha desde',
  date_to: 'fecha hasta',
  min_total: 'total mínimo',
  max_total: 'total máximo',
  extraction_method: 'método de extracción',
  exported: 'exportada',
  has_errors: 'con errores',
  sort_by: 'ordenar por',
  sort_dir: 'sentido del orden',
  include_lines: 'incluir líneas',
  all_runs: 'todas las auditorías',
}

const LOCATION_ROOTS = new Set(['body', 'query', 'path', 'header', 'cookie'])

function ctxText(ctx: ValidationIssue['ctx'], key: string): string {
  const value = ctx?.[key]
  return value === undefined || value === null ? '' : String(value)
}

function plural(count: string, one: string, many: string): string {
  return `${count} ${count === '1' ? one : many}`
}

/** Spanish text for a pydantic error `type`; falls back to the (English) message when the type is unknown. */
function issueMessage(issue: ValidationIssue): string {
  const { type, ctx, msg } = issue
  switch (type) {
    case 'missing':
      return 'es obligatorio'
    case 'string_type':
      return 'debe ser un texto'
    case 'string_too_short':
      return `debe tener al menos ${plural(ctxText(ctx, 'min_length'), 'carácter', 'caracteres')}`
    case 'string_too_long':
      return `no puede superar los ${plural(ctxText(ctx, 'max_length'), 'carácter', 'caracteres')}`
    case 'string_pattern_mismatch':
      return 'no tiene un formato válido'
    case 'greater_than':
      return `debe ser mayor que ${ctxText(ctx, 'gt')}`
    case 'greater_than_equal':
      return `debe ser como mínimo ${ctxText(ctx, 'ge')}`
    case 'less_than':
      return `debe ser menor que ${ctxText(ctx, 'lt')}`
    case 'less_than_equal':
      return `no puede superar ${ctxText(ctx, 'le')}`
    case 'decimal_parsing':
    case 'decimal_type':
    case 'float_parsing':
    case 'float_type':
    case 'int_parsing':
    case 'int_type':
    case 'finite_number':
      return 'debe ser un número válido'
    case 'decimal_max_digits':
      return `admite como máximo ${plural(ctxText(ctx, 'max_digits'), 'dígito', 'dígitos')}`
    case 'decimal_max_places':
      return `admite como máximo ${plural(ctxText(ctx, 'decimal_places'), 'decimal', 'decimales')}`
    case 'decimal_whole_digits':
      return `admite como máximo ${plural(ctxText(ctx, 'whole_digits'), 'dígito entero', 'dígitos enteros')}`
    case 'date_parsing':
    case 'date_type':
    case 'date_from_datetime_parsing':
    case 'datetime_parsing':
    case 'datetime_type':
      return 'debe ser una fecha válida (AAAA-MM-DD)'
    case 'bool_parsing':
    case 'bool_type':
      return 'debe ser verdadero o falso'
    case 'enum':
    case 'literal_error':
      return 'tiene un valor no admitido'
    case 'too_short':
      return `debe tener al menos ${plural(ctxText(ctx, 'min_length'), 'elemento', 'elementos')}`
    case 'too_long':
      return `no puede tener más de ${plural(ctxText(ctx, 'max_length'), 'elemento', 'elementos')}`
    case 'json_invalid':
      return 'el cuerpo de la petición no es un JSON válido'
    case 'extra_forbidden':
      return 'no es un campo admitido'
    case 'list_type':
    case 'dict_type':
    case 'model_attributes_type':
      return 'tiene un formato no válido'
    case 'value_error':
      return msg.replace(/^Value error,\s*/i, '')
    case 'assertion_error':
      return msg.replace(/^Assertion failed,?\s*/i, '')
    default:
      return msg
  }
}

function capitalize(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text
}

/** "Línea 1 · cantidad: debe ser mayor que 0" for one validation error. Line numbers are 1-based. */
export function describeValidationIssue(issue: ValidationIssue): string {
  const location = [...issue.loc]
  if (typeof location[0] === 'string' && LOCATION_ROOTS.has(location[0])) location.shift()
  const lineAt = location.findIndex((part, index) => part === 'lines' && typeof location[index + 1] === 'number')
  const lineNumber = lineAt >= 0 ? (location[lineAt + 1] as number) + 1 : null
  const fieldParts = (lineAt >= 0 ? location.slice(lineAt + 2) : location).filter(
    (part): part is string => typeof part === 'string',
  )
  const field = fieldParts.map((part) => FIELD_LABELS[part] ?? part.replace(/_/g, ' ')).join(' · ')
  const message = issueMessage(issue)

  if (lineNumber !== null) return field ? `Línea ${lineNumber} · ${field}: ${message}` : `Línea ${lineNumber}: ${message}`
  return field ? `${capitalize(field)}: ${message}` : capitalize(message)
}

/** Spanish list of problems for a 422 `detail` array (duplicates removed). */
export function describeValidationErrors(detail: readonly ValidationIssue[]): string[] {
  return [...new Set(detail.map(describeValidationIssue))]
}

function isIssueList(detail: unknown): detail is ValidationIssue[] {
  return (
    Array.isArray(detail) &&
    detail.length > 0 &&
    detail.every((item) => typeof item === 'object' && item !== null && Array.isArray((item as ValidationIssue).loc))
  )
}

interface ErrorPayload {
  json: ApiErrorBody | null
  text: string | null
}

async function readErrorPayload(body: TransportResponse['body']): Promise<ErrorPayload> {
  if (body === null || body === undefined) return { json: null, text: null }
  if (typeof Blob !== 'undefined' && body instanceof Blob) {
    let text = ''
    try {
      text = await body.text()
    } catch {
      return { json: null, text: null }
    }
    try {
      const parsed: unknown = JSON.parse(text)
      return typeof parsed === 'object' && parsed !== null
        ? { json: parsed as ApiErrorBody, text: null }
        : { json: null, text: text.trim() || null }
    } catch {
      return { json: null, text: text.trim() || null }
    }
  }
  if (typeof body === 'string') return { json: null, text: body.trim() || null }
  return { json: typeof body === 'object' && !Array.isArray(body) ? (body as ApiErrorBody) : null, text: null }
}

/** Short plain-text bodies are worth showing; HTML error pages from proxies are not. */
function usablePlainText(text: string | null): string | null {
  if (!text || text.length > 200 || /^\s*</.test(text) || ENGLISH_DEFAULTS.test(text)) return null
  return text
}

export async function errorFromResponse(response: Pick<TransportResponse, 'status' | 'body'>): Promise<ApiError> {
  const { status } = response
  const { json, text } = await readErrorPayload(response.body)
  const detail = json?.detail

  if (isIssueList(detail)) {
    const problems = describeValidationErrors(detail)
    return new ApiError(status, problems.join('. '), problems)
  }
  if (typeof detail === 'string' && detail.trim() && !ALWAYS_MAPPED.has(status) && !ENGLISH_DEFAULTS.test(detail.trim())) {
    return new ApiError(status, detail.trim())
  }
  const generic = messageForStatus(status)
  const plain = ALWAYS_MAPPED.has(status) || status in STATUS_MESSAGES || status === 413 ? null : usablePlainText(text)
  return new ApiError(status, plain ? `${generic} ${plain}` : generic)
}

// ---------------------------------------------------------------------------------------------- core

async function send(request: TransportRequest): Promise<TransportResponse> {
  let response: TransportResponse
  try {
    response = await activeTransport(request)
  } catch (error) {
    if (error instanceof ApiError || isAbortError(error)) throw error
    throw new ApiError(0, CONNECTION_MESSAGE)
  }
  if (response.status < 200 || response.status >= 300) throw await errorFromResponse(response)
  return response
}

async function requestJson<T>(request: TransportRequest): Promise<T> {
  const response = await send(request)
  if (response.status === 204 || response.body === null || response.body === undefined) return undefined as T
  return response.body as T
}

function filenameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1])
    } catch {
      // fall through to the plain filename
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header)
  return plain ? plain[1] : fallback
}

function asBlob(body: TransportResponse['body'], contentType: string | null): Blob {
  if (typeof Blob !== 'undefined' && body instanceof Blob) return body
  const type = contentType ?? 'application/octet-stream'
  if (typeof body === 'string') return new Blob([body], { type })
  return new Blob([body === null || body === undefined ? '' : JSON.stringify(body)], { type: contentType ?? 'application/json' })
}

/** Saves a Blob through a temporary object URL (revoked a second later so the browser can finish the download). */
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** Downloads a binary response from the transport and saves it. Returns the saved filename. */
async function download(path: string, query: QueryParams | undefined, fallbackName: string): Promise<string> {
  const response = await send({ method: 'GET', path, query })
  const filename = filenameFromDisposition(headerValue(response.headers, 'content-disposition'), fallbackName)
  saveBlob(asBlob(response.body, headerValue(response.headers, 'content-type')), filename)
  return filename
}

/** Uploads one invoice file; the HTTP transport reports real upload progress. */
export async function uploadInvoice(
  file: File,
  onProgress?: (progress: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<UploadResponse> {
  const form = new FormData()
  form.append('file', file)
  return requestJson<UploadResponse>({
    method: 'POST',
    path: `${API_PREFIX}/invoices/upload`,
    body: form,
    signal,
    onUploadProgress: onProgress,
  })
}

export const api = {
  health: (): Promise<HealthResponse> => requestJson({ method: 'GET', path: '/health' }),

  stats: (): Promise<DashboardStats> => requestJson({ method: 'GET', path: `${API_PREFIX}/stats/dashboard` }),

  listInvoices: (filters: InvoiceFilters, page: number, pageSize: number): Promise<Page<InvoiceSummary>> =>
    requestJson({ method: 'GET', path: `${API_PREFIX}/invoices`, query: { ...filters, page, page_size: pageSize } }),

  getInvoice: (id: number): Promise<InvoiceDetail> => requestJson({ method: 'GET', path: `${API_PREFIX}/invoices/${id}` }),

  getLogs: (id: number, allRuns = false): Promise<ValidationLog[]> =>
    requestJson({ method: 'GET', path: `${API_PREFIX}/invoices/${id}/logs`, query: { all_runs: allRuns } }),

  updateInvoice: (id: number, payload: InvoiceUpdate): Promise<InvoiceDetail> =>
    requestJson({ method: 'PATCH', path: `${API_PREFIX}/invoices/${id}`, body: payload }),

  revalidate: (id: number): Promise<AuditReport> =>
    requestJson({ method: 'POST', path: `${API_PREFIX}/invoices/${id}/revalidate` }),

  deleteInvoice: (id: number): Promise<void> => requestJson({ method: 'DELETE', path: `${API_PREFIX}/invoices/${id}` }),

  downloadFacturae: (id: number): Promise<string> =>
    download(`${API_PREFIX}/invoices/${id}/facturae`, undefined, `facturae_${id}.xml`),

  downloadCsv: (filters: InvoiceFilters, includeLines = false): Promise<string> =>
    download(`${API_PREFIX}/invoices/export/csv`, { ...filters, include_lines: includeLines }, 'facturas.csv'),

  /** Direct link to the original document, or null when the transport is not plain HTTP (use `openOriginalFile`). */
  originalFileUrl: (id: number): string | null =>
    isHttpTransport() ? `${API_BASE}${API_PREFIX}/invoices/${id}/file` : null,

  /** Fetches the original document through the transport and opens it in a new tab. */
  openOriginalFile: async (id: number): Promise<void> => {
    // Opened synchronously so the click still counts as a user gesture for popup blockers.
    const tab = window.open('', '_blank')
    try {
      const response = await send({ method: 'GET', path: `${API_PREFIX}/invoices/${id}/file` })
      const blob = asBlob(response.body, headerValue(response.headers, 'content-type'))
      const url = URL.createObjectURL(blob)
      if (tab) tab.location.href = url
      else window.location.assign(url)
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (error) {
      tab?.close()
      throw error
    }
  },

  /** Interactive API documentation, or null when there is no HTTP server to serve it. */
  docsUrl: (): string | null => (isHttpTransport() ? `${API_BASE}/docs` : null),
}
