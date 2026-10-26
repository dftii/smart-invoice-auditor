// The visitor's own Anthropic API key for the in-browser demo ("bring your own key").
// It lives only in this browser: sessionStorage by default (gone when the tab closes), localStorage if the visitor
// asks to remember it. The demo transport sends it with uploads to the in-browser backend, which calls
// api.anthropic.com directly; it never reaches any other server.

import { useSyncExternalStore } from 'react'

const STORAGE_KEY = 'smartinvoice.anthropic-api-key'
const MESSAGES_URL = 'https://api.anthropic.com/v1/messages'
const KEY_PATTERN = /^sk-ant-[A-Za-z0-9_-]{20,}$/

const listeners = new Set<() => void>()
let currentKey: string | null = readStoredKey()

function readStoredKey(): string | null {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY) ?? window.localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

function emit() {
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function getAiKey(): string | null {
  return currentKey
}

/** Whether the key is kept across visits (localStorage) rather than only for this tab. */
export function isAiKeyRemembered(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) !== null
  } catch {
    return false
  }
}

export function setAiKey(key: string, remember: boolean): void {
  currentKey = key
  try {
    window.sessionStorage.removeItem(STORAGE_KEY)
    window.localStorage.removeItem(STORAGE_KEY)
    ;(remember ? window.localStorage : window.sessionStorage).setItem(STORAGE_KEY, key)
  } catch {
    // Storage blocked (private mode, site data disabled): the key still works until the page is reloaded.
  }
  emit()
}

export function clearAiKey(): void {
  currentKey = null
  try {
    window.sessionStorage.removeItem(STORAGE_KEY)
    window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Nothing stored.
  }
  emit()
}

export function useAiKey(): string | null {
  return useSyncExternalStore(subscribe, getAiKey, () => null)
}

/** `sk-ant-…a1b2`: enough to recognise the key without showing it. */
export function maskAiKey(key: string): string {
  return `${key.slice(0, 7)}…${key.slice(-4)}`
}

export function looksLikeAiKey(key: string): boolean {
  return KEY_PATTERN.test(key)
}

/**
 * Checks the key with a call that costs nothing: an empty Messages request. The API authenticates the key first
 * (401/403 when it is wrong) and then rejects the empty body with 400, so 400 means "valid key". The Messages endpoint
 * is used because, unlike /v1/models, its error responses carry CORS headers and the browser can read the status.
 * Throws an Error with a Spanish message.
 */
export async function verifyAiKey(key: string, signal?: AbortSignal): Promise<void> {
  let response: Response
  try {
    response = await fetch(MESSAGES_URL, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-api-key': key,
        'anthropic-version': '2023-06-01',
        'anthropic-dangerous-direct-browser-access': 'true',
      },
      body: '{}',
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new Error('No se pudo conectar con la API de Anthropic. Revisa tu conexión o el bloqueador de anuncios.')
  }
  if (response.status === 400 || response.ok) return
  if (response.status === 401) throw new Error('La clave no es válida. Cópiala de nuevo desde la consola de Anthropic.')
  if (response.status === 403) throw new Error('La clave no tiene permiso para usar la API.')
  if (response.status === 429) throw new Error('Has superado el límite de peticiones de tu cuenta. Prueba en un momento.')
  throw new Error(`La API de Anthropic devolvió un error ${response.status}. Prueba de nuevo más tarde.`)
}
