import { useRef, type KeyboardEvent, type ReactNode } from 'react'
import { TriangleAlert } from 'lucide-react'
import { Button } from '../ui/Button'
import { trapTab, useDialogFocus } from './useDialog'

interface ConfirmDialogProps {
  title: string
  children: ReactNode
  confirmLabel: string
  cancelLabel?: string
  danger?: boolean
  loading?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  cancelLabel = 'Cancelar',
  danger = false,
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const ref = useRef<HTMLDivElement>(null)
  useDialogFocus(ref)

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.stopPropagation()
      if (!loading) onCancel()
      return
    }
    trapTab(event, ref.current)
  }

  return (
    <div className="fixed inset-0 z-[55] flex items-center justify-center p-4" onKeyDown={handleKeyDown}>
      <div className="absolute inset-0 animate-fade-in bg-slate-900/50" onClick={loading ? undefined : onCancel} />
      <div
        ref={ref}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        tabIndex={-1}
        className="relative w-full max-w-md animate-pop-in rounded-2xl bg-white p-6 shadow-xl ring-1 ring-slate-900/10 focus:outline-none"
      >
        <div className="flex items-start gap-4">
          <span
            className={`flex size-10 shrink-0 items-center justify-center rounded-full ${danger ? 'bg-red-50 text-red-600' : 'bg-amber-50 text-amber-600'}`}
          >
            <TriangleAlert aria-hidden className="size-5" />
          </span>
          <div className="min-w-0">
            <h2 id="confirm-title" className="text-base font-semibold text-slate-900">
              {title}
            </h2>
            <div className="mt-2 text-sm text-slate-600">{children}</div>
          </div>
        </div>
        <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="secondary" onClick={onCancel} disabled={loading} data-autofocus>
            {cancelLabel}
          </Button>
          <Button variant={danger ? 'danger' : 'primary'} onClick={onConfirm} loading={loading}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}
