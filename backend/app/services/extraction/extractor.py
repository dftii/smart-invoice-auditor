"""Orchestrator: validates the upload, picks the strategy and applies the failure policy."""

from __future__ import annotations

import logging
from typing import Literal

from app.core.config import Settings, get_settings
from app.models import ExtractionMethod
from app.schemas import ExtractionResult
from app.services.extraction.ai_strategy import ClaudeVisionStrategy
from app.services.extraction.base import (
    MAX_OCR_TEXT_CHARS,
    ExtractionError,
    ExtractionStrategy,
    InvalidDocumentError,
    detect_mime_type,
)
from app.services.extraction.demo import DemoParserStrategy

logger = logging.getLogger(__name__)

class InvoiceExtractor:
    def __init__(self, settings: Settings | None = None, strategy: ExtractionStrategy | None = None) -> None:
        self._settings = settings or get_settings()
        if strategy is None:
            strategy = ClaudeVisionStrategy(self._settings) if self._settings.ai_enabled else DemoParserStrategy()
        self._strategy = strategy

    @property
    def strategy(self) -> ExtractionStrategy:
        return self._strategy

    @property
    def mode(self) -> Literal["AI", "DEMO"]:
        return "AI" if self._strategy.method == ExtractionMethod.AI else "DEMO"

    def extract(
        self, content: bytes, filename: str, mime_type: str, ocr_text: str | None = None
    ) -> ExtractionResult:
        """``ocr_text``: text the client recognised with OCR (images, scanned PDFs); only text-based strategies use it."""
        effective_mime = self._validate(content, filename, mime_type)
        ocr_text = (ocr_text or "").strip()[:MAX_OCR_TEXT_CHARS] or None
        try:
            result = self._run(self._strategy, content, filename, effective_mime, ocr_text)
        except Exception as exc:
            if self._strategy.method != ExtractionMethod.AI or not self._settings.ai_fallback_to_demo:
                if isinstance(exc, ExtractionError):
                    raise
                logger.exception("Unexpected extraction failure")
                raise ExtractionError("Error inesperado durante la extracción") from exc
            logger.warning("AI extraction failed, falling back to demo mode: %s", exc)
            result = self._run(DemoParserStrategy(), content, filename, effective_mime, ocr_text)
            result.warnings.insert(0, "La extracción con IA falló; se ha usado el modo demo como alternativa.")
        validate_result(result)
        return result

    @staticmethod
    def _run(
        strategy: ExtractionStrategy, content: bytes, filename: str, mime_type: str, ocr_text: str | None
    ) -> ExtractionResult:
        if ocr_text and getattr(strategy, "accepts_ocr_text", False):
            return strategy.extract(content, filename, mime_type, ocr_text=ocr_text)  # type: ignore[call-arg]
        return strategy.extract(content, filename, mime_type)

    def _validate(self, content: bytes, filename: str, mime_type: str) -> str:
        if not content:
            raise InvalidDocumentError("El archivo está vacío")
        limit = self._settings.max_upload_size_bytes
        if len(content) > limit:
            raise InvalidDocumentError(f"El archivo supera el tamaño máximo permitido de {limit // (1024 * 1024)} MB")
        detected = detect_mime_type(content, filename)
        declared = (mime_type or "").split(";")[0].strip().lower()
        effective = detected if detected != "application/octet-stream" else declared
        if effective not in self._settings.allowed_mime_types:
            allowed = ", ".join(self._settings.allowed_mime_types)
            raise InvalidDocumentError(
                f"Tipo de archivo no admitido ({effective or 'desconocido'}). Admitidos: {allowed}"
            )
        return effective


def validate_result(result: ExtractionResult) -> None:
    """Reject extractions that cannot be trusted: a line with zero quantity is never a valid invoice line."""
    for number, line in enumerate(result.lines, start=1):
        if line.quantity == 0:
            raise ExtractionError(f"La línea {number} tiene cantidad cero")


def get_extractor() -> InvoiceExtractor:
    """FastAPI dependency: extractor bound to the current settings (AI when an API key is configured)."""
    return InvoiceExtractor(get_settings())
