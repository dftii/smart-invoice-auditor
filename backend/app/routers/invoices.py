"""Invoice endpoints: upload, manual creation, listing, correction, audit, export and file download."""

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.config import Settings, get_settings
from app.core.errors import InvalidInputError, NotFoundError, PayloadTooLargeError, UnsupportedMediaTypeError
from app.db.database import get_db
from app.models import Invoice, ValidationLog
from app.schemas import (
    AuditReport,
    BulkRevalidateRequest,
    BulkRevalidateResponse,
    InvoiceCreate,
    InvoiceFilters,
    InvoiceRead,
    InvoiceSummary,
    InvoiceUpdate,
    Page,
    UploadResponse,
    ValidationLogRead,
)
from app.services import invoice_service as service
from app.services.auditor import InvoiceAuditor
from app.services.csv_exporter import export_invoices_csv
from app.services.extractor import InvoiceExtractor, detect_mime_type, get_extractor
from app.services.face_exporter import FacturaeExporter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invoices", tags=["Facturas"])

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
ExtractorDep = Annotated[InvoiceExtractor, Depends(get_extractor)]
FilterParams = Annotated[InvoiceFilters, Query()]
PageParam = Annotated[int, Query(ge=1, description="Número de página (empieza en 1)")]
PageSizeParam = Annotated[int, Query(ge=1, le=200, description="Elementos por página")]

NOT_FOUND_RESPONSE = {404: {"description": "Factura no encontrada"}}


def _require_invoice(db: Session, invoice_id: int) -> Invoice:
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise NotFoundError(f"Factura {invoice_id} no encontrada")
    return invoice


def _attachment(filename: str) -> dict[str, str]:
    safe = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_" for ch in filename)
    return {"Content-Disposition": f'attachment; filename="{safe}"'}


# --------------------------------------------------------------------------- Creation


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Subir una factura (PDF o imagen), extraer sus datos y auditarla",
    responses={
        413: {"description": "El archivo supera el tamaño máximo"},
        415: {"description": "Tipo de archivo no soportado"},
        422: {"description": "No se pudo extraer la factura"},
    },
)
def upload_invoice(
    file: Annotated[UploadFile, File(description="Factura en PDF, PNG, JPEG o WEBP")],
    db: DbSession,
    settings: SettingsDep,
    extractor: ExtractorDep,
) -> UploadResponse:
    limit = settings.max_upload_size_bytes
    content = file.file.read(limit + 1)
    if len(content) > limit:
        raise PayloadTooLargeError(f"El archivo supera el tamaño máximo de {settings.max_upload_size_mb} MB")
    if not content:
        raise InvalidInputError("El archivo está vacío")

    filename = service.safe_filename(file.filename) or "factura"
    mime_type = detect_mime_type(content, filename)
    if mime_type not in settings.allowed_mime_types:
        allowed = ", ".join(settings.allowed_mime_types)
        raise UnsupportedMediaTypeError(f"Tipo de archivo no soportado ({mime_type}). Formatos admitidos: {allowed}")

    file_hash = service.sha256_hex(content)
    stored: service.StoredFile | None = None
    try:
        extraction = extractor.extract(content, filename, mime_type)
        stored = service.store_upload(content, file_hash, mime_type, settings)
        invoice = service.create_invoice_from_extraction(
            db,
            extraction,
            filename=filename,
            mime_type=mime_type,
            file_hash=file_hash,
            file_path=stored.relative_path,
        )
        report = InvoiceAuditor(db).audit(invoice)
        db.commit()
    except Exception:
        db.rollback()
        if stored is not None and stored.created:
            service.remove_stored_file(stored.relative_path, settings)
        raise

    logger.info("Invoice %s uploaded (%s, status %s)", invoice.id, extraction.strategy_name, report.status.value)
    return UploadResponse(
        invoice=service.read_invoice(db, invoice.id),
        audit=report,
        extraction_method=extraction.method,
        extraction_warnings=extraction.warnings,
    )


@router.post(
    "",
    response_model=InvoiceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una factura manualmente y auditarla",
)
def create_invoice(payload: InvoiceCreate, db: DbSession) -> InvoiceRead:
    try:
        invoice = service.create_invoice_manual(db, payload)
        InvoiceAuditor(db).audit(invoice)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return service.read_invoice(db, invoice.id)


# --------------------------------------------------------------------------- Listing / export


@router.get("", response_model=Page[InvoiceSummary], summary="Listar facturas con filtros y paginación")
def list_invoices(
    db: DbSession,
    filters: FilterParams,
    page: PageParam = 1,
    page_size: PageSizeParam = 20,
) -> Page[InvoiceSummary]:
    return service.paginate_invoices(db, filters, page, page_size)


@router.get(
    "/export/csv",
    summary="Exportar facturas a CSV (compatible con Excel)",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "Archivo CSV UTF-8 con BOM y separador ';'"}},
)
def export_csv(
    db: DbSession,
    filters: FilterParams,
    include_lines: Annotated[bool, Query(description="Una fila por línea de factura")] = False,
) -> Response:
    statement = service.query_invoices(filters)
    if include_lines:
        statement = statement.options(selectinload(Invoice.lines))
    invoices = db.scalars(statement).all()
    body = export_invoices_csv(invoices, include_lines=include_lines)
    filename = f"facturas_{date.today():%Y%m%d}.csv"
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers=_attachment(filename),
    )


@router.post(
    "/revalidate",
    response_model=BulkRevalidateResponse,
    summary="Revalidar varias facturas (o todas)",
)
def bulk_revalidate(payload: BulkRevalidateRequest, db: DbSession) -> BulkRevalidateResponse:
    statement = select(Invoice).options(joinedload(Invoice.supplier), selectinload(Invoice.lines)).order_by(Invoice.id)
    if payload.invoice_ids is not None:
        statement = statement.where(Invoice.id.in_(payload.invoice_ids))
    if payload.status is not None:
        statement = statement.where(Invoice.audit_status == payload.status)
    try:
        auditor = InvoiceAuditor(db)
        reports = [auditor.audit(invoice) for invoice in db.scalars(statement).all()]
        db.commit()
    except Exception:
        db.rollback()
        raise
    return BulkRevalidateResponse(processed=len(reports), reports=reports)


# --------------------------------------------------------------------------- Single invoice


@router.get("/{invoice_id}", response_model=InvoiceRead, summary="Detalle de una factura", responses=NOT_FOUND_RESPONSE)
def get_invoice(invoice_id: int, db: DbSession) -> InvoiceRead:
    return service.read_invoice(db, invoice_id)


@router.get(
    "/{invoice_id}/logs",
    response_model=list[ValidationLogRead],
    summary="Registro de validaciones de una factura",
    responses=NOT_FOUND_RESPONSE,
)
def get_invoice_logs(
    invoice_id: int,
    db: DbSession,
    all_runs: Annotated[bool, Query(description="Incluir también las auditorías anteriores")] = False,
) -> list[ValidationLog]:
    invoice = _require_invoice(db, invoice_id)
    return service.all_logs(db, invoice) if all_runs else service.latest_logs(db, invoice)


@router.patch(
    "/{invoice_id}",
    response_model=InvoiceRead,
    summary="Corregir una factura y volver a auditarla",
    responses=NOT_FOUND_RESPONSE,
)
def update_invoice(invoice_id: int, payload: InvoiceUpdate, db: DbSession) -> InvoiceRead:
    try:
        invoice = service.get_invoice(db, invoice_id)
        service.apply_invoice_update(db, invoice, payload)
        InvoiceAuditor(db).audit(invoice)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return service.read_invoice(db, invoice_id)


@router.delete(
    "/{invoice_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar una factura y su archivo original",
    responses=NOT_FOUND_RESPONSE,
)
def delete_invoice(invoice_id: int, db: DbSession, settings: SettingsDep) -> Response:
    invoice = _require_invoice(db, invoice_id)
    stored_path = invoice.source_file_path
    try:
        db.delete(invoice)
        db.commit()
    except Exception:
        db.rollback()
        raise
    if not service.is_file_referenced(db, stored_path):
        service.remove_stored_file(stored_path, settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{invoice_id}/revalidate",
    response_model=AuditReport,
    summary="Volver a ejecutar la auditoría de una factura",
    responses=NOT_FOUND_RESPONSE,
)
def revalidate_invoice(invoice_id: int, db: DbSession) -> AuditReport:
    try:
        invoice = service.get_invoice(db, invoice_id)
        report = InvoiceAuditor(db).audit(invoice)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return report


@router.get(
    "/{invoice_id}/facturae",
    summary="Descargar la factura en formato Facturae 3.2.2 (FACe)",
    response_class=Response,
    responses={
        200: {"content": {"application/xml": {}}, "description": "XML Facturae"},
        404: {"description": "Factura no encontrada"},
        409: {"description": "La factura no está en estado VALIDATED"},
    },
)
def download_facturae(
    invoice_id: int,
    db: DbSession,
    settings: SettingsDep,
    sign: Annotated[bool | None, Query(description="Firmar con XAdES; por defecto, solo si hay certificado")] = None,
) -> Response:
    invoice = service.get_invoice(db, invoice_id)
    exporter = FacturaeExporter(settings)
    xml = exporter.to_xml(invoice, sign=sign)
    service.mark_exported(db, invoice)
    return Response(content=xml, media_type="application/xml", headers=_attachment(exporter.filename_for(invoice)))


@router.get(
    "/{invoice_id}/file",
    summary="Descargar el archivo original de la factura",
    response_class=FileResponse,
    responses=NOT_FOUND_RESPONSE,
)
def download_original_file(invoice_id: int, db: DbSession, settings: SettingsDep) -> FileResponse:
    invoice = _require_invoice(db, invoice_id)
    path = service.resolve_stored_path(invoice.source_file_path, settings)
    if path is None:
        raise NotFoundError("Esta factura no tiene archivo original disponible")
    return FileResponse(
        path,
        media_type=invoice.source_mime_type or "application/octet-stream",
        filename=invoice.source_filename or path.name,
        content_disposition_type="inline",
    )
