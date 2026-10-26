import { CloudUpload, Sparkles } from 'lucide-react'
import { useRef, useState, type DragEvent, type KeyboardEvent } from 'react'
import { cn } from '../../lib/cn'
import { ACCEPT_ATTRIBUTE } from './validation'

const MODE_TEXT = {
  AI: 'Modo IA: extracción con Claude',
  DEMO: 'Modo Demo: parser de texto y OCR, sin coste',
} as const

interface DropAreaProps {
  extractionMode: 'AI' | 'DEMO' | null
  maxUploadMb: number
  onFiles: (files: File[]) => void
}

export function DropArea({ extractionMode, maxUploadMb, onFiles }: DropAreaProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const openPicker = () => inputRef.current?.click()

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      openPicker()
    }
  }

  const handleDragOver = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
    setDragging(true)
  }

  const handleDragLeave = (event: DragEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false)
  }

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    onFiles(Array.from(event.dataTransfer.files))
  }

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label="Subir facturas: arrastra archivos aquí, haz clic para seleccionarlos o pega una imagen"
      onClick={openPicker}
      onKeyDown={handleKeyDown}
      onDragOver={handleDragOver}
      onDragEnter={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={cn(
        'group flex cursor-pointer flex-col items-center gap-3 rounded-2xl border-2 border-dashed px-4 py-10 text-center transition-all duration-200 sm:py-12',
        dragging
          ? 'scale-[1.02] border-indigo-500 bg-indigo-50 shadow-lg shadow-indigo-500/10'
          : 'border-slate-300 bg-white hover:border-indigo-400 hover:bg-indigo-50/40',
      )}
    >
      <span
        aria-hidden
        className={cn(
          'flex size-14 items-center justify-center rounded-2xl transition-all duration-200',
          dragging ? 'scale-110 bg-indigo-600 text-white' : 'bg-indigo-50 text-indigo-600 group-hover:bg-indigo-100',
        )}
      >
        <CloudUpload className={cn('size-7', dragging && 'animate-pulse')} />
      </span>
      <div className="space-y-1">
        <p className="text-base font-semibold text-slate-900">
          {dragging ? 'Suelta las facturas para subirlas' : 'Arrastra tus facturas (PDF o imagen) o haz clic para seleccionarlas'}
        </p>
        <p className="text-sm text-slate-500">
          PDF, PNG, JPG o WEBP · máx. {maxUploadMb} MB por archivo · también puedes pegar una imagen con Ctrl+V
        </p>
      </div>
      {extractionMode && (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
          <Sparkles aria-hidden className={cn('size-3.5', extractionMode === 'AI' ? 'text-violet-500' : 'text-slate-400')} />
          {MODE_TEXT[extractionMode]}
        </span>
      )}
      <input
        ref={inputRef}
        type="file"
        multiple
        hidden
        accept={ACCEPT_ATTRIBUTE}
        aria-label="Seleccionar facturas"
        onClick={(event) => event.stopPropagation()}
        onChange={(event) => {
          onFiles(Array.from(event.target.files ?? []))
          event.target.value = ''
        }}
      />
    </div>
  )
}
