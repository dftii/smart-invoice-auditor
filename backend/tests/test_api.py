"""End-to-end API tests: upload pipeline, manual CRUD, audit, exports, suppliers and dashboard."""

import csv
import hashlib
import io
import math
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from lxml import etree
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ExtractionMethod, Invoice, InvoiceLine, Supplier, ValidationLog
from app.schemas import ExtractedLine, ExtractedParty, ExtractionResult
from app.services import invoice_service
from app.services.auditor import InvoiceAuditor
from app.services.csv_exporter import INVOICE_COLUMNS, LINE_COLUMNS
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
SUPPLIER_THREE = make_cif("B", "3000000")
BUYER_CIF = make_cif("S", "2800000")
DIR3 = {
    "dir3_accounting_office": "L01280066",
    "dir3_managing_body": "L01280066",
    "dir3_processing_unit": "L01280066",
}


def money(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def line_dict(
    concept: str, quantity: str, unit_price: str, vat_rate: str = "21", irpf_rate: str = "0", discount_rate: str = "0"
) -> dict:
    base = (Decimal(quantity) * Decimal(unit_price) * (100 - Decimal(discount_rate)) / 100).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    vat = (base * Decimal(vat_rate) / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    irpf = (base * Decimal(irpf_rate) / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    return {
        "concept": concept,
        "quantity": quantity,
        "unit_price": unit_price,
        "discount_rate": discount_rate,
        "vat_rate": vat_rate,
        "irpf_rate": irpf_rate,
        "line_base": money(base),
        "vat_amount": money(vat),
        "irpf_amount": money(irpf),
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
    irpf = sum(Decimal(item.get("irpf_amount", "0")) for item in lines)
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
        "total_irpf": money(irpf),
        "total_amount": money(base + vat - irpf),
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
    fake = FakeExtractor()
    client.app.dependency_overrides[get_extractor] = lambda: fake
    return fake


def pdf_bytes(tag: str = "a") -> bytes:
    return f"%PDF-1.4\n% {tag}\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF".encode()


def upload(client, content: bytes, filename: str = "factura.pdf", content_type: str = "application/pdf"):
    return client.post(f"{API}/invoices/upload", files={"file": (filename, content, content_type)})


def stored_files() -> list[Path]:
    root = get_settings().upload_dir
    return [path for path in root.rglob("*") if path.is_file()] if root.exists() else []


def logs_of(client, invoice_id: int, **params) -> list[dict]:
    response = client.get(f"{API}/invoices/{invoice_id}/logs", params=params)
    assert response.status_code == 200, response.text
    return response.json()


TIME_FIELDS = {"created_at", "updated_at", "last_audited_at"}


def stable(body):
    """The body without timestamps (their serialisation differs between a fresh and a reloaded row, see the xfail)."""
    if isinstance(body, dict):
        return {key: stable(value) for key, value in body.items() if key not in TIME_FIELDS}
    if isinstance(body, list):
        return [stable(item) for item in body]
    return body


def failed_logs(body: dict) -> list[dict]:
    return [log for log in body["validation_logs"] if not log["passed"]]


# --------------------------------------------------------------------------- System


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == get_settings().app_name
    assert body["version"] == get_settings().app_version
    assert body["database"] == "ok"
    assert body["extraction_mode"] == "DEMO"  # conftest removes the API key


def test_health_reports_unavailable_database(client):
    from app.db.database import get_db
    class BrokenSession:
        def execute(self, *_args, **_kwargs):
            raise OperationalError("SELECT 1", {}, Exception("disco lleno"))

    client.app.dependency_overrides[get_db] = lambda: BrokenSession()
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "Base de datos no disponible"}


EXPECTED_ROUTES = {
    ("POST", f"{API}/invoices/upload"),
    ("POST", f"{API}/invoices"),
    ("GET", f"{API}/invoices"),
    ("GET", f"{API}/invoices/export/csv"),
    ("POST", f"{API}/invoices/revalidate"),
    ("GET", f"{API}/invoices/{{invoice_id}}"),
    ("PATCH", f"{API}/invoices/{{invoice_id}}"),
    ("DELETE", f"{API}/invoices/{{invoice_id}}"),
    ("GET", f"{API}/invoices/{{invoice_id}}/logs"),
    ("POST", f"{API}/invoices/{{invoice_id}}/revalidate"),
    ("GET", f"{API}/invoices/{{invoice_id}}/facturae"),
    ("GET", f"{API}/invoices/{{invoice_id}}/file"),
    ("GET", f"{API}/suppliers"),
    ("POST", f"{API}/suppliers"),
    ("GET", f"{API}/suppliers/{{supplier_id}}"),
    ("PATCH", f"{API}/suppliers/{{supplier_id}}"),
    ("GET", f"{API}/suppliers/{{supplier_id}}/invoices"),
    ("GET", f"{API}/stats/dashboard"),
    ("GET", "/health"),
}


def test_openapi_schema_lists_every_endpoint(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    schema = response.json()

    assert schema["openapi"].startswith("3.")
    assert schema["info"]["title"] == get_settings().app_name
    assert schema["info"]["version"] == get_settings().app_version
    documented = {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
        if method in {"get", "post", "patch", "put", "delete"}
    }
    assert documented == EXPECTED_ROUTES
    assert {"InvoiceRead", "InvoiceCreate", "InvoiceUpdate", "AuditReport", "DashboardStats"} <= set(
        schema["components"]["schemas"]
    )
    assert {tag["name"] for tag in schema["tags"]} == {"Facturas", "Proveedores", "Estadísticas", "Sistema"}


def test_openapi_documents_upload_error_responses_and_unique_operation_ids(client):
    schema = client.get("/openapi.json").json()
    upload_responses = schema["paths"][f"{API}/invoices/upload"]["post"]["responses"]
    assert {"201", "413", "415", "422"} <= set(upload_responses)
    operation_ids = [op["operationId"] for ops in schema["paths"].values() for op in ops.values()]
    assert len(operation_ids) == len(set(operation_ids))


def test_docs_pages_available(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200


def test_unknown_route_is_404_and_wrong_method_is_405(client):
    assert client.get(f"{API}/nada").status_code == 404
    assert client.delete(f"{API}/suppliers/1").status_code == 405
    assert client.put(f"{API}/invoices/1", json={}).status_code == 405


# --------------------------------------------------------------------------- Manual creation and audit


def test_manual_create_is_validated(client):
    body = create_invoice(client)
    assert body["audit_status"] == "VALIDATED"
    assert body["error_count"] == 0
    assert body["warning_count"] == 0
    assert body["extraction_method"] == "MANUAL"
    assert body["tax_base"] == "250.00"
    assert body["total_vat"] == "52.50"
    assert body["total_irpf"] == "0.00"
    assert body["total_amount"] == "302.50"
    assert [line["line_number"] for line in body["lines"]] == [1, 2]
    assert [line["line_base"] for line in body["lines"]] == ["100.00", "150.00"]
    assert body["supplier"]["tax_id"] == SUPPLIER_ONE
    assert body["supplier"]["tax_id_valid"] is True
    assert body["supplier"]["tax_id_type"] == "CIF"
    assert body["audit_summary"].startswith("Factura validada: se superaron correctamente")
    assert body["validation_logs"]
    assert all(log["passed"] for log in body["validation_logs"])
    assert {log["run_id"] for log in body["validation_logs"]} == {body["last_audit_run_id"]}


def test_manual_create_detects_discrepancy(client):
    bad_line = line_dict("Servicio", "3", "50.00")
    bad_line["line_base"] = "155.00"
    body = create_invoice(client, lines=[bad_line], tax_base="155.00", total_vat="31.50", total_amount="186.50")
    assert body["audit_status"] == "DISCREPANCY"
    errors = {log["rule_code"]: log for log in failed_logs(body) if log["severity"] == "ERROR"}
    assert set(errors) == {"LINE_BASE", "LINE_VAT"}  # the VAT was computed on the real base, not on the typed one
    error = errors["LINE_BASE"]
    assert error["line_number"] == 1
    assert (error["expected_value"], error["actual_value"]) == ("150.00", "155.00")
    assert "150.00" in body["audit_summary"]


def test_manual_create_detects_cent_errors_in_totals(client):
    body = create_invoice(client, total_vat="52.51", total_amount="302.51")
    assert body["audit_status"] == "DISCREPANCY"
    assert body["error_count"] == 1
    [error] = failed_logs(body)
    assert error["rule_code"] == "VAT_TOTAL"
    assert error["message"] == "El IVA total debería ser 52.50 € y figura 52.51 € (diferencia 0.01 €)"


def test_manual_create_invalid_tax_id_is_discrepancy(client):
    body = create_invoice(client, supplier_tax_id="B12345670")
    assert body["audit_status"] == "DISCREPANCY"
    assert body["supplier"]["tax_id_valid"] is False
    [error] = failed_logs(body)
    assert error["rule_code"] == "SUPPLIER_TAX_ID"
    assert error["message"].endswith("Dígito de control incorrecto: se esperaba '4' y se encontró '0'")


def test_manual_create_irpf_invoice_is_validated(client):
    body = create_invoice(client, lines=[line_dict("Honorarios", "1", "1000.00", irpf_rate="15")])
    assert body["audit_status"] == "VALIDATED"
    assert (body["tax_base"], body["total_vat"], body["total_irpf"], body["total_amount"]) == (
        "1000.00",
        "210.00",
        "150.00",
        "1060.00",
    )
    assert body["lines"][0]["irpf_amount"] == "150.00"


def test_manual_create_rounds_money_half_up(client):
    # 2.675 is an exact Decimal here: the stored cent must be 2.68 and the totals stay consistent.
    body = create_invoice(
        client,
        lines=[line_dict("Pieza", "1", "2.68", "0")],
        tax_base="2.675",
        total_vat="0.00",
        total_amount="2.68",
    )
    assert body["tax_base"] == "2.68"
    assert "LINE_BASE" not in {log["rule_code"] for log in failed_logs(body)}


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(lambda p: p.update(lines=[]), id="no-lines"),
        pytest.param(lambda p: p.update(issue_date="no-es-una-fecha"), id="bad-date"),
        pytest.param(lambda p: p.pop("supplier"), id="no-supplier"),
        pytest.param(lambda p: p.pop("invoice_number"), id="no-number"),
        pytest.param(lambda p: p.update(invoice_number=""), id="empty-number"),
        pytest.param(lambda p: p.update(invoice_number="X" * 51), id="long-number"),
        pytest.param(lambda p: p.update(currency="EURO"), id="bad-currency"),
        pytest.param(lambda p: p.update(tax_base="abc"), id="non-numeric-total"),
        pytest.param(lambda p: p.pop("total_amount"), id="no-total"),
        pytest.param(lambda p: p["lines"][0].update(quantity="0"), id="zero-quantity"),
        pytest.param(lambda p: p["lines"][0].update(quantity="-1"), id="negative-quantity"),
        pytest.param(lambda p: p["lines"][0].update(vat_rate="101"), id="vat-over-100"),
        pytest.param(lambda p: p["lines"][0].update(discount_rate="-1"), id="negative-discount"),
        pytest.param(lambda p: p["lines"][0].update(concept=""), id="empty-concept"),
        pytest.param(lambda p: p["supplier"].update(tax_id="---"), id="supplier-tax-id-only-separators"),
        pytest.param(lambda p: p["supplier"].update(name=""), id="empty-supplier-name"),
        pytest.param(lambda p: p.update(buyer_country_code="ES"), id="two-letter-country"),
    ],
)
def test_manual_create_rejects_invalid_payloads_without_persisting(client, mutation):
    payload = invoice_payload()
    mutation(payload)
    response = client.post(f"{API}/invoices", json=payload)
    assert response.status_code == 422
    assert client.get(f"{API}/invoices").json()["total"] == 0
    assert client.get(f"{API}/suppliers").json()["total"] == 0


def test_manual_create_rejects_out_of_range_amount(client):
    response = client.post(f"{API}/invoices", json=invoice_payload(tax_base="9" * 29))
    assert response.status_code == 422
    assert response.json()["detail"] == f"Importe fuera de rango: {'9' * 29}"
    assert client.get(f"{API}/invoices").json()["total"] == 0
    assert client.get(f"{API}/suppliers").json()["total"] == 0


def test_manual_create_rolls_back_on_unexpected_failure(client, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("fallo simulado")

    with monkeypatch.context() as patch, pytest.raises(RuntimeError):
        patch.setattr(InvoiceAuditor, "audit", explode)
        client.post(f"{API}/invoices", json=invoice_payload())
    assert client.get(f"{API}/invoices").json()["total"] == 0
    assert client.get(f"{API}/suppliers").json()["total"] == 0


def test_duplicate_manual_invoice(client):
    first = create_invoice(client, "DUP-1")
    second = create_invoice(client, "DUP-1")
    assert first["audit_status"] == "VALIDATED"
    assert second["audit_status"] == "DUPLICATE"
    assert second["id"] != first["id"]
    assert second["supplier"]["id"] == first["supplier"]["id"]
    [error] = failed_logs(second)
    assert error["rule_code"] == "DUPLICATE"
    assert f"#{first['id']}" in error["message"]


def test_manual_invoice_reuses_existing_supplier(client):
    first = create_invoice(client, "S-1")
    second = create_invoice(client, "S-2", supplier_name="Otro nombre comercial")
    assert first["supplier"]["id"] == second["supplier"]["id"]
    assert second["supplier"]["name"] == "Suministros Norte S.L."
    assert client.get(f"{API}/suppliers").json()["total"] == 1


def test_existing_supplier_gets_missing_contact_data_completed_but_not_overwritten(client):
    first = create_invoice(client, "C-1", supplier_name="Sin datos S.L.")
    supplier_id = first["supplier"]["id"]
    client.patch(f"{API}/suppliers/{supplier_id}", json={"city": None, "email": None})

    payload = invoice_payload("C-2")
    payload["supplier"].update(city="Valencia", email="info@norte.es", address="Otra calle 99")
    second = client.post(f"{API}/invoices", json=payload).json()

    assert second["supplier"]["id"] == supplier_id
    assert second["supplier"]["city"] == "Valencia"  # was empty -> completed
    assert second["supplier"]["email"] == "info@norte.es"
    assert second["supplier"]["address"] == "Calle Mayor 1"  # already known -> kept


# --------------------------------------------------------------------------- Correction (PATCH) and re-audit


def test_patch_fixes_cent_error_and_reaudits_with_only_the_latest_logs(client):
    created = create_invoice(client, total_amount="302.52")
    assert created["audit_status"] == "DISCREPANCY"
    [old_error] = failed_logs(created)
    assert old_error["rule_code"] == "GRAND_TOTAL"
    assert old_error["message"] == (
        "El importe total debería ser 302.50 € (250.00 € + 52.50 € de IVA - 0.00 € de IRPF) "
        "y figura 302.52 € (diferencia 0.02 €)"
    )
    assert (old_error["expected_value"], old_error["actual_value"]) == ("302.50", "302.52")
    invoice_id = created["id"]

    patched = client.patch(f"{API}/invoices/{invoice_id}", json={"total_amount": "302.50"})
    assert patched.status_code == 200
    body = patched.json()
    assert body["audit_status"] == "VALIDATED"
    assert (body["error_count"], body["warning_count"]) == (0, 0)
    assert body["total_amount"] == "302.50"
    assert body["audit_summary"].startswith("Factura validada")
    assert body["last_audit_run_id"] != created["last_audit_run_id"]
    assert {log["run_id"] for log in body["validation_logs"]} == {body["last_audit_run_id"]}
    assert failed_logs(body) == []
    assert len(body["validation_logs"]) == len(created["validation_logs"])
    assert body["last_audited_at"] >= created["last_audited_at"]

    latest = logs_of(client, invoice_id)
    everything = logs_of(client, invoice_id, all_runs=True)
    assert {log["run_id"] for log in latest} == {body["last_audit_run_id"]}
    assert len(everything) == len(latest) + len(created["validation_logs"])
    assert {log["run_id"] for log in everything} == {body["last_audit_run_id"], created["last_audit_run_id"]}
    history_errors = [log for log in everything if not log["passed"]]
    assert [log["message"] for log in history_errors] == [old_error["message"]]


def test_patch_that_introduces_a_cent_error_flips_to_discrepancy(client):
    created = create_invoice(client)
    patched = client.patch(f"{API}/invoices/{created['id']}", json={"total_vat": "52.49"}).json()
    assert patched["audit_status"] == "DISCREPANCY"
    assert {log["rule_code"] for log in failed_logs(patched)} == {"VAT_TOTAL", "GRAND_TOTAL"}
    assert len(logs_of(client, created["id"])) == len(patched["validation_logs"])


def test_patch_rounds_amounts_half_up_before_auditing(client):
    created = create_invoice(client)
    body = client.patch(f"{API}/invoices/{created['id']}", json={"total_amount": "302.505"}).json()
    assert body["total_amount"] == "302.51"  # banker's rounding would store 302.50
    [error] = failed_logs(body)
    assert (error["rule_code"], error["expected_value"], error["actual_value"]) == ("GRAND_TOTAL", "302.50", "302.51")


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


def test_patch_lines_are_renumbered_following_explicit_order(client):
    created = create_invoice(client)
    second = {**line_dict("Segunda", "1", "10.00"), "line_number": 5}
    first = {**line_dict("Primera", "1", "20.00"), "line_number": 2}
    body = client.patch(
        f"{API}/invoices/{created['id']}",
        json={"lines": [second, first], "tax_base": "30.00", "total_vat": "6.30", "total_amount": "36.30"},
    ).json()
    assert [(line["line_number"], line["concept"]) for line in body["lines"]] == [(1, "Primera"), (2, "Segunda")]
    assert body["audit_status"] == "VALIDATED"


def test_patch_with_empty_lines_leaves_an_invoice_without_lines(client):
    created = create_invoice(client)
    body = client.patch(f"{API}/invoices/{created['id']}", json={"lines": []}).json()
    assert body["lines"] == []
    assert body["audit_status"] == "DISCREPANCY"
    assert "HAS_LINES" in {log["rule_code"] for log in failed_logs(body)}


def test_patch_plain_fields_are_trimmed_and_audited(client):
    created = create_invoice(client)
    body = client.patch(
        f"{API}/invoices/{created['id']}",
        json={
            "invoice_number": "  2024-777  ",
            "invoice_series": " A ",
            "notes": "  revisada  ",
            "buyer_name": " Ayuntamiento Nuevo ",
            "buyer_postal_code": "8002",
            "operation_date": "2024-03-01",
            "due_date": "2024-02-01",
            "dir3_managing_body": None,
        },
    ).json()
    assert body["invoice_number"] == "2024-777"
    assert body["invoice_series"] == "A"
    assert body["notes"] == "revisada"
    assert body["buyer_name"] == "Ayuntamiento Nuevo"
    assert body["operation_date"] == "2024-03-01"
    assert body["dir3_managing_body"] is None
    assert body["audit_status"] == "DISCREPANCY"  # due date before the issue date
    failed = {log["rule_code"]: log for log in failed_logs(body)}
    assert set(failed) == {"DATES", "FACE_DIR3", "FACE_PARTY_DATA"}
    assert failed["DATES"]["message"].endswith("en 43 día(s)")
    assert "'8002' no tiene 5 dígitos" in failed["FACE_PARTY_DATA"]["message"]


def test_patch_can_clear_optional_fields(client):
    created = create_invoice(client, invoice_series="F", notes="algo")
    body = client.patch(
        f"{API}/invoices/{created['id']}", json={"due_date": None, "invoice_series": None, "notes": None}
    ).json()
    assert (body["due_date"], body["invoice_series"], body["notes"]) == (None, None, None)
    assert body["audit_status"] == "VALIDATED"


def test_patch_only_touches_the_given_fields(client):
    created = create_invoice(client, notes="original")
    body = client.patch(f"{API}/invoices/{created['id']}", json={"buyer_city": "Toledo"}).json()
    assert body["buyer_city"] == "Toledo"
    for field in ("invoice_number", "issue_date", "tax_base", "total_amount", "notes", "buyer_name", "currency"):
        assert body[field] == created[field], field
    assert len(body["lines"]) == 2


def test_patch_supplier_tax_id_relinks_supplier(client):
    created = create_invoice(client, supplier_tax_id="B12345670")
    assert created["audit_status"] == "DISCREPANCY"

    patched = client.patch(f"{API}/invoices/{created['id']}", json={"supplier_tax_id": SUPPLIER_ONE})
    assert patched.status_code == 200
    body = patched.json()
    assert body["audit_status"] == "VALIDATED"
    assert body["supplier_tax_id"] == SUPPLIER_ONE
    assert body["supplier"]["id"] == created["supplier"]["id"]  # the only invoice of the supplier: renamed in place
    assert body["supplier"]["tax_id"] == SUPPLIER_ONE
    assert body["supplier"]["tax_id_valid"] is True
    assert client.get(f"{API}/suppliers").json()["total"] == 1


def test_patch_supplier_tax_id_clones_supplier_when_shared_with_other_invoices(client):
    first = create_invoice(client, "SH-1")
    second = create_invoice(client, "SH-2")
    assert first["supplier"]["id"] == second["supplier"]["id"]

    new_tax_id = make_cif("A", "1111111")
    body = client.patch(f"{API}/invoices/{second['id']}", json={"supplier_tax_id": f"es {new_tax_id.lower()}"}).json()

    assert body["supplier"]["id"] != first["supplier"]["id"]
    assert body["supplier"]["tax_id"] == new_tax_id
    assert body["supplier"]["name"] == "Suministros Norte S.L."  # contact data is copied
    assert body["supplier"]["address"] == "Calle Mayor 1"
    assert body["supplier"]["tax_id_valid"] is True
    assert client.get(f"{API}/invoices/{first['id']}").json()["supplier"]["tax_id"] == SUPPLIER_ONE
    assert client.get(f"{API}/suppliers").json()["total"] == 2


def test_patch_supplier_tax_id_to_another_existing_supplier(client):
    first = create_invoice(client, "EX-1")
    other = create_invoice(client, "EX-2", supplier_tax_id=SUPPLIER_TWO, supplier_name="Papelería Sur S.A.")

    body = client.patch(f"{API}/invoices/{first['id']}", json={"supplier_tax_id": SUPPLIER_TWO}).json()

    assert body["supplier"]["id"] == other["supplier"]["id"]
    assert body["supplier"]["name"] == "Papelería Sur S.A."
    assert body["supplier_tax_id"] == SUPPLIER_TWO
    suppliers = client.get(f"{API}/suppliers").json()
    assert {item["tax_id"]: item["invoice_count"] for item in suppliers["items"]} == {
        SUPPLIER_ONE: 0,
        SUPPLIER_TWO: 2,
    }


def test_patch_supplier_tax_id_with_same_value_keeps_the_supplier(client):
    created = create_invoice(client)
    body = client.patch(f"{API}/invoices/{created['id']}", json={"supplier_tax_id": f"ES-{SUPPLIER_ONE}"}).json()
    assert body["supplier"]["id"] == created["supplier"]["id"]
    assert client.get(f"{API}/suppliers").json()["total"] == 1


def test_patch_supplier_tax_id_without_usable_characters_is_rejected(client):
    created = create_invoice(client)
    response = client.patch(f"{API}/invoices/{created['id']}", json={"supplier_tax_id": "ÑÑÑ"})
    assert response.status_code == 422
    assert response.json()["detail"] == "El NIF/CIF del proveedor no puede estar vacío"
    assert client.get(f"{API}/invoices/{created['id']}").json()["supplier_tax_id"] == SUPPLIER_ONE


@pytest.mark.parametrize("field", ["invoice_number", "issue_date", "supplier_tax_id", "tax_base", "total_amount"])
def test_patch_rejects_null_for_required_fields(client, field):
    created = create_invoice(client)
    response = client.patch(f"{API}/invoices/{created['id']}", json={field: None})
    assert response.status_code == 422
    assert field in response.json()["detail"]
    assert stable(client.get(f"{API}/invoices/{created['id']}").json()) == stable(created)


def test_patch_rejects_malformed_values(client):
    created = create_invoice(client)
    for payload in ({"issue_date": "ayer"}, {"total_amount": "mucho"}, {"buyer_country_code": "ES"}):
        assert client.patch(f"{API}/invoices/{created['id']}", json=payload).status_code == 422
    assert client.patch(f"{API}/invoices/abc", json={"notes": "x"}).status_code == 422


# --------------------------------------------------------------------------- Revalidation


def test_revalidate_single(client):
    good = create_invoice(client, "R-1")
    single = client.post(f"{API}/invoices/{good['id']}/revalidate")
    assert single.status_code == 200
    report = single.json()
    assert report["invoice_id"] == good["id"]
    assert report["status"] == "VALIDATED"
    assert (report["error_count"], report["warning_count"]) == (0, 0)
    assert report["duplicate_of"] == []
    assert report["summary"].startswith("Factura validada")
    assert len(report["checks"]) == len(good["validation_logs"])
    assert report["run_id"] != good["last_audit_run_id"]
    assert report["audited_at"] >= good["last_audited_at"]

    detail = client.get(f"{API}/invoices/{good['id']}").json()
    assert detail["last_audit_run_id"] == report["run_id"]
    assert {log["run_id"] for log in detail["validation_logs"]} == {report["run_id"]}
    assert len(logs_of(client, good["id"], all_runs=True)) == 2 * len(report["checks"])


def test_revalidate_picks_up_cent_errors_introduced_outside_the_api(client, db_session):
    created = create_invoice(client)
    db_session.execute(Invoice.__table__.update().where(Invoice.id == created["id"]).values(tax_base=Decimal("250.03")))
    db_session.commit()

    report = client.post(f"{API}/invoices/{created['id']}/revalidate").json()

    assert report["status"] == "DISCREPANCY"
    failed = [c for c in report["checks"] if not c["passed"]]
    assert {c["rule_code"] for c in failed} == {"TAX_BASE_SUM", "GRAND_TOTAL"}
    base_check = next(c for c in failed if c["rule_code"] == "TAX_BASE_SUM")
    assert base_check["message"] == (
        "La base imponible debería ser 250.00 € (suma de las bases de las líneas) y figura 250.03 € (diferencia 0.03 €)"
    )
    assert client.get(f"{API}/invoices/{created['id']}").json()["audit_status"] == "DISCREPANCY"


def test_revalidate_clears_duplicate_when_the_original_is_deleted(client):
    original = create_invoice(client, "DUP-9")
    copy = create_invoice(client, "DUP-9")
    assert copy["audit_status"] == "DUPLICATE"
    assert client.delete(f"{API}/invoices/{original['id']}").status_code == 204

    report = client.post(f"{API}/invoices/{copy['id']}/revalidate").json()
    assert report["status"] == "VALIDATED"
    assert report["duplicate_of"] == []


def test_bulk_revalidate_selection_and_filters(client):
    good = create_invoice(client, "R-1")
    bad = create_invoice(client, "R-2", total_amount="1.00")
    other_bad = create_invoice(client, "R-3", total_amount="2.00")

    by_ids = client.post(f"{API}/invoices/revalidate", json={"invoice_ids": [bad["id"]]})
    assert by_ids.status_code == 200
    assert by_ids.json()["processed"] == 1
    [report] = by_ids.json()["reports"]
    assert (report["invoice_id"], report["status"]) == (bad["id"], "DISCREPANCY")
    assert report["run_id"] != bad["last_audit_run_id"]

    only_discrepancies = client.post(f"{API}/invoices/revalidate", json={"status": "DISCREPANCY"}).json()
    assert only_discrepancies["processed"] == 2
    assert [r["invoice_id"] for r in only_discrepancies["reports"]] == [bad["id"], other_bad["id"]]

    ids_and_status = client.post(
        f"{API}/invoices/revalidate", json={"invoice_ids": [good["id"], bad["id"]], "status": "VALIDATED"}
    ).json()
    assert [r["invoice_id"] for r in ids_and_status["reports"]] == [good["id"]]

    everything = client.post(f"{API}/invoices/revalidate", json={}).json()
    assert everything["processed"] == 3
    assert [r["invoice_id"] for r in everything["reports"]] == [good["id"], bad["id"], other_bad["id"]]


@pytest.mark.parametrize("ids", [[], [9999], [9998, 9999]])
def test_bulk_revalidate_with_no_matching_invoices(client, ids):
    create_invoice(client)
    body = client.post(f"{API}/invoices/revalidate", json={"invoice_ids": ids}).json()
    assert body == {"processed": 0, "reports": []}


def test_bulk_revalidate_status_that_matches_nothing(client):
    create_invoice(client)
    body = client.post(f"{API}/invoices/revalidate", json={"status": "DUPLICATE"}).json()
    assert body["processed"] == 0


def test_bulk_revalidate_flips_statuses_after_out_of_band_changes(client, db_session):
    first = create_invoice(client, "B-1")
    second = create_invoice(client, "B-2")
    db_session.execute(
        Invoice.__table__.update().where(Invoice.id == second["id"]).values(total_amount=Decimal("1.00"))
    )
    db_session.commit()

    reports = client.post(f"{API}/invoices/revalidate", json={"status": "VALIDATED"}).json()["reports"]

    assert {r["invoice_id"]: r["status"] for r in reports} == {first["id"]: "VALIDATED", second["id"]: "DISCREPANCY"}
    assert client.get(f"{API}/invoices", params={"status": "DISCREPANCY"}).json()["total"] == 1


def test_bulk_revalidate_rejects_bad_requests(client):
    assert client.post(f"{API}/invoices/revalidate", json={"status": "BOGUS"}).status_code == 422
    assert client.post(f"{API}/invoices/revalidate", json={"invoice_ids": ["x"]}).status_code == 422


def test_bulk_revalidate_rolls_back_when_one_audit_fails(client, monkeypatch):
    first = create_invoice(client, "RB-1")
    create_invoice(client, "RB-2")
    original = InvoiceAuditor.audit
    calls = []

    def flaky(self, invoice):
        calls.append(invoice.id)
        if len(calls) == 2:
            raise RuntimeError("fallo simulado")
        return original(self, invoice)

    with monkeypatch.context() as patch, pytest.raises(RuntimeError):
        patch.setattr(InvoiceAuditor, "audit", flaky)
        client.post(f"{API}/invoices/revalidate", json={})

    assert client.get(f"{API}/invoices/{first['id']}").json()["last_audit_run_id"] == first["last_audit_run_id"]
    assert len(logs_of(client, first["id"], all_runs=True)) == len(first["validation_logs"])


# --------------------------------------------------------------------------- Detail, logs and delete


def test_invoice_detail_shape(client):
    created = create_invoice(client, "D-1", invoice_series="F", operation_date="2024-03-10", notes="nota")
    body = client.get(f"{API}/invoices/{created['id']}").json()
    assert stable(body) == stable(created)
    assert body["invoice_series"] == "F"
    assert body["supplier_tax_id"] == SUPPLIER_ONE
    assert body["buyer_tax_id"] == BUYER_CIF
    assert body["buyer_country_code"] == "ESP"
    assert body["currency"] == "EUR"
    assert body["operation_date"] == "2024-03-10"
    assert body["due_date"] == "2024-12-31"
    assert body["notes"] == "nota"
    assert body["source_filename"] is None and body["source_file_hash"] is None
    assert body["lines"][0]["unit_price"] == "50.000000"
    assert body["lines"][0]["quantity"] == "2.0000"
    assert re.fullmatch(r"[0-9a-f-]{36}", body["last_audit_run_id"])


def test_audit_timestamp_is_serialised_identically_before_and_after_reload(client):
    created = create_invoice(client)
    reloaded = client.get(f"{API}/invoices/{created['id']}").json()
    assert reloaded["last_audited_at"] == created["last_audited_at"]


def test_logs_endpoint_only_returns_latest_run_by_default(client):
    created = create_invoice(client)
    client.post(f"{API}/invoices/{created['id']}/revalidate")
    latest = logs_of(client, created["id"])
    assert len({log["run_id"] for log in latest}) == 1
    assert [log["id"] for log in latest] == sorted(log["id"] for log in latest)
    assert len(logs_of(client, created["id"], all_runs=True)) == 2 * len(latest)
    assert logs_of(client, created["id"], all_runs=False) == latest


def test_never_audited_invoice_has_no_logs(client, db_session):
    supplier = Supplier(name="Sin auditar S.L.", tax_id=SUPPLIER_THREE)
    invoice = Invoice(
        supplier=supplier,
        supplier_tax_id=SUPPLIER_THREE,
        invoice_number="P-1",
        issue_date=date(2024, 5, 1),
        tax_base=Decimal("10.00"),
        total_amount=Decimal("10.00"),
    )
    db_session.add(invoice)
    db_session.commit()

    assert logs_of(client, invoice.id) == []
    assert logs_of(client, invoice.id, all_runs=True) == []
    body = client.get(f"{API}/invoices/{invoice.id}").json()
    assert body["audit_status"] == "PENDING"
    assert body["validation_logs"] == [] and body["last_audit_run_id"] is None and body["last_audited_at"] is None
    assert invoice_service.to_invoice_read(invoice).validation_logs == []


def test_delete_invoice(client, db_session):
    created = create_invoice(client)
    assert client.delete(f"{API}/invoices/{created['id']}").status_code == 204
    assert client.get(f"{API}/invoices/{created['id']}").status_code == 404
    assert client.get(f"{API}/invoices").json()["total"] == 0
    assert client.delete(f"{API}/invoices/{created['id']}").status_code == 404

    # Lines and audit logs go away with the invoice; the supplier stays.
    assert db_session.scalar(select(func.count()).select_from(InvoiceLine)) == 0
    assert db_session.scalar(select(func.count()).select_from(ValidationLog)) == 0
    assert client.get(f"{API}/suppliers/{created['supplier']['id']}").json()["invoice_count"] == 0


def test_delete_removes_the_stored_file(client, fake_extractor):
    content = pdf_bytes("borrar")
    invoice_id = upload(client, content).json()["invoice"]["id"]
    [path] = stored_files()
    assert path.read_bytes() == content

    assert client.delete(f"{API}/invoices/{invoice_id}").status_code == 204

    assert not path.exists()
    assert stored_files() == []
    assert client.get(f"{API}/invoices/{invoice_id}/file").status_code == 404


def test_delete_survives_a_file_that_cannot_be_removed(client, fake_extractor, monkeypatch):
    invoice_id = upload(client, pdf_bytes("bloqueado")).json()["invoice"]["id"]

    def locked(self, *_args, **_kwargs):
        raise PermissionError("archivo en uso")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", locked)
        assert client.delete(f"{API}/invoices/{invoice_id}").status_code == 204

    assert client.get(f"{API}/invoices/{invoice_id}").status_code == 404
    assert len(stored_files()) == 1


def test_delete_rolls_back_on_database_failure(client, monkeypatch):
    created = create_invoice(client)

    def explode(*_args, **_kwargs):
        raise RuntimeError("fallo simulado")

    with monkeypatch.context() as patch, pytest.raises(RuntimeError):
        patch.setattr(Session, "delete", explode)
        client.delete(f"{API}/invoices/{created['id']}")
    assert client.get(f"{API}/invoices/{created['id']}").status_code == 200


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
        assert response.json() == {"detail": "Factura 9999 no encontrada"}, path


# --------------------------------------------------------------------------- Original file download


def test_original_file_is_not_available_for_manual_invoices(client):
    created = create_invoice(client)
    response = client.get(f"{API}/invoices/{created['id']}/file")
    assert response.status_code == 404
    assert response.json() == {"detail": "Esta factura no tiene archivo original disponible"}


def test_original_file_missing_on_disk_is_404(client, fake_extractor):
    invoice_id = upload(client, pdf_bytes("perdido")).json()["invoice"]["id"]
    [path] = stored_files()
    path.unlink()
    assert client.get(f"{API}/invoices/{invoice_id}/file").status_code == 404


@pytest.mark.parametrize(
    "stored_path", ["../../secreto.txt", "..\\..\\secreto.txt", "/etc/passwd", "C:/Windows/win.ini"]
)
def test_original_file_never_escapes_the_upload_directory(client, db_session, stored_path):
    created = create_invoice(client)
    db_session.execute(
        Invoice.__table__.update().where(Invoice.id == created["id"]).values(source_file_path=stored_path)
    )
    db_session.commit()
    assert client.get(f"{API}/invoices/{created['id']}/file").status_code == 404


# --------------------------------------------------------------------------- Upload pipeline


def test_upload_pipeline_with_stored_file(client, fake_extractor):
    content = pdf_bytes("uno")
    response = upload(client, content)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["extraction_method"] == "DEMO"
    assert body["extraction_warnings"] == ["Dato de prueba"]
    assert body["audit"]["status"] == "VALIDATED"
    assert body["audit"]["run_id"] == body["invoice"]["last_audit_run_id"]
    invoice = body["invoice"]
    assert invoice["audit_status"] == "VALIDATED"
    assert invoice["extraction_method"] == "DEMO"
    assert invoice["extraction_confidence"] == 0.9
    assert invoice["source_filename"] == "factura.pdf"
    assert invoice["source_mime_type"] == "application/pdf"
    assert invoice["source_file_hash"] == hashlib.sha256(content).hexdigest()
    assert invoice["supplier"]["tax_id"] == SUPPLIER_ONE
    assert [line["concept"] for line in invoice["lines"]] == ["Servicio"]
    assert fake_extractor.calls == 1

    files = stored_files()
    assert len(files) == 1
    assert files[0].name == f"{invoice['source_file_hash']}.pdf"
    assert files[0].read_bytes() == content

    original = client.get(f"{API}/invoices/{invoice['id']}/file")
    assert original.status_code == 200
    assert original.content == content
    assert original.headers["content-type"] == "application/pdf"
    assert original.headers["content-disposition"].startswith("inline;")
    assert "factura.pdf" in original.headers["content-disposition"]


@pytest.mark.parametrize(
    ("content", "filename", "declared", "mime", "extension"),
    [
        pytest.param(b"%PDF-1.7\n" + b"0" * 64, "a.pdf", "application/pdf", "application/pdf", ".pdf", id="pdf"),
        pytest.param(b"\x89PNG\r\n\x1a\n" + b"0" * 64, "a.png", "image/png", "image/png", ".png", id="png"),
        pytest.param(b"\xff\xd8\xff\xe0" + b"0" * 64, "a.jpg", "image/jpeg", "image/jpeg", ".jpg", id="jpeg"),
        pytest.param(b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 64, "a.webp", "image/webp", "image/webp", ".webp", id="webp"),
        pytest.param(
            b"\x89PNG\r\n\x1a\n" + b"0" * 64,
            "scan",
            "application/octet-stream",
            "image/png",
            ".png",
            id="png-by-content",
        ),
        pytest.param(
            b"%PDF-1.4\n" + b"0" * 64, "nota.txt", "text/plain", "application/pdf", ".pdf", id="pdf-wrong-name"
        ),
    ],
)
def test_upload_accepts_supported_types_detected_from_content(
    client, fake_extractor, content, filename, declared, mime, extension
):
    response = upload(client, content, filename, declared)
    assert response.status_code == 201, response.text
    invoice = response.json()["invoice"]
    assert invoice["source_mime_type"] == mime
    [path] = stored_files()
    assert path.suffix == extension
    assert client.get(f"{API}/invoices/{invoice['id']}/file").headers["content-type"] == mime


def test_upload_png_sample_through_the_real_pipeline(client):
    png = next((path for path in SAMPLES_DIR.glob("*.png")), None) if SAMPLES_DIR.exists() else None
    if png is None:
        pytest.skip("sample PNG has not been generated")
    response = upload(client, png.read_bytes(), png.name, "image/png")
    assert response.status_code == 201, response.text
    invoice = response.json()["invoice"]
    assert invoice["source_mime_type"] == "image/png"
    assert invoice["source_filename"] == png.name
    assert stored_files()[0].suffix == ".png"


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
    assert second_body["audit"]["duplicate_of"] == [first["invoice"]["id"]]
    assert {
        c["field"] for c in second_body["audit"]["checks"] if c["rule_code"] == "DUPLICATE" and not c["passed"]
    } == {
        "invoice_number",
        "source_file_hash",
    }
    assert len(stored_files()) == 1

    assert client.delete(f"{API}/invoices/{first['invoice']['id']}").status_code == 204
    assert len(stored_files()) == 1, "the file is still referenced by the duplicate"
    assert client.get(f"{API}/invoices/{second_body['invoice']['id']}/file").content == content
    assert client.delete(f"{API}/invoices/{second_body['invoice']['id']}").status_code == 204
    assert stored_files() == []


def test_same_content_different_number_is_still_a_duplicate_file(client, fake_extractor):
    content = pdf_bytes("mismo")
    first = upload(client, content).json()
    fake_extractor.result = make_extraction("F-200")
    second = upload(client, content).json()
    assert second["audit"]["status"] == "DUPLICATE"
    assert second["audit"]["duplicate_of"] == [first["invoice"]["id"]]
    assert [c["field"] for c in second["audit"]["checks"] if c["rule_code"] == "DUPLICATE" and not c["passed"]] == [
        "source_file_hash"
    ]


def test_upload_extraction_error_returns_422_and_leaves_nothing(client, fake_extractor):
    fake_extractor.error = ExtractionError("el documento no contiene una factura")
    response = upload(client, pdf_bytes("err"))
    assert response.status_code == 422
    assert response.json() == {"detail": "No se pudo extraer la factura: el documento no contiene una factura"}
    assert stored_files() == []
    assert client.get(f"{API}/invoices").json()["total"] == 0
    assert client.get(f"{API}/suppliers").json()["total"] == 0


def test_upload_rolls_back_and_removes_file_on_failure(client, fake_extractor, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("fallo simulado")

    with monkeypatch.context() as patch, pytest.raises(RuntimeError):
        patch.setattr(invoice_service, "create_invoice_from_extraction", explode)
        upload(client, pdf_bytes("boom"))
    assert stored_files() == []
    assert client.get(f"{API}/invoices").json()["total"] == 0


def test_failed_upload_keeps_a_file_that_another_invoice_uses(client, fake_extractor, monkeypatch):
    content = pdf_bytes("compartido")
    assert upload(client, content).status_code == 201

    def explode(*_args, **_kwargs):
        raise RuntimeError("fallo simulado")

    with monkeypatch.context() as patch, pytest.raises(RuntimeError):
        patch.setattr(InvoiceAuditor, "audit", explode)
        upload(client, content)
    assert len(stored_files()) == 1
    assert client.get(f"{API}/invoices").json()["total"] == 1


@pytest.mark.parametrize(
    ("content", "filename", "declared", "detected"),
    [
        pytest.param(
            b"esto es texto plano, no una factura", "notas.txt", "text/plain", "application/octet-stream", id="text"
        ),
        pytest.param(b"GIF89a" + b"0" * 32, "animacion.gif", "image/gif", "application/octet-stream", id="gif"),
        pytest.param(
            b"PK\x03\x04" + b"0" * 32, "factura.docx", "application/zip", "application/octet-stream", id="zip"
        ),
        pytest.param(
            b"MZ" + b"0" * 32, "programa.exe", "application/pdf", "application/octet-stream", id="exe-labelled-pdf"
        ),
    ],
)
def test_upload_rejects_unsupported_types(client, fake_extractor, content, filename, declared, detected):
    response = upload(client, content, filename, declared)
    assert response.status_code == 415
    assert response.json() == {
        "detail": f"Tipo de archivo no soportado ({detected}). "
        "Formatos admitidos: application/pdf, image/png, image/jpeg, image/webp"
    }
    assert fake_extractor.calls == 0
    assert stored_files() == []
    assert client.get(f"{API}/invoices").json()["total"] == 0


def test_upload_size_limit_boundary(client, fake_extractor, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_size_mb", 1)
    limit = 1024 * 1024
    exact = b"%PDF-1.4" + b"0" * (limit - 8)
    assert len(exact) == limit
    assert upload(client, exact).status_code == 201

    response = upload(client, exact + b"0")
    assert response.status_code == 413
    assert response.json() == {"detail": "El archivo supera el tamaño máximo de 1 MB"}
    assert fake_extractor.calls == 1
    assert len(stored_files()) == 1
    assert client.get(f"{API}/invoices").json()["total"] == 1


def test_upload_rejects_oversized_file(client, fake_extractor, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_size_mb", 1)
    response = upload(client, b"%PDF-1.4" + b"0" * (1024 * 1024))
    assert response.status_code == 413
    assert "tamaño máximo" in response.json()["detail"]
    assert fake_extractor.calls == 0
    assert stored_files() == []


def test_upload_rejects_empty_file(client, fake_extractor):
    response = upload(client, b"")
    assert response.status_code == 422
    assert response.json() == {"detail": "El archivo está vacío"}
    assert fake_extractor.calls == 0
    assert stored_files() == []


def test_upload_requires_file_field(client):
    assert client.post(f"{API}/invoices/upload").status_code == 422
    assert client.post(f"{API}/invoices/upload", data={"file": "texto"}).status_code == 422


def test_upload_without_issuer_tax_id_creates_a_flagged_placeholder_supplier(client, fake_extractor):
    fake_extractor.result.supplier = ExtractedParty(name="Sin NIF S.L.", tax_id=None)
    first = upload(client, pdf_bytes("sin-nif-1")).json()
    invoice = first["invoice"]

    assert invoice["audit_status"] == "DISCREPANCY"
    assert invoice["supplier_tax_id"] == ""
    assert re.fullmatch(r"SINID[0-9A-F]{12}", invoice["supplier"]["tax_id"])
    assert invoice["supplier"]["tax_id_valid"] is False
    [error] = [log for log in failed_logs(invoice) if log["severity"] == "ERROR"]
    assert error["rule_code"] == "SUPPLIER_TAX_ID"
    assert error["message"] == (
        "El identificador fiscal del emisor '' no es válido: No se ha indicado ningún NIF/NIE/CIF"
    )

    fake_extractor.result = make_extraction("F-101")
    fake_extractor.result.supplier = ExtractedParty(name="SIN NIF S.L.", tax_id=None)
    second = upload(client, pdf_bytes("sin-nif-2")).json()["invoice"]
    assert (
        second["supplier"]["id"] == invoice["supplier"]["id"]
    )  # the placeholder is stable per name (case-insensitive)


def test_upload_with_invalid_issuer_tax_id_flags_the_supplier(client, fake_extractor):
    fake_extractor.result = make_extraction("F-300", tax_id="B12345670")
    invoice = upload(client, pdf_bytes("cif-malo")).json()["invoice"]
    assert invoice["audit_status"] == "DISCREPANCY"
    assert invoice["supplier"]["tax_id"] == "B12345670"
    assert invoice["supplier"]["tax_id_valid"] is False
    assert invoice["supplier"]["tax_id_type"] == "CIF"


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
    assert body["extraction_method"] == "DEMO"
    assert body["audit"]["checks"]


SAMPLE_PDFS = (
    sorted(path for path in SAMPLES_DIR.iterdir() if path.suffix.lower() in (".pdf", ".png"))
    if SAMPLES_DIR.exists()
    else []
)
# Expected audit outcome per generated sample: (status, failed ERROR rules, minimum number of lines).
SAMPLE_OUTCOMES = {
    "01_factura_valida_iva21.pdf": ("VALIDATED", set(), 1),
    "02_factura_iva_mixto_21_10_4.pdf": ("VALIDATED", set(), 3),
    "03_factura_profesional_irpf15.pdf": ("VALIDATED", set(), 1),
    "04_factura_error_calculo_linea.pdf": ("DISCREPANCY", {"LINE_BASE"}, 1),
    "05_factura_cif_proveedor_invalido.pdf": ("DISCREPANCY", {"SUPPLIER_TAX_ID"}, 1),
    "06_factura_face_dir3.pdf": ("VALIDATED", set(), 1),
    "07_factura_imagen_demo.png": ("DISCREPANCY", {"SYNTHETIC_DATA"}, 1),  # images are never really read
}


@pytest.mark.skipif(not SAMPLE_PDFS, reason="sample PDFs have not been generated")
@pytest.mark.parametrize("sample", SAMPLE_PDFS, ids=[path.name for path in SAMPLE_PDFS])
def test_upload_each_sample_pdf(client, sample):
    status, error_rules, min_lines = SAMPLE_OUTCOMES[sample.name]
    response = upload(
        client, sample.read_bytes(), sample.name, "image/png" if sample.suffix == ".png" else "application/pdf"
    )
    assert response.status_code == 201, response.text
    body = response.json()
    invoice = body["invoice"]
    assert len(invoice["lines"]) >= min_lines
    assert invoice["audit_status"] == body["audit"]["status"] == status
    assert {
        c["rule_code"] for c in body["audit"]["checks"] if not c["passed"] and c["severity"] == "ERROR"
    } == error_rules
    assert invoice["source_filename"] == sample.name
    assert invoice["source_mime_type"] == ("image/png" if sample.suffix == ".png" else "application/pdf")
    assert Decimal(invoice["total_amount"]) > 0
    assert invoice["error_count"] == len(error_rules)
    assert client.get(f"{API}/invoices/{invoice['id']}/file").content == sample.read_bytes()


@pytest.mark.skipif(not SAMPLE_PDFS, reason="sample PDFs have not been generated")
def test_samples_cover_valid_and_invalid_invoices(client):
    statuses = {
        upload(client, sample.read_bytes(), sample.name).json()["invoice"]["audit_status"] for sample in SAMPLE_PDFS
    }
    assert {"VALIDATED", "DISCREPANCY"} <= statuses


@pytest.mark.skipif(not SAMPLE_PDFS, reason="sample PDFs have not been generated")
def test_sample_with_calculation_error_reports_the_offending_line(client):
    sample = SAMPLES_DIR / "04_factura_error_calculo_linea.pdf"
    if not sample.exists():
        pytest.skip("sample not generated")
    body = upload(client, sample.read_bytes(), sample.name).json()
    [error] = [c for c in body["audit"]["checks"] if not c["passed"] and c["rule_code"] == "LINE_BASE"]
    assert error["line_number"] == 1
    assert Decimal(error["expected_value"]) != Decimal(error["actual_value"])
    assert error["message"].startswith("Línea 1: la base imponible debería ser ")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("factura.pdf", "factura.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\doc.pdf", "doc.pdf"),
        ("a/b\\c.pdf", "c.pdf"),
        ("fac\x00tu\x07ra.pdf", "factura.pdf"),
        ("carpeta/", None),
        ("  espacios.pdf  ", "espacios.pdf"),
        ("x" * 300, "x" * 255),
    ],
)
def test_safe_filename(raw, expected):
    assert invoice_service.safe_filename(raw) == expected


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


@dataclass(frozen=True)
class Row:
    """Ground truth of one invoice, written by hand from the data each fixture creates."""

    id: int
    number: str
    supplier: str  # "one" | "two"
    supplier_id: int
    issued: date
    status: str
    total: Decimal
    method: str

    @property
    def tax_id(self) -> str:
        return SUPPLIER_ONE if self.supplier == "one" else SUPPLIER_TWO

    @property
    def supplier_name(self) -> str:
        return "Suministros Norte S.L." if self.supplier == "one" else "Papelería Sur S.A."


@pytest.fixture()
def rows(client, catalog, fake_extractor):
    """The four catalog invoices plus one uploaded invoice and its duplicate (6 rows, 3 statuses, 2 methods)."""
    first_upload = upload(client, pdf_bytes("dataset")).json()["invoice"]
    second_upload = upload(client, pdf_bytes("dataset")).json()["invoice"]
    one, two = catalog["a"]["supplier"]["id"], catalog["c"]["supplier"]["id"]
    assert first_upload["supplier"]["id"] == second_upload["supplier"]["id"] == one
    rows = [
        Row(catalog["a"]["id"], "A-001", "one", one, date(2024, 1, 10), "VALIDATED", Decimal("302.50"), "MANUAL"),
        Row(catalog["b"]["id"], "A-002", "one", one, date(2024, 1, 20), "VALIDATED", Decimal("110.00"), "MANUAL"),
        Row(catalog["c"]["id"], "B-001", "two", two, date(2024, 2, 5), "VALIDATED", Decimal("41.60"), "MANUAL"),
        Row(catalog["d"]["id"], "B-002", "two", two, date(2024, 2, 18), "DISCREPANCY", Decimal("250.00"), "MANUAL"),
        Row(first_upload["id"], "F-100", "one", one, date(2024, 3, 15), "VALIDATED", Decimal("121.00"), "DEMO"),
        Row(second_upload["id"], "F-100", "one", one, date(2024, 3, 15), "DUPLICATE", Decimal("121.00"), "DEMO"),
    ]
    for row in rows:
        stored = client.get(f"{API}/invoices/{row.id}").json()
        assert (stored["invoice_number"], stored["audit_status"], stored["total_amount"]) == (
            row.number,
            row.status,
            f"{row.total}",
        )
    return rows


SORT_KEYS = {
    "issue_date": lambda row: row.issued,
    "total_amount": lambda row: row.total,
    "created_at": lambda row: row.id,
    "invoice_number": lambda row: row.number,
    "audit_status": lambda row: row.status,
}


def expected_order(rows, params, sort_by="issue_date", sort_dir="desc") -> list[int]:
    """Independent re-implementation of filtering + sorting (ties broken by id in the sort direction)."""

    def keep(row: Row) -> bool:
        checks = [
            params.get("status") in (None, row.status),
            params.get("supplier_id") in (None, row.supplier_id),
            params.get("tax_id") is None
            or re.sub(r"[^0-9A-Z]", "", params["tax_id"].upper()).removeprefix("ES") == row.tax_id,
            params.get("date_from") is None or row.issued >= date.fromisoformat(params["date_from"]),
            params.get("date_to") is None or row.issued <= date.fromisoformat(params["date_to"]),
            params.get("min_total") is None or row.total >= Decimal(params["min_total"]),
            params.get("max_total") is None or row.total <= Decimal(params["max_total"]),
            params.get("extraction_method") in (None, row.method),
            not (params.get("search") or "").strip()
            or any(
                params["search"].strip().lower() in text.lower() for text in (row.number, row.supplier_name, row.tax_id)
            ),
        ]
        return all(checks)

    selected = [row for row in rows if keep(row)]
    selected.sort(key=lambda row: (SORT_KEYS[sort_by](row), row.id), reverse=sort_dir == "desc")
    return [row.id for row in selected]


def with_supplier_ids(rows, params):
    resolved = dict(params)
    if resolved.get("supplier_id") in ("one", "two"):
        resolved["supplier_id"] = next(row.supplier_id for row in rows if row.supplier == resolved["supplier_id"])
    return resolved


FILTER_CASES = [
    pytest.param({}, id="no-filter"),
    pytest.param({"status": "VALIDATED"}, id="status-validated"),
    pytest.param({"status": "DISCREPANCY"}, id="status-discrepancy"),
    pytest.param({"status": "DUPLICATE"}, id="status-duplicate"),
    pytest.param({"status": "PENDING"}, id="status-pending-none"),
    pytest.param({"supplier_id": "one"}, id="supplier-one"),
    pytest.param({"supplier_id": "two"}, id="supplier-two"),
    pytest.param({"supplier_id": 99999}, id="supplier-unknown"),
    pytest.param({"tax_id": SUPPLIER_ONE}, id="tax-id"),
    pytest.param({"tax_id": f" es {SUPPLIER_TWO[:3].lower()}-{SUPPLIER_TWO[3:]} "}, id="tax-id-noisy"),
    pytest.param({"tax_id": "B00000000"}, id="tax-id-unknown"),
    pytest.param({"date_from": "2024-02-05"}, id="date-from-inclusive"),
    pytest.param({"date_to": "2024-02-05"}, id="date-to-inclusive"),
    pytest.param({"date_from": "2024-01-20", "date_to": "2024-01-20"}, id="single-day"),
    pytest.param({"date_from": "2024-01-11", "date_to": "2024-03-14"}, id="date-window"),
    pytest.param({"date_from": "2024-03-01", "date_to": "2024-01-01"}, id="inverted-window"),
    pytest.param({"min_total": "121.00"}, id="min-total-inclusive"),
    pytest.param({"max_total": "110.00"}, id="max-total-inclusive"),
    pytest.param({"min_total": "110.00", "max_total": "121.00"}, id="total-range"),
    pytest.param({"min_total": "302.50", "max_total": "302.50"}, id="total-exact"),
    pytest.param({"min_total": "302.51"}, id="min-total-above-everything"),
    pytest.param({"extraction_method": "DEMO"}, id="method-demo"),
    pytest.param({"extraction_method": "MANUAL"}, id="method-manual"),
    pytest.param({"extraction_method": "AI"}, id="method-ai-none"),
    pytest.param({"search": "norte"}, id="search-supplier-name"),
    pytest.param({"search": "PAPELER"}, id="search-supplier-name-uppercase"),
    pytest.param({"search": "b-00"}, id="search-number"),
    pytest.param({"search": "f-100"}, id="search-number-upload"),
    pytest.param({"search": SUPPLIER_TWO.lower()[:6]}, id="search-tax-id-fragment"),
    pytest.param({"search": "100%"}, id="search-percent-is-literal"),
    pytest.param({"search": "A_001"}, id="search-underscore-is-literal"),
    pytest.param({"search": "   "}, id="search-blank"),
    pytest.param({"status": "VALIDATED", "supplier_id": "two"}, id="status+supplier"),
    pytest.param({"status": "VALIDATED", "date_from": "2024-01-15", "date_to": "2024-02-28"}, id="status+dates"),
    pytest.param({"tax_id": SUPPLIER_ONE, "min_total": "120", "max_total": "400"}, id="tax-id+totals"),
    pytest.param({"extraction_method": "DEMO", "status": "DUPLICATE"}, id="method+status"),
    pytest.param({"search": "a-0", "status": "VALIDATED", "max_total": "200"}, id="search+status+total"),
    pytest.param(
        {"supplier_id": "one", "extraction_method": "MANUAL", "date_to": "2024-01-15"}, id="supplier+method+date"
    ),
    pytest.param(
        {
            "status": "VALIDATED",
            "supplier_id": "one",
            "tax_id": SUPPLIER_ONE,
            "date_from": "2024-01-01",
            "date_to": "2024-12-31",
            "min_total": "100",
            "max_total": "400",
            "extraction_method": "MANUAL",
            "search": "a-0",
        },
        id="every-filter-at-once",
    ),
]


@pytest.mark.parametrize("params", FILTER_CASES)
def test_list_filters_match_an_independent_oracle(client, rows, params):
    resolved = with_supplier_ids(rows, params)
    response = client.get(f"{API}/invoices", params=resolved)
    assert response.status_code == 200, response.text
    body = response.json()
    expected = expected_order(rows, resolved)
    assert [item["id"] for item in body["items"]] == expected
    assert body["total"] == len(expected)
    assert body["pages"] == math.ceil(len(expected) / 20)


def test_search_is_case_insensitive_for_accented_letters(client, catalog):
    assert sorted(numbers(client.get(f"{API}/invoices", params={"search": "PAPELERÍA"}))) == ["B-001", "B-002"]


def test_filter_cases_are_not_vacuous(rows):
    """Guard against an oracle that always answers the same: the cases must produce several distinct results."""
    results = {tuple(expected_order(rows, with_supplier_ids(rows, case.values[0]))) for case in FILTER_CASES}
    assert len(results) >= 12
    assert () in results


def test_list_default_sort_and_summary_shape(client, catalog):
    response = client.get(f"{API}/invoices")
    body = response.json()
    assert (body["total"], body["page"], body["page_size"], body["pages"]) == (4, 1, 20, 1)
    assert [item["invoice_number"] for item in body["items"]] == ["B-002", "B-001", "A-002", "A-001"]
    first = body["items"][0]
    assert first["supplier_name"] == "Papelería Sur S.A."
    assert first["supplier_tax_id"] == SUPPLIER_TWO
    assert first["total_amount"] == "250.00"
    assert first["audit_status"] == "DISCREPANCY"
    assert first["error_count"] == 1
    assert first["issue_date"] == "2024-02-18"
    assert "lines" not in first and "validation_logs" not in first


@pytest.mark.parametrize("sort_dir", ["asc", "desc"])
@pytest.mark.parametrize("sort_by", sorted(SORT_KEYS))
def test_list_sorting_matches_oracle(client, rows, sort_by, sort_dir):
    response = client.get(f"{API}/invoices", params={"sort_by": sort_by, "sort_dir": sort_dir, "page_size": 200})
    assert [item["id"] for item in response.json()["items"]] == expected_order(rows, {}, sort_by, sort_dir)


def test_list_sorting_explicit_orders(client, catalog):
    by_total_asc = numbers(client.get(f"{API}/invoices", params={"sort_by": "total_amount", "sort_dir": "asc"}))
    assert by_total_asc == ["B-001", "A-002", "B-002", "A-001"]
    by_total_desc = numbers(client.get(f"{API}/invoices", params={"sort_by": "total_amount", "sort_dir": "desc"}))
    assert by_total_desc == by_total_asc[::-1]
    by_number = numbers(client.get(f"{API}/invoices", params={"sort_by": "invoice_number", "sort_dir": "asc"}))
    assert by_number == ["A-001", "A-002", "B-001", "B-002"]
    by_date = numbers(client.get(f"{API}/invoices", params={"sort_by": "issue_date", "sort_dir": "asc"}))
    assert by_date == ["A-001", "A-002", "B-001", "B-002"]
    by_status = numbers(client.get(f"{API}/invoices", params={"sort_by": "audit_status", "sort_dir": "asc"}))
    assert by_status[0] == "B-002"  # DISCREPANCY sorts before VALIDATED


def test_list_sorting_ties_are_broken_by_id_in_the_sort_direction(client, rows):
    ascending = client.get(f"{API}/invoices", params={"sort_by": "invoice_number", "sort_dir": "asc"}).json()["items"]
    descending = client.get(f"{API}/invoices", params={"sort_by": "invoice_number", "sort_dir": "desc"}).json()["items"]
    twins = [item["id"] for item in ascending if item["invoice_number"] == "F-100"]
    assert twins == sorted(twins)
    assert [item["id"] for item in descending if item["invoice_number"] == "F-100"] == twins[::-1]


@pytest.mark.parametrize(
    "params",
    [
        {"sort_by": "nonexistent"},
        {"sort_by": "supplier_name"},
        {"sort_dir": "sideways"},
        {"status": "BOGUS"},
        {"extraction_method": "MAGIC"},
        {"date_from": "15-01-2024"},
        {"date_to": "mañana"},
        {"min_total": "mucho"},
        {"supplier_id": "uno"},
        {"page": "primera"},
    ],
)
def test_list_rejects_invalid_query_values(client, params):
    assert client.get(f"{API}/invoices", params=params).status_code == 422


@pytest.mark.parametrize("page_size", [1, 2, 3, 4, 5, 6, 7, 200])
def test_list_walks_every_page_exactly_once(client, rows, page_size):
    everything = expected_order(rows, {})
    collected, page = [], 1
    while True:
        body = client.get(f"{API}/invoices", params={"page": page, "page_size": page_size}).json()
        assert (body["page"], body["page_size"], body["total"]) == (page, page_size, 6)
        assert body["pages"] == math.ceil(6 / page_size)
        if not body["items"]:
            break
        assert len(body["items"]) == (page_size if page < body["pages"] else 6 - page_size * (body["pages"] - 1))
        collected += [item["id"] for item in body["items"]]
        page += 1
    assert collected == everything
    assert page == body["pages"] + 1


def test_list_page_beyond_the_end_is_empty_but_keeps_totals(client, catalog):
    for page in (3, 4, 1000, 10**9):
        body = client.get(f"{API}/invoices", params={"page": page, "page_size": 3}).json()
        assert body == {"items": [], "total": 4, "page": page, "page_size": 3, "pages": 2}


def test_list_pagination_applies_to_filtered_results(client, rows):
    body = client.get(f"{API}/invoices", params={"status": "VALIDATED", "page_size": 2, "page": 2}).json()
    expected = expected_order(rows, {"status": "VALIDATED"})
    assert body["total"] == 4 and body["pages"] == 2
    assert [item["id"] for item in body["items"]] == expected[2:]


def test_list_pagination_bounds(client):
    assert client.get(f"{API}/invoices", params={"page": 0}).status_code == 422
    assert client.get(f"{API}/invoices", params={"page": -1}).status_code == 422
    assert client.get(f"{API}/invoices", params={"page_size": 0}).status_code == 422
    assert client.get(f"{API}/invoices", params={"page_size": 201}).status_code == 422
    assert client.get(f"{API}/invoices", params={"page_size": 200}).status_code == 200
    assert client.get(f"{API}/invoices", params={"page_size": 1}).status_code == 200
    assert client.get(f"{API}/invoices").json() == {"items": [], "total": 0, "page": 1, "page_size": 20, "pages": 0}


def test_list_page_size_200_returns_up_to_200_items(client, db_session):
    supplier = Supplier(name="Masivo S.L.", tax_id=SUPPLIER_THREE)
    db_session.add_all(
        Invoice(
            supplier=supplier,
            supplier_tax_id=SUPPLIER_THREE,
            invoice_number=f"M-{index:03d}",
            issue_date=date(2024, 1, 1),
            tax_base=Decimal("1.00"),
            total_amount=Decimal("1.00"),
        )
        for index in range(205)
    )
    db_session.commit()
    body = client.get(f"{API}/invoices", params={"page_size": 200}).json()
    assert (body["total"], body["pages"], len(body["items"])) == (205, 2, 200)
    tail = client.get(f"{API}/invoices", params={"page_size": 200, "page": 2}).json()
    assert len(tail["items"]) == 5
    assert {i["id"] for i in body["items"]}.isdisjoint({i["id"] for i in tail["items"]})


# --------------------------------------------------------------------------- Facturae


FACTURAE_NS = "http://www.facturae.gob.es/formato/Versiones/Facturaev3_2_2.xml"


def test_facturae_download_for_validated_invoice(client):
    created = create_invoice(client)
    response = client.get(f"{API}/invoices/{created['id']}/facturae", params={"sign": False})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/xml")
    assert response.headers["content-disposition"] == f'attachment; filename="facturae_{SUPPLIER_ONE}_2024-001.xml"'

    root = etree.fromstring(response.content)
    assert etree.QName(root).localname == "Facturae"
    assert etree.QName(root).namespace == FACTURAE_NS
    assert root.findtext("FileHeader/SchemaVersion") == "3.2.2"
    assert root.findtext("FileHeader/Batch/InvoicesCount") == "1"
    assert root.findtext("FileHeader/Batch/TotalInvoicesAmount/TotalAmount") == "302.50"
    assert root.findtext("Parties/SellerParty/TaxIdentification/TaxIdentificationNumber") == SUPPLIER_ONE
    assert root.findtext("Parties/SellerParty/LegalEntity/CorporateName") == "Suministros Norte S.L."
    assert root.findtext("Parties/BuyerParty/TaxIdentification/TaxIdentificationNumber") == BUYER_CIF

    [xml_invoice] = root.findall("Invoices/Invoice")
    assert xml_invoice.findtext("InvoiceHeader/InvoiceNumber") == "2024-001"
    assert xml_invoice.findtext("InvoiceIssueData/IssueDate") == "2024-03-15"
    assert xml_invoice.findtext("InvoiceTotals/TotalGrossAmount") == "250.00"
    assert xml_invoice.findtext("InvoiceTotals/TotalTaxOutputs") == "52.50"
    assert xml_invoice.findtext("InvoiceTotals/TotalTaxesWithheld") == "0.00"
    assert xml_invoice.findtext("InvoiceTotals/InvoiceTotal") == "302.50"
    assert [node.text for node in xml_invoice.findall("Items/InvoiceLine/ItemDescription")] == [
        "Servicio de consultoría",
        "Licencia anual",
    ]
    [vat] = xml_invoice.findall("TaxesOutputs/Tax")
    assert (
        vat.findtext("TaxRate"),
        vat.findtext("TaxableBase/TotalAmount"),
        vat.findtext("TaxAmount/TotalAmount"),
    ) == (
        "21.00",
        "250.00",
        "52.50",
    )


def test_facturae_includes_the_irpf_withholding(client):
    created = create_invoice(client, lines=[line_dict("Honorarios", "1", "1000.00", irpf_rate="15")])
    root = etree.fromstring(client.get(f"{API}/invoices/{created['id']}/facturae", params={"sign": False}).content)
    [xml_invoice] = root.findall("Invoices/Invoice")
    assert xml_invoice.findtext("InvoiceTotals/TotalTaxesWithheld") == "150.00"
    assert xml_invoice.findtext("InvoiceTotals/InvoiceTotal") == "1060.00"
    [withheld] = xml_invoice.findall("TaxesWithheld/Tax")
    assert withheld.findtext("TaxRate") == "15.00"
    assert withheld.findtext("TaxAmount/TotalAmount") == "150.00"


@pytest.mark.parametrize(
    ("number", "series", "filename"),
    [
        ("2024-001", None, f"facturae_{SUPPLIER_ONE}_2024-001.xml"),
        ("2024-001", "F", f"facturae_{SUPPLIER_ONE}_F-2024-001.xml"),
        ("2024/001", None, f"facturae_{SUPPLIER_ONE}_2024_001.xml"),
        ("FAC 12 ñ", None, f"facturae_{SUPPLIER_ONE}_FAC_12.xml"),
    ],
)
def test_facturae_filename_is_ascii_safe(client, number, series, filename):
    created = create_invoice(client, number, invoice_series=series)
    response = client.get(f"{API}/invoices/{created['id']}/facturae", params={"sign": False})
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == f'attachment; filename="{filename}"'


def test_facturae_conflict_for_discrepancy(client):
    created = create_invoice(client, total_amount="1.00")
    response = client.get(f"{API}/invoices/{created['id']}/facturae")
    assert response.status_code == 409
    assert response.json() == {
        "detail": "Solo se pueden exportar a Facturae las facturas validadas (estado actual: DISCREPANCY)."
    }


def test_facturae_conflict_for_duplicate_and_pending(client, db_session):
    create_invoice(client, "F-1")
    duplicate = create_invoice(client, "F-1")
    response = client.get(f"{API}/invoices/{duplicate['id']}/facturae")
    assert response.status_code == 409
    assert response.json()["detail"].endswith("(estado actual: DUPLICATE).")

    pending = Invoice(
        supplier_id=duplicate["supplier"]["id"],
        supplier_tax_id=SUPPLIER_ONE,
        invoice_number="PEND",
        issue_date=date(2024, 5, 1),
        tax_base=Decimal("1.00"),
        total_amount=Decimal("1.00"),
    )
    db_session.add(pending)
    db_session.commit()
    response = client.get(f"{API}/invoices/{pending.id}/facturae")
    assert response.status_code == 409
    assert response.json()["detail"].endswith("(estado actual: PENDING).")


def test_facturae_becomes_available_after_fixing_a_cent_error(client):
    created = create_invoice(client, total_amount="302.52")
    assert client.get(f"{API}/invoices/{created['id']}/facturae", params={"sign": False}).status_code == 409
    client.patch(f"{API}/invoices/{created['id']}", json={"total_amount": "302.50"})
    response = client.get(f"{API}/invoices/{created['id']}/facturae", params={"sign": False})
    assert response.status_code == 200
    root = etree.fromstring(response.content)
    assert root.findtext("Invoices/Invoice/InvoiceTotals/InvoiceTotal") == "302.50"


def test_facturae_signing_is_refused_without_an_api_key(client):
    created = create_invoice(client)
    response = client.get(f"{API}/invoices/{created['id']}/facturae", params={"sign": True})
    assert response.status_code == 403
    assert "API_KEY" in response.json()["detail"]


def test_facturae_signing_requires_a_certificate(client, monkeypatch):
    created = create_invoice(client)
    monkeypatch.setattr(get_settings(), "api_key", SecretStr("clave-de-prueba"))
    response = client.get(
        f"{API}/invoices/{created['id']}/facturae", params={"sign": True}, headers={"X-API-Key": "clave-de-prueba"}
    )
    assert response.status_code == 409
    assert "no hay certificado configurado" in response.json()["detail"]


# --------------------------------------------------------------------------- CSV export


def read_csv(response) -> list[list[str]]:
    assert response.status_code == 200, response.text
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:], newline=""), delimiter=";"))


def csv_dicts(response) -> list[dict[str, str]]:
    rows = read_csv(response)
    return [dict(zip(rows[0], row, strict=True)) for row in rows[1:]]


def test_csv_export_headers_and_rows(client, catalog):
    response = client.get(f"{API}/invoices/export/csv")
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == f'attachment; filename="facturas_{date.today():%Y%m%d}.csv"'
    assert response.content[:3] == b"\xef\xbb\xbf"
    assert response.content.count(b"\xef\xbb\xbf") == 1
    assert b"\r\n" in response.content

    rows = read_csv(response)
    header, data = rows[0], rows[1:]
    assert header == INVOICE_COLUMNS
    assert len(data) == 4
    assert {len(row) for row in rows} == {len(INVOICE_COLUMNS)}
    by_number = {row[header.index("Número")]: row for row in data}
    row = by_number["A-001"]
    assert row[header.index("ID")] == str(catalog["a"]["id"])
    assert row[header.index("Fecha emisión")] == "2024-01-10"
    assert row[header.index("Fecha operación")] == ""
    assert row[header.index("Fecha vencimiento")] == "2024-12-31"
    assert row[header.index("Proveedor")] == "Suministros Norte S.L."
    assert row[header.index("NIF/CIF proveedor")] == SUPPLIER_ONE
    assert row[header.index("NIF/CIF cliente")] == BUYER_CIF
    assert row[header.index("Moneda")] == "EUR"
    assert row[header.index("Base imponible")] == "250.00"
    assert row[header.index("IVA")] == "52.50"
    assert row[header.index("IRPF")] == "0.00"
    assert row[header.index("Total")] == "302.50"
    assert row[header.index("Estado")] == "Validada"
    assert (row[header.index("Errores")], row[header.index("Avisos")]) == ("0", "0")
    assert row[header.index("Método extracción")] == "Manual"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", row[header.index("Fecha auditoría (UTC)")])
    assert by_number["B-002"][header.index("Estado")] == "Discrepancia"
    assert by_number["B-002"][header.index("Errores")] == "1"
    assert by_number["B-002"][header.index("Total")] == "250.00"
    assert "Se han detectado discrepancias" in by_number["B-002"][header.index("Resumen auditoría")]


def test_csv_export_uses_dot_decimals_without_thousand_separators(client):
    create_invoice(client, lines=[line_dict("Obra", "1", "1234567.80", irpf_rate="15")])
    [row] = csv_dicts(client.get(f"{API}/invoices/export/csv"))
    assert (row["Base imponible"], row["IVA"], row["IRPF"], row["Total"]) == (
        "1234567.80",
        "259259.24",
        "185185.17",
        "1308641.87",
    )
    assert "," not in "".join(row[key] for key in ("Base imponible", "IVA", "IRPF", "Total"))


def test_csv_export_with_lines(client, catalog):
    response = client.get(f"{API}/invoices/export/csv", params={"include_lines": True})
    rows = read_csv(response)
    header, data = rows[0], rows[1:]
    assert header == INVOICE_COLUMNS + LINE_COLUMNS
    assert len(data) == 5  # A-001 has two lines, the others one
    assert {len(row) for row in rows} == {len(header)}

    first_invoice = [dict(zip(header, row, strict=True)) for row in data if row[header.index("Número")] == "A-001"]
    assert [row["Línea"] for row in first_invoice] == ["1", "2"]
    assert first_invoice[0]["Concepto"] == "Servicio de consultoría"
    assert (first_invoice[0]["Cantidad"], first_invoice[0]["Precio unitario"]) == ("2.0000", "50.000000")
    assert (first_invoice[0]["Descuento %"], first_invoice[0]["IVA %"], first_invoice[0]["IRPF %"]) == (
        "0.00",
        "21.00",
        "0.00",
    )
    assert (first_invoice[0]["Base línea"], first_invoice[0]["Cuota IVA"], first_invoice[0]["Cuota IRPF"]) == (
        "100.00",
        "21.00",
        "0.00",
    )
    assert first_invoice[1]["Base línea"] == "150.00"
    assert first_invoice[0]["Total"] == first_invoice[1]["Total"] == "302.50"  # invoice columns repeat on each row


def test_csv_export_invoice_without_lines_still_gets_a_row(client):
    created = create_invoice(client)
    client.patch(f"{API}/invoices/{created['id']}", json={"lines": []})
    [row] = csv_dicts(client.get(f"{API}/invoices/export/csv", params={"include_lines": True}))
    assert row["Número"] == "2024-001"
    assert all(row[column] == "" for column in LINE_COLUMNS)
    assert row["Estado"] == "Discrepancia"


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"status": "DISCREPANCY"}, id="status"),
        pytest.param({"supplier_id": "one"}, id="supplier"),
        pytest.param({"date_from": "2024-01-15", "date_to": "2024-02-10"}, id="dates"),
        pytest.param({"min_total": "100", "max_total": "260"}, id="totals"),
        pytest.param({"extraction_method": "DEMO"}, id="method"),
        pytest.param({"search": "papel", "status": "VALIDATED"}, id="search+status"),
        pytest.param({"tax_id": SUPPLIER_TWO, "min_total": "100"}, id="tax-id+total"),
    ],
)
@pytest.mark.parametrize("include_lines", [False, True])
def test_csv_export_honours_the_same_filters_as_the_listing(client, rows, params, include_lines):
    resolved = with_supplier_ids(rows, params)
    listing = client.get(f"{API}/invoices", params={**resolved, "page_size": 200}).json()["items"]
    exported = csv_dicts(client.get(f"{API}/invoices/export/csv", params={**resolved, "include_lines": include_lines}))
    ids = [int(row["ID"]) for row in exported]
    expected = expected_order(rows, resolved)
    assert [item["id"] for item in listing] == expected
    assert list(dict.fromkeys(ids)) == expected  # same invoices, same order (one row per line when lines are on)
    if not include_lines:
        assert ids == expected


def test_csv_export_row_order_follows_sort_parameters(client, catalog):
    ascending = csv_dicts(
        client.get(f"{API}/invoices/export/csv", params={"sort_by": "total_amount", "sort_dir": "asc"})
    )
    assert [row["Número"] for row in ascending] == ["B-001", "A-002", "B-002", "A-001"]
    descending = csv_dicts(client.get(f"{API}/invoices/export/csv", params={"sort_by": "invoice_number"}))
    assert [row["Número"] for row in descending] == ["B-002", "B-001", "A-002", "A-001"]


def test_csv_export_ignores_pagination_parameters(client, catalog):
    assert len(csv_dicts(client.get(f"{API}/invoices/export/csv", params={"page": 2, "page_size": 1}))) == 4


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
def test_csv_export_neutralises_formula_injection_in_every_text_column(client, prefix):
    payload = invoice_payload(
        "INV-1",
        supplier_name=f"{prefix}Proveedor",
        invoice_series=f"{prefix}S",
        buyer_name=f"{prefix}Cliente",
        lines=[line_dict(f"{prefix}Concepto", "1", "10.00")],
    )
    assert client.post(f"{API}/invoices", json=payload).status_code == 201
    [row] = csv_dicts(client.get(f"{API}/invoices/export/csv", params={"include_lines": True}))

    assert row["Proveedor"] == f"'{prefix}Proveedor"
    assert row["Serie"] == f"'{prefix}S"
    assert row["Cliente"] == f"'{prefix}Cliente"
    assert row["Concepto"] == f"'{prefix}Concepto"


@pytest.mark.parametrize("prefix", ["\t", "\r"])
def test_csv_export_neutralises_control_character_prefixes(client, prefix):
    # Names, series and concepts are trimmed on input, so only the untrimmed buyer name can start with them.
    create_invoice(client, "INV-1", buyer_name=f"{prefix}Cliente")
    [row] = csv_dicts(client.get(f"{API}/invoices/export/csv"))
    assert row["Cliente"] == f"'{prefix}Cliente"


def test_csv_export_neutralises_dangerous_invoice_numbers_and_file_names(client, fake_extractor):
    create_invoice(client, "=CMD()")
    create_invoice(client, "+34600111222")
    fake_extractor.result = make_extraction("@SUM(A1)")
    assert upload(client, pdf_bytes("formula"), "=HYPERLINK(1).pdf").status_code == 201
    exported = csv_dicts(client.get(f"{API}/invoices/export/csv"))
    assert sorted(row["Número"] for row in exported) == ["'+34600111222", "'=CMD()", "'@SUM(A1)"]
    assert {row["Archivo origen"] for row in exported} == {"", "'=HYPERLINK(1).pdf"}


def test_csv_export_leaves_harmless_text_untouched(client):
    create_invoice(client, "a=b", supplier_name="Pérez-Gómez & Hijos, S.L.", buyer_name="x@y.es")
    [row] = csv_dicts(client.get(f"{API}/invoices/export/csv"))
    assert (row["Número"], row["Proveedor"], row["Cliente"]) == ("a=b", "Pérez-Gómez & Hijos, S.L.", "x@y.es")


def test_csv_export_quotes_delimiters_quotes_and_newlines(client):
    create_invoice(client, 'A;B"C', buyer_name="Línea 1\nLínea 2; sección")
    text = client.get(f"{API}/invoices/export/csv").content.decode("utf-8")
    assert '"A;B""C"' in text
    [row] = csv_dicts(client.get(f"{API}/invoices/export/csv"))
    assert row["Número"] == 'A;B"C'
    assert row["Cliente"] == "Línea 1\nLínea 2; sección"


def test_csv_export_empty(client):
    response = client.get(f"{API}/invoices/export/csv")
    assert read_csv(response) == [INVOICE_COLUMNS]
    with_lines = client.get(f"{API}/invoices/export/csv", params={"include_lines": True})
    assert read_csv(with_lines) == [INVOICE_COLUMNS + LINE_COLUMNS]


def test_csv_export_rejects_invalid_filters(client):
    assert client.get(f"{API}/invoices/export/csv", params={"status": "BOGUS"}).status_code == 422
    assert client.get(f"{API}/invoices/export/csv", params={"include_lines": "quizás"}).status_code == 422


# --------------------------------------------------------------------------- Stats


def test_dashboard_stats_empty(client):
    response = client.get(f"{API}/stats/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert body["total_invoices"] == 0
    assert body["total_suppliers"] == 0
    assert (body["total_tax_base"], body["total_vat"], body["total_irpf"], body["total_amount"]) == (
        "0.00",
        "0.00",
        "0.00",
        "0.00",
    )
    assert body["validation_rate"] == 0
    assert body["monthly"] == [] and body["top_suppliers"] == []
    assert body["top_failed_rules"] == [] and body["vat_breakdown"] == []
    assert body["extraction_mode"] == "DEMO"
    assert [(item["status"], item["count"], item["total_amount"]) for item in body["by_status"]] == [
        ("PENDING", 0, "0.00"),
        ("VALIDATED", 0, "0.00"),
        ("DISCREPANCY", 0, "0.00"),
        ("DUPLICATE", 0, "0.00"),
    ]


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


@pytest.fixture()
def stats_dataset(client, db_session):
    """Six invoices spanning every status, three suppliers, three months, IRPF and four VAT rates.

    Hand-computed expectations (see test_dashboard_stats_match_a_hand_computed_dataset):
      S-1  one   2023-12-20  1x1000.00 @21% IRPF 15%         base 1000.00 vat 210.00 irpf 150.00 total 1060.00 VALIDATED
      S-2  one   2024-01-05  3x10.00 @10%                    base   30.00 vat   3.00             total   33.00 VALIDATED
      S-3  two   2024-01-25  1x500.00 @4%, total typed 519.98 base  500.00 vat  20.00             total  519.98 DISCREPANCY
      S-3  two   2024-01-25  same again, total 520.00         base  500.00 vat  20.00             total  520.00 DUPLICATE
      S-5  three 2024-01-30  1x100.00 @0%                    base  100.00 vat   0.00             total  100.00 VALIDATED (warning)
      S-6  three 2024-02-02  inserted without auditing        base   10.00 vat   0.00             total   10.00 PENDING
    """
    create_invoice(
        client, "S-1", issue_date="2023-12-20", lines=[line_dict("Honorarios", "1", "1000.00", irpf_rate="15")]
    )
    create_invoice(client, "S-2", issue_date="2024-01-05", lines=[line_dict("Mantenimiento", "3", "10.00", "10")])
    two = {"supplier_tax_id": SUPPLIER_TWO, "supplier_name": "Papelería Sur S.A.", "issue_date": "2024-01-25"}
    create_invoice(client, "S-3", lines=[line_dict("Libros", "1", "500.00", "4")], total_amount="519.98", **two)
    create_invoice(client, "S-3", lines=[line_dict("Libros", "1", "500.00", "4")], **two)
    three = create_invoice(
        client,
        "S-5",
        supplier_tax_id=SUPPLIER_THREE,
        supplier_name="Exenta S.L.",
        issue_date="2024-01-30",
        lines=[line_dict("Formación", "1", "100.00", "0")],
    )
    db_session.add(
        Invoice(
            supplier_id=three["supplier"]["id"],
            supplier_tax_id=SUPPLIER_THREE,
            invoice_number="S-6",
            issue_date=date(2024, 2, 2),
            tax_base=Decimal("10.00"),
            total_vat=Decimal("0.00"),
            total_irpf=Decimal("0.00"),
            total_amount=Decimal("10.00"),
        )
    )
    db_session.commit()


def test_dashboard_stats_match_a_hand_computed_dataset(client, stats_dataset):
    body = client.get(f"{API}/stats/dashboard").json()

    assert body["total_invoices"] == 6
    assert body["total_suppliers"] == 3
    assert body["total_tax_base"] == "2140.00"  # 1000 + 30 + 500 + 500 + 100 + 10
    assert body["total_vat"] == "253.00"  # 210 + 3 + 20 + 20
    assert body["total_irpf"] == "150.00"
    assert body["total_amount"] == "2242.98"  # 1060 + 33 + 519.98 + 520 + 100 + 10
    assert body["validation_rate"] == 50.0  # S-1, S-2, S-5 of 6

    assert body["by_status"] == [
        {"status": "PENDING", "count": 1, "total_amount": "10.00"},
        {"status": "VALIDATED", "count": 3, "total_amount": "1193.00"},
        {"status": "DISCREPANCY", "count": 1, "total_amount": "519.98"},
        {"status": "DUPLICATE", "count": 1, "total_amount": "520.00"},
    ]
    assert sum(item["count"] for item in body["by_status"]) == body["total_invoices"]

    assert body["monthly"] == [
        {
            "month": "2023-12",
            "invoice_count": 1,
            "tax_base": "1000.00",
            "total_vat": "210.00",
            "total_irpf": "150.00",
            "total_amount": "1060.00",
        },
        {
            "month": "2024-01",
            "invoice_count": 4,
            "tax_base": "1130.00",
            "total_vat": "43.00",
            "total_irpf": "0.00",
            "total_amount": "1172.98",
        },
        {
            "month": "2024-02",
            "invoice_count": 1,
            "tax_base": "10.00",
            "total_vat": "0.00",
            "total_irpf": "0.00",
            "total_amount": "10.00",
        },
    ]

    assert [(s["tax_id"], s["name"], s["invoice_count"], s["total_amount"]) for s in body["top_suppliers"]] == [
        (SUPPLIER_ONE, "Suministros Norte S.L.", 2, "1093.00"),
        (SUPPLIER_TWO, "Papelería Sur S.A.", 2, "1039.98"),
        (SUPPLIER_THREE, "Exenta S.L.", 2, "110.00"),
    ]

    assert body["vat_breakdown"] == [
        {"vat_rate": "21.00", "tax_base": "1000.00", "vat_amount": "210.00"},
        {"vat_rate": "10.00", "tax_base": "30.00", "vat_amount": "3.00"},
        {"vat_rate": "4.00", "tax_base": "1000.00", "vat_amount": "40.00"},
        {"vat_rate": "0.00", "tax_base": "100.00", "vat_amount": "0.00"},
    ]

    # Failed checks (errors and warnings) of each invoice's latest run; ties ordered by rule code.
    assert body["top_failed_rules"] == [
        {"rule_code": "DUPLICATE", "count": 1},
        {"rule_code": "GRAND_TOTAL", "count": 1},
        {"rule_code": "VAT_RATE", "count": 1},
    ]


def test_dashboard_stats_reflect_corrections_and_deletions(client, stats_dataset):
    ids = {
        item["invoice_number"] + item["audit_status"]: item["id"]
        for item in client.get(f"{API}/invoices").json()["items"]
    }

    assert client.patch(f"{API}/invoices/{ids['S-3DISCREPANCY']}", json={"total_amount": "520.00"}).status_code == 200
    assert client.delete(f"{API}/invoices/{ids['S-3DUPLICATE']}").status_code == 204
    body = client.get(f"{API}/stats/dashboard").json()

    assert body["total_invoices"] == 5
    assert body["total_amount"] == "1723.00"  # 1060 + 33 + 520 + 100 + 10
    assert body["validation_rate"] == 80.0
    assert [(s["status"], s["count"]) for s in body["by_status"]] == [
        ("PENDING", 1),
        ("VALIDATED", 4),
        ("DISCREPANCY", 0),
        ("DUPLICATE", 0),
    ]
    assert body["top_failed_rules"] == [{"rule_code": "VAT_RATE", "count": 1}]
    assert body["monthly"][1]["invoice_count"] == 3


@pytest.mark.parametrize(("valid", "total", "rate"), [(1, 3, 33.33), (2, 3, 66.67), (1, 1, 100.0), (0, 2, 0.0)])
def test_dashboard_validation_rate_is_rounded_to_two_decimals(client, valid, total, rate):
    for index in range(total):
        wrong = {} if index < valid else {"total_amount": "1.00"}
        create_invoice(client, f"V-{index}", **wrong)
    assert client.get(f"{API}/stats/dashboard").json()["validation_rate"] == rate


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
    assert listing["items"][1]["invoice_count"] == 2 and listing["items"][1]["total_invoiced"] == "412.50"

    detail = client.get(f"{API}/suppliers/{papeleria['id']}")
    assert detail.status_code == 200
    assert detail.json() == papeleria
    assert detail.json()["tax_id"] == SUPPLIER_TWO


@pytest.mark.parametrize(
    ("search", "expected"),
    [
        ("norte", ["Suministros Norte S.L."]),
        ("NORTE", ["Suministros Norte S.L."]),
        ("  sur  ", ["Papelería Sur S.A."]),
        ("papelería", ["Papelería Sur S.A."]),
        ("S.", ["Papelería Sur S.A.", "Suministros Norte S.L."]),
        (SUPPLIER_TWO, ["Papelería Sur S.A."]),
        (SUPPLIER_TWO.lower(), ["Papelería Sur S.A."]),
        (SUPPLIER_ONE[:5], ["Suministros Norte S.L."]),
        ("", ["Papelería Sur S.A.", "Suministros Norte S.L."]),
        ("   ", ["Papelería Sur S.A.", "Suministros Norte S.L."]),
        ("%", []),
        ("_", []),
        ("Nor_e", []),
        ("inexistente", []),
    ],
)
def test_suppliers_search(client, catalog, search, expected):
    body = client.get(f"{API}/suppliers", params={"search": search}).json()
    assert [item["name"] for item in body["items"]] == expected
    assert body["total"] == len(expected)


def test_suppliers_pagination_and_order(client):
    for index, name in enumerate(["Zeta", "Alfa", "Beta", "Alfa"]):
        tax_id = make_cif("B", f"{4000000 + index}")
        client.post(f"{API}/suppliers", json={"name": name, "tax_id": tax_id})
    everything = client.get(f"{API}/suppliers", params={"page_size": 200}).json()
    assert [item["name"] for item in everything["items"]] == ["Alfa", "Alfa", "Beta", "Zeta"]
    assert [item["id"] for item in everything["items"][:2]] == sorted(item["id"] for item in everything["items"][:2])

    page2 = client.get(f"{API}/suppliers", params={"page_size": 3, "page": 2}).json()
    assert (page2["total"], page2["pages"], [item["name"] for item in page2["items"]]) == (4, 2, ["Zeta"])
    beyond = client.get(f"{API}/suppliers", params={"page_size": 3, "page": 9}).json()
    assert beyond == {"items": [], "total": 4, "page": 9, "page_size": 3, "pages": 2}
    assert client.get(f"{API}/suppliers", params={"page": 0}).status_code == 422
    assert client.get(f"{API}/suppliers", params={"page_size": 201}).status_code == 422
    assert client.get(f"{API}/suppliers", params={"page_size": 200}).status_code == 200


def test_supplier_create_update_and_conflict(client):
    payload = {"name": "Nuevo Proveedor S.L.", "tax_id": f"es {make_cif('B', '7654321').lower()}", "city": "Sevilla"}
    created = client.post(f"{API}/suppliers", json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["tax_id"] == make_cif("B", "7654321")
    assert body["tax_id_type"] == "CIF"
    assert body["tax_id_valid"] is True
    assert body["person_type"] == "J"
    assert body["country_code"] == "ESP"
    assert body["invoice_count"] == 0 and body["total_invoiced"] == "0.00"

    duplicate = client.post(f"{API}/suppliers", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": f"Ya existe un proveedor con el NIF/CIF {make_cif('B', '7654321')}"}
    reformatted = client.post(f"{API}/suppliers", json={**payload, "tax_id": make_cif("B", "7654321").lower()})
    assert reformatted.status_code == 409
    assert client.get(f"{API}/suppliers").json()["total"] == 1

    patched = client.patch(f"{API}/suppliers/{body['id']}", json={"name": "Renombrado S.L.", "email": "a@b.es"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renombrado S.L."
    assert patched.json()["email"] == "a@b.es"
    assert patched.json()["city"] == "Sevilla"
    assert patched.json()["tax_id"] == body["tax_id"]
    assert patched.json()["updated_at"] >= body["updated_at"]
    assert client.patch(f"{API}/suppliers/{body['id']}", json={"name": None}).status_code == 422


@pytest.mark.parametrize(
    ("tax_id", "tax_id_type", "person_type", "valid"),
    [
        ("12345678Z", "NIF", "F", True),
        ("x-1234567-l", "NIE", "F", True),
        ("K1234567L", "NIF", "F", True),
        ("Q2826000H", "CIF", "J", True),
        ("12345678A", "NIF", "J", False),
        ("B12345670", "CIF", "J", False),
        ("T1234567L", "UNKNOWN", "J", False),
    ],
)
def test_supplier_tax_id_classification(client, tax_id, tax_id_type, person_type, valid):
    response = client.post(f"{API}/suppliers", json={"name": "Clasificado", "tax_id": tax_id})
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["tax_id_type"], body["tax_id_valid"]) == (tax_id_type, valid)
    if valid:
        assert body["person_type"] == person_type


def test_supplier_with_invalid_tax_id_is_flagged(client):
    response = client.post(f"{API}/suppliers", json={"name": "Dudoso", "tax_id": "B12345670"})
    assert response.status_code == 201
    assert response.json()["tax_id_valid"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"tax_id": SUPPLIER_ONE},
        {"name": "Sin NIF"},
        {"name": "", "tax_id": SUPPLIER_ONE},
        {"name": "Guiones", "tax_id": "--- ---"},
        {"name": "Largo", "tax_id": "B" * 21},
        {"name": "País", "tax_id": SUPPLIER_ONE, "country_code": "ES"},
        {"name": "Tipo", "tax_id": SUPPLIER_ONE, "person_type": "X"},
    ],
)
def test_supplier_create_rejects_invalid_payloads(client, payload):
    assert client.post(f"{API}/suppliers", json=payload).status_code == 422
    assert client.get(f"{API}/suppliers").json()["total"] == 0


def test_supplier_patch_semantics(client):
    created = client.post(
        f"{API}/suppliers", json={"name": "Parcial S.L.", "tax_id": SUPPLIER_ONE, "city": "Cádiz", "phone": "600"}
    ).json()
    url = f"{API}/suppliers/{created['id']}"

    assert stable(client.patch(url, json={}).json()) == stable(created)  # nothing to change
    cleared = client.patch(url, json={"city": None, "phone": "700"}).json()
    assert cleared["city"] is None and cleared["phone"] == "700"
    assert client.patch(url, json={"tax_id": SUPPLIER_TWO}).json()["tax_id"] == SUPPLIER_ONE  # not updatable
    assert client.patch(url, json={"person_type": "F"}).json()["person_type"] == "F"
    for field in ("name", "country_code", "person_type"):
        assert client.patch(url, json={field: None}).status_code == 422, field
    assert client.patch(url, json={"country_code": "ES"}).status_code == 422
    assert client.get(url).json()["person_type"] == "F"


def test_supplier_totals_follow_invoice_changes(client, catalog):
    supplier_id = catalog["c"]["supplier"]["id"]
    assert client.get(f"{API}/suppliers/{supplier_id}").json()["invoice_count"] == 2
    client.delete(f"{API}/invoices/{catalog['d']['id']}")
    detail = client.get(f"{API}/suppliers/{supplier_id}").json()
    assert (detail["invoice_count"], detail["total_invoiced"]) == (1, "41.60")
    client.patch(f"{API}/invoices/{catalog['c']['id']}", json={"tax_base": "40.00", "total_amount": "50.00"})
    assert client.get(f"{API}/suppliers/{supplier_id}").json()["total_invoiced"] == "50.00"


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
    assert numbers(client.get(f"{API}/suppliers/{supplier_id}/invoices", params={"page_size": 1, "page": 2})) == [
        "A-001"
    ]
    assert client.get(f"{API}/suppliers/{supplier_id}/invoices", params={"page": 3}).json()["items"] == []


def test_supplier_invoices_endpoint_scopes_every_filter_to_the_supplier(client, catalog):
    supplier_id = catalog["a"]["supplier"]["id"]
    other_supplier_id = catalog["c"]["supplier"]["id"]
    url = f"{API}/suppliers/{supplier_id}/invoices"

    assert numbers(client.get(url, params={"min_total": "200"})) == ["A-001"]
    assert numbers(client.get(url, params={"status": "DISCREPANCY"})) == []
    assert numbers(client.get(url, params={"search": "a-002"})) == ["A-002"]
    assert numbers(client.get(url, params={"date_from": "2024-01-15"})) == ["A-002"]
    # A supplier_id filter in the query string cannot widen the scope to another supplier.
    assert sorted(numbers(client.get(url, params={"supplier_id": other_supplier_id}))) == ["A-001", "A-002"]
    assert client.get(url, params={"status": "BOGUS"}).status_code == 422


def test_supplier_404s(client):
    assert client.get(f"{API}/suppliers/999").json() == {"detail": "Proveedor 999 no encontrado"}
    assert client.patch(f"{API}/suppliers/999", json={"name": "X"}).status_code == 404
    response = client.get(f"{API}/suppliers/999/invoices")
    assert response.status_code == 404
    assert "no encontrado" in response.json()["detail"]
    assert client.get(f"{API}/suppliers/abc").status_code == 422


# --------------------------------------------------------------------------- Facturae export tracking


def test_new_invoice_is_not_exported(client):
    created = create_invoice(client)
    assert created["facturae_exported_at"] is None
    assert created["facturae_export_count"] == 0


def test_facturae_download_records_the_export(client):
    created = create_invoice(client)
    for expected_count in (1, 2):
        assert client.get(f"{API}/invoices/{created['id']}/facturae", params={"sign": False}).status_code == 200
        detail = client.get(f"{API}/invoices/{created['id']}").json()
        assert detail["facturae_export_count"] == expected_count
        assert detail["facturae_exported_at"] is not None
        assert detail["facturae_exported_at"].endswith("Z") or detail["facturae_exported_at"].endswith("+00:00")


def test_rejected_facturae_download_is_not_recorded(client):
    created = create_invoice(client, tax_base="999.00")
    assert client.get(f"{API}/invoices/{created['id']}/facturae", params={"sign": False}).status_code == 409
    detail = client.get(f"{API}/invoices/{created['id']}").json()
    assert detail["facturae_export_count"] == 0
    assert detail["facturae_exported_at"] is None


def test_editing_an_exported_invoice_clears_the_export_date(client):
    created = create_invoice(client)
    client.get(f"{API}/invoices/{created['id']}/facturae", params={"sign": False})
    patched = client.patch(f"{API}/invoices/{created['id']}", json={"notes": "Revisada"}).json()
    assert patched["facturae_exported_at"] is None
    assert patched["facturae_export_count"] == 1


def test_exported_filter(client):
    exported = create_invoice(client, "E-1")
    pending = create_invoice(client, "E-2")
    client.get(f"{API}/invoices/{exported['id']}/facturae", params={"sign": False})

    only_exported = client.get(f"{API}/invoices", params={"exported": True}).json()
    assert [item["id"] for item in only_exported["items"]] == [exported["id"]]
    assert only_exported["items"][0]["facturae_export_count"] == 1
    not_exported = client.get(f"{API}/invoices", params={"exported": False}).json()
    assert [item["id"] for item in not_exported["items"]] == [pending["id"]]


def test_dashboard_face_kpis(client):
    exported = create_invoice(client, "K-1")
    create_invoice(client, "K-2")
    create_invoice(client, "K-3", tax_base="1.00")
    create_invoice(client, "K-2")
    client.get(f"{API}/invoices/{exported['id']}/facturae", params={"sign": False})

    stats = client.get(f"{API}/stats/dashboard").json()
    assert stats["exported_to_face"] == 1
    assert stats["ready_for_face"] == 1
    assert stats["invoices_with_errors"] == 2


@pytest.mark.parametrize("term", ["ARQ-26/0033", "arq-26-0033", "26/0033", "0033"])
def test_search_matches_series_and_number_as_displayed(client, term):
    created = create_invoice(client, "0033", invoice_series="ARQ-26")
    create_invoice(client, "0034", invoice_series="ARQ-26")
    found = client.get(f"{API}/invoices", params={"search": term}).json()
    assert [item["id"] for item in found["items"]] == [created["id"]]


def test_has_errors_filter_matches_the_dashboard_kpi(client):
    create_invoice(client, "H-1")
    discrepancy = create_invoice(client, "H-2", tax_base="1.00")
    duplicate = create_invoice(client, "H-1")
    stats = client.get(f"{API}/stats/dashboard").json()
    with_errors = client.get(f"{API}/invoices", params={"has_errors": True, "sort_by": "created_at", "sort_dir": "asc"})
    assert [item["id"] for item in with_errors.json()["items"]] == [discrepancy["id"], duplicate["id"]]
    assert with_errors.json()["total"] == stats["invoices_with_errors"]
    without = client.get(f"{API}/invoices", params={"has_errors": False}).json()
    assert without["total"] == 1
