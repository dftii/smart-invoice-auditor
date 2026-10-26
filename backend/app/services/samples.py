"""Catalogue of the sample invoices offered to try the application ("probar con un ejemplo").

The files live in ``Settings.samples_dir`` (backend/samples, written by scripts/generate_sample_invoices.py).
Only the files listed here are served, so a request can never reach any other path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.models import AuditStatus
from app.schemas import SampleInvoice


@dataclass(frozen=True)
class _Entry:
    filename: str
    title: str
    description: str
    expected_status: AuditStatus


CATALOGUE: tuple[_Entry, ...] = (
    _Entry(
        "01_factura_valida_iva21.pdf",
        "Factura correcta",
        "Factura en PDF con IVA al 21 % que cuadra al céntimo.",
        AuditStatus.VALIDATED,
    ),
    _Entry(
        "02_factura_iva_mixto_21_10_4.pdf",
        "IVA mixto",
        "Líneas al 21 %, 10 % y 4 % de IVA en la misma factura.",
        AuditStatus.VALIDATED,
    ),
    _Entry(
        "03_factura_profesional_irpf15.pdf",
        "Profesional con IRPF",
        "Factura de un profesional con retención de IRPF del 15 %.",
        AuditStatus.VALIDATED,
    ),
    _Entry(
        "06_factura_face_dir3.pdf",
        "Para FACe",
        "Factura a una administración pública con sus códigos DIR3, lista para exportar a Facturae.",
        AuditStatus.VALIDATED,
    ),
    _Entry(
        "07_factura_imagen_demo.png",
        "Foto de factura",
        "Imagen sin texto: en Modo Demo se lee con OCR en el navegador.",
        AuditStatus.VALIDATED,
    ),
    _Entry(
        "04_factura_error_calculo_linea.pdf",
        "Error de cálculo",
        "Una línea con el importe mal calculado: la auditoría la detecta.",
        AuditStatus.DISCREPANCY,
    ),
    _Entry(
        "05_factura_cif_proveedor_invalido.pdf",
        "CIF inválido",
        "El CIF del emisor tiene mal el dígito de control.",
        AuditStatus.DISCREPANCY,
    ),
)

_MEDIA_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}


def media_type_of(filename: str) -> str:
    return _MEDIA_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")


def list_samples(settings: Settings) -> list[SampleInvoice]:
    """The catalogue entries whose file exists, in catalogue order (valid invoices first)."""
    samples: list[SampleInvoice] = []
    for entry in CATALOGUE:
        path = settings.samples_dir / entry.filename
        if not path.is_file():
            continue
        samples.append(
            SampleInvoice(
                filename=entry.filename,
                title=entry.title,
                description=entry.description,
                media_type=media_type_of(entry.filename),
                size_bytes=path.stat().st_size,
                expected_status=entry.expected_status,
            )
        )
    return samples


def sample_path(settings: Settings, filename: str) -> Path | None:
    """Path of a catalogue sample, or None when the name is not in the catalogue or the file is missing."""
    if filename not in {entry.filename for entry in CATALOGUE}:
        return None
    path = settings.samples_dir / filename
    return path if path.is_file() else None
