import { useCallback, useEffect, useState } from 'react'

/**
 * Value that follows `value` after `delayMs` of quiet. `flush(next)` applies a value immediately (reset buttons,
 * external presets) so the screen never waits for the timer after an explicit action.
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): [T, (next: T) => void] {
  const [debounced, setDebounced] = useState(value)

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs)
    return () => window.clearTimeout(timer)
  }, [value, delayMs])

  const flush = useCallback((next: T) => setDebounced(next), [])
  return [debounced, flush]
}
