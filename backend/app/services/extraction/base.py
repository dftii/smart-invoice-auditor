"""Shared types of the extraction engine: errors, strategy interface and MIME detection."""

from abc import ABC, abstractmethod

from app.models import ExtractionMethod
from app.schemas import ExtractionResult

MAX_PDF_PAGES = 10
MAX_RAW_TEXT_CHARS = 20_000
# Text recognised by OCR in the user's browser (images, scanned PDFs): about 6,000 characters per page, 10 pages.
MAX_OCR_TEXT_CHARS = 60_000

PDF_MIME = "application/pdf"
IMAGE_MIMES = ("image/png", "image/jpeg", "image/webp")
_EXTENSION_MIMES = {
    ".pdf": PDF_MIME,
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class ExtractionError(Exception):
    """The document could not be converted into an ExtractionResult."""


class InvalidDocumentError(ExtractionError):
    """The uploaded file is empty, too large or of an unsupported type."""



def detect_mime_type(content: bytes, filename: str) -> str:
    """Detect the MIME type from magic bytes, falling back to the file extension."""
    head = content[:1024]
    if b"%PDF-" in head:
        return PDF_MIME
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    lowered = (filename or "").lower()
    for extension, mime in _EXTENSION_MIMES.items():
        if lowered.endswith(extension):
            return mime
    return "application/octet-stream"




class ExtractionStrategy(ABC):
    name: str
    method: ExtractionMethod
    # Strategies that set this receive ``ocr_text=`` (text recognised in the browser) as a keyword argument.
    accepts_ocr_text: bool = False

    @abstractmethod
    def extract(self, content: bytes, filename: str, mime_type: str) -> ExtractionResult:
        """Convert the raw document into a structured ExtractionResult or raise ExtractionError."""

