"""Invoice and supplier persistence, querying, file storage and ORM -> schema conversion."""

import hashlib
import logging
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, contains_eager, joinedload, selectinload

from app.core.config import Settings
from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.models import (
    ExtractionMethod,
    Invoice,
    InvoiceLine,
    PersonType,
    Supplier,
    TaxIdType,
    ValidationLog,
)
from app.schemas import (
    ExtractedLine,
    ExtractedParty,
    ExtractionResult,
    InvoiceCreate,
    InvoiceFilters,
    InvoiceLineCreate,
    InvoiceRead,
    InvoiceSummary,
    InvoiceUpdate,
    Page,
    SupplierBase,
    SupplierCreate,
    SupplierUpdate,
    SupplierWithStats,
)
from app.services.tax_id import TaxIdValidation, normalize_tax_id, validate_tax_id

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")
QUANTITY_STEP = Decimal("0.0001")
UNIT_PRICE_STEP = Decimal("0.000001")
RAW_TEXT_LIMIT = 20_000

MIME_EXTENSIONS = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}

SORT_COLUMNS = {
    "issue_date": Invoice.issue_date,
    "total_amount": Invoice.total_amount,
    "created_at": Invoice.created_at,
    "invoice_number": Invoice.invoice_number,
    "audit_status": Invoice.audit_status,
}

NON_NULLABLE_UPDATE_FIELDS = {
    "invoice_number",
    "issue_date",
    "supplier_tax_id",
    "buyer_country_code",
    "tax_base",
    "total_vat",
    "total_irpf",
    "total_amount",
}
MONEY_UPDATE_FIELDS = {"tax_base", "total_vat", "total_irpf", "total_amount"}
PLAIN_UPDATE_FIELDS = (
    "invoice_number",
    "invoice_series",
    "issue_date",
    "operation_date",
    "due_date",
    "buyer_name",
    "buyer_tax_id",
    "buyer_address",
    "buyer_postal_code",
    "buyer_city",
    "buyer_province",
    "buyer_country_code",
    "dir3_accounting_office",
    "dir3_managing_body",
    "dir3_processing_unit",
    "notes",
)


# --------------------------------------------------------------------------- Helpers


def _quantize(value: Decimal, step: Decimal) -> Decimal:
    try:
        return value.quantize(step, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise InvalidInputError(f"Importe fuera de rango: {value}") from exc


def money(value: Decimal) -> Decimal:
    return _quantize(value, CENT)


def _clip(value: str | None, length: int) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned[:length] if cleaned else None


def _like_pattern(term: str) -> str:
    escaped = term.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# --------------------------------------------------------------------------- Suppliers


def _placeholder_tax_id(name: str) -> str:
    """Stable pseudo tax id for documents whose issuer has none (the audit flags the invoice anyway)."""
    return "SINID" + hashlib.sha1(name.lower().encode("utf-8")).hexdigest()[:12].upper()


def _apply_validation(supplier: Supplier, validation: TaxIdValidation | None, fallback: PersonType | None) -> None:
    if validation is None:
        supplier.tax_id_type = TaxIdType.UNKNOWN
        supplier.tax_id_valid = False
        supplier.person_type = fallback or supplier.person_type or PersonType.LEGAL_ENTITY
        return
    supplier.tax_id_type = validation.tax_id_type
    supplier.tax_id_valid = validation.is_valid
    supplier.person_type = validation.person_type if validation.is_valid else (fallback or validation.person_type)


def upsert_supplier(db: Session, party: ExtractedParty | SupplierBase) -> Supplier:
    """Find a supplier by normalized tax id or create it; missing contact data of existing ones is completed."""
    name = _clip(party.name, 255) or "Proveedor desconocido"
    has_tax_id = bool(party.tax_id and normalize_tax_id(party.tax_id))
    validation = validate_tax_id(party.tax_id) if has_tax_id else None
    tax_id = _clip(validation.normalized, 20) if validation else _placeholder_tax_id(name)

    supplier = db.scalar(select(Supplier).where(Supplier.tax_id == tax_id))
    if supplier is None:
        supplier = Supplier(name=name, tax_id=tax_id, country_code=_clip(party.country_code, 3) or "ESP")
        db.add(supplier)
    _apply_validation(supplier, validation, getattr(party, "person_type", None))

    for field, length in (
        ("address", 255),
        ("postal_code", 10),
        ("city", 100),
        ("province", 100),
        ("email", 255),
        ("phone", 30),
    ):
        if not getattr(supplier, field):
            setattr(supplier, field, _clip(getattr(party, field), length))
    db.flush()
    return supplier


def _supplier_stats_statement() -> Select[Any]:
    stats = (
        select(
            Invoice.supplier_id.label("supplier_id"),
            func.count(Invoice.id).label("invoice_count"),
            func.sum(Invoice.total_amount).label("total_invoiced"),
        )
        .group_by(Invoice.supplier_id)
        .subquery()
    )
    return select(
        Supplier,
        func.coalesce(stats.c.invoice_count, 0),
        func.coalesce(stats.c.total_invoiced, Decimal("0.00")),
    ).outerjoin(stats, stats.c.supplier_id == Supplier.id)


def _to_supplier_with_stats(row: Any) -> SupplierWithStats:
    supplier, invoice_count, total_invoiced = row
    data = {name: getattr(supplier, name) for name in SupplierWithStats.model_fields if hasattr(supplier, name)}
    data["invoice_count"] = int(invoice_count or 0)
    data["total_invoiced"] = money(Decimal(total_invoiced or 0))
    return SupplierWithStats.model_validate(data)


def get_supplier(db: Session, supplier_id: int) -> Supplier:
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise NotFoundError(f"Proveedor {supplier_id} no encontrado")
    return supplier


def get_supplier_with_stats(db: Session, supplier_id: int) -> SupplierWithStats:
    row = db.execute(_supplier_stats_statement().where(Supplier.id == supplier_id)).first()
    if row is None:
        raise NotFoundError(f"Proveedor {supplier_id} no encontrado")
    return _to_supplier_with_stats(row)


def list_suppliers(db: Session, search: str | None, page: int, page_size: int) -> Page[SupplierWithStats]:
    statement = _supplier_stats_statement()
    count_statement = select(func.count(Supplier.id))
    if search and search.strip():
        pattern = _like_pattern(search)
        condition = or_(Supplier.name.ilike(pattern, escape="\\"), Supplier.tax_id.ilike(pattern, escape="\\"))
        statement = statement.where(condition)
        count_statement = count_statement.where(condition)
    statement = statement.order_by(Supplier.name, Supplier.id)
    rows, total = paginate(db, statement, count_statement, page=page, page_size=page_size)
    return build_page([_to_supplier_with_stats(row) for row in rows], total, page, page_size)


def create_supplier(db: Session, payload: SupplierCreate) -> Supplier:
    validation = validate_tax_id(payload.tax_id)
    if db.scalar(select(Supplier.id).where(Supplier.tax_id == validation.normalized)) is not None:
        raise ConflictError(f"Ya existe un proveedor con el NIF/CIF {validation.normalized}")
    return upsert_supplier(db, payload)


def update_supplier(db: Session, supplier: Supplier, payload: SupplierUpdate) -> Supplier:
    for field in payload.model_fields_set:
        value = getattr(payload, field)
        if value is None and field in ("name", "country_code", "person_type"):
            raise InvalidInputError(f"El campo {field} no puede ser nulo")
        setattr(supplier, field, value)
    db.flush()
    return supplier


# --------------------------------------------------------------------------- Lines


def _build_lines(lines: Sequence[InvoiceLineCreate | ExtractedLine]) -> list[InvoiceLine]:
    """Create ORM lines renumbered 1..n, honouring any explicit line_number ordering."""
    ordered = sorted(
        enumerate(lines),
        key=lambda pair: (getattr(pair[1], "line_number", None) or math.inf, pair[0]),
    )
    built = []
    for number, (_, line) in enumerate(ordered, start=1):
        built.append(
            InvoiceLine(
                line_number=number,
                concept=line.concept.strip()[:500],
                quantity=_quantize(line.quantity, QUANTITY_STEP),
                unit_of_measure=getattr(line, "unit_of_measure", "01"),
                unit_price=_quantize(line.unit_price, UNIT_PRICE_STEP),
                discount_rate=money(line.discount_rate),
                vat_rate=money(line.vat_rate),
                irpf_rate=money(line.irpf_rate),
                line_base=money(line.line_base),
                vat_amount=money(line.vat_amount),
                irpf_amount=money(line.irpf_amount),
            )
        )
    return built


# --------------------------------------------------------------------------- Creation


def create_invoice_from_extraction(
    db: Session,
    extraction: ExtractionResult,
    *,
    filename: str | None,
    mime_type: str | None,
    file_hash: str | None,
    file_path: str | None,
) -> Invoice:
    supplier = upsert_supplier(db, extraction.supplier)
    buyer = extraction.buyer
    printed_tax_id = normalize_tax_id(extraction.supplier.tax_id) if extraction.supplier.tax_id else ""
    raw = extraction.model_dump(mode="json")
    if raw.get("raw_text"):
        raw["raw_text"] = raw["raw_text"][:RAW_TEXT_LIMIT]

    invoice = Invoice(
        supplier=supplier,
        supplier_tax_id=printed_tax_id[:20],
        invoice_number=_clip(extraction.invoice_number, 50) or "S/N",
        invoice_series=_clip(extraction.invoice_series, 20),
        issue_date=extraction.issue_date,
        operation_date=extraction.operation_date,
        due_date=extraction.due_date,
        currency=(_clip(extraction.currency, 3) or "EUR").upper(),
        buyer_name=_clip(buyer.name, 255),
        buyer_tax_id=_clip(buyer.tax_id, 20),
        buyer_address=_clip(buyer.address, 255),
        buyer_postal_code=_clip(buyer.postal_code, 10),
        buyer_city=_clip(buyer.city, 100),
        buyer_province=_clip(buyer.province, 100),
        buyer_country_code=_clip(buyer.country_code, 3) or "ESP",
        dir3_accounting_office=_clip(extraction.dir3_accounting_office, 20),
        dir3_managing_body=_clip(extraction.dir3_managing_body, 20),
        dir3_processing_unit=_clip(extraction.dir3_processing_unit, 20),
        tax_base=money(extraction.tax_base),
        total_vat=money(extraction.total_vat),
        total_irpf=money(extraction.total_irpf),
        total_amount=money(extraction.total_amount),
        extraction_method=extraction.method,
        extraction_confidence=extraction.confidence,
        raw_extraction=raw,
        source_filename=_clip(filename, 255),
        source_mime_type=_clip(mime_type, 100),
        source_file_hash=file_hash,
        source_file_path=file_path,
        lines=_build_lines(extraction.lines),
    )
    db.add(invoice)
    db.flush()
    return invoice


def create_invoice_manual(db: Session, payload: InvoiceCreate) -> Invoice:
    supplier = upsert_supplier(db, payload.supplier)
    invoice = Invoice(
        supplier=supplier,
        supplier_tax_id=supplier.tax_id,
        invoice_number=payload.invoice_number.strip(),
        invoice_series=_clip(payload.invoice_series, 20),
        issue_date=payload.issue_date,
        operation_date=payload.operation_date,
        due_date=payload.due_date,
        currency=payload.currency.upper(),
        buyer_name=payload.buyer_name,
        buyer_tax_id=payload.buyer_tax_id,
        buyer_address=payload.buyer_address,
        buyer_postal_code=payload.buyer_postal_code,
        buyer_city=payload.buyer_city,
        buyer_province=payload.buyer_province,
        buyer_country_code=payload.buyer_country_code,
        dir3_accounting_office=payload.dir3_accounting_office,
        dir3_managing_body=payload.dir3_managing_body,
        dir3_processing_unit=payload.dir3_processing_unit,
        tax_base=money(payload.tax_base),
        total_vat=money(payload.total_vat),
        total_irpf=money(payload.total_irpf),
        total_amount=money(payload.total_amount),
        extraction_method=ExtractionMethod.MANUAL,
        notes=payload.notes,
        lines=_build_lines(payload.lines),
    )
    db.add(invoice)
    db.flush()
    return invoice


# --------------------------------------------------------------------------- Update


def _relink_supplier(db: Session, invoice: Invoice, new_tax_id: str) -> None:
    """Point the invoice to the supplier owning `new_tax_id`, renaming or cloning records when needed."""
    validation = validate_tax_id(new_tax_id)
    tax_id = _clip(validation.normalized, 20)
    if not tax_id:
        raise InvalidInputError("El NIF/CIF del proveedor no puede estar vacío")

    old = invoice.supplier
    target = db.scalar(select(Supplier).where(Supplier.tax_id == tax_id))
    if target is None:
        other_invoices = db.scalar(
            select(func.count(Invoice.id)).where(Invoice.supplier_id == old.id, Invoice.id != invoice.id)
        )
        if other_invoices:
            target = Supplier(
                name=old.name,
                tax_id=tax_id,
                address=old.address,
                postal_code=old.postal_code,
                city=old.city,
                province=old.province,
                country_code=old.country_code,
                email=old.email,
                phone=old.phone,
            )
            db.add(target)
        else:
            old.tax_id = tax_id
            target = old
    _apply_validation(target, validation, None)
    invoice.supplier = target
    invoice.supplier_tax_id = tax_id
    db.flush()


def apply_invoice_update(db: Session, invoice: Invoice, payload: InvoiceUpdate) -> Invoice:
    """Apply a partial correction. `lines`, when sent, replaces all existing lines."""
    fields = payload.model_fields_set
    for field in fields & NON_NULLABLE_UPDATE_FIELDS:
        if getattr(payload, field) is None:
            raise InvalidInputError(f"El campo {field} no puede ser nulo")

    for field in PLAIN_UPDATE_FIELDS:
        if field in fields:
            value = getattr(payload, field)
            setattr(invoice, field, value.strip() if isinstance(value, str) else value)
    for field in MONEY_UPDATE_FIELDS & fields:
        setattr(invoice, field, money(getattr(payload, field)))

    if "supplier_tax_id" in fields and payload.supplier_tax_id != invoice.supplier_tax_id:
        _relink_supplier(db, invoice, payload.supplier_tax_id or "")

    if "lines" in fields and payload.lines is not None:
        new_lines = _build_lines(payload.lines)
        invoice.lines.clear()
        db.flush()  # delete old rows first: (invoice_id, line_number) is unique
        invoice.lines.extend(new_lines)
    db.flush()
    return invoice


# --------------------------------------------------------------------------- Loading


def get_invoice(db: Session, invoice_id: int) -> Invoice:
    invoice = db.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id)
        .options(joinedload(Invoice.supplier), selectinload(Invoice.lines))
    )
    if invoice is None:
        raise NotFoundError(f"Factura {invoice_id} no encontrada")
    return invoice


def latest_logs(db: Session, invoice: Invoice) -> list[ValidationLog]:
    if not invoice.last_audit_run_id:
        return []
    return list(
        db.scalars(
            select(ValidationLog)
            .where(ValidationLog.invoice_id == invoice.id, ValidationLog.run_id == invoice.last_audit_run_id)
            .order_by(ValidationLog.id)
        )
    )


def all_logs(db: Session, invoice: Invoice) -> list[ValidationLog]:
    return list(
        db.scalars(select(ValidationLog).where(ValidationLog.invoice_id == invoice.id).order_by(ValidationLog.id))
    )


def read_invoice(db: Session, invoice_id: int) -> InvoiceRead:
    invoice = get_invoice(db, invoice_id)
    return to_invoice_read(invoice, latest_logs(db, invoice))


# --------------------------------------------------------------------------- Querying


def _apply_filters(statement: Select[Any], filters: InvoiceFilters) -> Select[Any]:
    if filters.status is not None:
        statement = statement.where(Invoice.audit_status == filters.status)
    if filters.supplier_id is not None:
        statement = statement.where(Invoice.supplier_id == filters.supplier_id)
    if filters.tax_id and normalize_tax_id(filters.tax_id):
        statement = statement.where(Invoice.supplier_tax_id == normalize_tax_id(filters.tax_id))
    if filters.date_from is not None:
        statement = statement.where(Invoice.issue_date >= filters.date_from)
    if filters.date_to is not None:
        statement = statement.where(Invoice.issue_date <= filters.date_to)
    if filters.min_total is not None:
        statement = statement.where(Invoice.total_amount >= filters.min_total)
    if filters.max_total is not None:
        statement = statement.where(Invoice.total_amount <= filters.max_total)
    if filters.extraction_method is not None:
        statement = statement.where(Invoice.extraction_method == filters.extraction_method)
    if filters.search and filters.search.strip():
        pattern = _like_pattern(filters.search)
        statement = statement.where(
            or_(
                Invoice.invoice_number.ilike(pattern, escape="\\"),
                Supplier.name.ilike(pattern, escape="\\"),
                Invoice.supplier_tax_id.ilike(pattern, escape="\\"),
            )
        )
    return statement


def query_invoices(filters: InvoiceFilters) -> Select[tuple[Invoice]]:
    """Filtered, sorted statement with the supplier eagerly loaded through the join."""
    column = SORT_COLUMNS[filters.sort_by]
    descending = filters.sort_dir == "desc"
    ordering = [column.desc() if descending else column.asc(), Invoice.id.desc() if descending else Invoice.id.asc()]
    statement = select(Invoice).join(Invoice.supplier).options(contains_eager(Invoice.supplier))
    return _apply_filters(statement, filters).order_by(*ordering)


def count_invoices_statement(filters: InvoiceFilters) -> Select[tuple[int]]:
    statement = select(func.count(Invoice.id)).select_from(Invoice).join(Invoice.supplier)
    return _apply_filters(statement, filters)


def paginate(
    db: Session,
    statement: Select[Any],
    count_statement: Select[Any],
    *,
    page: int,
    page_size: int,
    scalars: bool = False,
) -> tuple[list[Any], int]:
    """Run `statement` for one page and `count_statement` for the total (scalars=True unwraps single-entity rows)."""
    total = db.scalar(count_statement) or 0
    paged = statement.limit(page_size).offset((page - 1) * page_size)
    rows = list(db.scalars(paged)) if scalars else list(db.execute(paged))
    return rows, total


def build_page(items: list[Any], total: int, page: int, page_size: int) -> Page[Any]:
    return Page(items=items, total=total, page=page, page_size=page_size, pages=math.ceil(total / page_size))


def paginate_invoices(db: Session, filters: InvoiceFilters, page: int, page_size: int) -> Page[InvoiceSummary]:
    invoices, total = paginate(
        db, query_invoices(filters), count_invoices_statement(filters), page=page, page_size=page_size, scalars=True
    )
    return build_page([to_invoice_summary(invoice) for invoice in invoices], total, page, page_size)


# --------------------------------------------------------------------------- Serialisation


def to_invoice_read(invoice: Invoice, logs: Sequence[ValidationLog] | None = None) -> InvoiceRead:
    """Build the detail schema; validation_logs only contains the latest audit run."""
    if logs is None:
        logs = [log for log in invoice.validation_logs if log.run_id == invoice.last_audit_run_id]
    data = {name: getattr(invoice, name) for name in InvoiceRead.model_fields if name != "validation_logs"}
    data["validation_logs"] = list(logs)
    return InvoiceRead.model_validate(data)


def to_invoice_summary(invoice: Invoice) -> InvoiceSummary:
    data = {name: getattr(invoice, name) for name in InvoiceSummary.model_fields if name != "supplier_name"}
    data["supplier_name"] = invoice.supplier.name
    return InvoiceSummary.model_validate(data)


# --------------------------------------------------------------------------- File storage


@dataclass(frozen=True)
class StoredFile:
    relative_path: str
    created: bool


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def store_upload(content: bytes, file_hash: str, mime_type: str, settings: Settings) -> StoredFile:
    """Persist content as <upload_dir>/<yyyy>/<mm>/<hash><ext>; the client filename never touches the path."""
    extension = MIME_EXTENSIONS.get(mime_type, ".bin")
    now = datetime.now(UTC)
    relative = Path(f"{now:%Y}") / f"{now:%m}" / f"{file_hash}{extension}"
    target = settings.upload_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return StoredFile(relative.as_posix(), created=False)
    temporary = target.with_name(target.name + ".part")
    temporary.write_bytes(content)
    os.replace(temporary, target)
    return StoredFile(relative.as_posix(), created=True)


def resolve_stored_path(relative_path: str | None, settings: Settings) -> Path | None:
    """Absolute path of a stored file, or None if missing or outside the upload directory."""
    if not relative_path:
        return None
    root = settings.upload_dir.resolve()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def remove_stored_file(relative_path: str | None, settings: Settings) -> None:
    path = resolve_stored_path(relative_path, settings)
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        logger.warning("Could not remove stored file %s", path)


def is_file_referenced(db: Session, relative_path: str | None, *, excluding_invoice_id: int | None = None) -> bool:
    if not relative_path:
        return False
    statement = select(func.count(Invoice.id)).where(Invoice.source_file_path == relative_path)
    if excluding_invoice_id is not None:
        statement = statement.where(Invoice.id != excluding_invoice_id)
    return bool(db.scalar(statement))


def safe_filename(name: str | None) -> str | None:
    """Last path component of a client-supplied name with control characters removed (display only)."""
    if not name:
        return None
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(ch for ch in base if ch.isprintable()).strip()
    return cleaned[:255] or None
