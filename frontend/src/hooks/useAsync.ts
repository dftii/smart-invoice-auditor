import { useCallback, useEffect, useMemo, useState } from 'react'

export interface AsyncState<T> {
  /** Last successful result; kept while a newer request is in flight. */
  data: T | null
  /** Error of the latest request (cleared while a new one is loading). */
  error: Error | null
  loading: boolean
  reload: () => void
}

interface Settled<T> {
  data: T | null
  error: Error | null
  /** The request this outcome belongs to. */
  request: object | null
}

/**
 * Runs `fetcher` whenever its identity or `refreshKey` changes (or `reload()` is called).
 * Results of superseded requests are discarded, so out-of-order responses never overwrite newer data.
 * `fetcher` must be stable (module function or `useCallback`).
 */
export function useAsync<T>(fetcher: () => Promise<T>, refreshKey = 0): AsyncState<T> {
  const [settled, setSettled] = useState<Settled<T>>({ data: null, error: null, request: null })
  const [reloadCount, setReloadCount] = useState(0)

  // A fresh object per (fetcher, refreshKey, reload) combination identifies the request in flight.
  const request = useMemo(() => ({ fetcher, refreshKey, reloadCount }), [fetcher, refreshKey, reloadCount])

  useEffect(() => {
    let current = true
    fetcher().then(
      (data) => {
        if (current) setSettled({ data, error: null, request })
      },
      (reason: unknown) => {
        if (!current) return
        const error = reason instanceof Error ? reason : new Error(String(reason))
        setSettled((previous) => ({ data: previous.data, error, request }))
      },
    )
    return () => {
      current = false
    }
  }, [fetcher, request])

  const reload = useCallback(() => setReloadCount((count) => count + 1), [])

  const loading = settled.request !== request
  return { data: settled.data, error: loading ? null : settled.error, loading, reload }
}
