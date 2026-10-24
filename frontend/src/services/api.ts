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
  ValidationLog,
} from '../types/api'

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const API = `${BASE_URL}/api/v1`

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function messageFromBody(status: number, body: ApiErrorBody | null): string {
  const detail = body?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((item) => {
        const field = item.loc.filter((part) => part !== 'body' && part !== 'query').join('.')
        return field ? `${field}: ${item.msg}` : item.msg
      })
      .join(' · ')
  }
  if (status === 0) return 'No se puede conectar con la API. ¿Está arrancado el backend en el puerto 8000?'
  return `Error inesperado del servidor (HTTP ${status})`
}

async function parseError(response: Response): Promise<ApiError> {
  let body: ApiErrorBody | null = null
  try {
    body = (await response.json()) as ApiErrorBody
  } catch {
    body = null
  }
  return new ApiError(response.status, messageFromBody(response.status, body))
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API}${path}`, {
      ...init,
      headers: { Accept: 'application/json', ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers },
    })
  } catch {
    throw new ApiError(0, messageFromBody(0, null))
  }
  if (!response.ok) throw await parseError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function buildQuery(params: Record<string, string | number | boolean | undefined | null>): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    query.set(key, String(value))
  }
  const text = query.toString()
  return text ? `?${text}` : ''
}

function filenameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (utf8) return decodeURIComponent(utf8[1])
  const plain = /filename="?([^";]+)"?/i.exec(header)
  return plain ? plain[1] : fallback
}

/** Downloads a binary response and saves it through a temporary object URL. Returns the saved filename. */
async function download(path: string, fallbackName: string): Promise<string> {
  let response: Response
  try {
    response = await fetch(`${API}${path}`)
  } catch {
    throw new ApiError(0, messageFromBody(0, null))
  }
  if (!response.ok) throw await parseError(response)
  const blob = await response.blob()
  const filename = filenameFromDisposition(response.headers.get('Content-Disposition'), fallbackName)
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
  return filename
}

export interface UploadProgress {
  loaded: number
  total: number
}

/** XHR instead of fetch so the UI can show real upload progress. */
export function uploadInvoice(
  file: File,
  onProgress?: (progress: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<UploadResponse> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API}/invoices/upload`)
    xhr.responseType = 'json'
    xhr.setRequestHeader('Accept', 'application/json')
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.({ loaded: event.loaded, total: event.total })
    }
    xhr.onload = () => {
      const body = xhr.response as (UploadResponse & ApiErrorBody) | null
      if (xhr.status >= 200 && xhr.status < 300 && body) resolve(body)
      else reject(new ApiError(xhr.status, messageFromBody(xhr.status, body)))
    }
    xhr.onerror = () => reject(new ApiError(0, messageFromBody(0, null)))
    xhr.onabort = () => reject(new DOMException('Subida cancelada', 'AbortError'))
    signal?.addEventListener('abort', () => xhr.abort(), { once: true })
    const form = new FormData()
    form.append('file', file)
    xhr.send(form)
  })
}

export const api = {
  health: async (): Promise<HealthResponse> => {
    let response: Response
    try {
      response = await fetch(`${BASE_URL}/health`, { headers: { Accept: 'application/json' } })
    } catch {
      throw new ApiError(0, messageFromBody(0, null))
    }
    if (!response.ok) throw await parseError(response)
    return (await response.json()) as HealthResponse
  },

  stats: (): Promise<DashboardStats> => request('/stats/dashboard'),

  listInvoices: (filters: InvoiceFilters, page: number, pageSize: number): Promise<Page<InvoiceSummary>> =>
    request(`/invoices${buildQuery({ ...filters, page, page_size: pageSize })}`),

  getInvoice: (id: number): Promise<InvoiceDetail> => request(`/invoices/${id}`),

  getLogs: (id: number, allRuns = false): Promise<ValidationLog[]> =>
    request(`/invoices/${id}/logs${buildQuery({ all_runs: allRuns })}`),

  updateInvoice: (id: number, payload: InvoiceUpdate): Promise<InvoiceDetail> =>
    request(`/invoices/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  revalidate: (id: number): Promise<AuditReport> => request(`/invoices/${id}/revalidate`, { method: 'POST' }),

  deleteInvoice: (id: number): Promise<void> => request(`/invoices/${id}`, { method: 'DELETE' }),

  downloadFacturae: (id: number): Promise<string> => download(`/invoices/${id}/facturae`, `facturae_${id}.xml`),

  downloadCsv: (filters: InvoiceFilters, includeLines = false): Promise<string> =>
    download(`/invoices/export/csv${buildQuery({ ...filters, include_lines: includeLines })}`, 'facturas.csv'),

  originalFileUrl: (id: number): string => `${API}/invoices/${id}/file`,
}
