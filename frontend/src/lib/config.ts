// Runtime settings shared by services and UI. Single home of the upload-size fallback.

/** Fallback when the backend does not report `max_upload_size_mb` (backend default: 15). */
export const DEFAULT_MAX_UPLOAD_MB = 15

let maxUploadMb = DEFAULT_MAX_UPLOAD_MB

export function getMaxUploadMb(): number {
  return maxUploadMb
}

/** Applies the limit reported by /health; anything that is not a positive number restores the fallback. */
export function setMaxUploadMb(value: number | null | undefined): void {
  maxUploadMb = typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : DEFAULT_MAX_UPLOAD_MB
}
