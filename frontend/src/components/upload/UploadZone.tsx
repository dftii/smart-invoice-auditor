import { Eraser, FastForward } from 'lucide-react'
import type { UploadResponse } from '../../types/api'
import { Button } from '../ui/Button'
import { DropArea } from './DropArea'
import { UploadQueueItem } from './UploadQueueItem'
import { useUploadQueue } from './useUploadQueue'

export interface UploadZoneProps {
  extractionMode: 'AI' | 'DEMO' | null
  onUploaded: (result: UploadResponse) => void
  onInspect: (invoiceId: number) => void
}

export function UploadZone({ extractionMode, onUploaded, onInspect }: UploadZoneProps) {
  const queue = useUploadQueue(onUploaded)

  return (
    <section aria-label="Subida de facturas" className="space-y-4">
      <DropArea extractionMode={extractionMode} onFiles={queue.addFiles} />

      {queue.items.length > 0 && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-slate-700">
              Cola de facturas <span className="font-normal text-slate-500">({queue.items.length})</span>
            </h3>
            <div className="flex items-center gap-1">
              {queue.activeCount > 0 && (
                <Button variant="ghost" size="sm" icon={<FastForward aria-hidden className="size-4" />} onClick={queue.skipAll}>
                  Omitir animación
                </Button>
              )}
              {queue.finishedCount > 0 && (
                <Button variant="ghost" size="sm" icon={<Eraser aria-hidden className="size-4" />} onClick={queue.clearFinished}>
                  Limpiar
                </Button>
              )}
            </div>
          </div>
          <ul className="space-y-3">
            {queue.items.map((item) => (
              <UploadQueueItem
                key={item.id}
                item={item}
                onCancel={queue.cancel}
                onRetry={queue.retry}
                onRemove={queue.remove}
                onSkip={queue.skip}
                onInspect={onInspect}
              />
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
