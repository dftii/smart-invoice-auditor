"""Tests for scripts/seed.py: scenario coverage, audit outcomes, Facturae export, idempotency and --reset."""

import re
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from lxml import etree
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import AuditStatus, Invoice, InvoiceLine, Severity, Supplier, ValidationLog
from app.schemas import InvoiceCreate, InvoiceLineCreate, SupplierCreate
from app.services.auditor import (
    DUPLICATE,
    FACE_DIR3,
    GRAND_TOTAL,
    IRPF_TOTAL,
    LINE_BASE,
    LINE_IRPF,
    LINE_VAT,
    SUPPLIER_TAX_ID,
    TAX_BASE_SUM,
    VAT_TOTAL,
    InvoiceAuditor,
)
from app.services.face_exporter import FacturaeExporter, FacturaeExportError
from app.services.invoice_service import create_invoice_manual
from app.services.tax_id import validate_tax_id
from scripts import seed as seed_script

XSD_PATH = Path(__file__).parent / "xsd" / "Facturaev3_2_2.xsd"
DIR3_CODE = re.compile(r"^[A-Z][A-Z0-9]\d{7}$")
TODAY = date.today()


@pytest.fixture(scope="module")
def xsd() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(XSD_PATH)))


@pytest.fixture()
def scenarios() -> list[seed_script.Scenario]:
    return seed_script.build_scenarios(TODAY)


@pytest.fixture()
def seeded(db_session: Session, scenarios) -> list[tuple[seed_script.Scenario, Invoice]]:
    invoices = seed_script.seed(db_session, today=TODAY)
    return list(zip(scenarios, invoices, strict=True))


def failed(invoice: Invoice, severity: Severity) -> list[ValidationLog]:
    return [
        log
        for log in invoice.validation_logs
        if log.run_id == invoice.last_audit_run_id and not log.passed and log.severity is severity
    ]


def failed_codes(invoice: Invoice, severity: Severity) -> list[str]:
    return sorted(log.rule_code for log in failed(invoice, severity))


def pick(seeded, predicate):
    return [(scenario, invoice) for scenario, invoice in seeded if predicate(scenario)]


def is_tax_id_case(scenario: seed_script.Scenario) -> bool:
    return scenario.expected_errors == (SUPPLIER_TAX_ID,)


def is_math_case(scenario: seed_script.Scenario) -> bool:
    return scenario.expected_status is AuditStatus.DISCREPANCY and not is_tax_id_case(scenario)


# ----------------------------------------------------------------------------- Volume and mix


def test_seeds_exactly_25_invoices(db_session, seeded):
    assert len(seeded) == 25
    assert db_session.scalar(select(func.count(Invoice.id))) == 25
    assert db_session.scalar(select(func.count(InvoiceLine.id))) >= 40


def test_scenario_definitions_cover_the_requested_mix(scenarios):
    ready = [s for s in scenarios if s.is_ready_for_face]
    with_warnings = [s for s in scenarios if s.expected_status is AuditStatus.VALIDATED and s.expected_warnings]
    assert (len(ready), len(with_warnings)) == (8, 6)
    assert sum(is_math_case(s) for s in scenarios) == 7
    assert sum(is_tax_id_case(s) for s in scenarios) == 2
    assert sum(s.duplicate_of is not None for s in scenarios) == 2
    # Every math scenario injects a different, specific failing rule.
    math_rules = [s.expected_errors for s in scenarios if is_math_case(s)]
    assert len(set(math_rules)) == 7
    assert all(s.mutate is not None for s in scenarios if is_math_case(s))


def test_status_counts_match_scenarios(db_session, seeded):
    expected = Counter(scenario.expected_status for scenario, _ in seeded)
    actual = Counter(invoice.audit_status for _, invoice in seeded)
    assert actual == expected
    assert dict(actual) == {AuditStatus.VALIDATED: 14, AuditStatus.DISCREPANCY: 9, AuditStatus.DUPLICATE: 2}
    rows = db_session.execute(select(Invoice.audit_status, func.count(Invoice.id)).group_by(Invoice.audit_status)).all()
    assert dict(rows) == dict(expected)


def test_every_invoice_matches_its_scenario_rules(seeded):
    for scenario, invoice in seeded:
        assert invoice.audit_status is scenario.expected_status, scenario.key
        assert failed_codes(invoice, Severity.ERROR) == sorted(scenario.expected_errors), scenario.key
        assert failed_codes(invoice, Severity.WARNING) == sorted(scenario.expected_warnings), scenario.key
        assert invoice.error_count == len(scenario.expected_errors)
        assert invoice.warning_count == len(scenario.expected_warnings)


def test_invoices_are_marked_as_demo_data_created_manually(seeded):
    for _, invoice in seeded:
        assert invoice.notes.startswith(seed_script.MARKER)
        assert invoice.extraction_method.value == "MANUAL"
        assert invoice.currency == "EUR"


def test_reauditing_gives_the_same_status(db_session, seeded):
    auditor = InvoiceAuditor(db_session)
    for scenario, invoice in seeded:
        assert auditor.audit(invoice).status is scenario.expected_status, scenario.key


# ----------------------------------------------------------------------------- Suppliers, buyers and dates


def test_suppliers_use_real_tax_id_algorithms(db_session, seeded):
    suppliers = list(db_session.scalars(select(Supplier)))
    assert 10 <= len(suppliers) <= 12
    invalid = [s for s in suppliers if not validate_tax_id(s.tax_id).is_valid]
    assert len(invalid) == 2
    assert all(not s.tax_id_valid for s in invalid)
    assert all(s.tax_id_valid for s in suppliers if s not in invalid)
    assert {s.tax_id_type.value for s in suppliers} == {"CIF", "NIF"}
    assert all(re.fullmatch(r"\d{5}", s.postal_code) and s.email and s.province for s in suppliers)


def test_buyers_are_public_and_private_with_plausible_dir3(seeded):
    with_dir3 = [invoice for _, invoice in seeded if invoice.dir3_accounting_office]
    without_dir3 = [invoice for _, invoice in seeded if not invoice.dir3_accounting_office]
    assert with_dir3 and without_dir3
    for invoice in with_dir3:
        for code in (invoice.dir3_accounting_office, invoice.dir3_managing_body, invoice.dir3_processing_unit):
            assert DIR3_CODE.match(code), code
    names = " ".join(invoice.buyer_name for invoice in with_dir3)
    assert all(word in names for word in ("Ayuntamiento", "Diputación", "Universidad"))
    assert any("S.A." in invoice.buyer_name or "S.L." in invoice.buyer_name for invoice in without_dir3)
    assert all(re.fullmatch(r"\d{5}", invoice.buyer_postal_code) for _, invoice in seeded)


def test_dates_are_past_ordered_and_spread_over_fourteen_months(seeded):
    issue_dates = [invoice.issue_date for _, invoice in seeded]
    assert issue_dates == sorted(issue_dates)
    assert max(issue_dates) < TODAY
    assert (TODAY - min(issue_dates)).days >= 365
    assert (TODAY - min(issue_dates)).days <= 14 * 31
    months = {(d.year, d.month) for d in issue_dates}
    first, last = min(months), max(months)
    span = (last[0] - first[0]) * 12 + last[1] - first[1] + 1
    assert span >= 13
    assert len(months) == span  # no empty month between the first and the last invoice
    assert all(invoice.due_date >= invoice.issue_date for _, invoice in seeded)


def test_vat_irpf_and_discount_variety(seeded):
    lines = [line for _, invoice in seeded for line in invoice.lines]
    assert {int(line.vat_rate) for line in lines} >= {21, 10, 4, 0}
    assert {int(line.irpf_rate) for line in lines} >= {0, 7, 15}
    assert any(line.discount_rate > 0 for line in lines)


# ----------------------------------------------------------------------------- Ready for FACe


def test_ready_invoices_have_no_warnings_and_export_valid_facturae(seeded, xsd):
    ready = pick(seeded, lambda s: s.is_ready_for_face)
    assert len(ready) == 8
    exporter = FacturaeExporter()
    for scenario, invoice in ready:
        assert invoice.audit_status is AuditStatus.VALIDATED
        assert invoice.warning_count == 0 and invoice.error_count == 0, scenario.key
        root = etree.fromstring(exporter.to_xml(invoice, sign=False))
        assert xsd.validate(root), (scenario.key, xsd.error_log)


def test_invoices_with_warnings_are_validated_but_have_the_expected_warning(seeded):
    flagged = pick(seeded, lambda s: s.expected_status is AuditStatus.VALIDATED and s.expected_warnings)
    assert len(flagged) == 6
    assert FACE_DIR3 in {log.rule_code for _, invoice in flagged for log in failed(invoice, Severity.WARNING)}
    for scenario, invoice in flagged:
        assert invoice.audit_status is AuditStatus.VALIDATED
        assert invoice.warning_count == len(scenario.expected_warnings) >= 1


# ----------------------------------------------------------------------------- Errors


@pytest.mark.parametrize(
    ("key", "rule", "max_difference"),
    [
        ("m1", VAT_TOTAL, "0.05"),
        ("m2", GRAND_TOTAL, "0.05"),
        ("m3", LINE_BASE, None),
        ("m4", LINE_VAT, "0.05"),
        ("m5", LINE_IRPF, None),
        ("m6", TAX_BASE_SUM, "0.05"),
        ("m7", IRPF_TOTAL, None),
    ],
)
def test_math_error_invoices_fail_only_their_specific_rule(seeded, key, rule, max_difference):
    from decimal import Decimal

    by_key = {scenario.key: invoice for scenario, invoice in seeded}
    invoice = by_key[key]
    assert invoice.audit_status is AuditStatus.DISCREPANCY
    errors = failed(invoice, Severity.ERROR)
    assert [log.rule_code for log in errors] == [rule]
    if max_difference is not None:
        difference = abs(Decimal(errors[0].expected_value) - Decimal(errors[0].actual_value))
        assert Decimal("0.01") <= difference <= Decimal(max_difference)


def test_invalid_supplier_tax_id_invoices_fail_supplier_tax_id(seeded):
    invalid = pick(seeded, is_tax_id_case)
    assert len(invalid) == 2
    for _, invoice in invalid:
        assert invoice.audit_status is AuditStatus.DISCREPANCY
        assert [log.rule_code for log in failed(invoice, Severity.ERROR)] == [SUPPLIER_TAX_ID]
        assert not validate_tax_id(invoice.supplier_tax_id).is_valid
        assert not invoice.supplier.tax_id_valid


def test_duplicates_reference_the_earlier_invoice(seeded):
    by_key = {scenario.key: invoice for scenario, invoice in seeded}
    duplicates = pick(seeded, lambda s: s.duplicate_of is not None)
    assert len(duplicates) == 2
    for scenario, invoice in duplicates:
        original = by_key[scenario.duplicate_of]
        assert invoice.audit_status is AuditStatus.DUPLICATE
        assert original.audit_status is not AuditStatus.DUPLICATE
        assert original.id < invoice.id
        assert (invoice.supplier_id, invoice.invoice_series, invoice.invoice_number) == (
            original.supplier_id,
            original.invoice_series,
            original.invoice_number,
        )
        [log] = failed(invoice, Severity.ERROR)
        assert log.rule_code == DUPLICATE
        assert f"#{original.id}" in log.message


# ----------------------------------------------------------------------------- Seed building blocks


def test_scenario_definitions_are_deterministic():
    first = seed_script.build_scenarios(TODAY)
    second = seed_script.build_scenarios(TODAY)
    assert [(s.key, s.invoice_number, s.issue_date, s.supplier.tax_id) for s in first] == [
        (s.key, s.invoice_number, s.issue_date, s.supplier.tax_id) for s in second
    ]


def test_corrupt_tax_id_breaks_only_the_control_character():
    for valid in (
        seed_script.make_nif(12345678),
        seed_script.make_cif("B", "4612345"),
        seed_script.make_cif("P", "2800721"),
    ):
        assert validate_tax_id(valid).is_valid
        broken = seed_script.corrupt_tax_id(valid)
        assert broken[:-1] == valid[:-1]
        assert not validate_tax_id(broken).is_valid


def test_seed_refuses_to_run_twice(db_session, seeded):
    with pytest.raises(seed_script.AlreadySeededError):
        seed_script.seed(db_session, today=TODAY)
    assert db_session.scalar(select(func.count(Invoice.id))) == 25


def test_seed_fails_loudly_when_an_expectation_is_not_met(db_session, monkeypatch):
    original = seed_script.build_scenarios

    def tampered(today=None):
        items = original(today)
        items[0] = seed_script.replace(items[0], expected_status=AuditStatus.DISCREPANCY)
        return items

    monkeypatch.setattr(seed_script, "build_scenarios", tampered)
    with pytest.raises(seed_script.SeedError, match="estado VALIDATED"):
        seed_script.seed(db_session, today=TODAY)


def test_seed_fails_loudly_when_a_ready_invoice_cannot_be_exported(db_session, monkeypatch):
    def broken_export(self, invoice, sign=None):
        raise FacturaeExportError("simulado")

    monkeypatch.setattr(FacturaeExporter, "to_xml", broken_export)
    with pytest.raises(seed_script.SeedError, match="no se exporta a Facturae"):
        seed_script.seed(db_session, today=TODAY)


def test_reset_only_removes_seeded_data(db_session, seeded):
    other = create_invoice_manual(
        db_session,
        InvoiceCreate(
            supplier=SupplierCreate(name="Taller Pérez, S.L.", tax_id=seed_script.make_cif("B", "2812345")),
            invoice_number="T-1",
            issue_date=date(2026, 1, 15),
            tax_base="100.00",
            total_vat="21.00",
            total_amount="121.00",
            lines=[
                InvoiceLineCreate(
                    concept="Reparación",
                    quantity=1,
                    unit_price="100.00",
                    vat_rate=21,
                    line_base="100.00",
                    vat_amount="21.00",
                )
            ],
        ),
    )
    suppliers_before = db_session.scalar(select(func.count(Supplier.id)))
    assert seed_script.reset(db_session) == (25, suppliers_before - 1)
    assert db_session.scalars(select(Invoice)).all() == [other]
    assert db_session.scalar(select(func.count(InvoiceLine.id))) == 1
    assert db_session.scalar(select(func.count(ValidationLog.id))) == 0
    assert not seed_script.is_seeded(db_session)


# ----------------------------------------------------------------------------- CLI


@pytest.fixture()
def database_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'x.db').as_posix()}"


def counts(url: str) -> tuple[int, int, int]:
    engine = create_engine(url)
    try:
        with sessionmaker(bind=engine)() as db:
            return (
                db.scalar(select(func.count(Invoice.id))),
                db.scalar(select(func.count(Supplier.id))),
                db.scalar(select(func.count(ValidationLog.id))),
            )
    finally:
        engine.dispose()


def test_main_seeds_an_empty_database_and_prints_a_spanish_summary(database_url, capsys):
    assert seed_script.main(["--database-url", database_url]) == 0
    output = capsys.readouterr().out
    assert "Total de facturas: 25" in output
    assert "Listas para FACe (validadas sin advertencias): 8" in output
    assert "Validadas con advertencias: 6" in output
    assert "Error matemático (discrepancias): 7" in output
    assert "Duplicadas: 2" in output
    assert "Primera regla fallida" in output
    invoices, suppliers, logs = counts(database_url)
    assert invoices == 25 and 10 <= suppliers <= 12 and logs > 25


def test_main_is_idempotent_without_reset(database_url, capsys):
    assert seed_script.main(["--database-url", database_url]) == 0
    before = counts(database_url)
    capsys.readouterr()
    assert seed_script.main(["--database-url", database_url]) == 0
    assert "Ya existen facturas de demostración" in capsys.readouterr().out
    assert counts(database_url) == before


def test_main_reset_recreates_seeded_data_and_keeps_other_invoices(database_url, capsys):
    assert seed_script.main(["--database-url", database_url]) == 0
    engine = create_engine(database_url)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        other = create_invoice_manual(
            db,
            InvoiceCreate(
                supplier=SupplierCreate(name="Taller Pérez, S.L.", tax_id=seed_script.make_cif("B", "2812345")),
                invoice_number="T-1",
                issue_date=date(2026, 1, 15),
                tax_base="100.00",
                total_vat="21.00",
                total_amount="121.00",
                lines=[
                    InvoiceLineCreate(
                        concept="Reparación",
                        quantity=1,
                        unit_price="100.00",
                        vat_rate=21,
                        line_base="100.00",
                        vat_amount="21.00",
                    )
                ],
            ),
        )
        db.commit()
        other_id = other.id
    engine.dispose()
    invoices, suppliers, _ = counts(database_url)
    assert invoices == 26

    capsys.readouterr()
    assert seed_script.main(["--reset", "--database-url", database_url]) == 0
    output = capsys.readouterr().out
    assert "Eliminadas 25 facturas de demostración" in output
    assert "Total de facturas: 25" in output
    assert counts(database_url)[:2] == (26, suppliers)

    engine = create_engine(database_url)
    with sessionmaker(bind=engine)() as db:
        assert db.get(Invoice, other_id).notes is None
        seeded_count = db.scalar(select(func.count(Invoice.id)).where(Invoice.notes.startswith(seed_script.MARKER)))
    engine.dispose()
    assert seeded_count == 25


def test_main_rolls_back_everything_on_error(database_url, monkeypatch, capsys):
    calls = 0
    real_to_xml = FacturaeExporter.to_xml

    def failing_on_fourth_export(self, invoice, sign=None):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise FacturaeExportError("simulado")
        return real_to_xml(self, invoice, sign=sign)

    monkeypatch.setattr(FacturaeExporter, "to_xml", failing_on_fourth_export)
    assert seed_script.main(["--database-url", database_url]) == 1
    assert "Se han revertido todos los cambios" in capsys.readouterr().err
    assert counts(database_url) == (0, 0, 0)


def test_init_db_adds_columns_missing_from_an_older_schema(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from app.db.database import add_missing_columns, init_db

    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    init_db(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE invoices DROP COLUMN facturae_exported_at"))
        connection.execute(text("ALTER TABLE invoices DROP COLUMN facturae_export_count"))

    assert sorted(add_missing_columns(engine)) == ["invoices.facturae_export_count", "invoices.facturae_exported_at"]
    columns = {column["name"] for column in inspect(engine).get_columns("invoices")}
    assert {"facturae_exported_at", "facturae_export_count"} <= columns
    assert add_missing_columns(engine) == []
    engine.dispose()
