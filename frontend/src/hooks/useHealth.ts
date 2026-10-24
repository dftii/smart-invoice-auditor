import { useCallback, useEffect, useRef, useState } from 'react'
import { setMaxUploadMb } from '../lib/config'
import { api } from '../services/api'
import type { HealthResponse } from '../types/api'

export type HealthStatus = 'checking' | 'online' | 'offline'

export interface HealthState {
  /** Outcome of the latest check (`checking` only until the first one finishes). */
  status: HealthStatus
  /** Last successful response, kept while the API is unreachable. */
  data: HealthResponse | null
  /** True once any check has succeeded: from then on an outage must not unmount the page. */
  everOnline: boolean
  /** A check is in flight. */
  checking: boolean
  recheck: () => void
}

export const HEALTH_POLL_MS = 30_000
/** Focus and visibility events fire together; ignore the second one. */
const FOCUS_THROTTLE_MS = 2_000

interface Snapshot {
  status: HealthStatus
  data: HealthResponse | null
  everOnline: boolean
  checking: boolean
}

/**
 * Polls /health every 30 s and whenever the window regains focus, so the header dot and the offline banner always
 * reflect the latest result. `onRecovered` runs when the API comes back after having been offline.
 */
export function useHealth(onRecovered?: () => void): HealthState {
  const [snapshot, setSnapshot] = useState<Snapshot>({ status: 'checking', data: null, everOnline: false, checking: true })
  const latestRequest = useRef(0)
  const lastStatus = useRef<HealthStatus>('checking')
  const lastCheckAt = useRef(0)
  const recovered = useRef(onRecovered)

  useEffect(() => {
    recovered.current = onRecovered
  }, [onRecovered])

  const check = useCallback(() => {
    const request = ++latestRequest.current
    lastCheckAt.current = Date.now()
    api.health().then(
      (data) => {
        if (request !== latestRequest.current) return
        setMaxUploadMb(data.max_upload_size_mb)
        const wasOffline = lastStatus.current === 'offline'
        lastStatus.current = 'online'
        setSnapshot({ status: 'online', data, everOnline: true, checking: false })
        if (wasOffline) recovered.current?.()
      },
      () => {
        if (request !== latestRequest.current) return
        lastStatus.current = 'offline'
        setSnapshot((current) => ({ ...current, status: 'offline', checking: false }))
      },
    )
  }, [])

  useEffect(() => {
    check()
    const timer = window.setInterval(check, HEALTH_POLL_MS)
    const onWake = () => {
      if (document.visibilityState === 'hidden') return
      if (Date.now() - lastCheckAt.current > FOCUS_THROTTLE_MS) check()
    }
    window.addEventListener('focus', onWake)
    document.addEventListener('visibilitychange', onWake)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', onWake)
      document.removeEventListener('visibilitychange', onWake)
    }
  }, [check])

  // Manual retries show a spinner; background polls stay silent.
  const recheck = useCallback(() => {
    setSnapshot((current) => ({ ...current, checking: true }))
    check()
  }, [check])

  return { ...snapshot, recheck }
}
