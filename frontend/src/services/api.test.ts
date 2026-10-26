import { afterEach, describe, expect, it } from 'vitest'
import {
  ApiError,
  api,
  describeValidationErrors,
  isHttpTransport,
  resolveApiBase,
  setTransport,
  uploadInvoice,
  type Transport,
  type TransportRequest,
} from './api'
import { setMaxUploadMb } from '../lib/config'
import type { InvoiceUpdate } from '../types/api'

function respond(status: number, body: unknown, headers: Record<string, string> = {}): Transport {
  return async () => ({ status, headers, body: body as never })
}

async function failure(transport: Transport, call: () => Promise<unknown> = () => api.getInvoice(1)): Promise<ApiError> {
  setTransport(transport)
  try {
    await call()
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError)
    return error as ApiError
  }
  throw new Error('expected the call to fail')
}

afterEach(() => {
  setTransport(null)
  setMaxUploadMb(null)
})

describe('transport seam', () => {
  it('sends every request through the active transport with path, query and JSON body', async () => {
    const seen: TransportRequest[] = []
    setTransport(async (request) => {
      seen.push(request)
      return { status: 200, headers: {}, body: { items: [], total: 0, page: 1, page_size: 10, pages: 0 } }
    })
    expect(isHttpTransport()).toBe(false)
    await api.listInvoices({ status: 'VALIDATED', search: '' }, 2, 25)
    await api.updateInvoice(7, { notes: 'x' } satisfies InvoiceUpdate)
    await api.health()
    expect(seen[0]).toMatchObject({
      method: 'GET',
      path: '/api/v1/invoices',
      query: { status: 'VALIDATED', page: 2, page_size: 25 },
    })
    expect(seen[1]).toMatchObject({ method: 'PATCH', path: '/api/v1/invoices/7', body: { notes: 'x' } })
    expect(seen[2]).toMatchObject({ method: 'GET', path: '/health' })
    setTransport(null)
    expect(isHttpTransport()).toBe(true)
  })

  it('uploads FormData with progress and abort signal', async () => {
    let received: TransportRequest | undefined
    setTransport(async (request) => {
      received = request
      return { status: 200, headers: {}, body: { ok: true } }
    })
    const controller = new AbortController()
    await uploadInvoice(new File(['x'], 'a.pdf', { type: 'application/pdf' }), () => {}, controller.signal)
    expect(received?.body).toBeInstanceOf(FormData)
    expect(received?.path).toBe('/api/v1/invoices/upload')
    expect(received?.signal).toBe(controller.signal)
    expect(typeof received?.onUploadProgress).toBe('function')
  })

  it('returns undefined for 204 and exposes direct links only over HTTP', async () => {
    setTransport(respond(204, null))
    await expect(api.deleteInvoice(1)).resolves.toBeUndefined()
    expect(api.originalFileUrl(1)).toBeNull()
    expect(api.docsUrl()).toBeNull()
    setTransport(null)
    expect(api.originalFileUrl(1)).toMatch(/\/api\/v1\/invoices\/1\/file$/)
    expect(api.docsUrl()).toMatch(/\/docs$/)
  })

  it('lets AbortError through and wraps network failures', async () => {
    setTransport(async () => {
      throw new DOMException('x', 'AbortError')
    })
    await expect(api.stats()).rejects.toMatchObject({ name: 'AbortError' })
    const error = await failure(async () => {
      throw new TypeError('Failed to fetch')
    })
    expect(error.status).toBe(0)
    expect(error.message).toContain('No se puede conectar')
    expect(error.message).not.toContain('8000')
  })
})

describe('error messages', () => {
  it('translates 422 validation errors with 1-based line numbers', async () => {
    const error = await failure(
      respond(422, {
        detail: [
          {
            loc: ['body', 'lines', 0, 'quantity'],
            msg: 'Input should be greater than 0',
            type: 'greater_than',
            ctx: { gt: '0' },
          },
          { loc: ['body', 'lines', 2, 'vat_rate'], msg: 'Input should be a valid decimal', type: 'decimal_parsing' },
          { loc: ['body', 'issue_date'], msg: 'Input should be a valid date', type: 'date_parsing' },
          {
            loc: ['body', 'invoice_number'],
            msg: 'String should have at most 50 characters',
            type: 'string_too_long',
            ctx: { max_length: 50 },
          },
          { loc: ['body', 'lines', 1, 'concept'], msg: 'Field required', type: 'missing' },
        ],
      }),
    )
    expect(error.status).toBe(422)
    expect(error.problems).toEqual([
      'Línea 1 · cantidad: debe ser mayor que 0',
      'Línea 3 · tipo de IVA: debe ser un número válido',
      'Fecha de expedición: debe ser una fecha válida (AAAA-MM-DD)',
      'Número de factura: no puede superar los 50 caracteres',
      'Línea 2 · concepto: es obligatorio',
    ])
    expect(error.message).toContain('Línea 1 · cantidad')
  })

  it('handles unknown fields, value errors, empty locations and duplicates', () => {
    expect(
      describeValidationErrors([
        { loc: ['body', 'something_new'], msg: 'Weird', type: 'weird' },
        { loc: ['body'], msg: 'Value error, El NIF no es válido', type: 'value_error' },
        { loc: ['query', 'page_size'], msg: 'x', type: 'less_than_equal', ctx: { le: 100 } },
        { loc: ['body', 'something_new'], msg: 'Weird', type: 'weird' },
      ]),
    ).toEqual(['Something new: Weird', 'El NIF no es válido', 'Tamaño de página: no puede superar 100'])
  })

  it('maps statuses to Spanish', async () => {
    setMaxUploadMb(15)
    const tooLarge = await failure(respond(413, null))
    expect(tooLarge.message).toContain('El archivo supera el tamaño máximo')
    expect(tooLarge.message).toContain('15 MB')
    expect((await failure(respond(415, null))).message).toContain('Formato de archivo no admitido')
    expect((await failure(respond(401, { detail: 'Not authenticated' }))).message).toContain('Falta la clave de API')
    for (const status of [502, 503, 504]) {
      expect((await failure(respond(status, null))).message).toContain('El servidor no responde')
    }
    expect((await failure(respond(404, { detail: 'Not Found' }))).message).toBe('No se ha encontrado el recurso solicitado.')
    expect((await failure(respond(500, null))).message).toContain('Error interno del servidor (HTTP 500)')
  })

  it('prefers the backend Spanish detail', async () => {
    expect((await failure(respond(409, { detail: 'La factura ya existe' }))).message).toBe('La factura ya existe')
    expect(
      (await failure(respond(413, { detail: 'El archivo supera el tamaño máximo de 20 MB' }))).message,
    ).toContain('20 MB')
    expect((await failure(respond(503, { detail: 'Base de datos no disponible' }))).message).toBe(
      'Base de datos no disponible',
    )
  })

  it('parses non-JSON error bodies', async () => {
    const html = new Blob(['<html><body>502 Bad Gateway</body></html>'], { type: 'text/html' })
    expect((await failure(respond(502, html))).message).toContain('El servidor no responde')
    const text = new Blob(['Upstream exploded'], { type: 'text/plain' })
    const error = await failure(respond(500, text))
    expect(error.message).toContain('HTTP 500')
    expect(error.message).toContain('Upstream exploded')
    const jsonBlob = new Blob([JSON.stringify({ detail: 'Mensaje del servidor' })], { type: 'application/json' })
    expect((await failure(respond(400, jsonBlob))).message).toBe('Mensaje del servidor')
    const htmlOn418 = new Blob(['<html>teapot</html>'], { type: 'text/html' })
    expect((await failure(respond(418, htmlOn418))).message).toBe('Error inesperado (HTTP 418).')
  })
})

describe('resolveApiBase', () => {
  it('uses the configured URL, else the app base path (never root-absolute under a sub-path)', () => {
    expect(resolveApiBase('https://api.example.com/', '/x/')).toBe('https://api.example.com')
    expect(resolveApiBase('', '/')).toBe('')
    expect(resolveApiBase(undefined, undefined)).toBe('')
    expect(resolveApiBase('', '/smart-invoice-auditor/')).toBe('/smart-invoice-auditor')
    expect(resolveApiBase('  ', './')).toBe('')
  })
})

describe('sample invoices', () => {
  const sample = {
    filename: '07_factura_imagen_demo.png',
    title: 'Foto de factura',
    description: 'Imagen sin texto',
    media_type: 'image/png',
    size_bytes: 3,
    expected_status: 'VALIDATED' as const,
  }

  it('lists the samples from the API', async () => {
    const seen: TransportRequest[] = []
    setTransport(async (request) => {
      seen.push(request)
      return { status: 200, headers: { 'content-type': 'application/json' }, body: [sample] as never }
    })
    await expect(api.listSamples()).resolves.toEqual([sample])
    expect(seen[0]).toMatchObject({ method: 'GET', path: '/api/v1/samples' })
  })

  it('downloads a sample as a File named and typed like the original', async () => {
    const seen: TransportRequest[] = []
    setTransport(async (request) => {
      seen.push(request)
      return { status: 200, headers: { 'content-type': 'image/png' }, body: new Blob([new Uint8Array([1, 2, 3])]) }
    })
    const file = await api.sampleFile(sample)
    expect(seen[0].path).toBe('/api/v1/samples/07_factura_imagen_demo.png')
    expect(file.name).toBe('07_factura_imagen_demo.png')
    expect(file.type).toBe('image/png')
    expect(new Uint8Array(await file.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3]))
  })

  it('reports a missing sample as an API error', async () => {
    const error = await failure(respond(404, { detail: 'Factura de ejemplo no encontrada' }), () => api.sampleFile(sample))
    expect(error.status).toBe(404)
    expect(error.message).toContain('ejemplo')
  })
})
