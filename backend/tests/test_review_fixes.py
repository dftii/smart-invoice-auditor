"""Regression tests for the review findings: isolation, synthetic data, credit notes, duplicates, limits, leaks."""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateColumn

from app.core.config import Settings, get_settings
from app.core.limits import MULTIPART_OVERHEAD_BYTES, UploadSizeLimitMiddleware
from app.db.database import Base, add_missing_columns, get_engine, init_db
from app.main import create_app
from app.models import Invoice, UTCDateTime
from app.schemas import InvoiceLineRead, InvoiceRead, InvoiceSummary
from app.services.extraction import (
    ClaudeVisionStrategy,
    ExtractionError,
    InvoiceExtractor,
    get_extractor,
    result_from_tool_input,
)
from app.services.iban import is_valid_iban, normalize_iban
from tests.conftest import REAL_DB_PATH
from tests.test_api import (
    API,
    SUPPLIER_ONE,
    FakeExtractor,
    create_invoice,
    invoice_payload,
    line_dict,
    make_extraction,
    pdf_bytes,
    upload,
)

VALID_IBAN = "ES9121000418450200051332"


@pytest.fixture()
def fake_extractor(client):
    fake = FakeExtractor()
    client.app.dependency_overrides[get_extractor] = lambda: fake
    return fake


# --------------------------------------------------------------------------- 1. database isolation


def test_tests_never_touch_the_real_database(real_db_snapshot, client):
    assert client.get("/health").status_code == 200
    create_invoice(client)
    exists_before, mtime_before = real_db_snapshot
    assert REAL_DB_PATH.exists() == exists_before
    if exists_before:
        assert REAL_DB_PATH.stat().st_mtime_ns == mtime_before
    assert Path(get_settings().database_url.removeprefix("sqlite:///")).resolve() != REAL_DB_PATH.resolve()


def test_default_engine_is_lazy_and_not_the_real_database():
    assert "smartinvoice.db" not in str(get_engine().url)


def test_create_app_with_engine_creates_its_schema_in_that_engine():
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with TestClient(create_app(engine=engine)) as test_client:
        assert test_client.get("/health").status_code == 200
        assert test_client.get(f"{API}/invoices").json()["total"] == 0
    assert "invoices" in inspect(engine).get_table_names()
    engine.dispose()


# --------------------------------------------------------------------------- 2. synthetic data


def test_demo_upload_of_an_unreadable_document_is_synthetic_and_never_valid(client):
    response = upload(client, pdf_bytes("sintetica"))
    assert response.status_code == 201, response.text
    body = response.json()
    invoice = body["invoice"]
    assert invoice["is_synthetic"] is True
    assert invoice["audit_status"] == "DISCREPANCY"
    failed = [c for c in body["audit"]["checks"] if not c["passed"] and c["severity"] == "ERROR"]
    synthetic = [c for c in failed if c["rule_code"] == "SYNTHETIC_DATA"]
    assert [c["message"] for c in synthetic] == ["Datos sintéticos de demostración: no corresponden al documento"]

    listing = client.get(f"{API}/invoices").json()["items"]
    assert listing[0]["is_synthetic"] is True


def test_synthetic_invoice_cannot_be_exported_even_if_forced_to_validated(client, engine):
    response = upload(client, pdf_bytes("forzada"))
    invoice_id = response.json()["invoice"]["id"]
    export = client.get(f"{API}/invoices/{invoice_id}/facturae")
    assert export.status_code == 409
    from sqlalchemy.orm import Session

    with Session(engine) as db:  # tamper with the status: the exporter itself must still refuse
        db.execute(text("UPDATE invoices SET audit_status = 'VALIDATED' WHERE id = :id"), {"id": invoice_id})
        db.commit()
    export = client.get(f"{API}/invoices/{invoice_id}/facturae")
    assert export.status_code == 409
    assert "sintéticos" in export.json()["detail"]


def test_synthetic_flag_survives_corrections_and_manual_invoices_are_not_synthetic(client):
    invoice_id = upload(client, pdf_bytes("persistente")).json()["invoice"]["id"]
    patched = client.patch(f"{API}/invoices/{invoice_id}", json={"notes": "revisada"}).json()
    assert patched["is_synthetic"] is True
    assert patched["audit_status"] == "DISCREPANCY"
    assert create_invoice(client)["is_synthetic"] is False


def test_ai_fallback_to_demo_is_off_by_default():
    assert Settings().ai_fallback_to_demo is False


def test_ai_failure_returns_422_by_default(client):
    settings = Settings(anthropic_api_key="sk-ant-test")
    strategy = ClaudeVisionStrategy(settings, client=_client_raising(RuntimeError("detalle interno /srv/clave")))
    client.app.dependency_overrides[get_extractor] = lambda: InvoiceExtractor(settings, strategy=strategy)
    response = upload(client, pdf_bytes("ia"))
    assert response.status_code == 422
    assert "detalle interno" not in response.text
    assert "/srv" not in response.text


def _client_raising(error: Exception):
    class Messages:
        def create(self, **_kwargs):
            raise error

    class Client:
        messages = Messages()

    return Client()


# --------------------------------------------------------------------------- 3. credit notes / zero quantities


def test_line_read_model_never_fails_on_stored_data():
    line = InvoiceLineRead(
        id=1,
        line_number=1,
        concept="",
        quantity=Decimal("-2"),
        unit_of_measure="1",
        unit_price=Decimal("-5"),
        discount_rate=Decimal("150"),
        vat_rate=Decimal("-1"),
        line_base=Decimal("10"),
        vat_amount=Decimal("2.1"),
    )
    assert line.quantity == Decimal("-2")


def test_stored_negative_quantity_does_not_break_the_detail_endpoint(client, engine):
    invoice_id = create_invoice(client)["id"]
    with engine.begin() as connection:
        connection.execute(text("UPDATE invoice_lines SET quantity = -3 WHERE invoice_id = :id"), {"id": invoice_id})
    response = client.get(f"{API}/invoices/{invoice_id}")
    assert response.status_code == 200
    assert Decimal(response.json()["lines"][0]["quantity"]) == Decimal("-3.0000")


def test_zero_quantity_line_is_rejected_on_creation(client):
    bad = line_dict("Servicio", "1", "10.00")
    bad["quantity"] = "0"
    response = client.post(f"{API}/invoices", json=invoice_payload("Q-0", lines=[bad]))
    assert response.status_code == 422


def test_negative_total_is_rejected_as_unsupported_rectifying_invoice(client):
    negative = line_dict("Abono", "1", "-100.00")
    response = client.post(f"{API}/invoices", json=invoice_payload("R-1", lines=[negative]))
    assert response.status_code == 422
    assert response.json()["detail"] == "Las facturas rectificativas no están soportadas todavía"
    assert client.get(f"{API}/invoices").json()["total"] == 0


def test_patch_to_a_negative_total_is_rejected_and_nothing_changes(client):
    created = create_invoice(client)
    response = client.patch(f"{API}/invoices/{created['id']}", json={"total_amount": "-5.00"})
    assert response.status_code == 422
    assert "rectificativas" in response.json()["detail"]
    assert client.get(f"{API}/invoices/{created['id']}").json()["total_amount"] == created["total_amount"]


def test_upload_with_negative_total_is_rejected_and_stores_no_file(client, fake_extractor):
    fake_extractor.result = make_extraction().model_copy(
        update={"tax_base": Decimal("-10.00"), "total_vat": Decimal("-2.10"), "total_amount": Decimal("-12.10")}
    )
    response = upload(client, pdf_bytes("abono"))
    assert response.status_code == 422
    assert "rectificativas" in response.json()["detail"]
    assert not [p for p in get_settings().upload_dir.rglob("*") if p.is_file()]


def test_ai_line_with_zero_quantity_is_an_extraction_error_not_quantity_one():
    data = {
        "invoice_number": "1",
        "issue_date": "2024-01-01",
        "supplier": {"name": "A", "tax_id": SUPPLIER_ONE},
        "lines": [{"concept": "x", "quantity": "0", "unit_price": "10", "vat_rate": "21"}],
    }
    with pytest.raises(ExtractionError, match="cantidad cero"):
        result_from_tool_input(data, "test")


def test_extractor_rejects_zero_quantity_results(client):
    from app.services.extraction import validate_result

    result = make_extraction()
    result.lines[0] = result.lines[0].model_copy(update={"quantity": Decimal(0)})
    with pytest.raises(ExtractionError, match="cantidad cero"):
        validate_result(result)


# --------------------------------------------------------------------------- 4. duplicates


def test_duplicate_state_is_refreshed_when_the_original_is_deleted(client):
    first = create_invoice(client, "DUP-1")
    second = create_invoice(client, "DUP-1")
    assert second["audit_status"] == "DUPLICATE"
    assert client.delete(f"{API}/invoices/{first['id']}").status_code == 204
    assert client.get(f"{API}/invoices/{second['id']}").json()["audit_status"] == "VALIDATED"


def test_duplicate_state_is_refreshed_when_the_original_number_is_corrected(client):
    first = create_invoice(client, "DUP-2")
    second = create_invoice(client, "DUP-2")
    assert second["audit_status"] == "DUPLICATE"
    assert client.patch(f"{API}/invoices/{first['id']}", json={"invoice_number": "DUP-2B"}).status_code == 200
    assert client.get(f"{API}/invoices/{second['id']}").json()["audit_status"] == "VALIDATED"


def test_editing_an_earlier_invoice_into_a_clash_flags_the_later_one(client):
    first = create_invoice(client, "A-1")
    second = create_invoice(client, "A-2")
    assert second["audit_status"] == "VALIDATED"
    client.patch(f"{API}/invoices/{first['id']}", json={"invoice_number": "A-2"})
    assert client.get(f"{API}/invoices/{second['id']}").json()["audit_status"] == "DUPLICATE"


def test_file_hash_duplicates_are_refreshed_after_delete(client, fake_extractor):
    fake_extractor.result = make_extraction("H-1")
    first = upload(client, pdf_bytes("mismo")).json()["invoice"]
    fake_extractor.result = make_extraction("H-2")
    second = upload(client, pdf_bytes("mismo")).json()["invoice"]
    assert second["audit_status"] == "DUPLICATE"
    client.delete(f"{API}/invoices/{first['id']}")
    assert client.get(f"{API}/invoices/{second['id']}").json()["audit_status"] == "VALIDATED"


def test_duplicate_by_number_is_skipped_when_tax_id_is_empty(engine):
    from sqlalchemy.orm import Session

    from app.models import Supplier
    from app.schemas import InvoiceCreate
    from app.services import invoice_service
    from app.services.auditor import InvoiceAuditor

    with Session(engine) as db:
        invoices = []
        for name in ("Proveedor A", "Proveedor B"):
            payload = InvoiceCreate(**invoice_payload("123", supplier_name=name))
            invoice = invoice_service.create_invoice_manual(db, payload)
            invoice.supplier_tax_id = ""
            invoices.append(invoice)
        db.flush()
        report = InvoiceAuditor(db).audit(invoices[1])
        assert report.duplicate_of == []
        assert not [c for c in report.checks if c.rule_code == "DUPLICATE" and not c.passed]
        assert isinstance(invoices[0].supplier, Supplier)


# --------------------------------------------------------------------------- 5. upload size


def test_oversized_content_length_is_rejected_before_the_body_is_read():
    calls = {"app": 0, "receive": 0}
    sent = []

    async def inner_app(scope, receive, send):
        calls["app"] += 1

    async def receive():
        calls["receive"] += 1
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    limit = get_settings().max_upload_size_bytes
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/invoices/upload",
        "headers": [(b"content-length", str(limit + MULTIPART_OVERHEAD_BYTES + 1).encode())],
    }
    asyncio.run(UploadSizeLimitMiddleware(inner_app)(scope, receive, send))
    assert calls == {"app": 0, "receive": 0}
    assert sent[0]["status"] == 413
    assert "tamaño máximo" in sent[1]["body"].decode()

    scope["headers"] = [(b"content-length", b"1000")]
    sent.clear()
    asyncio.run(UploadSizeLimitMiddleware(inner_app)(scope, receive, send))
    assert calls["app"] == 1


def test_oversized_upload_answers_413_and_streamed_check_still_applies(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_size_mb", 1)
    big = b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024)
    response = upload(client, big)
    assert response.status_code == 413
    assert "1 MB" in response.json()["detail"]


# --------------------------------------------------------------------------- 6. bounded integers


@pytest.mark.parametrize(
    "path",
    [
        f"{API}/invoices?page=2147483648",
        f"{API}/invoices?page_size=201",
        f"{API}/invoices?supplier_id=99999999999999999999",
        f"{API}/invoices/99999999999999999999",
        f"{API}/invoices/2147483648",
        f"{API}/invoices/2147483648/logs",
        f"{API}/invoices/2147483648/facturae",
        f"{API}/suppliers?page=99999999999",
        f"{API}/suppliers/2147483648",
        f"{API}/suppliers/2147483648/invoices",
        f"{API}/invoices?min_total=1e400",
    ],
)
def test_out_of_range_integers_answer_422_not_500(client, path):
    assert client.get(path).status_code == 422


def test_bulk_revalidate_rejects_out_of_range_ids(client):
    response = client.post(f"{API}/invoices/revalidate", json={"invoice_ids": [2**40]})
    assert response.status_code == 422


def test_absurd_amounts_answer_422(client):
    payload = invoice_payload("BIG", lines=[line_dict("x", "1", "10.00")])
    payload["tax_base"] = "10000000000000000"
    assert client.post(f"{API}/invoices", json=payload).status_code == 422


def test_maximum_valid_ids_are_simply_not_found(client):
    assert client.get(f"{API}/invoices/2147483647").status_code == 404
    assert client.get(f"{API}/suppliers/2147483647").status_code == 404


# --------------------------------------------------------------------------- 7. schema migration helper


def test_add_missing_columns_renders_defaults_and_indexes_for_the_dialect(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    init_db(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_invoices_supplier_tax_id"))
        connection.execute(text("ALTER TABLE invoices DROP COLUMN supplier_tax_id"))
        connection.execute(text("ALTER TABLE invoices DROP COLUMN is_synthetic"))
        connection.execute(text("ALTER TABLE invoices DROP COLUMN payment_iban"))
        connection.execute(text("INSERT INTO suppliers (id, name, tax_id, tax_id_type, tax_id_valid, person_type, "
                                "country_code, created_at, updated_at) VALUES (1, 'x', 'B1', 'CIF', 0, 'J', 'ESP', "
                                "'2024-01-01', '2024-01-01')"))
    # supplier_tax_id is NOT NULL without a default, so it cannot be added: the helper must say so loudly.
    with pytest.raises(RuntimeError, match="supplier_tax_id"):
        add_missing_columns(engine)
    engine.dispose()


def test_add_missing_columns_adds_synthetic_flag_iban_and_keeps_existing_rows(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'old2.db').as_posix()}")
    init_db(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE invoices DROP COLUMN is_synthetic"))
        connection.execute(text("ALTER TABLE invoices DROP COLUMN payment_iban"))
    assert sorted(add_missing_columns(engine)) == ["invoices.is_synthetic", "invoices.payment_iban"]
    columns = {c["name"]: c for c in inspect(engine).get_columns("invoices")}
    assert columns["is_synthetic"]["nullable"] is False
    assert str(columns["is_synthetic"]["default"]) in ("0", "false", "'0'")
    engine.dispose()


def test_added_columns_get_their_declared_indexes(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'old3.db').as_posix()}")
    init_db(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_invoices_source_file_hash"))
        connection.execute(text("ALTER TABLE invoices DROP COLUMN source_file_hash"))
    assert add_missing_columns(engine) == ["invoices.source_file_hash"]
    assert "ix_invoices_source_file_hash" in {i["name"] for i in inspect(engine).get_indexes("invoices")}
    engine.dispose()


def test_column_ddl_quotes_identifiers_and_strings_per_dialect():
    invoices = Base.metadata.tables["invoices"]
    ddl = str(CreateColumn(invoices.c.is_synthetic).compile(dialect=mysql.dialect()))
    assert ddl == "is_synthetic BOOL NOT NULL DEFAULT false"
    export_count = str(CreateColumn(invoices.c.facturae_export_count).compile(dialect=mysql.dialect()))
    assert "DEFAULT '0'" in export_count
    # Reserved words and odd names are quoted by the dialect's own preparer.
    assert mysql.dialect().identifier_preparer.quote("order") == "`order`"


def test_utc_datetime_keeps_microseconds_on_mysql():
    assert str(UTCDateTime().compile(dialect=mysql.dialect())) == "DATETIME(6)"


# --------------------------------------------------------------------------- 8. information leaks


def test_health_does_not_reveal_the_database_dialect(client):
    body = client.get("/health").json()
    assert body["database"] == "ok"
    assert set(body) == {"status", "app", "version", "database", "extraction_mode"}


def test_secret_settings_are_not_printed():
    settings = Settings(anthropic_api_key="sk-ant-secreta", facturae_cert_password="pw-secreto", api_key="clave")
    rendered = repr(settings) + str(settings.model_dump())
    for secret in ("sk-ant-secreta", "pw-secreto", "clave'"):
        assert secret not in rendered
    assert isinstance(settings.anthropic_api_key, SecretStr)
    assert settings.secret("anthropic_api_key") == "sk-ant-secreta"
    assert Settings(anthropic_api_key="   ").anthropic_api_key is None


def test_export_errors_do_not_leak_filesystem_paths(invoice_for_export, tmp_path, caplog):
    from app.services.face_exporter import FacturaeExporter, FacturaeExportError

    missing = tmp_path / "carpeta-secreta" / "cert.p12"
    exporter = FacturaeExporter(Settings(facturae_cert_path=missing))
    with caplog.at_level(logging.ERROR), pytest.raises(FacturaeExportError) as error:
        exporter.to_xml(invoice_for_export, sign=True)
    assert "carpeta-secreta" not in str(error.value)
    assert "carpeta-secreta" in caplog.text

    broken = tmp_path / "roto.p12"
    broken.write_bytes(b"no es un pkcs12")
    with pytest.raises(FacturaeExportError) as error:
        FacturaeExporter(Settings(facturae_cert_path=broken)).to_xml(invoice_for_export, sign=True)
    assert "roto.p12" not in str(error.value)


@pytest.fixture()
def invoice_for_export(db_session):
    from tests.test_face_exporter import _invoice, _supplier

    supplier = _supplier()
    db_session.add(supplier)
    invoice = _invoice(supplier)
    db_session.add(invoice)
    db_session.commit()
    return invoice


def test_unexpected_extraction_errors_are_not_echoed(client, fake_extractor):
    fake_extractor.error = ExtractionError("No se pudo leer")
    response = upload(client, pdf_bytes("x"))
    assert response.status_code == 422
    extractor = InvoiceExtractor(Settings(), strategy=_Exploding())
    with pytest.raises(ExtractionError) as error:
        extractor.extract(pdf_bytes("y"), "f.pdf", "application/pdf")
    assert "C:\\secreto" not in str(error.value)


class _Exploding:
    method = None

    def extract(self, *_args):
        raise OSError("C:\\secreto\\clave.txt")


# --------------------------------------------------------------------------- 9. AI client


def test_anthropic_client_never_retries_and_default_timeout_is_90_seconds(monkeypatch):
    import anthropic

    from app.services.extraction import ai_strategy

    created = {}

    def fake_anthropic(**kwargs):
        created.update(kwargs)
        return object()

    monkeypatch.setattr(anthropic, "Anthropic", fake_anthropic)
    ai_strategy._anthropic_client.cache_clear()
    ai_strategy._anthropic_client("sk-test", Settings().ai_timeout_seconds)
    assert created["max_retries"] == 0
    assert created["timeout"] == 90.0
    ai_strategy._anthropic_client.cache_clear()


# --------------------------------------------------------------------------- 10. currency


def test_patch_can_change_currency_and_normalises_it(client):
    created = create_invoice(client)
    response = client.patch(f"{API}/invoices/{created['id']}", json={"currency": "usd"})
    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "USD"
    assert any(c["rule_code"] == "CURRENCY" and not c["passed"] for c in body["validation_logs"])


@pytest.mark.parametrize("value", ["EU", "EURO", "12E", "€€€", "E R", None])
def test_patch_rejects_invalid_currency(client, value):
    created = create_invoice(client)
    assert client.patch(f"{API}/invoices/{created['id']}", json={"currency": value}).status_code == 422


def test_create_rejects_invalid_currency_and_uppercases_valid_one(client):
    assert client.post(f"{API}/invoices", json=invoice_payload("C-1", currency="e1r")).status_code == 422
    created = client.post(f"{API}/invoices", json=invoice_payload("C-2", currency="eur"))
    assert created.json()["currency"] == "EUR"


# --------------------------------------------------------------------------- 11. payment IBAN


@pytest.mark.parametrize("iban", [VALID_IBAN, "DE89370400440532013000", "GB82WEST12345698765432"])
def test_valid_ibans(iban):
    assert is_valid_iban(iban)


@pytest.mark.parametrize(
    "iban", ["", "ES9121000418450200051333", "ES91210004184502000513", "1234", "XX00", "ES91 2100 0418 4502 0005 133!"]
)
def test_invalid_ibans(iban):
    assert not is_valid_iban(iban)


def test_iban_is_normalised_on_create_and_patch_and_can_be_cleared(client):
    created = client.post(f"{API}/invoices", json=invoice_payload("I-1", payment_iban="es91 2100 0418 4502 0005 1332"))
    assert created.status_code == 201
    assert created.json()["payment_iban"] == VALID_IBAN
    invoice_id = created.json()["id"]
    assert client.patch(f"{API}/invoices/{invoice_id}", json={"payment_iban": None}).json()["payment_iban"] is None
    assert client.patch(f"{API}/invoices/{invoice_id}", json={"payment_iban": "ES00"}).status_code == 422
    assert client.patch(f"{API}/invoices/{invoice_id}", json={"payment_iban": " "}).json()["payment_iban"] is None
    assert (
        client.patch(f"{API}/invoices/{invoice_id}", json={"payment_iban": VALID_IBAN}).json()["payment_iban"]
        == VALID_IBAN
    )
    assert normalize_iban(" es91-2100 ") == "ES912100"


def test_exported_xml_carries_the_iban_only_when_known(client):
    with_iban = client.post(f"{API}/invoices", json=invoice_payload("X-1", payment_iban=VALID_IBAN)).json()
    without = client.post(f"{API}/invoices", json=invoice_payload("X-2")).json()
    xml_with = client.get(f"{API}/invoices/{with_iban['id']}/facturae", params={"sign": False})
    xml_without = client.get(f"{API}/invoices/{without['id']}/facturae", params={"sign": False})
    assert b"<IBAN>" + VALID_IBAN.encode() + b"</IBAN>" in xml_with.content
    assert b"PaymentDetails" not in xml_without.content


# --------------------------------------------------------------------------- 12. API key


@pytest.fixture()
def protected(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "api_key", SecretStr("s3creta"))
    return client


PROTECTED_ROUTES = [
    ("GET", f"{API}/invoices"),
    ("GET", f"{API}/invoices/1"),
    ("GET", f"{API}/suppliers"),
    ("GET", f"{API}/stats/dashboard"),
    ("GET", f"{API}/invoices/export/csv"),
    ("POST", f"{API}/invoices/revalidate"),
]


@pytest.mark.parametrize(("method", "path"), PROTECTED_ROUTES)
def test_api_routes_require_the_key_when_configured(protected, method, path):
    response = protected.request(method, path, json={} if method == "POST" else None)
    assert response.status_code == 401
    assert response.json()["detail"] == "Clave de API ausente o no válida: envíe la cabecera X-API-Key"
    wrong = protected.request(method, path, headers={"X-API-Key": "otra"}, json={} if method == "POST" else None)
    assert wrong.status_code == 401


def test_correct_key_is_accepted_and_health_stays_public(protected):
    assert protected.get("/health").status_code == 200
    assert protected.get(f"{API}/invoices", headers={"X-API-Key": "s3creta"}).status_code == 200


def test_no_key_configured_means_open_api(client):
    assert client.get(f"{API}/invoices").status_code == 200


def test_explicit_signing_is_forbidden_without_api_key_and_default_is_unsigned(client, monkeypatch, tmp_path):
    from tests.test_face_exporter import _make_pkcs12

    cert_path, _ = _make_pkcs12(tmp_path)
    monkeypatch.setattr(get_settings(), "facturae_cert_path", cert_path)
    monkeypatch.setattr(get_settings(), "facturae_cert_password", SecretStr("secreto-de-prueba"))
    invoice_id = create_invoice(client)["id"]
    forced = client.get(f"{API}/invoices/{invoice_id}/facturae", params={"sign": True})
    assert forced.status_code == 403
    default = client.get(f"{API}/invoices/{invoice_id}/facturae")
    assert default.status_code == 200
    assert b"Signature" not in default.content


def test_signing_works_when_the_api_key_is_configured(client, monkeypatch, tmp_path):
    from tests.test_face_exporter import CERT_PASSWORD, _make_pkcs12

    cert_path, _ = _make_pkcs12(tmp_path)
    monkeypatch.setattr(get_settings(), "facturae_cert_path", cert_path)
    monkeypatch.setattr(get_settings(), "facturae_cert_password", SecretStr(CERT_PASSWORD))
    invoice_id = create_invoice(client)["id"]
    monkeypatch.setattr(get_settings(), "api_key", SecretStr("s3creta"))
    headers = {"X-API-Key": "s3creta"}
    signed = client.get(f"{API}/invoices/{invoice_id}/facturae", params={"sign": True}, headers=headers)
    assert signed.status_code == 200
    assert b"<ds:Signature" in signed.content
    default = client.get(f"{API}/invoices/{invoice_id}/facturae", headers=headers)
    assert b"<ds:Signature" in default.content


def test_startup_warns_when_a_certificate_is_configured_without_api_key(caplog, tmp_path):
    from app.core.security import warn_if_signing_unprotected

    with caplog.at_level(logging.WARNING):
        warn_if_signing_unprotected(Settings(facturae_cert_path=tmp_path / "c.p12"))
    assert "API_KEY" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        warn_if_signing_unprotected(Settings(facturae_cert_path=tmp_path / "c.p12", api_key="k"))
        warn_if_signing_unprotected(Settings())
    assert caplog.text == ""


# --------------------------------------------------------------------------- 13. CSV


def test_csv_currency_cell_is_escaped(client, engine):
    invoice_id = create_invoice(client)["id"]
    with engine.begin() as connection:
        connection.execute(text("UPDATE invoices SET currency = '=1+' WHERE id = :id"), {"id": invoice_id})
    response = client.get(f"{API}/invoices/export/csv")
    assert "'=1+" in response.content.decode("utf-8")


def test_csv_export_is_capped(client, monkeypatch):
    from app.routers import invoices as invoices_router

    monkeypatch.setattr(invoices_router, "MAX_EXPORT_ROWS", 2)
    create_invoice(client, "E-1")
    create_invoice(client, "E-2")
    assert client.get(f"{API}/invoices/export/csv").status_code == 200
    create_invoice(client, "E-3")
    response = client.get(f"{API}/invoices/export/csv")
    assert response.status_code == 413
    assert "máximo de 2 filas" in response.json()["detail"]


def test_csv_cap_counts_line_rows_when_lines_are_included(client, monkeypatch):
    from app.routers import invoices as invoices_router

    monkeypatch.setattr(invoices_router, "MAX_EXPORT_ROWS", 3)
    create_invoice(client, "L-1")  # two lines
    create_invoice(client, "L-2")  # two lines
    assert client.get(f"{API}/invoices/export/csv").status_code == 200
    assert client.get(f"{API}/invoices/export/csv", params={"include_lines": True}).status_code == 413


# --------------------------------------------------------------------------- 14. misc


def test_schemas_expose_the_new_fields():
    assert "is_synthetic" in InvoiceRead.model_fields
    assert "payment_iban" in InvoiceRead.model_fields
    assert "is_synthetic" in InvoiceSummary.model_fields


def test_extraction_package_keeps_the_old_import_path():
    import app.services.extractor as legacy
    from app.services import extraction

    for name in ("InvoiceExtractor", "ClaudeVisionStrategy", "DemoParserStrategy", "get_extractor", "parse_decimal"):
        assert getattr(legacy, name) is getattr(extraction, name)


def test_invalid_settings_value_for_api_key_type():
    with pytest.raises(ValidationError):
        Settings(api_key=["no", "valido"])


def test_invoice_model_defaults(db_session):
    from tests.test_face_exporter import _invoice, _supplier

    supplier = _supplier()
    db_session.add(supplier)
    invoice = _invoice(supplier)
    db_session.add(invoice)
    db_session.commit()
    assert invoice.is_synthetic is False
    assert isinstance(invoice, Invoice)
