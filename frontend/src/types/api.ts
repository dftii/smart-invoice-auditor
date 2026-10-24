// Types mirroring the FastAPI schemas in backend/app/schemas.py.
// Decimal amounts travel as strings ("1234.50") to keep cent precision; parse them only for display or math.

export type DecimalString = string
export type ISODate = string // YYYY-MM-DD
export type ISODateTime = string

export type AuditStatus = 'PENDING' | 'VALIDATED' | 'DISCREPANCY' | 'DUPLICATE'
export type ExtractionMethod = 'AI' | 'DEMO' | 'MANUAL'
export type Severity = 'INFO' | 'WARNING' | 'ERROR'
export type TaxIdType = 'NIF' | 'NIE' | 'CIF' | 'UNKNOWN'
export type PersonType = 'F' | 'J'
export type ExtractionMode = 'AI' | 'DEMO'

export type RuleCode =
  | 'SUPPLIER_TAX_ID'
  | 'BUYER_TAX_ID'
  | 'DATES'
  | 'HAS_LINES'
  | 'LINE_BASE'
  | 'VAT_RATE'
  | 'LINE_VAT'
  | 'IRPF_RATE'
  | 'LINE_IRPF'
  | 'TAX_BASE_SUM'
  | 'VAT_TOTAL'
  | 'IRPF_TOTAL'
  | 'GRAND_TOTAL'
  | 'DUPLICATE'
  | 'CURRENCY'
  | 'FACE_DIR3'
  | 'FACE_PARTY_DATA'
  | 'SYNTHETIC_DATA'

export interface Supplier {
  id: number
  name: string
  tax_id: string
  tax_id_type: TaxIdType
  tax_id_valid: boolean
  person_type: PersonType
  address: string | null
  postal_code: string | null
  city: string | null
  province: string | null
  country_code: string
  email: string | null
  phone: string | null
  created_at: ISODateTime
  updated_at: ISODateTime
}

export interface InvoiceLine {
  id: number
  line_number: number
  concept: string
  quantity: DecimalString
  unit_of_measure: string
  unit_price: DecimalString
  discount_rate: DecimalString
  vat_rate: DecimalString
  irpf_rate: DecimalString
  line_base: DecimalString
  vat_amount: DecimalString
  irpf_amount: DecimalString
}

export interface ValidationLog {
  id: number
  run_id: string
  rule_code: RuleCode | string
  severity: Severity
  passed: boolean
  message: string
  field: string | null
  line_number: number | null
  expected_value: string | null
  actual_value: string | null
  created_at: ISODateTime
}

export interface InvoiceSummary {
  id: number
  supplier_id: number
  supplier_name: string
  supplier_tax_id: string
  invoice_number: string
  invoice_series: string | null
  issue_date: ISODate
  due_date: ISODate | null
  currency: string
  tax_base: DecimalString
  total_vat: DecimalString
  total_irpf: DecimalString
  total_amount: DecimalString
  audit_status: AuditStatus
  error_count: number
  warning_count: number
  extraction_method: ExtractionMethod
  extraction_confidence: number | null
  source_filename: string | null
  /** Demo/synthetic data: can be audited and edited, but the Facturae export is blocked. */
  is_synthetic: boolean
  last_audited_at: ISODateTime | null
  facturae_exported_at: ISODateTime | null
  facturae_export_count: number
  created_at: ISODateTime
}

export interface InvoiceDetail {
  id: number
  supplier: Supplier
  supplier_tax_id: string
  invoice_number: string
  invoice_series: string | null
  issue_date: ISODate
  operation_date: ISODate | null
  due_date: ISODate | null
  currency: string
  buyer_name: string | null
  buyer_tax_id: string | null
  buyer_address: string | null
  buyer_postal_code: string | null
  buyer_city: string | null
  buyer_province: string | null
  buyer_country_code: string
  dir3_accounting_office: string | null
  dir3_managing_body: string | null
  dir3_processing_unit: string | null
  /** IBAN the invoice is paid to (optional). */
  payment_iban: string | null
  tax_base: DecimalString
  total_vat: DecimalString
  total_irpf: DecimalString
  total_amount: DecimalString
  audit_status: AuditStatus
  audit_summary: string | null
  error_count: number
  warning_count: number
  last_audited_at: ISODateTime | null
  last_audit_run_id: string | null
  facturae_exported_at: ISODateTime | null
  facturae_export_count: number
  extraction_method: ExtractionMethod
  extraction_confidence: number | null
  source_filename: string | null
  source_mime_type: string | null
  /** Demo/synthetic data: can be audited and edited, but the Facturae export is blocked. */
  is_synthetic: boolean
  source_file_hash: string | null
  notes: string | null
  lines: InvoiceLine[]
  /** Logs of the latest audit run only. */
  validation_logs: ValidationLog[]
  created_at: ISODateTime
  updated_at: ISODateTime
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  pages: number
}

export type SortField = 'issue_date' | 'total_amount' | 'created_at' | 'invoice_number' | 'audit_status'
export type SortDir = 'asc' | 'desc'

export interface InvoiceFilters {
  status?: AuditStatus
  supplier_id?: number
  tax_id?: string
  date_from?: ISODate
  date_to?: ISODate
  min_total?: string
  max_total?: string
  extraction_method?: ExtractionMethod
  exported?: boolean
  has_errors?: boolean
  search?: string
  sort_by?: SortField
  sort_dir?: SortDir
}

export interface AuditCheck {
  rule_code: RuleCode | string
  severity: Severity
  passed: boolean
  message: string
  field: string | null
  line_number: number | null
  expected_value: string | null
  actual_value: string | null
}

export interface AuditReport {
  invoice_id: number
  run_id: string
  status: AuditStatus
  error_count: number
  warning_count: number
  summary: string
  checks: AuditCheck[]
  duplicate_of: number[]
  audited_at: ISODateTime
}

export interface UploadResponse {
  invoice: InvoiceDetail
  audit: AuditReport
  extraction_method: ExtractionMethod
  extraction_warnings: string[]
}

export interface InvoiceLineInput {
  line_number?: number
  concept: string
  quantity: DecimalString
  unit_of_measure?: string
  unit_price: DecimalString
  discount_rate?: DecimalString
  vat_rate: DecimalString
  irpf_rate?: DecimalString
  line_base: DecimalString
  vat_amount: DecimalString
  irpf_amount?: DecimalString
}

/** PATCH body: only the keys present are changed; `lines`, when sent, replaces every line. */
export interface InvoiceUpdate {
  invoice_number?: string
  invoice_series?: string | null
  issue_date?: ISODate
  operation_date?: ISODate | null
  due_date?: ISODate | null
  supplier_tax_id?: string
  buyer_name?: string | null
  buyer_tax_id?: string | null
  buyer_address?: string | null
  buyer_postal_code?: string | null
  buyer_city?: string | null
  buyer_province?: string | null
  buyer_country_code?: string
  dir3_accounting_office?: string | null
  dir3_managing_body?: string | null
  dir3_processing_unit?: string | null
  payment_iban?: string | null
  tax_base?: DecimalString
  total_vat?: DecimalString
  total_irpf?: DecimalString
  total_amount?: DecimalString
  notes?: string | null
  lines?: InvoiceLineInput[]
}

export interface StatusCount {
  status: AuditStatus
  count: number
  total_amount: DecimalString
}

export interface MonthlyTotal {
  month: string // YYYY-MM
  invoice_count: number
  tax_base: DecimalString
  total_vat: DecimalString
  total_irpf: DecimalString
  total_amount: DecimalString
}

export interface TopSupplier {
  supplier_id: number
  name: string
  tax_id: string
  invoice_count: number
  total_amount: DecimalString
}

export interface RuleFailureCount {
  rule_code: RuleCode | string
  count: number
}

export interface VatBreakdown {
  vat_rate: DecimalString
  tax_base: DecimalString
  vat_amount: DecimalString
}

export interface DashboardStats {
  total_invoices: number
  total_suppliers: number
  total_tax_base: DecimalString
  total_vat: DecimalString
  total_irpf: DecimalString
  total_amount: DecimalString
  validation_rate: number
  invoices_with_errors: number
  exported_to_face: number
  ready_for_face: number
  by_status: StatusCount[]
  monthly: MonthlyTotal[]
  top_suppliers: TopSupplier[]
  top_failed_rules: RuleFailureCount[]
  vat_breakdown: VatBreakdown[]
  extraction_mode: ExtractionMode
}

export interface HealthResponse {
  status: 'ok'
  app: string
  version: string
  /** Opaque description of the storage engine (e.g. "sqlite", "postgresql"); only shown, never interpreted. */
  database: string
  /** Upload limit enforced by the backend; the UI falls back to its own constant when absent. */
  max_upload_size_mb?: number
  extraction_mode: ExtractionMode
}

/** One FastAPI/pydantic validation error (HTTP 422). */
export interface ValidationIssue {
  loc: (string | number)[]
  msg: string
  type: string
  ctx?: Record<string, unknown>
}

/** FastAPI error body: `detail` is a Spanish message, or a list of validation errors (422). */
export interface ApiErrorBody {
  detail?: string | ValidationIssue[]
}
