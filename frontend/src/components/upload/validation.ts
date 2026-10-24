import { getMaxUploadMb } from '../../lib/config'

/** Accepted formats mirror the backend settings (PDF/PNG/JPEG/WEBP); the size limit comes from lib/config. */
export const ACCEPT_ATTRIBUTE = '.pdf,.png,.jpg,.jpeg,.webp,application/pdf,image/*'

const ALLOWED_EXTENSIONS = ['pdf', 'png', 'jpg', 'jpeg', 'webp']
const ALLOWED_MIME_TYPES = ['application/pdf', 'image/png', 'image/jpeg', 'image/webp']

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot < 0 ? '' : name.slice(dot + 1).toLowerCase()
}

/** Returns a Spanish error message, or null when the file can be uploaded. */
export function validateFile(file: File): string | null {
  if (file.size === 0) return 'El archivo está vacío.'
  const typeOk = ALLOWED_MIME_TYPES.includes(file.type) || (file.type === '' && ALLOWED_EXTENSIONS.includes(extensionOf(file.name)))
  if (!typeOk) return 'Formato no admitido. Sube un PDF o una imagen PNG, JPG o WEBP.'
  const maxMb = getMaxUploadMb()
  if (file.size > maxMb * 1024 * 1024) return `El archivo supera el tamaño máximo de ${maxMb} MB.`
  return null
}

export function isPdf(file: File): boolean {
  return file.type === 'application/pdf' || extensionOf(file.name) === 'pdf'
}

const PASTE_EXTENSIONS: Record<string, string> = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp' }

/** Clipboard images arrive as an anonymous "image.png"; give them a recognisable name. */
export function nameClipboardFile(file: File, index: number): File {
  const extension = PASTE_EXTENSIONS[file.type] ?? 'png'
  return new File([file], `captura-${Date.now()}-${index + 1}.${extension}`, { type: file.type, lastModified: Date.now() })
}
