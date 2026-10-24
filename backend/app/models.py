"""ORM models: suppliers, invoices, invoice lines and validation logs."""

import enum
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class TaxIdType(str, enum.Enum):
    NIF = "NIF"  # Persona física española (DNI + letra)
    NIE = "NIE"  # Extranjero residente (X/Y/Z + 7 dígitos + letra)
    CIF = "CIF"  # Persona jurídica (letra + 7 dígitos + control)
    UNKNOWN = "UNKNOWN"


class PersonType(str, enum.Enum):
    INDIVIDUAL = "F"  # Facturae PersonTypeCode: física
    LEGAL_ENTITY = "J"  # Facturae PersonTypeCode: jurídica


class AuditStatus(str, enum.Enum):
    PENDING = "PENDING"
    VALIDATED = "VALIDATED"
    DISCREPANCY = "DISCREPANCY"
    DUPLICATE = "DUPLICATE"


class ExtractionMethod(str, enum.Enum):
    AI = "AI"
    DEMO = "DEMO"
    MANUAL = "MANUAL"


class Severity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class UTCDateTime(TypeDecorator[datetime]):
    """Stores datetimes in UTC and always returns them timezone-aware.

    SQLite and MySQL DATETIME drop the offset, so values read back would otherwise be naive.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def enum_column(enum_cls: type[enum.Enum], length: int) -> Enum:
    """Portable VARCHAR-backed enum that stores the member value (not the name)."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


MONEY = Numeric(14, 2)
RATE = Numeric(5, 2)
QUANTITY = Numeric(14, 4)
UNIT_PRICE = Numeric(18, 6)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False
    )


class Supplier(TimestampMixin, Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tax_id: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    tax_id_type: Mapped[TaxIdType] = mapped_column(
        enum_column(TaxIdType, 10), default=TaxIdType.UNKNOWN, nullable=False
    )
    tax_id_valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    person_type: Mapped[PersonType] = mapped_column(
        enum_column(PersonType, 1), default=PersonType.LEGAL_ENTITY, nullable=False
    )
    address: Mapped[str | None] = mapped_column(String(255))
    postal_code: Mapped[str | None] = mapped_column(String(10))
    city: Mapped[str | None] = mapped_column(String(100))
    province: Mapped[str | None] = mapped_column(String(100))
    country_code: Mapped[str] = mapped_column(String(3), default="ESP", nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(30))

    invoices: Mapped[list["Invoice"]] = relationship(back_populates="supplier")

    def __repr__(self) -> str:
        return f"<Supplier id={self.id} tax_id={self.tax_id!r} name={self.name!r}>"


class Invoice(TimestampMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_supplier_number", "supplier_id", "invoice_number"),
        Index("ix_invoices_status_issue_date", "audit_status", "issue_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False)

    invoice_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    invoice_series: Mapped[str | None] = mapped_column(String(20))
    issue_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    operation_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)

    # Snapshot of the issuer tax id as printed on the invoice (may differ from supplier record).
    supplier_tax_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Buyer (receptor). For FACe this is the public administration.
    buyer_name: Mapped[str | None] = mapped_column(String(255))
    buyer_tax_id: Mapped[str | None] = mapped_column(String(20), index=True)
    buyer_address: Mapped[str | None] = mapped_column(String(255))
    buyer_postal_code: Mapped[str | None] = mapped_column(String(10))
    buyer_city: Mapped[str | None] = mapped_column(String(100))
    buyer_province: Mapped[str | None] = mapped_column(String(100))
    buyer_country_code: Mapped[str] = mapped_column(String(3), default="ESP", nullable=False)
    # DIR3 codes required by FACe: oficina contable, órgano gestor, unidad tramitadora.
    dir3_accounting_office: Mapped[str | None] = mapped_column(String(20))
    dir3_managing_body: Mapped[str | None] = mapped_column(String(20))
    dir3_processing_unit: Mapped[str | None] = mapped_column(String(20))

    # Totals as declared on the document. The auditor recomputes and compares them.
    tax_base: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_vat: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0.00"))
    total_irpf: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0.00"))
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    audit_status: Mapped[AuditStatus] = mapped_column(
        enum_column(AuditStatus, 20), default=AuditStatus.PENDING, nullable=False
    )
    audit_summary: Mapped[str | None] = mapped_column(Text)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_audited_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_audit_run_id: Mapped[str | None] = mapped_column(String(36))

    # Last time the Facturae XML was generated; cleared when the invoice is edited afterwards.
    facturae_exported_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    facturae_export_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    extraction_method: Mapped[ExtractionMethod] = mapped_column(
        enum_column(ExtractionMethod, 10), default=ExtractionMethod.MANUAL, nullable=False
    )
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    raw_extraction: Mapped[dict | None] = mapped_column(JSON)

    source_filename: Mapped[str | None] = mapped_column(String(255))
    source_mime_type: Mapped[str | None] = mapped_column(String(100))
    source_file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    source_file_path: Mapped[str | None] = mapped_column(String(500))

    notes: Mapped[str | None] = mapped_column(Text)

    supplier: Mapped[Supplier] = relationship(back_populates="invoices")
    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLine.line_number",
    )
    validation_logs: Mapped[list["ValidationLog"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="ValidationLog.id",
    )

    @property
    def full_number(self) -> str:
        return f"{self.invoice_series}-{self.invoice_number}" if self.invoice_series else self.invoice_number

    def __repr__(self) -> str:
        return f"<Invoice id={self.id} number={self.full_number!r} status={self.audit_status.value}>"


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    __table_args__ = (UniqueConstraint("invoice_id", "line_number", name="uq_invoice_line_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    concept: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(2), default="01", nullable=False)  # Facturae: 01 = unidades
    unit_price: Mapped[Decimal] = mapped_column(UNIT_PRICE, nullable=False)
    discount_rate: Mapped[Decimal] = mapped_column(RATE, default=Decimal("0.00"), nullable=False)
    vat_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    irpf_rate: Mapped[Decimal] = mapped_column(RATE, default=Decimal("0.00"), nullable=False)

    # Amounts as declared on the document.
    line_base: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    vat_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    irpf_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"), nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")

    def __repr__(self) -> str:
        return f"<InvoiceLine invoice_id={self.invoice_id} n={self.line_number} concept={self.concept!r}>"


class ValidationLog(Base):
    __tablename__ = "validation_logs"
    __table_args__ = (Index("ix_validation_logs_invoice_run", "invoice_id", "run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rule_code: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[Severity] = mapped_column(enum_column(Severity, 10), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str | None] = mapped_column(String(100))
    line_number: Mapped[int | None] = mapped_column(Integer)
    expected_value: Mapped[str | None] = mapped_column(String(100))
    actual_value: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="validation_logs")

    def __repr__(self) -> str:
        return f"<ValidationLog invoice_id={self.invoice_id} rule={self.rule_code} passed={self.passed}>"
