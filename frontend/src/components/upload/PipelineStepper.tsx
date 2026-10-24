import { CircleCheck, CircleX, CloudUpload, LoaderCircle, ScanLine, ScanText, ShieldCheck } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'
import { PIPELINE_STEPS, stepStates, type StepState, type UploadPhase } from './types'

const STEP_ICONS: ReactNode[] = [
  <CloudUpload key="upload" aria-hidden className="size-4" />,
  <ScanLine key="scan" aria-hidden className="size-4" />,
  <ScanText key="extract" aria-hidden className="size-4" />,
  <ShieldCheck key="audit" aria-hidden className="size-4" />,
  <CircleCheck key="result" aria-hidden className="size-4" />,
]

const STATE_LABELS: Record<StepState, string> = {
  complete: 'completado',
  active: 'en curso',
  pending: 'pendiente',
  error: 'con error',
}

const CIRCLE_STYLES: Record<StepState, string> = {
  complete: 'bg-emerald-500 text-white ring-emerald-500',
  active: 'bg-indigo-50 text-indigo-600 ring-indigo-500',
  pending: 'bg-white text-slate-400 ring-slate-300',
  error: 'bg-red-50 text-red-600 ring-red-500',
}

const LABEL_STYLES: Record<StepState, string> = {
  complete: 'text-emerald-700',
  active: 'font-semibold text-indigo-700',
  pending: 'text-slate-400',
  error: 'font-semibold text-red-700',
}

export function PipelineStepper({ phase }: { phase: UploadPhase }) {
  const states = stepStates(phase)

  return (
    <ol className="grid grid-cols-5" aria-label="Progreso del procesamiento">
      {PIPELINE_STEPS.map((label, index) => {
        const state = states[index]
        const showSpinner = state === 'active' && index > 0 && index < PIPELINE_STEPS.length - 1
        return (
          <li key={label} className="relative flex flex-col items-center gap-1.5" aria-current={state === 'active' ? 'step' : undefined}>
            {index < PIPELINE_STEPS.length - 1 && (
              <span
                aria-hidden
                className={cn(
                  'absolute top-4 left-[calc(50%+20px)] h-0.5 w-[calc(100%-40px)] rounded-full transition-colors duration-500',
                  state === 'complete' ? 'bg-emerald-400' : 'bg-slate-200',
                )}
              />
            )}
            <span
              className={cn(
                'relative z-10 flex size-8 items-center justify-center rounded-full ring-2 transition-all duration-300',
                CIRCLE_STYLES[state],
                state === 'active' && 'scale-110',
              )}
            >
              {state === 'error' ? (
                <CircleX aria-hidden className="size-4" />
              ) : showSpinner ? (
                <LoaderCircle aria-hidden className="size-4 animate-spin" />
              ) : (
                STEP_ICONS[index]
              )}
            </span>
            <span className={cn('text-center text-[11px] leading-tight transition-colors sm:text-xs', LABEL_STYLES[state])}>
              {label}
              <span className="sr-only"> ({STATE_LABELS[state]})</span>
            </span>
          </li>
        )
      })}
    </ol>
  )
}
