import type { AuditStatus } from '../types/api'

/** Visual state shown to the user: an exported VALIDATED invoice is shown as "XML FACe generado" (blue). */
export type DisplayStatus = AuditStatus | 'EXPORTED'

export function displayStatus(status: AuditStatus, exportedAt: string | null | undefined): DisplayStatus {
  return status === 'VALIDATED' && exportedAt ? 'EXPORTED' : status
}
