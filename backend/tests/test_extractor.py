"""Tests for the hybrid extraction engine: demo parser, synthetic generator, AI strategy (mocked) and fallback."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest

from app.core.config import Settings
from app.models import ExtractionMethod
from app.services import extractor as extractor_module
from app.services.extractor import (
    ClaudeVisionStrategy,
    DemoParserStrategy,
    ExtractionError,
    InvalidDocumentError,
    InvoiceExtractor,
    SpanishInvoiceTextParser,
    detect_mime_type,
    get_extractor,
    parse_decimal,
    synthetic_discrepancy_kind,
)

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover - older anthropic releases depend on httpx
    import httpx

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "generate_sample_invoices.py"
PDF = "application/pdf"
CENT = Decimal("0.01")

# Independent expectation table: filename -> (lines, base, vat, irpf, total)
EXPECTED_TOTALS = {
    "01_factura_valida_iva21.pdf": (2, "2656.50", "557.87", "0.00", "3214.37"),
    "02_factura_iva_mixto_21_10_4.pdf": (4, "518.80", "55.03", "0.00", "573.83"),
    "03_factura_profesional_irpf15.pdf": (2, "1500.00", "315.00", "225.00", "1590.00"),
    "04_factura_error_calculo_linea.pdf": (2, "420.00", "88.20", "0.00", "508.20"),
    "05_factura_cif_proveedor_invalido.pdf": (2, "3980.50", "483.91", "0.00", "4464.41"),
    "06_factura_face_dir3.pdf": (3, "2270.00", "476.70", "0.00", "2746.70"),
}


def _load_script():
    spec = importlib.util.spec_from_file_location("generate_sample_invoices", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_sample_invoices"] = module
    spec.loader.exec_module(module)
    return module


sample_script = _load_script()


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def valid_tax_id(value: str) -> bool:
    """Reference NIF/NIE/CIF validator, independent of the application code."""
    value = value.upper()
    if len(value) != 9:
        return False
    if value[0].isdigit() or value[0] in "XYZ":
        number = value[:8].replace("X", "0").replace("Y", "1").replace("Z", "2")
        return number.isdigit() and value[8] == "TRWAGMYFPDXBNJZSQVHLCKE"[int(number) % 23]
    digits = value[1:8]
    if value[0] not in "ABCDEFGHJNPQRSUVW" or not digits.isdigit():
        return False
    odd = sum(sum(divmod(int(d) * 2, 10)) for d in digits[0::2])
    even = sum(int(d) for d in digits[1::2])
    control = (10 - (odd + even) % 10) % 10
    if value[0] in "PQRSNW":
        return value[8] == "JABCDEFGHI"[control]
    if value[0] in "ABEH":
        return value[8] == str(control)
    return value[8] in (str(control), "JABCDEFGHI"[control])


@pytest.fixture(scope="module")
def samples_dir(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("samples")
    sample_script.generate_all(directory)
    return directory


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# --------------------------------------------------------------------------- Helpers: mime and decimals


def test_detect_mime_type_by_magic_bytes_and_extension():
    assert detect_mime_type(b"%PDF-1.7\n...", "whatever.bin") == PDF
    assert detect_mime_type(b"\x89PNG\r\n\x1a\n0000", "a.dat") == "image/png"
    assert detect_mime_type(b"\xff\xd8\xff\xe0JFIF", "a.dat") == "image/jpeg"
    assert detect_mime_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ", "a.dat") == "image/webp"
    assert detect_mime_type(b"plain", "scan.JPG") == "image/jpeg"
    assert detect_mime_type(b"plain", "notes.txt") == "application/octet-stream"
    # Magic bytes win over a misleading extension.
    assert detect_mime_type(b"%PDF-1.4", "photo.png") == PDF


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.234,56", "1234.56"),
        ("1234.56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("2.400,00 €", "2400.00"),
        ("-150,00", "-150.00"),
        ("21 %", "21"),
        ("3,5", "3.5"),
        ("1.234.567,89", "1234567.89"),
        ("12.40", "12.40"),
        ("0.125", "0.125"),
        ("1.000", "1000"),
        ("573.83 EUR", "573.83"),
    ],
)
def test_parse_decimal_accepts_spanish_and_plain_notations(raw, expected):
    assert parse_decimal(raw) == Decimal(expected)


# --------------------------------------------------------------------------- Demo parser on generated samples


@pytest.mark.parametrize("spec", sample_script.SAMPLES, ids=lambda s: s.filename)
def test_demo_parser_parses_every_generated_sample(spec, samples_dir):
    path = samples_dir / spec.filename
    result = DemoParserStrategy().extract(path.read_bytes(), spec.filename, PDF)

    assert result.method == ExtractionMethod.DEMO
    assert result.strategy_name == "demo-regex-parser"
    assert result.warnings == []
    assert result.invoice_number == spec.number
    assert result.invoice_series == spec.series
    assert result.issue_date == spec.issue_date
    assert result.due_date == spec.due_date
    assert result.operation_date == spec.operation_date
    assert result.currency == "EUR"

    assert result.supplier.name == spec.supplier.name
    assert result.supplier.tax_id == spec.supplier.tax_id
    assert result.supplier.address == spec.supplier.address
    assert result.supplier.postal_code == spec.supplier.postal_code
    assert result.supplier.city == spec.supplier.city
    assert result.supplier.province == spec.supplier.province
    assert result.supplier.email == spec.supplier.email
    assert result.buyer.name == spec.buyer.name
    assert result.buyer.tax_id == spec.buyer.tax_id
    assert result.buyer.postal_code == spec.buyer.postal_code

    expected_dir3 = spec.dir3 or (None, None, None)
    assert (
        result.dir3_accounting_office,
        result.dir3_managing_body,
        result.dir3_processing_unit,
    ) == expected_dir3

    assert len(result.lines) == len(spec.lines)
    for parsed, expected in zip(result.lines, spec.lines, strict=True):
        assert parsed.concept == expected.concept
        assert parsed.quantity == expected.quantity
        assert parsed.unit_price == expected.unit_price
        assert parsed.vat_rate == expected.vat_rate
        assert parsed.irpf_rate == expected.irpf_rate
        assert parsed.line_base == expected.base
        assert parsed.vat_amount == expected.vat_amount
        assert parsed.irpf_amount == expected.irpf_amount

    assert result.tax_base == spec.tax_base
    assert result.total_vat == spec.total_vat
    assert result.total_irpf == spec.total_irpf
    assert result.total_amount == spec.total_amount
    assert result.confidence >= 0.9
    assert result.raw_text


@pytest.mark.parametrize(("filename", "expected"), EXPECTED_TOTALS.items())
def test_generated_samples_have_exact_expected_totals(filename, expected, samples_dir):
    line_count, base, vat, irpf, total = expected
    result = DemoParserStrategy().extract((samples_dir / filename).read_bytes(), filename, PDF)
    assert len(result.lines) == line_count
    assert result.tax_base == Decimal(base)
    assert result.total_vat == Decimal(vat)
    assert result.total_irpf == Decimal(irpf)
    assert result.total_amount == Decimal(total)


def test_sample_tax_ids_are_valid_except_the_intentional_one():
    for spec in sample_script.SAMPLES:
        assert valid_tax_id(spec.buyer.tax_id), spec.filename
        assert valid_tax_id(spec.supplier.tax_id) is (spec.filename != "05_factura_cif_proveedor_invalido.pdf")


def test_math_error_sample_keeps_the_printed_wrong_line_amount(samples_dir):
    spec = next(s for s in sample_script.SAMPLES if s.filename.startswith("04_"))
    result = DemoParserStrategy().extract((samples_dir / spec.filename).read_bytes(), spec.filename, PDF)
    wrong = result.lines[0]
    assert wrong.quantity * wrong.unit_price == Decimal("300.00")
    assert wrong.line_base == Decimal("330.00")


def test_mixed_vat_sample_contains_the_three_rates(samples_dir):
    result = DemoParserStrategy().extract(
        (samples_dir / "02_factura_iva_mixto_21_10_4.pdf").read_bytes(), "02.pdf", PDF
    )
    assert {line.vat_rate for line in result.lines} == {Decimal(21), Decimal(10), Decimal(4)}


def test_generator_script_writes_pdfs_and_png(samples_dir):
    names = {path.name for path in samples_dir.iterdir()}
    assert {spec.filename for spec in sample_script.SAMPLES} <= names
    assert sample_script.IMAGE_SAMPLE_FILENAME in names
    assert len(sample_script.SAMPLES) >= 5
    png = (samples_dir / sample_script.IMAGE_SAMPLE_FILENAME).read_bytes()
    assert detect_mime_type(png, "x") == "image/png"


def test_repository_samples_directory_is_populated():
    samples = Path(__file__).resolve().parents[1] / "samples"
    assert len(list(samples.glob("*.pdf"))) >= 5
    assert list(samples.glob("*.png"))


def test_image_sample_falls_back_to_synthetic_with_warning(samples_dir):
    png = (samples_dir / sample_script.IMAGE_SAMPLE_FILENAME).read_bytes()
    result = DemoParserStrategy().extract(png, "factura.png", "image/png")
    assert result.method == ExtractionMethod.DEMO
    assert result.strategy_name == "demo-synthetic-generator"
    assert any("sintética" in warning for warning in result.warnings)
    assert result.confidence < 0.5


def test_unparseable_pdf_text_falls_back_to_synthetic():
    result = DemoParserStrategy().extract(b"%PDF-1.4 this is not a real pdf", "broken.pdf", PDF)
    assert result.strategy_name == "demo-synthetic-generator"
    assert result.warnings


# --------------------------------------------------------------------------- Text parser variants

SPANISH_TEXT = """\
FACTURA
Nº factura: 2024/88
Fecha: 5 de marzo de 2024
Vencimiento: 04/04/2024

Proveedor
Ibérica de Suministros S.L.
N.I.F.: ES-B-46271896
Calle Alcalá 12, 28014 Madrid (Madrid)

Cliente: Asesoría López y Asociados S.L.
CIF: A28730547

Descripción                         Uds.   P. unitario   Dto.   IVA   Importe
Servicio de mensajería urgente         10         7,25   10 %   21 %    65,25
Embalaje especial                       2        15,00          10 %    30,00

Subtotal: 95,25
IVA 21%: 13,70
IVA 10%: 3,00
Total IVA: 16,70
TOTAL A PAGAR: 111,95 €
"""


def test_text_parser_handles_free_form_spanish_layout():
    result = SpanishInvoiceTextParser().parse(SPANISH_TEXT)
    assert result is not None
    assert result.invoice_number == "2024/88"
    assert result.issue_date == date(2024, 3, 5)
    assert result.due_date == date(2024, 4, 4)
    assert result.supplier.name == "Ibérica de Suministros S.L."
    assert result.supplier.tax_id == "B46271896"
    assert result.supplier.postal_code == "28014"
    assert result.buyer.name == "Asesoría López y Asociados S.L."
    assert result.buyer.tax_id == "A28730547"
    assert [line.line_base for line in result.lines] == [Decimal("65.25"), Decimal("30.00")]
    assert result.lines[0].discount_rate == Decimal(10)
    assert result.lines[1].vat_rate == Decimal(10)
    assert result.tax_base == Decimal("95.25")
    assert result.total_vat == Decimal("16.70")
    assert result.total_amount == Decimal("111.95")


def test_text_parser_computes_missing_line_amounts_and_totals():
    text = """\
Factura nº: F-77
Fecha de emisión: 2024-06-01
Emisor
Estudio Gráfico Norte S.L.
CIF: B19283746
Cliente
Marta Gil Sanz
NIF: 12345678Z
Concepto  Cantidad  Precio  IVA
Diseño de logotipo  2  150.00  21
Impresión de tarjetas  500  0.10  21
"""
    result = SpanishInvoiceTextParser().parse(text)
    assert result is not None
    assert result.invoice_number == "F-77"
    assert result.issue_date == date(2024, 6, 1)
    assert [line.line_base for line in result.lines] == [Decimal("300.00"), Decimal("50.00")]
    assert [line.vat_amount for line in result.lines] == [Decimal("63.00"), Decimal("10.50")]
    assert result.tax_base == Decimal("350.00")
    assert result.total_vat == Decimal("73.50")
    assert result.total_amount == Decimal("423.50")
    assert len(result.warnings) == 3


def test_text_parser_returns_none_for_non_invoices():
    assert (
        SpanishInvoiceTextParser().parse("Acta de la reunión\nSe acordó aplazar\nla decisión\nhasta nuevo aviso")
        is None
    )
    assert SpanishInvoiceTextParser().parse("") is None


# --------------------------------------------------------------------------- Synthetic generator


def inconsistencies(result) -> set[str]:
    found: set[str] = set()
    for line in result.lines:
        if line.line_base != money(line.quantity * line.unit_price * (100 - line.discount_rate) / 100):
            found.add("LINE_BASE")
        if line.vat_amount != money(line.line_base * line.vat_rate / 100):
            found.add("LINE_VAT")
        if line.irpf_amount != money(line.line_base * line.irpf_rate / 100):
            found.add("LINE_IRPF")
        if line.vat_rate not in (Decimal(21), Decimal(10), Decimal(4), Decimal(0)):
            found.add("VAT_RATE")
    if result.tax_base != sum(line.line_base for line in result.lines):
        found.add("TAX_BASE_SUM")
    if result.total_vat != sum(line.vat_amount for line in result.lines):
        found.add("VAT_TOTAL")
    if result.total_irpf != sum(line.irpf_amount for line in result.lines):
        found.add("IRPF_TOTAL")
    if result.total_amount != result.tax_base + result.total_vat - result.total_irpf:
        found.add("GRAND_TOTAL")
    if not valid_tax_id(result.supplier.tax_id):
        found.add("SUPPLIER_TAX_ID")
    if result.buyer.tax_id is None or not valid_tax_id(result.buyer.tax_id):
        found.add("BUYER_TAX_ID")
    if result.due_date < result.issue_date:
        found.add("DATES")
    return found


KIND_TO_CODE = {
    "total_amount": "GRAND_TOTAL",
    "line_base": "LINE_BASE",
    "line_vat": "LINE_VAT",
    "supplier_tax_id": "SUPPLIER_TAX_ID",
    "due_date": "DATES",
    "vat_rate": "VAT_RATE",
}
SEEDS = [f"seed-{index}".encode() for index in range(300)]


def synth(seed: bytes):
    return DemoParserStrategy().extract(seed, "x.png", "image/png")


def test_synthetic_generation_is_deterministic():
    first, second = synth(b"same content"), synth(b"same content")
    assert first.model_dump() == second.model_dump()
    assert synth(b"other content").model_dump() != first.model_dump()


def test_synthetic_invoices_are_consistent_unless_a_discrepancy_was_injected():
    for seed in SEEDS:
        result = synth(seed)
        kind = synthetic_discrepancy_kind(seed)
        found = inconsistencies(result)
        if kind is None:
            assert found == set(), (seed, found)
        else:
            assert found == {KIND_TO_CODE[kind]}, (seed, kind, found)


def test_discrepancy_is_injected_in_about_one_of_five_seeds():
    injected = sum(1 for seed in SEEDS if synthetic_discrepancy_kind(seed) is not None)
    assert 0.12 <= injected / len(SEEDS) <= 0.28
    assert {synthetic_discrepancy_kind(seed) for seed in SEEDS} - {None} == set(KIND_TO_CODE)


def test_synthetic_invoices_are_varied_and_realistic():
    results = [synth(seed) for seed in SEEDS]
    rates = {line.vat_rate for result in results for line in result.lines}
    assert {Decimal(21), Decimal(10), Decimal(4)} <= rates
    professional = [r for r in results if r.total_irpf > 0]
    assert professional
    assert all(line.irpf_rate == Decimal(15) for r in professional for line in r.lines)
    assert all(r.supplier.tax_id[0].isdigit() for r in professional)
    assert any(r.dir3_accounting_office for r in results)
    assert any(r.dir3_accounting_office is None for r in results)
    assert all(r.issue_date <= date(2025, 12, 31) for r in results)
    assert len({r.supplier.name for r in results}) > 50
    assert all(r.strategy_name == "demo-synthetic-generator" and r.method == ExtractionMethod.DEMO for r in results)
    assert all(any("sintética" in w for w in r.warnings) for r in results)


# --------------------------------------------------------------------------- InvoiceExtractor: selection and validation


def test_strategy_selection_follows_api_key():
    with_key = InvoiceExtractor(make_settings(anthropic_api_key="sk-ant-test"))
    assert isinstance(with_key.strategy, ClaudeVisionStrategy)
    assert with_key.mode == "AI"

    without_key = InvoiceExtractor(make_settings(anthropic_api_key=None))
    assert isinstance(without_key.strategy, DemoParserStrategy)
    assert without_key.mode == "DEMO"
    assert InvoiceExtractor(make_settings(anthropic_api_key="  ")).mode == "DEMO"


def test_explicit_strategy_overrides_selection():
    extractor = InvoiceExtractor(make_settings(anthropic_api_key="sk-ant-test"), strategy=DemoParserStrategy())
    assert extractor.mode == "DEMO"


def test_get_extractor_uses_current_settings(monkeypatch):
    monkeypatch.setattr(extractor_module, "get_settings", lambda: make_settings(anthropic_api_key="sk-ant-test"))
    assert get_extractor().mode == "AI"
    monkeypatch.setattr(extractor_module, "get_settings", lambda: make_settings(anthropic_api_key=None))
    assert get_extractor().mode == "DEMO"


def test_extractor_end_to_end_in_demo_mode(samples_dir):
    extractor = InvoiceExtractor(make_settings(anthropic_api_key=None))
    path = samples_dir / "03_factura_profesional_irpf15.pdf"
    result = extractor.extract(path.read_bytes(), path.name, "application/octet-stream")
    assert result.total_amount == Decimal("1590.00")


def test_extractor_rejects_empty_oversized_and_unsupported_files():
    extractor = InvoiceExtractor(make_settings(anthropic_api_key=None, max_upload_size_mb=1))
    with pytest.raises(InvalidDocumentError, match="vacío"):
        extractor.extract(b"", "a.pdf", PDF)
    with pytest.raises(InvalidDocumentError, match="tamaño máximo"):
        extractor.extract(b"%PDF-" + b"0" * (1024 * 1024), "a.pdf", PDF)
    with pytest.raises(InvalidDocumentError, match="no admitido"):
        extractor.extract(b"just text", "notes.txt", "text/plain")
    with pytest.raises(InvalidDocumentError, match="no admitido"):
        extractor.extract(b"just text", "notes.txt", "")


def test_extractor_trusts_magic_bytes_over_declared_mime(samples_dir):
    seen = {}

    class Spy(DemoParserStrategy):
        def extract(self, content, filename, mime_type):
            seen["mime"] = mime_type
            return super().extract(content, filename, mime_type)

    path = samples_dir / "01_factura_valida_iva21.pdf"
    InvoiceExtractor(make_settings(), strategy=Spy()).extract(path.read_bytes(), "x.png", "image/png")
    assert seen["mime"] == PDF


# --------------------------------------------------------------------------- Claude strategy with a mocked client

TOOL_INPUT = {
    "supplier": {"name": "Consultora Atlas S.L.", "tax_id": "ES B-46271896", "city": "Valencia", "country_code": "ES"},
    "buyer": {"name": "Comercial Ribera, S.A.", "tax_id": "A28730547"},
    "invoice_number": "2024-0042",
    "invoice_series": "A",
    "issue_date": "2024-02-10",
    "due_date": "10/03/2024",
    "tax_base": "1.250,00",
    "total_vat": "262.50",
    "total_irpf": "0",
    "total_amount": 1512.5,
    "lines": [
        {"concept": "Consultoría", "quantity": "10", "unit_price": "100.00", "vat_rate": "21", "line_base": "1000.00"},
        {"concept": "Informe", "quantity": "1", "unit_price": "250.00", "vat_rate": "21%"},
    ],
    "dir3_accounting_office": "L01280796",
    "confidence": 1.7,
    "warnings": ["Sello ilegible"],
}


class FakeMessages:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        if callable(self.behaviour):
            return self.behaviour(kwargs)
        return self.behaviour


def tool_response(data: dict | None = None, stop_reason: str = "tool_use"):
    block = SimpleNamespace(type="tool_use", name="record_invoice", input=TOOL_INPUT if data is None else data)
    return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok"), block], stop_reason=stop_reason)


def fake_client(behaviour) -> SimpleNamespace:
    return SimpleNamespace(messages=FakeMessages(behaviour))


def claude(behaviour, **settings_overrides) -> ClaudeVisionStrategy:
    settings = make_settings(anthropic_api_key="sk-ant-test", **settings_overrides)
    return ClaudeVisionStrategy(settings, client=fake_client(behaviour))


def test_claude_strategy_converts_tool_output_to_extraction_result():
    strategy = claude(tool_response())
    result = strategy.extract(b"%PDF-1.7 fake", "factura.pdf", PDF)

    assert result.method == ExtractionMethod.AI
    assert result.strategy_name.startswith("claude-vision")
    assert result.confidence == 1.0
    assert result.supplier.tax_id == "B46271896"
    assert result.supplier.country_code == "ESP"
    assert result.buyer.name == "Comercial Ribera, S.A."
    assert (result.invoice_series, result.invoice_number) == ("A", "2024-0042")
    assert result.issue_date == date(2024, 2, 10)
    assert result.due_date == date(2024, 3, 10)
    assert result.tax_base == Decimal("1250.00")
    assert result.total_vat == Decimal("262.50")
    assert result.total_irpf == Decimal("0.00")
    assert result.total_amount == Decimal("1512.50")
    assert result.dir3_accounting_office == "L01280796"
    assert result.warnings == ["Sello ilegible"]
    assert [line.line_base for line in result.lines] == [Decimal("1000.00"), Decimal("250.00")]
    assert [line.vat_amount for line in result.lines] == [Decimal("210.00"), Decimal("52.50")]
    assert result.lines[1].vat_rate == Decimal(21)


def test_claude_strategy_clamps_confidence_and_computes_missing_totals():
    data = {k: v for k, v in TOOL_INPUT.items() if k not in ("tax_base", "total_vat", "total_amount", "confidence")}
    result = claude(tool_response(data)).extract(b"%PDF-1.7", "f.pdf", PDF)
    assert result.confidence == 0.5
    assert (result.tax_base, result.total_vat, result.total_amount) == (
        Decimal("1250.00"),
        Decimal("262.50"),
        Decimal("1512.50"),
    )
    assert len(result.warnings) == 4

    low = claude(tool_response({**TOOL_INPUT, "confidence": -3})).extract(b"%PDF-1.7", "f.pdf", PDF)
    assert low.confidence == 0.0
    mid = claude(tool_response({**TOOL_INPUT, "confidence": 0.83})).extract(b"%PDF-1.7", "f.pdf", PDF)
    assert mid.confidence == pytest.approx(0.83)


def test_claude_strategy_request_shape_for_pdf_and_image():
    strategy = claude(tool_response(), anthropic_model="claude-sonnet-4-6", ai_timeout_seconds=30)
    strategy.extract(b"%PDF-1.7 data", "f.pdf", PDF)
    strategy.extract(b"\x89PNG\r\n\x1a\n data", "f.png", "image/png")
    pdf_call, png_call = strategy._client.messages.calls

    assert pdf_call["model"] == "claude-sonnet-4-6"
    assert pdf_call["tool_choice"] == {"type": "tool", "name": "record_invoice"}
    assert [tool["name"] for tool in pdf_call["tools"]] == ["record_invoice"]
    properties = pdf_call["tools"][0]["input_schema"]["properties"]
    assert {"supplier", "buyer", "lines", "issue_date", "tax_base", "total_amount", "confidence"} <= set(properties)
    assert "NIF" in pdf_call["system"] and "IRPF" in pdf_call["system"] and "DIR3" in pdf_call["system"]

    pdf_block = pdf_call["messages"][0]["content"][0]
    assert pdf_block["type"] == "document"
    assert pdf_block["source"]["media_type"] == PDF
    assert pdf_block["source"]["type"] == "base64"
    png_block = png_call["messages"][0]["content"][0]
    assert png_block["type"] == "image"
    assert png_block["source"]["media_type"] == "image/png"


def test_claude_strategy_uses_auto_tool_choice_on_models_that_reject_forcing():
    strategy = claude(tool_response(), anthropic_model="claude-sonnet-5-5")
    strategy.extract(b"%PDF-1.7", "f.pdf", PDF)
    call = strategy._client.messages.calls[0]
    assert call["tool_choice"] == {"type": "auto"}
    assert "record_invoice" in call["system"].splitlines()[-1]


def test_claude_strategy_retries_with_auto_when_forced_tool_choice_is_rejected():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    rejection = anthropic.BadRequestError(
        "tool_choice: type 'tool' is not supported for this model",
        response=httpx.Response(400, request=request),
        body=None,
    )

    def behaviour(kwargs):
        if kwargs["tool_choice"]["type"] == "tool":
            raise rejection
        return tool_response()

    strategy = claude(behaviour, anthropic_model="claude-future-9")
    assert strategy.extract(b"%PDF-1.7", "f.pdf", PDF).method == ExtractionMethod.AI
    assert [c["tool_choice"]["type"] for c in strategy._client.messages.calls] == ["tool", "auto"]


def test_claude_strategy_wraps_api_errors():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    for error in (
        anthropic.APIConnectionError(request=request),
        anthropic.APITimeoutError(request=request),
        anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=request), body=None),
        anthropic.RateLimitError("slow down", response=httpx.Response(429, request=request), body=None),
        anthropic.InternalServerError("boom", response=httpx.Response(500, request=request), body=None),
        RuntimeError("unexpected"),
    ):
        with pytest.raises(ExtractionError):
            claude(error).extract(b"%PDF-1.7", "f.pdf", PDF)


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(content=[SimpleNamespace(type="text", text="no tool")], stop_reason="end_turn"),
        SimpleNamespace(content=[], stop_reason="max_tokens"),
        SimpleNamespace(content=[], stop_reason="refusal"),
        tool_response({**TOOL_INPUT, "lines": []}),
        tool_response({**TOOL_INPUT, "issue_date": "no es una fecha"}),
        tool_response({**TOOL_INPUT, "tax_base": "abc"}),
        tool_response({k: v for k, v in TOOL_INPUT.items() if k != "invoice_number"}),
    ],
)
def test_claude_strategy_rejects_unusable_responses(response):
    with pytest.raises(ExtractionError):
        claude(response).extract(b"%PDF-1.7", "f.pdf", PDF)


def test_claude_strategy_rejects_unsupported_media_type():
    with pytest.raises(ExtractionError, match="no soportado"):
        claude(tool_response()).extract(b"hello", "notes.txt", "text/plain")


# --------------------------------------------------------------------------- Fallback


def test_ai_failure_falls_back_to_demo_with_warning(samples_dir):
    settings = make_settings(anthropic_api_key="sk-ant-test", ai_fallback_to_demo=True)
    extractor = InvoiceExtractor(
        settings, strategy=ClaudeVisionStrategy(settings, client=fake_client(RuntimeError("down")))
    )
    path = samples_dir / "01_factura_valida_iva21.pdf"
    result = extractor.extract(path.read_bytes(), path.name, PDF)

    assert extractor.mode == "AI"
    assert result.method == ExtractionMethod.DEMO
    assert result.total_amount == Decimal("3214.37")
    assert "IA falló" in result.warnings[0]


def test_ai_failure_without_fallback_raises(samples_dir):
    settings = make_settings(anthropic_api_key="sk-ant-test", ai_fallback_to_demo=False)
    extractor = InvoiceExtractor(
        settings, strategy=ClaudeVisionStrategy(settings, client=fake_client(RuntimeError("down")))
    )
    path = samples_dir / "01_factura_valida_iva21.pdf"
    with pytest.raises(ExtractionError):
        extractor.extract(path.read_bytes(), path.name, PDF)


def test_ai_success_does_not_fall_back(samples_dir):
    settings = make_settings(anthropic_api_key="sk-ant-test")
    extractor = InvoiceExtractor(settings, strategy=ClaudeVisionStrategy(settings, client=fake_client(tool_response())))
    path = samples_dir / "01_factura_valida_iva21.pdf"
    result = extractor.extract(path.read_bytes(), path.name, PDF)
    assert result.method == ExtractionMethod.AI
    assert result.invoice_number == "2024-0042"
