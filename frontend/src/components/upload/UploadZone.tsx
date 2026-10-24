import { Eraser, FastForward } from 'lucide-react'
import { useEffect } from 'react'
import type { UploadResponse } from '../../types/api'
import { Button } from '../ui/Button'
import { DropArea } from './DropArea'
import { UploadQueueItem } from './UploadQueueItem'
import { useUploadQueue } from './useUploadQueue'
import { nameClipboardFile } from './validation'

export interface UploadZoneProps {
  extractionMode: 'AI' | 'DEMO' | null
  maxUploadMb: number
  onUploaded: (result: UploadResponse) => void
  /** An upload was abandoned after it had been sent: the server may have stored the invoice, so refresh the list. */
  onMaybeCreated?: () => void
  onInspect: (invoiceId: number) => void
}

/** Text fields keep their own paste behaviour; so does everything while a modal dialog is open. */
function pasteTargetsInput(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

export function UploadZone({ extractionMode, maxUploadMb, onUploaded, onMaybeCreated, onInspect }: UploadZoneProps) {
  const queue = useUploadQueue(onUploaded, onMaybeCreated)
  const { addFiles } = queue

  // Pasting an image anywhere on the page uploads it, except while typing in a field or with a dialog open.
  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      if (pasteTargetsInput(event.target) || pasteTargetsInput(document.activeElement)) return
      if (document.querySelector('[aria-modal="true"]')) return
      const images = Array.from(event.clipboardData?.files ?? []).filter((file) => file.type.startsWith('image/'))
      if (images.length === 0) return
      event.preventDefault()
      addFiles(images.map(nameClipboardFile))
    }
    document.addEventListener('paste', onPaste)
    return () => document.removeEventListener('paste', onPaste)
  }, [addFiles])

  return (
    <section aria-label="Subida de facturas" className="space-y-4">
      <DropArea extractionMode={extractionMode} maxUploadMb={maxUploadMb} onFiles={queue.addFiles} />

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
