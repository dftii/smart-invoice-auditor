"""Hybrid invoice extraction engine (Strategy Pattern).

* ``ClaudeVisionStrategy`` (ai_strategy)  - real AI extraction through the Anthropic API.
* ``DemoParserStrategy``   (demo)          - offline mode: pypdf text + Spanish-invoice regex parser; when the
                                             document has no usable text it fabricates a *synthetic* invoice
                                             (flagged ``is_synthetic``: it can never be validated or exported).
* ``InvoiceExtractor``     (extractor)     - validates the upload, picks the strategy and applies the failure policy.
"""

from app.services.extraction.ai_strategy import (
    RECORD_INVOICE_TOOL,
    SYSTEM_PROMPT,
    TOOL_NAME,
    ClaudeVisionStrategy,
    result_from_tool_input,
)
from app.services.extraction.base import (
    IMAGE_MIMES,
    PDF_MIME,
    ExtractionError,
    ExtractionStrategy,
    InvalidDocumentError,
    detect_mime_type,
)
from app.services.extraction.demo import DemoParserStrategy
from app.services.extraction.extractor import InvoiceExtractor, get_extractor, validate_result
from app.services.extraction.parsing import parse_decimal
from app.services.extraction.synthetic import synthetic_discrepancy_kind
from app.services.extraction.text_parser import SpanishInvoiceTextParser

__all__ = [
    "IMAGE_MIMES",
    "PDF_MIME",
    "RECORD_INVOICE_TOOL",
    "SYSTEM_PROMPT",
    "TOOL_NAME",
    "ClaudeVisionStrategy",
    "DemoParserStrategy",
    "ExtractionError",
    "ExtractionStrategy",
    "InvalidDocumentError",
    "InvoiceExtractor",
    "SpanishInvoiceTextParser",
    "detect_mime_type",
    "get_extractor",
    "parse_decimal",
    "result_from_tool_input",
    "synthetic_discrepancy_kind",
    "validate_result",
]
