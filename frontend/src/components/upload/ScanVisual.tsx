import { CircleCheck } from 'lucide-react'
import { cn } from '../../lib/cn'
import type { ProcessingStep } from './types'
import './upload.css'

const ROW_WIDTHS = ['w-2/5', 'w-full', 'w-4/5', 'w-full', 'w-3/5', 'w-full', 'w-2/3', 'w-4/5']
const RECOGNISED_ROWS: Record<ProcessingStep, number> = { scanning: 0, extracting: 4, auditing: 8 }

/** Paper mock with skeleton text rows, an OCR shimmer and a laser line sweeping over it. */
export function ScanVisual({ step }: { step: ProcessingStep }) {
  const recognised = RECOGNISED_ROWS[step]

  return (
    <div
      aria-hidden
      className="relative h-36 w-28 shrink-0 overflow-hidden rounded-lg bg-white p-2.5 shadow-sm ring-1 ring-slate-900/10"
    >
      <div className="mb-2 flex items-center justify-between">
        <div className="h-2.5 w-8 rounded bg-slate-300" />
        <div className="h-2.5 w-5 rounded bg-slate-200" />
      </div>
      <div className="space-y-1.5">
        {ROW_WIDTHS.map((width, index) => {
          const isRecognised = index < recognised
          return (
            <div
              key={index}
              className={cn(
                'h-1.5 rounded-full transition-colors duration-500',
                width,
                isRecognised ? 'bg-indigo-300' : 'upload-shimmer bg-slate-200',
              )}
              style={isRecognised ? undefined : { animationDelay: `${index * 120}ms` }}
            />
          )
        })}
      </div>
      <div className="absolute inset-x-2.5 bottom-2.5 flex items-center justify-between">
        <div className="h-2 w-7 rounded bg-slate-200" />
        <div className={cn('h-3 w-9 rounded transition-colors duration-500', step === 'auditing' ? 'bg-indigo-300' : 'upload-shimmer bg-slate-300')} />
      </div>
      {step === 'auditing' && (
        <CircleCheck className="absolute right-1.5 top-1.5 size-4 animate-pop-in text-emerald-500" />
      )}
      <div className="pointer-events-none absolute inset-0 animate-scan">
        <div className="h-full bg-gradient-to-b from-transparent to-indigo-400/30" />
        <div className="absolute inset-x-0 bottom-0 h-0.5 bg-indigo-500 shadow-[0_0_10px_2px_rgb(99_102_241/0.7)]" />
      </div>
    </div>
  )
}
