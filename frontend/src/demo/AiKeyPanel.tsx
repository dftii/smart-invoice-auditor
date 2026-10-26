// Demo only: lets the visitor switch the extraction to Claude with their own Anthropic API key.

import { KeyRound, ShieldCheck, Sparkles, X } from 'lucide-react'
import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { Button } from '../components/ui/Button'
import { clearAiKey, isAiKeyRemembered, looksLikeAiKey, maskAiKey, setAiKey, useAiKey, verifyAiKey } from './aiKey'

const CONSOLE_URL = 'https://console.anthropic.com/settings/keys'

export default function AiKeyPanel() {
  const aiKey = useAiKey()
  const [editing, setEditing] = useState(false)

  if (aiKey) return <ActiveKey aiKey={aiKey} />
  if (editing) return <KeyForm onClose={() => setEditing(false)} />
  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-slate-200 bg-white p-4 sm:flex-row sm:items-center">
      <span aria-hidden className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-violet-50 text-violet-600">
        <Sparkles className="size-5" />
      </span>
      <p className="min-w-0 flex-1 text-sm text-slate-600">
        La demo lee las facturas sin IA, con un parser de texto para PDF.{' '}
        <span className="text-slate-900">¿Tienes una API key de Anthropic?</span> Úsala para que Claude extraiga los
        datos, también de imágenes y escaneos.
      </p>
      <Button variant="secondary" icon={<KeyRound aria-hidden className="size-4" />} onClick={() => setEditing(true)}>
        Usar mi API key
      </Button>
    </div>
  )
}

function ActiveKey({ aiKey }: { aiKey: string }) {
  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-violet-200 bg-violet-50/60 p-4 sm:flex-row sm:items-center">
      <span aria-hidden className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-violet-600 text-white">
        <Sparkles className="size-5" />
      </span>
      <div className="min-w-0 flex-1 text-sm">
        <p className="font-semibold text-slate-900">Extracción con IA activada</p>
        <p className="text-slate-600">
          Claude leerá las facturas que subas con tu clave{' '}
          <code className="rounded bg-white px-1.5 py-0.5 font-mono text-xs text-slate-800 ring-1 ring-slate-200">
            {maskAiKey(aiKey)}
          </code>
          {isAiKeyRemembered() ? ', guardada en este navegador.' : ', solo durante esta pestaña.'}
        </p>
      </div>
      <Button variant="ghost" icon={<X aria-hidden className="size-4" />} onClick={clearAiKey}>
        Quitar clave
      </Button>
    </div>
  )
}

function KeyForm({ onClose }: { onClose: () => void }) {
  const inputId = useId()
  const hintId = useId()
  const errorId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const [value, setValue] = useState('')
  const [remember, setRemember] = useState(false)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    inputRef.current?.focus()
    return () => abortRef.current?.abort()
  }, [])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const key = value.trim()
    if (!looksLikeAiKey(key)) {
      setError('Eso no parece una API key de Anthropic: empieza por «sk-ant-».')
      inputRef.current?.focus()
      return
    }
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setChecking(true)
    setError(null)
    try {
      await verifyAiKey(key, controller.signal)
      setAiKey(key, remember)
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      setError(caught instanceof Error ? caught.message : 'No se pudo comprobar la clave.')
      setChecking(false)
      inputRef.current?.focus()
    }
  }

  function cancel() {
    abortRef.current?.abort()
    onClose()
  }

  return (
    <form
      onSubmit={handleSubmit}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && !checking) cancel()
      }}
      className="space-y-3 rounded-2xl border border-violet-200 bg-white p-4"
    >
      <div className="flex items-start gap-3">
        <span aria-hidden className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-violet-50 text-violet-600">
          <KeyRound className="size-5" />
        </span>
        <div className="min-w-0 flex-1 space-y-1">
          <label htmlFor={inputId} className="block text-sm font-semibold text-slate-900">
            Tu API key de Anthropic
          </label>
          <p id={hintId} className="text-xs text-slate-500">
            Créala en{' '}
            <a href={CONSOLE_URL} target="_blank" rel="noreferrer" className="font-medium text-indigo-600 hover:underline">
              console.anthropic.com
            </a>
            . Cada factura consume tokens de tu cuenta.
          </p>
        </div>
      </div>

      <input
        ref={inputRef}
        id={inputId}
        type="password"
        autoComplete="off"
        spellCheck={false}
        placeholder="sk-ant-…"
        value={value}
        disabled={checking}
        aria-describedby={error ? `${hintId} ${errorId}` : hintId}
        aria-invalid={error ? true : undefined}
        onChange={(event) => {
          setValue(event.target.value)
          setError(null)
        }}
        className="h-10 w-full rounded-lg border-0 px-3 font-mono text-sm text-slate-900 ring-1 ring-inset ring-slate-300 placeholder:text-slate-400 focus:ring-2 focus:ring-inset focus:ring-indigo-600 disabled:bg-slate-50 aria-[invalid=true]:ring-red-400"
      />
      {error && (
        <p id={errorId} role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}

      <label className="flex items-center gap-2 text-sm text-slate-700">
        <input
          type="checkbox"
          checked={remember}
          disabled={checking}
          onChange={(event) => setRemember(event.target.checked)}
          className="size-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-600"
        />
        Recordarla en este navegador
      </label>

      <p className="flex items-start gap-2 rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
        <ShieldCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-emerald-600" />
        <span>
          La clave se guarda solo en tu navegador y se envía únicamente a <span className="font-mono">api.anthropic.com</span>.
          Sin «Recordar», se borra al cerrar la pestaña. Úsala en un ordenador de confianza.
        </span>
      </p>

      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="ghost" onClick={cancel} disabled={checking}>
          Cancelar
        </Button>
        <Button
          type="submit"
          loading={checking}
          disabled={value.trim() === ''}
          icon={<Sparkles aria-hidden className="size-4" />}
        >
          {checking ? 'Comprobando clave…' : 'Activar IA'}
        </Button>
      </div>
    </form>
  )
}
