"""Offline extraction: pypdf text + regex parser, with a synthetic invoice as last resort."""

from __future__ import annotations

import io
import logging

import pypdf

from app.models import ExtractionMethod
from app.schemas import ExtractionResult
from app.services.extraction.base import (
    IMAGE_MIMES,
    MAX_OCR_TEXT_CHARS,
    MAX_PDF_PAGES,
    PDF_MIME,
    ExtractionStrategy,
    detect_mime_type,
)
from app.services.extraction.synthetic import SyntheticInvoiceFactory
from app.services.extraction.text_parser import SpanishInvoiceTextParser, normalize_ocr_text

logger = logging.getLogger(__name__)

def _pdf_texts(content: bytes) -> list[str]:
    """Layout-preserving and plain text of a PDF; empty list when unreadable."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            return []
        pages = reader.pages[:MAX_PDF_PAGES]
        layout = "\n".join(page.extract_text(extraction_mode="layout") or "" for page in pages)
        plain = "\n".join(page.extract_text() or "" for page in pages)
    except Exception as exc:  # noqa: BLE001 - pypdf raises many unrelated types on corrupt files
        logger.info("PDF text extraction failed: %s", exc)
        return []
    return [text for text in (layout, plain) if text.strip()]


OCR_WARNING = (
    "Datos leídos con OCR a partir de la imagen: revisa el NIF/CIF, las fechas y los importes antes de exportar."
)


class DemoParserStrategy(ExtractionStrategy):
    """Offline extraction: pypdf + regex parser, with a deterministic synthetic invoice as last resort.

    Images and scanned PDFs have no text layer: when the client sends the text it recognised with OCR
    (``ocr_text``, Tesseract in the browser), the same parser reads it before falling back to the synthetic invoice.
    """

    name = "demo-parser"
    method = ExtractionMethod.DEMO
    accepts_ocr_text = True

    def __init__(self) -> None:
        self._parser = SpanishInvoiceTextParser()

    def extract(self, content: bytes, filename: str, mime_type: str, ocr_text: str | None = None) -> ExtractionResult:
        media_type = mime_type if mime_type in (PDF_MIME, *IMAGE_MIMES) else detect_mime_type(content, filename)
        reason = "El documento es una imagen y no se ha recibido texto reconocido por OCR."
        if media_type == PDF_MIME:
            texts = _pdf_texts(content)
            reason = "El PDF no contiene texto legible (documento escaneado o dañado)."
            for text in texts:
                parsed = self._parser.parse(text)
                if parsed is not None:
                    return parsed
                reason = "El texto del PDF no pudo interpretarse como una factura española."
        if ocr_text and ocr_text.strip():
            parsed = self._parser.parse(normalize_ocr_text(ocr_text[:MAX_OCR_TEXT_CHARS]))
            if parsed is not None:
                parsed.strategy_name = f"{parsed.strategy_name}+ocr"
                parsed.warnings.insert(0, OCR_WARNING)
                return parsed
            reason = "El texto reconocido por OCR no pudo interpretarse como una factura española."
        return self.synthesize(content, reason)

    @staticmethod
    def synthesize(content: bytes, reason: str) -> ExtractionResult:
        result = SyntheticInvoiceFactory(content).build()
        result.warnings.append(
            f"{reason} Modo demo sin IA: se ha generado una factura sintética determinista a partir del contenido "
            "del archivo; los datos mostrados NO proceden del documento, por lo que la factura no podrá validarse "
            "ni exportarse. Configure ANTHROPIC_API_KEY para extraer los datos reales."
        )
        return result

