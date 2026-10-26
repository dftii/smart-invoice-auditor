"""Invoice endpoints: upload, manual creation, listing, correction, audit, export and file download."""

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.config import Settings, get_settings
from app.core.errors import (
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from app.models import Invoice, ValidationLog
from app.routers.params import DbSession, FilterParams, InvoiceIdPath, PageParam, PageSizeParam
from app.schemas import (
    AuditReport,
    BulkRevalidateRequest,
    BulkRevalidateResponse,
    InvoiceCreate,
    InvoiceRead,
    InvoiceSummary,
    InvoiceUpdate,
    Page,
    UploadResponse,
    ValidationLogRead,
)
from app.services import invoice_service as service
from app.services.auditor import InvoiceAuditor
from app.services.csv_exporter import MAX_EXPORT_ROWS, export_invoices_csv
from app.services.extraction.base import MAX_OCR_TEXT_CHARS
from app.services.extractor import InvoiceExtractor, detect_mime_type, get_extractor
from app.services.face_exporter import FacturaeExporter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invoices", tags=["Facturas"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
ExtractorDep = Annotated[InvoiceExtractor, Depends(get_extractor)]

NOT_FOUND_RESPONSE = {404: {"description": "Factura no encontrada"}}
SIGNING_REQUIRES_API_KEY = (
    "La firma electrónica de facturas requiere proteger la API: configure API_KEY en el servidor "
    "y envíe la cabecera X-API-Key"
)


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
    ocr_text: Annotated[
        str | None,
        Form(
            max_length=MAX_OCR_TEXT_CHARS,
            description="Texto reconocido por OCR en el cliente (imágenes y PDF escaneados). Opcional: el Modo Demo "
            "lo usa cuando el documento no tiene texto propio; el Modo IA lee el documento directamente.",
        ),
    ] = None,
) -> UploadResponse:
    # The Content-Length header was already checked by UploadSizeLimitMiddleware; this guards chunked uploads.
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
        extraction = extractor.extract(content, filename, mime_type, ocr_text=ocr_text)
        service.ensure_not_rectifying(
            extraction.tax_base, extraction.total_vat, extraction.total_irpf, extraction.total_amount
        )
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
    responses={
        200: {"content": {"text/csv": {}}, "description": "Archivo CSV UTF-8 con BOM y separador ';'"},
        413: {"description": "La exportación supera el máximo de filas"},
    },
)
def export_csv(
    db: DbSession,
    filters: FilterParams,
    include_lines: Annotated[bool, Query(description="Una fila por línea de factura")] = False,
) -> Response:
    statement = service.query_invoices(filters)
    if include_lines:
        statement = statement.options(selectinload(Invoice.lines))
    invoices = db.scalars(statement.limit(MAX_EXPORT_ROWS + 1)).all()
    rows = sum(max(len(invoice.lines), 1) for invoice in invoices) if include_lines else len(invoices)
    if rows > MAX_EXPORT_ROWS:
        limit_text = f"{MAX_EXPORT_ROWS:_}".replace("_", " ")
        raise PayloadTooLargeError(f"La exportación supera el máximo de {limit_text} filas: aplique filtros para reducirla")
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
def get_invoice(invoice_id: InvoiceIdPath, db: DbSession) -> InvoiceRead:
    return service.read_invoice(db, invoice_id)


@router.get(
    "/{invoice_id}/logs",
    response_model=list[ValidationLogRead],
    summary="Registro de validaciones de una factura",
    responses=NOT_FOUND_RESPONSE,
)
def get_invoice_logs(
    invoice_id: InvoiceIdPath,
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
def update_invoice(invoice_id: InvoiceIdPath, payload: InvoiceUpdate, db: DbSession) -> InvoiceRead:
    try:
        invoice = service.get_invoice(db, invoice_id)
        # Duplicate detection depends on other invoices, so those sharing the old or the new key are re-audited.
        stale_ids = set(service.related_invoice_ids(db, invoice))
        service.apply_invoice_update(db, invoice, payload)
        stale_ids.update(service.related_invoice_ids(db, invoice))
        InvoiceAuditor(db).audit(invoice)
        service.reaudit_invoices(db, stale_ids)
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
def delete_invoice(invoice_id: InvoiceIdPath, db: DbSession, settings: SettingsDep) -> Response:
    invoice = _require_invoice(db, invoice_id)
    stored_path = invoice.source_file_path
    try:
        stale_ids = service.related_invoice_ids(db, invoice)
        db.delete(invoice)
        db.flush()
        # Invoices that were flagged as duplicates of this one may no longer be.
        service.reaudit_invoices(db, stale_ids)
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
def revalidate_invoice(invoice_id: InvoiceIdPath, db: DbSession) -> AuditReport:
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
        403: {"description": "La firma requiere API_KEY configurada en el servidor"},
        404: {"description": "Factura no encontrada"},
        409: {"description": "La factura no está en estado VALIDATED"},
    },
)
def download_facturae(
    invoice_id: InvoiceIdPath,
    db: DbSession,
    settings: SettingsDep,
    sign: Annotated[
        bool | None,
        Query(description="Firmar con XAdES; por defecto, solo si hay certificado y la API está protegida con API_KEY"),
    ] = None,
) -> Response:
    protected = settings.api_key is not None  # the router dependency already checked the X-API-Key header
    if sign is True and not protected:
        raise ForbiddenError(SIGNING_REQUIRES_API_KEY)
    # Without an API key anyone could sign with the server certificate, so the default becomes "unsigned".
    effective_sign = sign if sign is not None else (settings.facturae_cert_path is not None and protected)
    invoice = service.get_invoice(db, invoice_id)
    exporter = FacturaeExporter(settings)
    xml = exporter.to_xml(invoice, sign=effective_sign)
    service.mark_exported(db, invoice)
    return Response(content=xml, media_type="application/xml", headers=_attachment(exporter.filename_for(invoice)))


@router.get(
    "/{invoice_id}/file",
    summary="Descargar el archivo original de la factura",
    response_class=FileResponse,
    responses=NOT_FOUND_RESPONSE,
)
def download_original_file(invoice_id: InvoiceIdPath, db: DbSession, settings: SettingsDep) -> FileResponse:
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
