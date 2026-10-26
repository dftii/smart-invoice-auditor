import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const VALID_KEY = 'sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789'

class MemoryStorage {
  private items = new Map<string, string>()
  getItem(key: string) {
    return this.items.get(key) ?? null
  }
  setItem(key: string, value: string) {
    this.items.set(key, value)
  }
  removeItem(key: string) {
    this.items.delete(key)
  }
}

let sessionStorage: MemoryStorage
let localStorage: MemoryStorage

async function loadModule() {
  vi.resetModules()
  return import('./aiKey')
}

beforeEach(() => {
  sessionStorage = new MemoryStorage()
  localStorage = new MemoryStorage()
  vi.stubGlobal('window', { sessionStorage, localStorage })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('looksLikeAiKey / maskAiKey', () => {
  it('accepts Anthropic keys and rejects anything else', async () => {
    const { looksLikeAiKey } = await loadModule()
    expect(looksLikeAiKey(VALID_KEY)).toBe(true)
    expect(looksLikeAiKey('sk-ant-short')).toBe(false)
    expect(looksLikeAiKey('sk-proj-abcdefghijklmnopqrstuvwxyz0123')).toBe(false)
    expect(looksLikeAiKey(`${VALID_KEY} `)).toBe(false)
  })

  it('shows only the prefix and the last four characters', async () => {
    const { maskAiKey } = await loadModule()
    expect(maskAiKey(VALID_KEY)).toBe('sk-ant-…6789')
  })
})

describe('key storage', () => {
  it('keeps the key for the tab only unless asked to remember it', async () => {
    const { getAiKey, setAiKey, isAiKeyRemembered } = await loadModule()
    setAiKey(VALID_KEY, false)
    expect(getAiKey()).toBe(VALID_KEY)
    expect(sessionStorage.getItem('smartinvoice.anthropic-api-key')).toBe(VALID_KEY)
    expect(localStorage.getItem('smartinvoice.anthropic-api-key')).toBeNull()
    expect(isAiKeyRemembered()).toBe(false)

    setAiKey(VALID_KEY, true)
    expect(sessionStorage.getItem('smartinvoice.anthropic-api-key')).toBeNull()
    expect(localStorage.getItem('smartinvoice.anthropic-api-key')).toBe(VALID_KEY)
    expect(isAiKeyRemembered()).toBe(true)
  })

  it('restores a remembered key on load and clears it from both storages', async () => {
    localStorage.setItem('smartinvoice.anthropic-api-key', VALID_KEY)
    const { getAiKey, clearAiKey } = await loadModule()
    expect(getAiKey()).toBe(VALID_KEY)
    clearAiKey()
    expect(getAiKey()).toBeNull()
    expect(localStorage.getItem('smartinvoice.anthropic-api-key')).toBeNull()
  })

  it('still works in memory when storage is blocked', async () => {
    vi.stubGlobal('window', {
      get sessionStorage(): Storage {
        throw new DOMException('blocked', 'SecurityError')
      },
      get localStorage(): Storage {
        throw new DOMException('blocked', 'SecurityError')
      },
    })
    const { getAiKey, setAiKey } = await loadModule()
    expect(getAiKey()).toBeNull()
    setAiKey(VALID_KEY, true)
    expect(getAiKey()).toBe(VALID_KEY)
  })
})

describe('verifyAiKey', () => {
  it('sends an empty Messages request with the browser-access headers; 400 means the key was accepted', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 400 }))
    vi.stubGlobal('fetch', fetchMock)
    const { verifyAiKey } = await loadModule()
    await expect(verifyAiKey(VALID_KEY)).resolves.toBeUndefined()
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('https://api.anthropic.com/v1/messages')
    expect(init.method).toBe('POST')
    expect(init.body).toBe('{}')
    expect(init.headers).toMatchObject({
      'x-api-key': VALID_KEY,
      'anthropic-version': '2023-06-01',
      'anthropic-dangerous-direct-browser-access': 'true',
    })
  })

  it.each([
    [401, 'La clave no es válida'],
    [403, 'no tiene permiso'],
    [429, 'límite de peticiones'],
    [529, 'error 529'],
  ])('turns HTTP %i into a Spanish message', async (status, message) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status })))
    const { verifyAiKey } = await loadModule()
    await expect(verifyAiKey(VALID_KEY)).rejects.toThrow(message)
  })

  it('reports network failures without leaking the key', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const { verifyAiKey } = await loadModule()
    const error = (await verifyAiKey(VALID_KEY).then(
      () => null,
      (caught: unknown) => caught,
    )) as Error
    expect(error).toBeInstanceOf(Error)
    expect(error.message).toContain('No se pudo conectar')
    expect(error.message).not.toContain(VALID_KEY)
  })
})
