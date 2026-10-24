"""CSV export of invoices, formatted for Excel (Spain): ';' delimiter, UTF-8 BOM, ISO dates, dot decimals."""

import csv
import io
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal

from app.models import AuditStatus, ExtractionMethod, Invoice, InvoiceLine

UTF8_BOM = "﻿"
DELIMITER = ";"
# Largest export served in one response (the whole file is built in memory).
MAX_EXPORT_ROWS = 50_000

STATUS_LABELS = {
    AuditStatus.PENDING: "Pendiente",
    AuditStatus.VALIDATED: "Validada",
    AuditStatus.DISCREPANCY: "Discrepancia",
    AuditStatus.DUPLICATE: "Duplicada",
}
METHOD_LABELS = {
    ExtractionMethod.AI: "IA",
    ExtractionMethod.DEMO: "Demo",
    ExtractionMethod.MANUAL: "Manual",
}

INVOICE_COLUMNS = [
    "ID",
    "Serie",
    "Número",
    "Fecha emisión",
    "Fecha operación",
    "Fecha vencimiento",
    "Proveedor",
    "NIF/CIF proveedor",
    "Cliente",
    "NIF/CIF cliente",
    "Moneda",
    "Base imponible",
    "IVA",
    "IRPF",
    "Total",
    "Estado",
    "Errores",
    "Avisos",
    "Resumen auditoría",
    "Método extracción",
    "Archivo origen",
    "Fecha auditoría (UTC)",
]
LINE_COLUMNS = [
    "Línea",
    "Concepto",
    "Cantidad",
    "Precio unitario",
    "Descuento %",
    "IVA %",
    "IRPF %",
    "Base línea",
    "Cuota IVA",
    "Cuota IRPF",
]

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _text(value: str | None) -> str:
    """Free text cell; neutralises spreadsheet formula injection from extracted/user-supplied content."""
    if not value:
        return ""
    return f"'{value}" if value.startswith(_FORMULA_PREFIXES) else value


def _date(value: date | None) -> str:
    return value.isoformat() if value else ""


def _datetime(value: datetime | None) -> str:
    if not value:
        return ""
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.replace(microsecond=0).isoformat()


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _number(value: Decimal) -> str:
    return format(value, "f")


def _invoice_cells(invoice: Invoice) -> list[str]:
    return [
        str(invoice.id),
        _text(invoice.invoice_series),
        _text(invoice.invoice_number),
        _date(invoice.issue_date),
        _date(invoice.operation_date),
        _date(invoice.due_date),
        _text(invoice.supplier.name),
        _text(invoice.supplier_tax_id),
        _text(invoice.buyer_name),
        _text(invoice.buyer_tax_id),
        _text(invoice.currency),
        _money(invoice.tax_base),
        _money(invoice.total_vat),
        _money(invoice.total_irpf),
        _money(invoice.total_amount),
        STATUS_LABELS[invoice.audit_status],
        str(invoice.error_count),
        str(invoice.warning_count),
        _text(invoice.audit_summary),
        METHOD_LABELS[invoice.extraction_method],
        _text(invoice.source_filename),
        _datetime(invoice.last_audited_at),
    ]


def _line_cells(line: InvoiceLine | None) -> list[str]:
    if line is None:
        return [""] * len(LINE_COLUMNS)
    return [
        str(line.line_number),
        _text(line.concept),
        _number(line.quantity),
        _number(line.unit_price),
        _money(line.discount_rate),
        _money(line.vat_rate),
        _money(line.irpf_rate),
        _money(line.line_base),
        _money(line.vat_amount),
        _money(line.irpf_amount),
    ]


def export_invoices_csv(invoices: Iterable[Invoice], include_lines: bool = False) -> str:
    """Render invoices as CSV text (BOM included). With include_lines there is one row per invoice line."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=DELIMITER, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(INVOICE_COLUMNS + (LINE_COLUMNS if include_lines else []))
    for invoice in invoices:
        base = _invoice_cells(invoice)
        if not include_lines:
            writer.writerow(base)
            continue
        for line in invoice.lines or [None]:
            writer.writerow(base + _line_cells(line))
    return UTF8_BOM + buffer.getvalue()
