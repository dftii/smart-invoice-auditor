"""End-to-end API tests: upload pipeline, manual CRUD, audit, exports, suppliers and dashboard."""

import csv
import io
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

from app.core.config import get_settings
from app.models import ExtractionMethod
from app.schemas import ExtractedLine, ExtractedParty, ExtractionResult
from app.services import invoice_service
from app.services.extractor import ExtractionError, get_extractor
from app.services.tax_id import compute_cif_control, validate_tax_id

API = "/api/v1"
SAMPLES_DIR = Path(__file__).resolve().parents[1] / "samples"
CENT = Decimal("0.01")


# --------------------------------------------------------------------------- Helpers


def make_cif(letter: str, digits7: str) -> str:
    digit, control_letter = compute_cif_control(letter, digits7)
    cif = f"{letter}{digits7}{control_letter if letter in 'PQRSNW' else digit}"
    assert validate_tax_id(cif).is_valid
    return cif


SUPPLIER_ONE = make_cif("B", "1234567")
SUPPLIER_TWO = make_cif("A", "5847291")
BUYER_CIF = make_cif("S", "2800000")
DIR3 = {
    "dir3_accounting_office": "L01280066",
    "dir3_managing_body": "L01280066",
    "dir3_processing_unit": "L01280066",
}


def money(value: Decimal) -> str:
    return format(value.quantize(CENT), "f")


def line_dict(concept: str, quantity: str, unit_price: str, vat_rate: str = "21") -> dict:
    base = (Decimal(quantity) * Decimal(unit_price)).quantize(CENT)
    vat = (base * Decimal(vat_rate) / 100).quantize(CENT)
    return {
        "concept": concept,
        "quantity": quantity,
        "unit_price": unit_price,
        "vat_rate": vat_rate,
        "line_base": money(base),
        "vat_amount": money(vat),
    }


def invoice_payload(
    number: str = "2024-001",
    *,
    supplier_tax_id: str = SUPPLIER_ONE,
    supplier_name: str = "Suministros Norte S.L.",
    issue_date: str = "2024-03-15",
    lines: list[dict] | None = None,
    **overrides,
) -> dict:
    lines = lines or [line_dict("Servicio de consultoría", "2", "50.00"), line_dict("Licencia anual", "1", "150.00")]
    base = sum(Decimal(item["line_base"]) for item in lines)
    vat = sum(Decimal(item["vat_amount"]) for item in lines)
    payload = {
        "supplier": {
            "name": supplier_name,
            "tax_id": supplier_tax_id,
            "address": "Calle Mayor 1",
            "postal_code": "28001",
            "city": "Madrid",
            "province": "Madrid",
        },
        "invoice_number": number,
        "issue_date": issue_date,
        "due_date": "2024-12-31",
        "buyer_name": "Ayuntamiento de Ejemplo",
        "buyer_tax_id": BUYER_CIF,
        "buyer_address": "Plaza del Ayuntamiento 1",
        "buyer_postal_code": "28005",
        "buyer_city": "Madrid",
        "buyer_province": "Madrid",
        **DIR3,
        "tax_base": money(base),
        "total_vat": money(vat),
        "total_irpf": "0.00",
        "total_amount": money(base + vat),
        "lines": lines,
    }
    payload.update(overrides)
    return payload


def create_invoice(client, number: str = "2024-001", **kwargs) -> dict:
    response = client.post(f"{API}/invoices", json=invoice_payload(number, **kwargs))
    assert response.status_code == 201, response.text
    return response.json()


def make_extraction(number: str = "F-100", tax_id: str = SUPPLIER_ONE) -> ExtractionResult:
    return ExtractionResult(
        method=ExtractionMethod.DEMO,
        strategy_name="fake",
        confidence=0.9,
        supplier=ExtractedParty(name="Suministros Norte S.L.", tax_id=tax_id, city="Madrid"),
        buyer=ExtractedParty(name="Ayuntamiento de Ejemplo", tax_id=BUYER_CIF),
        invoice_number=number,
        issue_date=date(2024, 3, 15),
        due_date=date(2024, 12, 31),
        tax_base=Decimal("100.00"),
        total_vat=Decimal("21.00"),
        total_amount=Decimal("121.00"),
        lines=[
            ExtractedLine(
                concept="Servicio",
                quantity=Decimal(1),
                unit_price=Decimal("100.00"),
                vat_rate=Decimal(21),
                line_base=Decimal("100.00"),
                vat_amount=Decimal("21.00"),
            )
        ],
        warnings=["Dato de prueba"],
        **DIR3,
    )


class FakeExtractor:
    mode = "DEMO"

    def __init__(self) -> None:
        self.result = make_extraction()
        self.error: Exception | None = None
        self.calls = 0

    def extract(self, content: bytes, filename: str, mime_type: str) -> ExtractionResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result.model_copy(deep=True)


@pytest.fixture()
def fake_extractor(client):
    from app.main import app

    fake = FakeExtractor()
    app.dependency_overrides[get_extractor] = lambda: fake
    return fake


def pdf_bytes(tag: str = "a") -> bytes:
    return f"%PDF-1.4\n% {tag}\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF".encode()


def upload(client, content: bytes, filename: str = "factura.pdf", content_type: str = "application/pdf"):
    return client.post(f"{API}/invoices/upload", files={"file": (filename, content, content_type)})


def stored_files() -> list[Path]:
    root = get_settings().upload_dir
    return [path for path in root.rglob("*") if path.is_file()] if root.exists() else []


# --------------------------------------------------------------------------- System


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "sqlite"
    assert body["extraction_mode"] in ("AI", "DEMO")


def test_openapi_and_docs_available(client):
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert f"{API}/invoices/upload" in schema["paths"]
    assert f"{API}/stats/dashboard" in schema["paths"]


# --------------------------------------------------------------------------- Manual creation and audit


def test_manual_create_is_validated(client):
    body = create_invoice(client)
    assert body["audit_status"] == "VALIDATED"
    assert body["error_count"] == 0
    assert body["extraction_method"] == "MANUAL"
    assert body["tax_base"] == "250.00"
    assert body["total_vat"] == "52.50"
    assert body["total_amount"] == "302.50"
    assert [line["line_number"] for line in body["lines"]] == [1, 2]
    assert body["supplier"]["tax_id"] == SUPPLIER_ONE
    assert body["supplier"]["tax_id_valid"] is True
    assert body["supplier"]["tax_id_type"] == "CIF"
    assert body["validation_logs"]
    assert {log["run_id"] for log in body["validation_logs"]} == {body["last_audit_run_id"]}


def test_manual_create_detects_discrepancy(client):
    bad_line = line_dict("Servicio", "3", "50.00")
    bad_line["line_base"] = "155.00"
    body = create_invoice(client, lines=[bad_line], tax_base="155.00", total_vat="31.50", total_amount="186.50")
    assert body["audit_status"] == "DISCREPANCY"
    assert body["error_count"] >= 1
    failed = [log for log in body["validation_logs"] if not log["passed"] and log["severity"] == "ERROR"]
    assert any(log["rule_code"] == "LINE_BASE" for log in failed)
    assert "150.00" in body["audit_summary"]


def test_manual_create_invalid_tax_id_is_discrepancy(client):
    body = create_invoice(client, supplier_tax_id="B12345670")
    assert body["audit_status"] == "DISCREPANCY"
    assert body["supplier"]["tax_id_valid"] is False
    assert any(log["rule_code"] == "SUPPLIER_TAX_ID" and not log["passed"] for log in body["validation_logs"])


def test_manual_create_validation_errors(client):
    payload = invoice_payload()
    payload["lines"] = []
    assert client.post(f"{API}/invoices", json=payload).status_code == 422
    payload = invoice_payload()
    payload["issue_date"] = "no-es-una-fecha"
    assert client.post(f"{API}/invoices", json=payload).status_code == 422


def test_duplicate_manual_invoice(client):
    first = create_invoice(client, "DUP-1")
    second = create_invoice(client, "DUP-1")
    assert first["audit_status"] == "VALIDATED"
    assert second["audit_status"] == "DUPLICATE"
    assert second["id"] != first["id"]
    assert second["supplier"]["id"] == first["supplier"]["id"]


def test_patch_fixes_discrepancy_and_reaudits(client):
    created = create_invoice(client, total_amount="999.99")
    assert created["audit_status"] == "DISCREPANCY"
    invoice_id = created["id"]

    patched = client.patch(f"{API}/invoices/{invoice_id}", json={"total_amount": "302.50"})
    assert patched.status_code == 200
    body = patched.json()
    assert body["audit_status"] == "VALIDATED"
    assert body["error_count"] == 0
    assert body["last_audit_run_id"] != created["last_audit_run_id"]
    assert {log["run_id"] for log in body["validation_logs"]} == {body["last_audit_run_id"]}

    latest = client.get(f"{API}/invoices/{invoice_id}/logs").json()
    everything = client.get(f"{API}/invoices/{invoice_id}/logs", params={"all_runs": True}).json()
    assert {log["run_id"] for log in latest} == {body["last_audit_run_id"]}
    assert len(everything) > len(latest)
    assert len({log["run_id"] for log in everything}) == 2


def test_patch_replaces_lines(client):
    created = create_invoice(client)
    new_lines = [line_dict("Servicio único", "1", "100.00")]
    response = client.patch(
        f"{API}/invoices/{created['id']}",
        json={"lines": new_lines, "tax_base": "100.00", "total_vat": "21.00", "total_amount": "121.00"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["lines"]) == 1
    assert body["lines"][0]["concept"] == "Servicio único"
    assert body["lines"][0]["line_number"] == 1
    assert body["audit_status"] == "VALIDATED"


def test_patch_supplier_tax_id_relinks_supplier(client):
    created = create_invoice(client, supplier_tax_id="B12345670")
    assert created["audit_status"] == "DISCREPANCY"

    patched = client.patch(f"{API}/invoices/{created['id']}", json={"supplier_tax_id": SUPPLIER_ONE})
    assert patched.status_code == 200
    body = patched.json()
    assert body["audit_status"] == "VALIDATED"
    assert body["supplier_tax_id"] == SUPPLIER_ONE
    assert body["supplier"]["tax_id"] == SUPPLIER_ONE
    assert body["supplier"]["tax_id_valid"] is True


def test_patch_rejects_null_for_required_field(client):
    created = create_invoice(client)
    response = client.patch(f"{API}/invoices/{created['id']}", json={"invoice_number": None})
    assert response.status_code == 422
    assert "invoice_number" in response.json()["detail"]
    assert client.get(f"{API}/invoices/{created['id']}").json()["invoice_number"] == "2024-001"


def test_revalidate_single_and_bulk(client):
    good = create_invoice(client, "R-1")
    bad = create_invoice(client, "R-2", total_amount="1.00")

    single = client.post(f"{API}/invoices/{good['id']}/revalidate")
    assert single.status_code == 200
    report = single.json()
    assert report["invoice_id"] == good["id"]
    assert report["status"] == "VALIDATED"
    assert report["checks"]
    assert report["run_id"] != good["last_audit_run_id"]

    bulk = client.post(f"{API}/invoices/revalidate", json={"invoice_ids": [bad["id"]]})
    assert bulk.status_code == 200
    assert bulk.json()["processed"] == 1
    assert bulk.json()["reports"][0]["status"] == "DISCREPANCY"

    only_discrepancies = client.post(f"{API}/invoices/revalidate", json={"status": "DISCREPANCY"})
    assert only_discrepancies.json()["processed"] == 1

    everything = client.post(f"{API}/invoices/revalidate", json={})
    assert everything.json()["processed"] == 2


def test_delete_invoice(client):
    created = create_invoice(client)
    assert client.delete(f"{API}/invoices/{created['id']}").status_code == 204
    assert client.get(f"{API}/invoices/{created['id']}").status_code == 404
    assert client.get(f"{API}/invoices").json()["total"] == 0


def test_unknown_invoice_returns_spanish_404(client):
    for method, path in [
        ("get", "/invoices/9999"),
        ("get", "/invoices/9999/logs"),
        ("get", "/invoices/9999/file"),
        ("get", "/invoices/9999/facturae"),
        ("post", "/invoices/9999/revalidate"),
        ("delete", "/invoices/9999"),
        ("patch", "/invoices/9999"),
    ]:
        kwargs = {"json": {"notes": "x"}} if method == "patch" else {}
        response = getattr(client, method)(f"{API}{path}", **kwargs)
        assert response.status_code == 404, path
        assert "no encontrada" in response.json()["detail"], path


# --------------------------------------------------------------------------- Upload pipeline


def test_upload_pipeline_with_stored_file(client, fake_extractor):
    content = pdf_bytes("uno")
    response = upload(client, content)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["extraction_method"] == "DEMO"
    assert body["extraction_warnings"] == ["Dato de prueba"]
    assert body["audit"]["status"] == "VALIDATED"
    invoice = body["invoice"]
    assert invoice["audit_status"] == "VALIDATED"
    assert invoice["source_filename"] == "factura.pdf"
    assert invoice["source_mime_type"] == "application/pdf"
    assert re.fullmatch(r"[0-9a-f]{64}", invoice["source_file_hash"])

    files = stored_files()
    assert len(files) == 1
    assert files[0].name == f"{invoice['source_file_hash']}.pdf"
    assert files[0].read_bytes() == content

    original = client.get(f"{API}/invoices/{invoice['id']}/file")
    assert original.status_code == 200
    assert original.content == content
    assert original.headers["content-type"] == "application/pdf"


def test_upload_never_uses_client_filename_in_path(client, fake_extractor):
    response = upload(client, pdf_bytes("traversal"), filename="../../..\\evil name.pdf")
    assert response.status_code == 201
    assert response.json()["invoice"]["source_filename"] == "evil name.pdf"
    root = get_settings().upload_dir.resolve()
    files = stored_files()
    assert len(files) == 1
    assert files[0].resolve().is_relative_to(root)
    assert "evil" not in files[0].name


def test_duplicate_upload_is_flagged_and_file_shared(client, fake_extractor):
    content = pdf_bytes("dup")
    first = upload(client, content).json()
    second = upload(client, content)
    assert second.status_code == 201
    second_body = second.json()
    assert first["invoice"]["audit_status"] == "VALIDATED"
    assert second_body["invoice"]["audit_status"] == "DUPLICATE"
    assert second_body["audit"]["status"] == "DUPLICATE"
    assert first["invoice"]["id"] in second_body["audit"]["duplicate_of"]
    assert len(stored_files()) == 1

    assert client.delete(f"{API}/invoices/{first['invoice']['id']}").status_code == 204
    assert len(stored_files()) == 1, "the file is still referenced by the duplicate"
    assert client.delete(f"{API}/invoices/{second_body['invoice']['id']}").status_code == 204
    assert stored_files() == []


def test_upload_extraction_error_returns_422_and_leaves_nothing(client, fake_extractor):
    fake_extractor.error = ExtractionError("el documento no contiene una factura")
    response = upload(client, pdf_bytes("err"))
    assert response.status_code == 422
    assert "el documento no contiene una factura" in response.json()["detail"]
    assert stored_files() == []
    assert client.get(f"{API}/invoices").json()["total"] == 0


def test_upload_rolls_back_and_removes_file_on_failure(client, fake_extractor, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(invoice_service, "create_invoice_from_extraction", explode)
    with pytest.raises(RuntimeError):
        upload(client, pdf_bytes("boom"))
    assert stored_files() == []
    monkeypatch.undo()
    assert client.get(f"{API}/invoices").json()["total"] == 0


def test_upload_rejects_unsupported_type(client, fake_extractor):
    response = upload(client, b"esto es texto plano, no una factura", "notas.txt", "text/plain")
    assert response.status_code == 415
    assert "no soportado" in response.json()["detail"]
    assert fake_extractor.calls == 0
    assert stored_files() == []


def test_upload_rejects_oversized_file(client, fake_extractor, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_size_mb", 1)
    response = upload(client, b"%PDF-1.4" + b"0" * (1024 * 1024))
    assert response.status_code == 413
    assert "tamaño máximo" in response.json()["detail"]
    assert fake_extractor.calls == 0


def test_upload_rejects_empty_file(client, fake_extractor):
    response = upload(client, b"")
    assert response.status_code == 422
    assert "vacío" in response.json()["detail"]


def test_upload_requires_file_field(client):
    assert client.post(f"{API}/invoices/upload").status_code == 422


def test_upload_generated_pdf_with_real_extractor(client):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.drawString(72, 780, "FACTURA Nº 2024-777")
    pdf.drawString(72, 760, "Fecha: 15/03/2024")
    pdf.drawString(72, 740, f"Emisor: Suministros Norte S.L. CIF {SUPPLIER_ONE}")
    pdf.drawString(72, 720, "Servicio de consultoría 2 x 50,00 = 100,00")
    pdf.drawString(72, 700, "Base imponible 100,00  IVA 21% 21,00  TOTAL 121,00")
    pdf.save()

    response = upload(client, buffer.getvalue(), "generada.pdf")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["invoice"]["lines"]
    assert body["invoice"]["audit_status"] in ("VALIDATED", "DISCREPANCY", "DUPLICATE")
    assert body["extraction_method"] in ("AI", "DEMO")
    assert body["audit"]["checks"]


SAMPLE_PDFS = (
    sorted(path for path in SAMPLES_DIR.iterdir() if path.suffix.lower() in (".pdf", ".png"))
    if SAMPLES_DIR.exists()
    else []
)


@pytest.mark.skipif(not SAMPLE_PDFS, reason="sample PDFs have not been generated")
@pytest.mark.parametrize("sample", SAMPLE_PDFS, ids=[path.name for path in SAMPLE_PDFS])
def test_upload_each_sample_pdf(client, sample):
    response = upload(
        client, sample.read_bytes(), sample.name, "image/png" if sample.suffix == ".png" else "application/pdf"
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["invoice"]["lines"]
    assert body["audit"]["checks"]
    assert body["invoice"]["audit_status"] == body["audit"]["status"]
    assert Decimal(body["invoice"]["total_amount"]) > 0


@pytest.mark.skipif(not SAMPLE_PDFS, reason="sample PDFs have not been generated")
def test_samples_cover_valid_and_invalid_invoices(client):
    statuses = {
        upload(client, sample.read_bytes(), sample.name).json()["invoice"]["audit_status"] for sample in SAMPLE_PDFS
    }
    assert "VALIDATED" in statuses
    assert "DISCREPANCY" in statuses


# --------------------------------------------------------------------------- Listing, filters, sorting


@pytest.fixture()
def catalog(client):
    """Four invoices from two suppliers, one with a total mismatch."""
    created = {
        "a": create_invoice(client, "A-001", issue_date="2024-01-10"),
        "b": create_invoice(
            client,
            "A-002",
            issue_date="2024-01-20",
            lines=[line_dict("Mantenimiento", "1", "100.00", "10")],
        ),
        "c": create_invoice(
            client,
            "B-001",
            supplier_tax_id=SUPPLIER_TWO,
            supplier_name="Papelería Sur S.A.",
            issue_date="2024-02-05",
            lines=[line_dict("Papel", "10", "4.00", "4")],
        ),
        "d": create_invoice(
            client,
            "B-002",
            supplier_tax_id=SUPPLIER_TWO,
            supplier_name="Papelería Sur S.A.",
            issue_date="2024-02-18",
            lines=[line_dict("Mobiliario", "1", "200.00")],
            total_amount="250.00",
        ),
    }
    assert [created[key]["audit_status"] for key in "abcd"] == ["VALIDATED", "VALIDATED", "VALIDATED", "DISCREPANCY"]
    return created


def numbers(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["invoice_number"] for item in response.json()["items"]]


def test_list_default_sort_and_summary_shape(client, catalog):
    response = client.get(f"{API}/invoices")
    body = response.json()
    assert (body["total"], body["page"], body["page_size"], body["pages"]) == (4, 1, 20, 1)
    assert [item["invoice_number"] for item in body["items"]] == ["B-002", "B-001", "A-002", "A-001"]
    first = body["items"][0]
    assert first["supplier_name"] == "Papelería Sur S.A."
    assert first["total_amount"] == "250.00"
    assert "lines" not in first


def test_list_pagination(client, catalog):
    page1 = client.get(f"{API}/invoices", params={"page": 1, "page_size": 3}).json()
    page2 = client.get(f"{API}/invoices", params={"page": 2, "page_size": 3}).json()
    assert (page1["total"], page1["pages"], len(page1["items"])) == (4, 2, 3)
    assert len(page2["items"]) == 1
    assert {item["id"] for item in page1["items"]}.isdisjoint({item["id"] for item in page2["items"]})
    assert client.get(f"{API}/invoices", params={"page": 3, "page_size": 3}).json()["items"] == []


def test_list_pagination_bounds(client):
    assert client.get(f"{API}/invoices", params={"page": 0}).status_code == 422
    assert client.get(f"{API}/invoices", params={"page_size": 0}).status_code == 422
    assert client.get(f"{API}/invoices", params={"page_size": 201}).status_code == 422
    assert client.get(f"{API}/invoices", params={"page_size": 200}).status_code == 200
    assert client.get(f"{API}/invoices").json() == {"items": [], "total": 0, "page": 1, "page_size": 20, "pages": 0}


def test_list_sorting(client, catalog):
    by_total_asc = numbers(client.get(f"{API}/invoices", params={"sort_by": "total_amount", "sort_dir": "asc"}))
    assert by_total_asc == ["B-001", "A-002", "B-002", "A-001"]
    by_number = numbers(client.get(f"{API}/invoices", params={"sort_by": "invoice_number", "sort_dir": "asc"}))
    assert by_number == ["A-001", "A-002", "B-001", "B-002"]
    by_date = numbers(client.get(f"{API}/invoices", params={"sort_by": "issue_date", "sort_dir": "asc"}))
    assert by_date == ["A-001", "A-002", "B-001", "B-002"]
    assert client.get(f"{API}/invoices", params={"sort_by": "nonexistent"}).status_code == 422


def test_list_filters(client, catalog):
    supplier_id = catalog["c"]["supplier"]["id"]
    assert sorted(numbers(client.get(f"{API}/invoices", params={"status": "DISCREPANCY"}))) == ["B-002"]
    assert sorted(numbers(client.get(f"{API}/invoices", params={"status": "VALIDATED"}))) == ["A-001", "A-002", "B-001"]
    assert sorted(numbers(client.get(f"{API}/invoices", params={"supplier_id": supplier_id}))) == ["B-001", "B-002"]
    assert sorted(numbers(client.get(f"{API}/invoices", params={"tax_id": SUPPLIER_ONE.lower()}))) == ["A-001", "A-002"]
    window = {"date_from": "2024-01-15", "date_to": "2024-02-10"}
    assert sorted(numbers(client.get(f"{API}/invoices", params=window))) == ["A-002", "B-001"]
    assert sorted(numbers(client.get(f"{API}/invoices", params={"min_total": "200", "max_total": "260"}))) == ["B-002"]
    assert sorted(numbers(client.get(f"{API}/invoices", params={"search": "papel"}))) == ["B-001", "B-002"]
    assert sorted(numbers(client.get(f"{API}/invoices", params={"search": "a-00"}))) == ["A-001", "A-002"]
    assert numbers(client.get(f"{API}/invoices", params={"extraction_method": "AI"})) == []
    assert len(numbers(client.get(f"{API}/invoices", params={"extraction_method": "MANUAL"}))) == 4
    assert numbers(client.get(f"{API}/invoices", params={"search": "100%"})) == []
    combined = client.get(f"{API}/invoices", params={"status": "VALIDATED", "supplier_id": supplier_id})
    assert numbers(combined) == ["B-001"]
    assert client.get(f"{API}/invoices", params={"status": "BOGUS"}).status_code == 422


def test_filtered_total_matches_filtered_items(client, catalog):
    body = client.get(f"{API}/invoices", params={"status": "VALIDATED", "page_size": 2}).json()
    assert body["total"] == 3
    assert body["pages"] == 2
    assert len(body["items"]) == 2


# --------------------------------------------------------------------------- Facturae


def test_facturae_download_for_validated_invoice(client):
    created = create_invoice(client)
    response = client.get(f"{API}/invoices/{created['id']}/facturae", params={"sign": False})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/xml")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert re.search(r'filename="facturae_[A-Za-z0-9._-]+\.xml"', disposition)
    root = etree.fromstring(response.content)
    assert etree.QName(root).localname == "Facturae"
    assert "Facturaev3_2_2" in etree.QName(root).namespace


def test_facturae_conflict_for_discrepancy(client):
    created = create_invoice(client, total_amount="1.00")
    response = client.get(f"{API}/invoices/{created['id']}/facturae")
    assert response.status_code == 409
    assert response.json()["detail"]


# --------------------------------------------------------------------------- CSV export


def read_csv(response) -> list[list[str]]:
    assert response.status_code == 200, response.text
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:]), delimiter=";"))


def test_csv_export_headers_and_rows(client, catalog):
    response = client.get(f"{API}/invoices/export/csv")
    assert response.headers["content-type"].startswith("text/csv")
    assert "utf-8" in response.headers["content-type"].lower()
    assert re.fullmatch(r'attachment; filename="facturas_\d{8}\.csv"', response.headers["content-disposition"])
    assert response.headers["content-disposition"].count(date.today().strftime("%Y%m%d")) == 1

    rows = read_csv(response)
    header, data = rows[0], rows[1:]
    assert header[:6] == ["ID", "Serie", "Número", "Fecha emisión", "Fecha operación", "Fecha vencimiento"]
    assert "Base imponible" in header and "Estado" in header
    assert "Concepto" not in header
    assert len(data) == 4
    by_number = {row[header.index("Número")]: row for row in data}
    row = by_number["A-001"]
    assert row[header.index("Fecha emisión")] == "2024-01-10"
    assert row[header.index("Base imponible")] == "250.00"
    assert row[header.index("IVA")] == "52.50"
    assert row[header.index("Total")] == "302.50"
    assert row[header.index("Estado")] == "Validada"
    assert row[header.index("NIF/CIF proveedor")] == SUPPLIER_ONE
    assert by_number["B-002"][header.index("Estado")] == "Discrepancia"


def test_csv_export_with_lines_and_filters(client, catalog):
    rows = read_csv(client.get(f"{API}/invoices/export/csv", params={"include_lines": True}))
    header, data = rows[0], rows[1:]
    assert header[-10] == "Línea" and "Concepto" in header and "Cuota IVA" in header
    assert len(data) == 5  # A-001 has two lines, the others one
    assert {row[header.index("Concepto")] for row in data} >= {"Servicio de consultoría", "Licencia anual", "Papel"}

    only_discrepancies = read_csv(client.get(f"{API}/invoices/export/csv", params={"status": "DISCREPANCY"}))
    assert len(only_discrepancies) == 2
    assert only_discrepancies[1][only_discrepancies[0].index("Número")] == "B-002"


def test_csv_export_neutralises_formula_injection(client):
    create_invoice(client, "=CMD()", supplier_name="@Proveedor")
    rows = read_csv(client.get(f"{API}/invoices/export/csv"))
    header, row = rows[0], rows[1]
    assert row[header.index("Número")] == "'=CMD()"
    assert row[header.index("Proveedor")] == "'@Proveedor"


def test_csv_export_empty(client):
    rows = read_csv(client.get(f"{API}/invoices/export/csv"))
    assert len(rows) == 1


# --------------------------------------------------------------------------- Stats


def test_dashboard_stats_empty(client):
    response = client.get(f"{API}/stats/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert body["total_invoices"] == 0
    assert body["total_amount"] == "0.00"
    assert body["validation_rate"] == 0
    assert body["monthly"] == [] and body["top_suppliers"] == []
    assert [item["status"] for item in body["by_status"]] == ["PENDING", "VALIDATED", "DISCREPANCY", "DUPLICATE"]


def test_dashboard_stats_numbers(client, catalog):
    body = client.get(f"{API}/stats/dashboard").json()
    assert body["total_invoices"] == 4
    assert body["total_suppliers"] == 2
    assert body["total_tax_base"] == "590.00"
    assert body["total_vat"] == "106.10"
    assert body["total_irpf"] == "0.00"
    assert body["total_amount"] == "704.10"
    assert body["validation_rate"] == 75.0
    assert body["extraction_mode"] in ("AI", "DEMO")

    status = {item["status"]: item for item in body["by_status"]}
    assert status["VALIDATED"]["count"] == 3 and status["VALIDATED"]["total_amount"] == "454.10"
    assert status["DISCREPANCY"]["count"] == 1 and status["DISCREPANCY"]["total_amount"] == "250.00"
    assert status["DUPLICATE"]["count"] == 0 and status["PENDING"]["count"] == 0

    monthly = {item["month"]: item for item in body["monthly"]}
    assert list(monthly) == ["2024-01", "2024-02"]
    assert monthly["2024-01"]["invoice_count"] == 2
    assert monthly["2024-01"]["tax_base"] == "350.00"
    assert monthly["2024-01"]["total_amount"] == "412.50"
    assert monthly["2024-02"]["total_amount"] == "291.60"

    top = body["top_suppliers"]
    assert [item["tax_id"] for item in top] == [SUPPLIER_ONE, SUPPLIER_TWO]
    assert top[0]["invoice_count"] == 2 and top[0]["total_amount"] == "412.50"

    vat = {item["vat_rate"]: item for item in body["vat_breakdown"]}
    assert list(vat) == ["21.00", "10.00", "4.00"]
    assert vat["21.00"]["tax_base"] == "450.00" and vat["21.00"]["vat_amount"] == "94.50"
    assert vat["10.00"]["vat_amount"] == "10.00"
    assert vat["4.00"]["tax_base"] == "40.00" and vat["4.00"]["vat_amount"] == "1.60"

    assert body["top_failed_rules"][0] == {"rule_code": "GRAND_TOTAL", "count": 1}


def test_dashboard_failed_rules_use_latest_run_only(client, catalog):
    discrepancy_id = catalog["d"]["id"]
    assert client.patch(f"{API}/invoices/{discrepancy_id}", json={"total_amount": "242.00"}).status_code == 200
    body = client.get(f"{API}/stats/dashboard").json()
    assert body["top_failed_rules"] == []
    assert body["validation_rate"] == 100.0


def test_dashboard_top_suppliers_limited_to_five(client):
    for index in range(7):
        tax_id = make_cif("B", f"{2000000 + index}")
        create_invoice(client, f"N-{index}", supplier_tax_id=tax_id, supplier_name=f"Proveedor {index}")
    body = client.get(f"{API}/stats/dashboard").json()
    assert body["total_suppliers"] == 7
    assert len(body["top_suppliers"]) == 5


# --------------------------------------------------------------------------- Suppliers


def test_suppliers_list_and_detail(client, catalog):
    listing = client.get(f"{API}/suppliers").json()
    assert listing["total"] == 2
    assert [item["name"] for item in listing["items"]] == ["Papelería Sur S.A.", "Suministros Norte S.L."]
    papeleria = listing["items"][0]
    assert papeleria["invoice_count"] == 2
    assert papeleria["total_invoiced"] == "291.60"
    assert papeleria["tax_id_valid"] is True

    detail = client.get(f"{API}/suppliers/{papeleria['id']}")
    assert detail.status_code == 200
    assert detail.json()["tax_id"] == SUPPLIER_TWO

    assert [item["tax_id"] for item in client.get(f"{API}/suppliers", params={"search": "norte"}).json()["items"]] == [
        SUPPLIER_ONE
    ]
    assert client.get(f"{API}/suppliers", params={"search": SUPPLIER_TWO[:5]}).json()["total"] == 1
    assert (
        client.get(f"{API}/suppliers", params={"page_size": 1, "page": 2})
        .json()["items"][0]["name"]
        .startswith("Suministros")
    )
    assert client.get(f"{API}/suppliers", params={"page": 0}).status_code == 422


def test_supplier_create_update_and_conflict(client):
    payload = {"name": "Nuevo Proveedor S.L.", "tax_id": f"es {make_cif('B', '7654321').lower()}", "city": "Sevilla"}
    created = client.post(f"{API}/suppliers", json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["tax_id"] == make_cif("B", "7654321")
    assert body["tax_id_type"] == "CIF"
    assert body["tax_id_valid"] is True
    assert body["person_type"] == "J"
    assert body["invoice_count"] == 0 and body["total_invoiced"] == "0.00"

    duplicate = client.post(f"{API}/suppliers", json=payload)
    assert duplicate.status_code == 409
    assert "Ya existe" in duplicate.json()["detail"]

    patched = client.patch(f"{API}/suppliers/{body['id']}", json={"name": "Renombrado S.L.", "email": "a@b.es"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renombrado S.L."
    assert patched.json()["email"] == "a@b.es"
    assert patched.json()["city"] == "Sevilla"
    assert client.patch(f"{API}/suppliers/{body['id']}", json={"name": None}).status_code == 422


def test_supplier_with_invalid_tax_id_is_flagged(client):
    response = client.post(f"{API}/suppliers", json={"name": "Dudoso", "tax_id": "B12345670"})
    assert response.status_code == 201
    assert response.json()["tax_id_valid"] is False


def test_supplier_invoices_endpoint(client, catalog):
    supplier_id = catalog["a"]["supplier"]["id"]
    response = client.get(
        f"{API}/suppliers/{supplier_id}/invoices", params={"sort_by": "issue_date", "sort_dir": "asc"}
    )
    assert response.status_code == 200
    assert numbers(response) == ["A-001", "A-002"]
    assert response.json()["total"] == 2
    paged = client.get(f"{API}/suppliers/{supplier_id}/invoices", params={"page_size": 1})
    assert paged.json()["pages"] == 2


def test_supplier_404s(client):
    assert client.get(f"{API}/suppliers/999").status_code == 404
    assert client.patch(f"{API}/suppliers/999", json={"name": "X"}).status_code == 404
    response = client.get(f"{API}/suppliers/999/invoices")
    assert response.status_code == 404
    assert "no encontrado" in response.json()["detail"]


def test_manual_invoice_reuses_existing_supplier(client):
    first = create_invoice(client, "S-1")
    second = create_invoice(client, "S-2", supplier_name="Otro nombre comercial")
    assert first["supplier"]["id"] == second["supplier"]["id"]
    assert second["supplier"]["name"] == "Suministros Norte S.L."
    assert client.get(f"{API}/suppliers").json()["total"] == 1
