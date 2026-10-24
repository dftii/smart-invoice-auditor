"""Pydantic v2 schemas for API I/O, extraction results and audit reports."""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from app.core.money import MAX_ABS_AMOUNT
from app.models import AuditStatus, ExtractionMethod, PersonType, Severity, TaxIdType
from app.services.iban import is_valid_iban, normalize_iban
from app.services.tax_id import normalize_tax_id

# Decimals travel as JSON strings ("1234.50") so no precision is lost in transit.
DecimalStr = Annotated[Decimal, PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json")]

T = TypeVar("T")

# Largest value of a 32-bit signed integer column / path parameter; anything above would overflow the database.
MAX_ID = 2**31 - 1
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _normalize_tax_id(value: str | None) -> str | None:
    return normalize_tax_id(value) or None


def _normalize_currency(value: str | None) -> str | None:
    """ISO 4217 alphabetic code: three letters, uppercase."""
    if value is None:
        return None
    code = value.strip().upper()
    if not _CURRENCY_RE.match(code):
        raise ValueError("La moneda debe ser un código ISO 4217 de tres letras (por ejemplo, EUR)")
    return code


def _normalize_iban(value: str | None) -> str | None:
    if value is None:
        return None
    iban = normalize_iban(value)
    if not iban:
        return None
    if not is_valid_iban(iban):
        raise ValueError("El IBAN no es válido (formato o dígitos de control incorrectos)")
    return iban


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- Suppliers


class SupplierBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    tax_id: str = Field(min_length=1, max_length=20)
    person_type: PersonType = PersonType.LEGAL_ENTITY
    address: str | None = Field(default=None, max_length=255)
    postal_code: str | None = Field(default=None, max_length=10)
    city: str | None = Field(default=None, max_length=100)
    province: str | None = Field(default=None, max_length=100)
    country_code: str = Field(default="ESP", min_length=3, max_length=3)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=30)

    @field_validator("tax_id")
    @classmethod
    def normalize_tax_id(cls, value: str) -> str:
        normalized = _normalize_tax_id(value)
        if not normalized:
            raise ValueError("tax_id vacío")
        return normalized


class SupplierCreate(SupplierBase):
    pass


class SupplierUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    person_type: PersonType | None = None
    address: str | None = Field(default=None, max_length=255)
    postal_code: str | None = Field(default=None, max_length=10)
    city: str | None = Field(default=None, max_length=100)
    province: str | None = Field(default=None, max_length=100)
    country_code: str | None = Field(default=None, min_length=3, max_length=3)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=30)


class SupplierRead(ORMModel):
    """Stored supplier: plain types without input constraints, so reading existing data never fails."""

    id: int
    name: str
    tax_id: str
    person_type: PersonType
    address: str | None = None
    postal_code: str | None = None
    city: str | None = None
    province: str | None = None
    country_code: str
    email: str | None = None
    phone: str | None = None
    tax_id_type: TaxIdType
    tax_id_valid: bool
    created_at: datetime
    updated_at: datetime


class SupplierWithStats(SupplierRead):
    invoice_count: int = 0
    total_invoiced: DecimalStr = Decimal("0.00")


# --------------------------------------------------------------------------- Invoice lines


class InvoiceLineFields(BaseModel):
    """Line fields without constraints: the shape of stored data (used by the Read model)."""

    concept: str
    quantity: DecimalStr
    unit_of_measure: str = "01"
    unit_price: DecimalStr
    discount_rate: DecimalStr = Decimal("0.00")
    vat_rate: DecimalStr
    irpf_rate: DecimalStr = Decimal("0.00")
    line_base: DecimalStr
    vat_amount: DecimalStr
    irpf_amount: DecimalStr = Decimal("0.00")


class InvoiceLineCreate(InvoiceLineFields):
    """Line sent by a client: validated. A zero or negative quantity is rejected."""

    concept: str = Field(min_length=1, max_length=500)
    quantity: DecimalStr = Field(gt=0)
    unit_of_measure: str = Field(default="01", min_length=2, max_length=2)
    discount_rate: DecimalStr = Field(default=Decimal("0.00"), ge=0, le=100)
    vat_rate: DecimalStr = Field(ge=0, le=100)
    irpf_rate: DecimalStr = Field(default=Decimal("0.00"), ge=0, le=100)
    line_number: int | None = Field(default=None, ge=1)


class InvoiceLineRead(InvoiceLineFields, ORMModel):
    id: int
    line_number: int


# --------------------------------------------------------------------------- Validation logs


class ValidationLogRead(ORMModel):
    id: int
    run_id: str
    rule_code: str
    severity: Severity
    passed: bool
    message: str
    field: str | None = None
    line_number: int | None = None
    expected_value: str | None = None
    actual_value: str | None = None
    created_at: datetime


# --------------------------------------------------------------------------- Invoices


class InvoiceBuyerFields(BaseModel):
    buyer_name: str | None = Field(default=None, max_length=255)
    buyer_tax_id: str | None = Field(default=None, max_length=20)
    buyer_address: str | None = Field(default=None, max_length=255)
    buyer_postal_code: str | None = Field(default=None, max_length=10)
    buyer_city: str | None = Field(default=None, max_length=100)
    buyer_province: str | None = Field(default=None, max_length=100)
    buyer_country_code: str = Field(default="ESP", min_length=3, max_length=3)
    dir3_accounting_office: str | None = Field(default=None, max_length=20)
    dir3_managing_body: str | None = Field(default=None, max_length=20)
    dir3_processing_unit: str | None = Field(default=None, max_length=20)

    @field_validator("buyer_tax_id")
    @classmethod
    def normalize_buyer_tax_id(cls, value: str | None) -> str | None:
        return _normalize_tax_id(value)


class InvoiceBuyerRead(BaseModel):
    """Stored buyer / DIR3 data, without input constraints."""

    buyer_name: str | None = None
    buyer_tax_id: str | None = None
    buyer_address: str | None = None
    buyer_postal_code: str | None = None
    buyer_city: str | None = None
    buyer_province: str | None = None
    buyer_country_code: str = "ESP"
    dir3_accounting_office: str | None = None
    dir3_managing_body: str | None = None
    dir3_processing_unit: str | None = None


class InvoiceCreate(InvoiceBuyerFields):
    """Manual invoice creation (no file). Supplier is upserted by tax id."""

    supplier: SupplierCreate
    invoice_number: str = Field(min_length=1, max_length=50)
    invoice_series: str | None = Field(default=None, max_length=20)
    issue_date: date
    operation_date: date | None = None
    due_date: date | None = None
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    payment_iban: str | None = Field(default=None, max_length=50, description="IBAN donde se abona la factura")
    tax_base: DecimalStr
    total_vat: DecimalStr = Decimal("0.00")
    total_irpf: DecimalStr = Decimal("0.00")
    total_amount: DecimalStr
    notes: str | None = None
    lines: list[InvoiceLineCreate] = Field(min_length=1)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return _normalize_currency(value) or "EUR"

    @field_validator("payment_iban")
    @classmethod
    def validate_iban(cls, value: str | None) -> str | None:
        return _normalize_iban(value)


class InvoiceUpdate(BaseModel):
    """Partial correction of an invoice. If `lines` is sent it fully replaces the existing lines."""

    invoice_number: str | None = Field(default=None, min_length=1, max_length=50)
    invoice_series: str | None = Field(default=None, max_length=20)
    issue_date: date | None = None
    operation_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    payment_iban: str | None = Field(default=None, max_length=50)
    supplier_tax_id: str | None = Field(default=None, max_length=20)
    buyer_name: str | None = Field(default=None, max_length=255)
    buyer_tax_id: str | None = Field(default=None, max_length=20)
    buyer_address: str | None = Field(default=None, max_length=255)
    buyer_postal_code: str | None = Field(default=None, max_length=10)
    buyer_city: str | None = Field(default=None, max_length=100)
    buyer_province: str | None = Field(default=None, max_length=100)
    buyer_country_code: str | None = Field(default=None, min_length=3, max_length=3)
    dir3_accounting_office: str | None = Field(default=None, max_length=20)
    dir3_managing_body: str | None = Field(default=None, max_length=20)
    dir3_processing_unit: str | None = Field(default=None, max_length=20)
    tax_base: DecimalStr | None = None
    total_vat: DecimalStr | None = None
    total_irpf: DecimalStr | None = None
    total_amount: DecimalStr | None = None
    notes: str | None = None
    lines: list[InvoiceLineCreate] | None = None

    @field_validator("buyer_tax_id")
    @classmethod
    def normalize_buyer_id(cls, value: str | None) -> str | None:
        return _normalize_tax_id(value)

    @field_validator("supplier_tax_id")
    @classmethod
    def normalize_supplier_id(cls, value: str | None) -> str | None:
        # An id with no usable characters stays "" (not None) so the service can explain that it is empty.
        return normalize_tax_id(value) if value is not None else None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        return _normalize_currency(value)

    @field_validator("payment_iban")
    @classmethod
    def validate_iban(cls, value: str | None) -> str | None:
        return _normalize_iban(value)


class InvoiceSummary(ORMModel):
    id: int
    supplier_id: int
    supplier_name: str
    supplier_tax_id: str
    invoice_number: str
    invoice_series: str | None = None
    issue_date: date
    due_date: date | None = None
    currency: str
    tax_base: DecimalStr
    total_vat: DecimalStr
    total_irpf: DecimalStr
    total_amount: DecimalStr
    audit_status: AuditStatus
    error_count: int
    warning_count: int
    extraction_method: ExtractionMethod
    extraction_confidence: float | None = None
    is_synthetic: bool = False
    source_filename: str | None = None
    last_audited_at: datetime | None = None
    facturae_exported_at: datetime | None = None
    facturae_export_count: int = 0
    created_at: datetime


class InvoiceRead(InvoiceBuyerRead, ORMModel):
    id: int
    supplier: SupplierRead
    supplier_tax_id: str
    invoice_number: str
    invoice_series: str | None = None
    issue_date: date
    operation_date: date | None = None
    due_date: date | None = None
    currency: str
    payment_iban: str | None = None
    tax_base: DecimalStr
    total_vat: DecimalStr
    total_irpf: DecimalStr
    total_amount: DecimalStr
    audit_status: AuditStatus
    audit_summary: str | None = None
    error_count: int
    warning_count: int
    last_audited_at: datetime | None = None
    last_audit_run_id: str | None = None
    facturae_exported_at: datetime | None = None
    facturae_export_count: int = 0
    extraction_method: ExtractionMethod
    extraction_confidence: float | None = None
    is_synthetic: bool = False
    source_filename: str | None = None
    source_mime_type: str | None = None
    source_file_hash: str | None = None
    notes: str | None = None
    lines: list[InvoiceLineRead]
    validation_logs: list[ValidationLogRead] = Field(
        default_factory=list, description="Logs of the latest audit run only"
    )
    created_at: datetime
    updated_at: datetime


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


SortField = Literal["issue_date", "total_amount", "created_at", "invoice_number", "audit_status"]


class InvoiceFilters(BaseModel):
    status: AuditStatus | None = None
    supplier_id: int | None = Field(default=None, ge=1, le=MAX_ID)
    tax_id: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    min_total: Decimal | None = Field(default=None, ge=-MAX_ABS_AMOUNT, le=MAX_ABS_AMOUNT)
    max_total: Decimal | None = Field(default=None, ge=-MAX_ABS_AMOUNT, le=MAX_ABS_AMOUNT)
    extraction_method: ExtractionMethod | None = None
    exported: bool | None = Field(default=None, description="true = Facturae XML already generated")
    has_errors: bool | None = Field(default=None, description="true = DISCREPANCY or DUPLICATE")
    search: str | None = Field(default=None, description="Matches invoice number, supplier name or tax id")
    sort_by: SortField = "issue_date"
    sort_dir: Literal["asc", "desc"] = "desc"


# --------------------------------------------------------------------------- Extraction


class ExtractedParty(BaseModel):
    name: str | None = None
    tax_id: str | None = None
    address: str | None = None
    postal_code: str | None = None
    city: str | None = None
    province: str | None = None
    country_code: str = "ESP"
    email: str | None = None
    phone: str | None = None

    @field_validator("tax_id")
    @classmethod
    def normalize(cls, value: str | None) -> str | None:
        return _normalize_tax_id(value)


class ExtractedLine(BaseModel):
    concept: str
    quantity: DecimalStr
    unit_price: DecimalStr
    discount_rate: DecimalStr = Decimal("0.00")
    vat_rate: DecimalStr
    irpf_rate: DecimalStr = Decimal("0.00")
    line_base: DecimalStr
    vat_amount: DecimalStr
    irpf_amount: DecimalStr = Decimal("0.00")


class ExtractionResult(BaseModel):
    """Structured output of any extraction strategy (AI or demo)."""

    method: ExtractionMethod
    strategy_name: str
    confidence: float = Field(ge=0.0, le=1.0)
    supplier: ExtractedParty
    buyer: ExtractedParty = Field(default_factory=ExtractedParty)
    invoice_number: str
    invoice_series: str | None = None
    issue_date: date
    operation_date: date | None = None
    due_date: date | None = None
    currency: str = "EUR"
    tax_base: DecimalStr
    total_vat: DecimalStr
    total_irpf: DecimalStr = Decimal("0.00")
    total_amount: DecimalStr
    lines: list[ExtractedLine] = Field(min_length=1)
    dir3_accounting_office: str | None = None
    dir3_managing_body: str | None = None
    dir3_processing_unit: str | None = None
    is_synthetic: bool = Field(default=False, description="True when the data was fabricated, not read")
    warnings: list[str] = Field(default_factory=list)
    raw_text: str | None = Field(default=None, description="Text extracted from the document, if any")


# --------------------------------------------------------------------------- Audit


class AuditCheck(BaseModel):
    rule_code: str
    severity: Severity
    passed: bool
    message: str
    field: str | None = None
    line_number: int | None = None
    expected_value: str | None = None
    actual_value: str | None = None


class AuditReport(BaseModel):
    invoice_id: int
    run_id: str
    status: AuditStatus
    error_count: int
    warning_count: int
    summary: str
    checks: list[AuditCheck]
    duplicate_of: list[int] = Field(default_factory=list)
    audited_at: datetime


class UploadResponse(BaseModel):
    invoice: InvoiceRead
    audit: AuditReport
    extraction_method: ExtractionMethod
    extraction_warnings: list[str]


class BulkRevalidateRequest(BaseModel):
    invoice_ids: list[Annotated[int, Field(ge=1, le=MAX_ID)]] | None = Field(
        default=None, max_length=10_000, description="None = revalidate all invoices"
    )
    status: AuditStatus | None = Field(default=None, description="Only revalidate invoices in this status")


class BulkRevalidateResponse(BaseModel):
    processed: int
    reports: list[AuditReport]


# --------------------------------------------------------------------------- Dashboard


class StatusCount(BaseModel):
    status: AuditStatus
    count: int
    total_amount: DecimalStr


class MonthlyTotal(BaseModel):
    month: str = Field(description="YYYY-MM")
    invoice_count: int
    tax_base: DecimalStr
    total_vat: DecimalStr
    total_irpf: DecimalStr
    total_amount: DecimalStr


class TopSupplier(BaseModel):
    supplier_id: int
    name: str
    tax_id: str
    invoice_count: int
    total_amount: DecimalStr


class RuleFailureCount(BaseModel):
    rule_code: str
    count: int


class VatBreakdown(BaseModel):
    vat_rate: DecimalStr
    tax_base: DecimalStr
    vat_amount: DecimalStr


class DashboardStats(BaseModel):
    total_invoices: int
    total_suppliers: int
    total_tax_base: DecimalStr
    total_vat: DecimalStr
    total_irpf: DecimalStr
    total_amount: DecimalStr
    validation_rate: float = Field(description="Percentage of invoices in VALIDATED status (0-100)")
    invoices_with_errors: int = Field(description="Invoices in DISCREPANCY or DUPLICATE status")
    exported_to_face: int = Field(description="Invoices whose Facturae XML has been generated")
    ready_for_face: int = Field(description="VALIDATED invoices with no warnings, not exported yet")
    by_status: list[StatusCount]
    monthly: list[MonthlyTotal]
    top_suppliers: list[TopSupplier]
    top_failed_rules: list[RuleFailureCount]
    vat_breakdown: list[VatBreakdown]
    extraction_mode: Literal["AI", "DEMO"]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    app: str
    version: str
    database: Literal["ok"]
    extraction_mode: Literal["AI", "DEMO"]
