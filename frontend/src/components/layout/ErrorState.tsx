import { CircleAlert, RefreshCw } from 'lucide-react'
import { Button } from '../ui/Button'
import { cn } from '../../lib/cn'

interface ErrorStateProps {
  title: string
  message: string
  onRetry: () => void
  className?: string
}

export function ErrorState({ title, message, onRetry, className }: ErrorStateProps) {
  return (
    <div role="alert" className={cn('flex flex-col items-center gap-3 px-6 py-10 text-center', className)}>
      <span className="flex size-11 items-center justify-center rounded-full bg-red-50 text-red-600 ring-1 ring-red-600/10">
        <CircleAlert aria-hidden className="size-5" />
      </span>
      <div>
        <p className="text-sm font-semibold text-slate-900">{title}</p>
        <p className="mt-1 max-w-md text-sm text-slate-600">{message}</p>
      </div>
      <Button variant="secondary" size="sm" icon={<RefreshCw aria-hidden className="size-4" />} onClick={onRetry}>
        Reintentar
      </Button>
    </div>
  )
}
