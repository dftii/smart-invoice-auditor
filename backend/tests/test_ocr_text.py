"""Text recognised by OCR in the browser (``ocr_text``): images and scanned PDFs in demo mode."""

import io
from decimal import Decimal
from pathlib import Path

import pypdf
import pytest

from app.core.config import Settings
from app.models import ExtractionMethod
from app.services.extraction import DemoParserStrategy, ExtractionStrategy, InvoiceExtractor
from app.services.extraction.base import MAX_OCR_TEXT_CHARS
from app.services.extraction.demo import OCR_WARNING
from app.services.extraction.text_parser import normalize_ocr_text
from app.services.extractor import get_extractor
from tests.test_api import API, FakeExtractor

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def sample_text(name: str = "01_factura_valida_iva21.pdf") -> str:
    """The text of a generated sample: the same text an accurate OCR pass over its image would return."""
    reader = pypdf.PdfReader(SAMPLES / name)
    return "\n".join(page.extract_text(extraction_mode="layout") or "" for page in reader.pages)


def scanned_pdf() -> bytes:
    """A valid PDF without any text layer, like the output of a scanner."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def upload_with_ocr(client, content: bytes, filename: str, content_type: str, ocr_text: str | None):
    data = {"ocr_text": ocr_text} if ocr_text is not None else None
    return client.post(f"{API}/invoices/upload", files={"file": (filename, content, content_type)}, data=data)


# --------------------------------------------------------------------------- DemoParserStrategy


def test_image_with_ocr_text_is_parsed_from_the_recognised_text():
    result = DemoParserStrategy().extract(PNG_BYTES, "foto.png", "image/png", ocr_text=sample_text())
    assert result.method == ExtractionMethod.DEMO
    assert result.is_synthetic is False
    assert result.strategy_name == "demo-regex-parser+ocr"
    assert result.warnings[0] == OCR_WARNING
    assert result.invoice_number
    assert result.supplier.tax_id
    assert result.lines


def test_image_ocr_result_matches_the_text_pdf_result():
    from_ocr = DemoParserStrategy().extract(PNG_BYTES, "foto.png", "image/png", ocr_text=sample_text())
    from_pdf = DemoParserStrategy().extract(
        (SAMPLES / "01_factura_valida_iva21.pdf").read_bytes(), "f.pdf", "application/pdf"
    )
    assert from_ocr.invoice_number == from_pdf.invoice_number
    assert from_ocr.total_amount == from_pdf.total_amount
    assert from_ocr.supplier.tax_id == from_pdf.supplier.tax_id


def test_scanned_pdf_with_ocr_text_is_parsed():
    result = DemoParserStrategy().extract(scanned_pdf(), "escaneo.pdf", "application/pdf", ocr_text=sample_text())
    assert result.is_synthetic is False
    assert result.strategy_name.endswith("+ocr")


def test_pdf_text_layer_takes_precedence_over_ocr_text():
    content = (SAMPLES / "01_factura_valida_iva21.pdf").read_bytes()
    result = DemoParserStrategy().extract(content, "f.pdf", "application/pdf", ocr_text=sample_text("06_factura_face_dir3.pdf"))
    assert result.strategy_name == "demo-regex-parser"
    assert OCR_WARNING not in result.warnings


def test_unreadable_ocr_text_falls_back_to_a_synthetic_invoice_with_the_reason():
    result = DemoParserStrategy().extract(PNG_BYTES, "foto.png", "image/png", ocr_text="l1l1 ~~ |||\n...\n%%\n,,")
    assert result.is_synthetic is True
    assert any("OCR no pudo interpretarse" in warning for warning in result.warnings)


def test_image_without_ocr_text_says_no_text_was_received():
    result = DemoParserStrategy().extract(PNG_BYTES, "foto.png", "image/png")
    assert result.is_synthetic is True
    assert any("no se ha recibido texto reconocido por OCR" in warning for warning in result.warnings)


# Real Tesseract.js output (spa, LSTM) for samples/07_factura_imagen_demo.png: note "n*%" instead of "nº".
TESSERACT_SAMPLE_07 = """FACTURA

Factura n*%: 2024-0001 Fecha: 2024-03-15
Emisor: Desarrollos Web Mediterráneo, S.L. (CIF B46271896)
Cliente: Comercial Ribera, S.A. (CIF A28730547)

Desarrollo de tienda online (fase 1) x1 2.400,00 €

Mantenimiento mensual del servidor x3 256,50 €

Base imponible: 2.656,50 €
IVA: 557,87 €
TOTAL: 3.214,37 €
"""


def test_real_tesseract_output_of_the_sample_image_is_read_completely():
    result = DemoParserStrategy().extract(PNG_BYTES, "foto.png", "image/png", ocr_text=TESSERACT_SAMPLE_07)
    assert result.is_synthetic is False
    assert result.invoice_number == "2024-0001"
    assert str(result.issue_date) == "2024-03-15"
    assert (result.supplier.name, result.supplier.tax_id) == ("Desarrollos Web Mediterráneo, S.L.", "B46271896")
    assert (result.buyer.name, result.buyer.tax_id) == ("Comercial Ribera, S.A.", "A28730547")
    assert [(line.concept, line.quantity, line.line_base) for line in result.lines] == [
        ("Desarrollo de tienda online (fase 1)", Decimal("1"), Decimal("2400.00")),
        ("Mantenimiento mensual del servidor", Decimal("3"), Decimal("256.50")),
    ]
    assert result.lines[1].unit_price == Decimal("85.5")
    assert (result.tax_base, result.total_vat, result.total_amount) == (
        Decimal("2656.50"),
        Decimal("557.87"),
        Decimal("3214.37"),
    )


def test_sample_image_read_by_ocr_passes_the_audit(client):
    response = upload_with_ocr(
        client, (SAMPLES / "07_factura_imagen_demo.png").read_bytes(), "foto.png", "image/png", TESSERACT_SAMPLE_07
    )
    assert response.status_code == 201, response.text
    assert response.json()["audit"]["status"] == "VALIDATED"
    assert response.json()["invoice"]["is_synthetic"] is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Factura n*%: 7", "Factura nº: 7"),
        ("FACTURA N°: A-1", "FACTURA nº: A-1"),
        ("Factura n.: 12", "Factura nº: 12"),
        ("N°. de factura: 33", "Nº de factura: 33"),
        ("Concepto | Cantidad | Importe", "Concepto    Cantidad    Importe"),
        ("No hay retención", "No hay retención"),
    ],
)
def test_normalize_ocr_text_fixes_common_misreads(raw, expected):
    assert normalize_ocr_text(raw) == expected


def test_party_name_written_before_the_tax_id_on_the_same_line():
    text = "\n".join(
        [
            "Factura nº: F-9 Fecha: 01/02/2026",
            "Proveedor: Talleres Ruiz, S.L., CIF: B46271896",
            "Cliente: Comercial Ribera, S.A. (NIF A28730547)",
            "Reparación x2 300,00",
            "Base imponible: 300,00",
            "IVA 21%: 63,00",
            "Total: 363,00",
        ]
    )
    result = DemoParserStrategy().extract(PNG_BYTES, "foto.png", "image/png", ocr_text=text)
    assert result.supplier.name == "Talleres Ruiz, S.L."
    assert result.buyer.name == "Comercial Ribera, S.A."
    assert result.lines[0].vat_rate == Decimal("21")
    assert result.lines[0].unit_price == Decimal("150")


# --------------------------------------------------------------------------- InvoiceExtractor


class _RecordingStrategy(ExtractionStrategy):
    """Strategy without OCR support (like the AI one): its extract() has no ocr_text parameter."""

    name = "recording"
    method = ExtractionMethod.AI

    def __init__(self) -> None:
        self.calls = 0

    def extract(self, content, filename, mime_type):
        self.calls += 1
        return DemoParserStrategy().extract(
            (SAMPLES / "01_factura_valida_iva21.pdf").read_bytes(), "f.pdf", "application/pdf"
        )


def test_strategies_without_ocr_support_never_receive_the_text():
    strategy = _RecordingStrategy()
    InvoiceExtractor(Settings(), strategy=strategy).extract(PNG_BYTES, "foto.png", "image/png", ocr_text="texto")
    assert strategy.calls == 1


def test_extractor_trims_and_caps_the_ocr_text(monkeypatch):
    received: list[str] = []
    original = DemoParserStrategy.extract

    def spy(self, content, filename, mime_type, ocr_text=None):
        received.append(ocr_text)
        return original(self, content, filename, mime_type, ocr_text=ocr_text)

    monkeypatch.setattr(DemoParserStrategy, "extract", spy)
    extractor = InvoiceExtractor(Settings(), strategy=DemoParserStrategy())
    extractor.extract(PNG_BYTES, "foto.png", "image/png", ocr_text="   \n  ")
    extractor.extract(PNG_BYTES, "foto.png", "image/png", ocr_text="x" * (MAX_OCR_TEXT_CHARS + 50))
    assert received[0] is None
    assert len(received[1]) == MAX_OCR_TEXT_CHARS


# --------------------------------------------------------------------------- API


def test_upload_forwards_the_ocr_text_to_the_extractor(client):
    fake = FakeExtractor()
    client.app.dependency_overrides[get_extractor] = lambda: fake
    response = upload_with_ocr(client, PNG_BYTES, "foto.png", "image/png", "FACTURA 1")
    assert response.status_code == 201, response.text
    assert fake.ocr_texts == ["FACTURA 1"]


def test_upload_without_ocr_text_passes_none(client):
    fake = FakeExtractor()
    client.app.dependency_overrides[get_extractor] = lambda: fake
    response = upload_with_ocr(client, PNG_BYTES, "foto.png", "image/png", None)
    assert response.status_code == 201, response.text
    assert fake.ocr_texts == [None]


def test_upload_rejects_oversized_ocr_text(client):
    response = upload_with_ocr(client, PNG_BYTES, "foto.png", "image/png", "x" * (MAX_OCR_TEXT_CHARS + 1))
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("content", "filename", "content_type"),
    [
        pytest.param(PNG_BYTES, "foto.png", "image/png", id="photo"),
        pytest.param(scanned_pdf(), "escaneo.pdf", "application/pdf", id="scanned-pdf"),
    ],
)
def test_upload_in_demo_mode_reads_the_invoice_from_the_ocr_text(client, content, filename, content_type):
    response = upload_with_ocr(client, content, filename, content_type, sample_text())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["extraction_method"] == "DEMO"
    assert body["invoice"]["is_synthetic"] is False
    assert body["extraction_warnings"][0] == OCR_WARNING
    assert body["audit"]["status"] == "VALIDATED"
