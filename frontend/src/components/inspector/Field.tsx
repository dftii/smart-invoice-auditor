import { useId, type ReactNode } from 'react'
import { cn } from '../../lib/cn'

const CONTROL =
  'block h-9 w-full rounded-lg border-0 bg-white px-2.5 text-sm text-slate-900 shadow-sm ring-1 ring-inset placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:bg-slate-50 disabled:text-slate-500'

function controlClass(invalid: boolean | undefined, numeric: boolean | undefined) {
  return cn(CONTROL, invalid ? 'ring-red-400 bg-red-50/50' : 'ring-slate-300', numeric && 'text-right tabular-nums')
}

interface FieldShellProps {
  id: string
  label: string
  hint?: ReactNode
  className?: string
  children: ReactNode
}

function FieldShell({ id, label, hint, className, children }: FieldShellProps) {
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-slate-600">
        {label}
      </label>
      {children}
      {hint && <div className="mt-1 text-xs">{hint}</div>}
    </div>
  )
}

interface TextInputProps {
  label: string
  value: string
  onChange: (value: string) => void
  type?: 'text' | 'date'
  numeric?: boolean
  invalid?: boolean
  disabled?: boolean
  placeholder?: string
  maxLength?: number
  hint?: ReactNode
  className?: string
  autoComplete?: string
}

export function TextInput({
  label,
  value,
  onChange,
  type = 'text',
  numeric,
  invalid,
  disabled,
  placeholder,
  maxLength,
  hint,
  className,
  autoComplete = 'off',
}: TextInputProps) {
  const id = useId()
  return (
    <FieldShell id={id} label={label} hint={hint} className={className}>
      <input
        id={id}
        type={type}
        inputMode={numeric ? 'decimal' : undefined}
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        maxLength={maxLength}
        autoComplete={autoComplete}
        aria-invalid={invalid || undefined}
        onChange={(event) => onChange(event.target.value)}
        className={controlClass(invalid, numeric)}
      />
    </FieldShell>
  )
}

interface SelectInputProps {
  label: string
  value: string
  options: { value: string; label: string }[]
  onChange: (value: string) => void
  invalid?: boolean
  disabled?: boolean
  hint?: ReactNode
  className?: string
}

export function SelectInput({ label, value, options, onChange, invalid, disabled, hint, className }: SelectInputProps) {
  const id = useId()
  return (
    <FieldShell id={id} label={label} hint={hint} className={className}>
      <select
        id={id}
        value={value}
        disabled={disabled}
        aria-invalid={invalid || undefined}
        onChange={(event) => onChange(event.target.value)}
        className={controlClass(invalid, false)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </FieldShell>
  )
}
