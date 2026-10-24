import { useEffect, useState } from 'react'
import type { UploadResponse } from '../../types/api'
import { createQueueEngine, type QueueEngine } from './queueEngine'
import { isTerminal, type UploadItem } from './types'

export interface UploadQueue extends Omit<QueueEngine, 'mount' | 'dispose' | 'setOnUploaded' | 'setOnMaybeCreated'> {
  items: UploadItem[]
  /** Files still waiting, uploading or animating. */
  activeCount: number
  finishedCount: number
}

export function useUploadQueue(
  onUploaded: (result: UploadResponse) => void,
  onMaybeCreated?: () => void,
): UploadQueue {
  const [items, setItems] = useState<UploadItem[]>([])
  const [engine] = useState(() => createQueueEngine({ publish: setItems }))

  useEffect(() => {
    engine.setOnUploaded(onUploaded)
  }, [engine, onUploaded])

  useEffect(() => {
    engine.setOnMaybeCreated(onMaybeCreated ?? (() => {}))
  }, [engine, onMaybeCreated])

  useEffect(() => {
    engine.mount()
    return () => engine.dispose()
  }, [engine])

  const finishedCount = items.filter((item) => isTerminal(item.phase)).length

  return {
    items,
    activeCount: items.length - finishedCount,
    finishedCount,
    addFiles: engine.addFiles,
    cancel: engine.cancel,
    retry: engine.retry,
    remove: engine.remove,
    skip: engine.skip,
    skipAll: engine.skipAll,
    clearFinished: engine.clearFinished,
  }
}
