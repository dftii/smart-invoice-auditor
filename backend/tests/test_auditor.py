"""Tests for the invoice auditor: every rule (pass and fail), rounding, duplicates, status priority and persistence."""

import itertools
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.models import (
    AuditStatus,
    Invoice,
    InvoiceLine,
    PersonType,
    Severity,
    Supplier,
    TaxIdType,
    ValidationLog,
)
from app.services import auditor as auditor_module
from app.services.auditor import (
    BUYER_TAX_ID,
    CURRENCY,
    DATES,
    DUPLICATE,
    FACE_DIR3,
    FACE_PARTY_DATA,
    GRAND_TOTAL,
    HAS_LINES,
    IRPF_RATE,
    IRPF_TOTAL,
    LINE_BASE,
    LINE_IRPF,
    LINE_VAT,
    SUPPLIER_TAX_ID,
    TAX_BASE_SUM,
    VAT_RATE,
    VAT_TOTAL,
    InvoiceAuditor,
)

ALL_RULES = {
    SUPPLIER_TAX_ID,
    BUYER_TAX_ID,
    DATES,
    HAS_LINES,
    LINE_BASE,
    VAT_RATE,
    LINE_VAT,
    IRPF_RATE,
    LINE_IRPF,
    TAX_BASE_SUM,
    VAT_TOTAL,
    IRPF_TOTAL,
    GRAND_TOTAL,
    DUPLICATE,
    CURRENCY,
    FACE_DIR3,
    FACE_PARTY_DATA,
}

SUPPLIER_CIF = "B65410011"
BUYER_CIF = "P0800000B"
TODAY = date(2024, 6, 30)
CENT = Decimal("0.01")
_counter = itertools.count(1)


def q(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def D(value) -> Decimal:
    return Decimal(str(value))


def line(quantity, unit_price, vat=21, irpf=0, discount=0, **overrides) -> dict:
    """Line specification whose declared amounts are computed independently of the auditor."""
    quantity, unit_price, discount = D(quantity), D(unit_price), D(discount)
    base = q(quantity * unit_price * (100 - discount) / 100)
    spec = {
        "quantity": quantity,
        "unit_price": unit_price,
        "discount_rate": discount,
        "vat_rate": D(vat),
        "irpf_rate": D(irpf),
        "line_base": base,
        "vat_amount": q(base * D(vat) / 100),
        "irpf_amount": q(base * D(irpf) / 100),
    }
    spec.update({k: D(v) if k in spec and k != "discount_rate" else v for k, v in overrides.items()})
    return spec


def get_supplier(db, tax_id: str = SUPPLIER_CIF) -> Supplier:
    supplier = db.scalar(select(Supplier).where(Supplier.tax_id == tax_id))
    if supplier is None:
        supplier = Supplier(
            name=f"Proveedor {tax_id}",
            tax_id=tax_id,
            address="Calle Mayor 1",
            postal_code="28013",
            city="Madrid",
            province="Madrid",
        )
        db.add(supplier)
        db.flush()
    return supplier


def make_invoice(db, lines=None, *, supplier_tax_id: str = SUPPLIER_CIF, flush: bool = True, **overrides) -> Invoice:
    """Create a consistent invoice (totals derived from the lines) and apply field overrides."""
    specs = lines if lines is not None else [line(2, "50.00"), line(1, "200.00")]
    supplier = get_supplier(db, supplier_tax_id)
    tax_base = sum((s["line_base"] for s in specs), Decimal(0))
    total_vat = sum((s["vat_amount"] for s in specs), Decimal(0))
    total_irpf = sum((s["irpf_amount"] for s in specs), Decimal(0))
    fields = {
        "supplier": supplier,
        "invoice_number": f"F-{next(_counter):04d}",
        "issue_date": date(2024, 3, 15),
        "due_date": date(2024, 4, 15),
        "currency": "EUR",
        "supplier_tax_id": supplier_tax_id,
        "buyer_name": "Ayuntamiento de Barcelona",
        "buyer_tax_id": BUYER_CIF,
        "buyer_address": "Plaça de Sant Jaume 1",
        "buyer_postal_code": "08002",
        "buyer_city": "Barcelona",
        "buyer_province": "Barcelona",
        "dir3_accounting_office": "L01080193",
        "dir3_managing_body": "L01080193",
        "dir3_processing_unit": "L01080193",
        "tax_base": tax_base,
        "total_vat": total_vat,
        "total_irpf": total_irpf,
        "total_amount": tax_base + total_vat - total_irpf,
    }
    fields.update(overrides)
    invoice = Invoice(**fields)
    invoice.lines = [InvoiceLine(line_number=i, concept=f"Concepto {i}", **spec) for i, spec in enumerate(specs, 1)]
    db.add(invoice)
    if flush:
        db.flush()
    return invoice


def checks_for(report, rule_code):
    return [c for c in report.checks if c.rule_code == rule_code]


def failed(report, rule_code, severity=None):
    return [c for c in checks_for(report, rule_code) if not c.passed and (severity is None or c.severity is severity)]


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(auditor_module, "_today", lambda: TODAY)


@pytest.fixture()
def auditor(db_session) -> InvoiceAuditor:
    return InvoiceAuditor(db_session)


class TestCleanInvoice:
    def test_valid_invoice_passes_every_rule(self, db_session, auditor):
        invoice = make_invoice(db_session)
        report = auditor.audit(invoice)

        assert report.status is AuditStatus.VALIDATED
        assert report.error_count == 0
        assert report.warning_count == 0
        assert report.duplicate_of == []
        assert {c.rule_code for c in report.checks} == ALL_RULES
        assert all(c.passed and c.severity is Severity.INFO for c in report.checks)
        assert "validada" in report.summary.lower()

    def test_invoice_fields_are_updated(self, db_session, auditor):
        invoice = make_invoice(db_session)
        report = auditor.audit(invoice)

        assert invoice.audit_status is AuditStatus.VALIDATED
        assert invoice.audit_summary == report.summary
        assert invoice.error_count == 0
        assert invoice.warning_count == 0
        assert invoice.last_audit_run_id == report.run_id
        assert invoice.last_audited_at == report.audited_at
        assert invoice.last_audited_at.tzinfo is not None

    def test_validation_logs_are_persisted_with_the_run_id(self, db_session, auditor):
        invoice = make_invoice(db_session)
        report = auditor.audit(invoice)

        logs = db_session.scalars(select(ValidationLog).where(ValidationLog.invoice_id == invoice.id)).all()
        assert len(logs) == len(report.checks)
        assert {log.run_id for log in logs} == {report.run_id}
        assert {log.rule_code for log in logs} == ALL_RULES
        assert [log.id for log in invoice.validation_logs] == [log.id for log in logs]

    def test_audit_never_commits(self, db_session, auditor, monkeypatch):
        def forbidden() -> None:
            raise AssertionError("the auditor must not commit")

        invoice = make_invoice(db_session)
        monkeypatch.setattr(db_session, "commit", forbidden)
        auditor.audit(invoice)
        assert db_session.in_transaction()

    def test_pending_invoice_is_flushed_and_audited(self, db_session, auditor):
        invoice = make_invoice(db_session, flush=False)
        assert invoice.id is None
        report = auditor.audit(invoice)
        assert invoice.id is not None
        assert report.invoice_id == invoice.id
        assert report.status is AuditStatus.VALIDATED


class TestSupplierTaxId:
    def test_company_supplier_is_classified(self, db_session, auditor):
        invoice = make_invoice(db_session)
        auditor.audit(invoice)
        assert invoice.supplier.tax_id_type is TaxIdType.CIF
        assert invoice.supplier.tax_id_valid is True
        assert invoice.supplier.person_type is PersonType.LEGAL_ENTITY

    def test_individual_supplier_is_classified(self, db_session, auditor):
        invoice = make_invoice(db_session, supplier_tax_id="12345678Z")
        report = auditor.audit(invoice)
        assert report.status is AuditStatus.VALIDATED
        assert invoice.supplier.tax_id_type is TaxIdType.NIF
        assert invoice.supplier.person_type is PersonType.INDIVIDUAL

    def test_foreign_resident_supplier_is_classified(self, db_session, auditor):
        invoice = make_invoice(db_session, supplier_tax_id="X1234567L")
        auditor.audit(invoice)
        assert invoice.supplier.tax_id_type is TaxIdType.NIE
        assert invoice.supplier.tax_id_valid is True

    def test_invalid_supplier_tax_id_is_an_error(self, db_session, auditor):
        invoice = make_invoice(db_session, supplier_tax_id="B65410012")
        report = auditor.audit(invoice)

        [check] = failed(report, SUPPLIER_TAX_ID)
        assert check.severity is Severity.ERROR
        assert check.message == (
            "El identificador fiscal del emisor 'B65410012' no es válido: "
            "Dígito de control incorrecto: se esperaba '1' y se encontró '2'"
        )
        assert report.status is AuditStatus.DISCREPANCY
        assert invoice.supplier.tax_id_valid is False
        assert invoice.supplier.tax_id_type is TaxIdType.CIF

    def test_supplier_with_formatted_tax_id_is_valid(self, db_session, auditor):
        invoice = make_invoice(db_session, supplier_tax_id=SUPPLIER_CIF)
        invoice.supplier_tax_id = "ES B-65410011"
        report = auditor.audit(invoice)
        assert not failed(report, SUPPLIER_TAX_ID)

    def test_empty_supplier_tax_id_is_an_error(self, db_session, auditor):
        invoice = make_invoice(db_session, supplier_tax_id=SUPPLIER_CIF)
        invoice.supplier_tax_id = ""
        report = auditor.audit(invoice)
        [check] = failed(report, SUPPLIER_TAX_ID)
        assert "No se ha indicado" in check.message


class TestBuyerTaxId:
    def test_invalid_buyer_is_an_error(self, db_session, auditor):
        invoice = make_invoice(db_session, buyer_tax_id="P0800000C")
        report = auditor.audit(invoice)
        [check] = failed(report, BUYER_TAX_ID)
        assert check.severity is Severity.ERROR
        assert "receptor" in check.message
        assert "se esperaba 'B' y se encontró 'C'" in check.message
        assert report.status is AuditStatus.DISCREPANCY

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_missing_buyer_is_only_a_warning(self, db_session, auditor, value):
        invoice = make_invoice(db_session, buyer_tax_id=value)
        report = auditor.audit(invoice)
        [check] = failed(report, BUYER_TAX_ID)
        assert check.severity is Severity.WARNING
        assert report.status is AuditStatus.VALIDATED
        assert report.warning_count == 1
        assert report.error_count == 0

    def test_individual_buyer_is_valid(self, db_session, auditor):
        invoice = make_invoice(db_session, buyer_tax_id="12345678Z")
        report = auditor.audit(invoice)
        assert not failed(report, BUYER_TAX_ID)


class TestDates:
    def test_due_date_before_issue_date_is_an_error(self, db_session, auditor):
        invoice = make_invoice(db_session, due_date=date(2024, 3, 1))
        report = auditor.audit(invoice)
        [check] = failed(report, DATES)
        assert check.severity is Severity.ERROR
        assert check.field == "due_date"
        assert "01/03/2024" in check.message and "15/03/2024" in check.message
        assert "14 día(s)" in check.message
        assert report.status is AuditStatus.DISCREPANCY

    def test_due_date_equal_to_issue_date_is_valid(self, db_session, auditor):
        invoice = make_invoice(db_session, due_date=date(2024, 3, 15))
        assert not failed(auditor.audit(invoice), DATES)

    def test_missing_due_date_is_valid(self, db_session, auditor):
        invoice = make_invoice(db_session, due_date=None)
        report = auditor.audit(invoice)
        assert not failed(report, DATES)
        assert {c.field for c in checks_for(report, DATES)} == {"issue_date"}

    def test_future_issue_date_is_an_error(self, db_session, auditor):
        invoice = make_invoice(db_session, issue_date=TODAY + timedelta(days=1), due_date=None)
        report = auditor.audit(invoice)
        [check] = failed(report, DATES)
        assert check.severity is Severity.ERROR
        assert check.field == "issue_date"
        assert "posterior a la fecha actual" in check.message

    def test_issue_date_today_is_valid(self, db_session, auditor):
        invoice = make_invoice(db_session, issue_date=TODAY, due_date=None)
        assert not failed(auditor.audit(invoice), DATES)

    def test_operation_date_after_issue_date_is_a_warning(self, db_session, auditor):
        invoice = make_invoice(db_session, operation_date=date(2024, 3, 20))
        report = auditor.audit(invoice)
        [check] = failed(report, DATES)
        assert check.severity is Severity.WARNING
        assert check.field == "operation_date"
        assert "5 día(s)" in check.message
        assert report.status is AuditStatus.VALIDATED

    def test_operation_date_before_issue_date_is_valid(self, db_session, auditor):
        invoice = make_invoice(db_session, operation_date=date(2024, 3, 1))
        assert not failed(auditor.audit(invoice), DATES)

    def test_today_uses_spanish_calendar_date_without_tzdata(self, monkeypatch):
        monkeypatch.undo()
        assert isinstance(auditor_module._today(), date)


class TestLines:
    def test_no_lines_is_an_error(self, db_session, auditor):
        invoice = make_invoice(db_session, lines=[])
        report = auditor.audit(invoice)
        [check] = failed(report, HAS_LINES)
        assert check.severity is Severity.ERROR
        assert report.status is AuditStatus.DISCREPANCY
        assert not failed(report, TAX_BASE_SUM)

    def test_line_base_mismatch_message(self, db_session, auditor):
        specs = [line(1, "100.00"), line(3, "50.00", line_base="155.00", vat_amount="32.55")]
        invoice = make_invoice(db_session, specs)
        report = auditor.audit(invoice)

        [check] = failed(report, LINE_BASE)
        assert check.line_number == 2
        assert check.severity is Severity.ERROR
        assert check.expected_value == "150.00"
        assert check.actual_value == "155.00"
        assert check.message == (
            "Línea 2: la base imponible debería ser 150.00 € (3 × 50.00 €) y figura 155.00 € (diferencia 5.00 €)"
        )
        assert report.status is AuditStatus.DISCREPANCY

    def test_line_base_with_discount(self, db_session, auditor):
        ok = make_invoice(db_session, [line(10, "9.99", discount=10)])
        assert ok.lines[0].line_base == Decimal("89.91")
        assert not failed(auditor.audit(ok), LINE_BASE)

        wrong = make_invoice(db_session, [line(10, "9.99", discount=10, line_base="99.90", vat_amount="20.98")])
        [check] = failed(auditor.audit(wrong), LINE_BASE)
        assert check.message == (
            "Línea 1: la base imponible debería ser 89.91 € (10 × 9.99 € - 10% de descuento) "
            "y figura 99.90 € (diferencia 9.99 €)"
        )

    def test_line_base_ignores_discount_when_absent(self, db_session, auditor):
        invoice = make_invoice(db_session, [line(2, "10.00", line_base="21.00", vat_amount="4.41")])
        [check] = failed(auditor.audit(invoice), LINE_BASE)
        assert "(2 × 10.00 €)" in check.message and "descuento" not in check.message

    @pytest.mark.parametrize(
        ("quantity", "price", "expected"),
        [
            ("3", "0.835", "2.51"),  # 2.505 rounds half up (banker's rounding would give 2.50)
            ("0.5", "0.01", "0.01"),  # 0.005 -> 0.01
            ("2.5", "19.99", "49.98"),  # 49.975 -> 49.98
            ("1", "0.004", "0.00"),  # below the half cent
            ("1", "33.3349", "33.33"),
            ("1", "33.335", "33.34"),
        ],
    )
    def test_line_base_rounds_half_up(self, db_session, auditor, quantity, price, expected):
        spec = line(quantity, price, vat=0)
        assert spec["line_base"] == Decimal(expected)
        invoice = make_invoice(db_session, [spec])
        assert not failed(auditor.audit(invoice), LINE_BASE)

    def test_line_base_half_cent_rejects_banker_rounding(self, db_session, auditor):
        invoice = make_invoice(db_session, [line(3, "0.835", vat=0, line_base="2.50")])
        [check] = failed(auditor.audit(invoice), LINE_BASE)
        assert check.expected_value == "2.51"
        assert check.actual_value == "2.50"

    def test_one_cent_difference_is_reported(self, db_session, auditor):
        invoice = make_invoice(db_session, [line(1, "10.00", vat=0, line_base="10.01")])
        [check] = failed(auditor.audit(invoice), LINE_BASE)
        assert "diferencia 0.01 €" in check.message

    def test_tolerance_setting_widens_the_comparison(self, db_session):
        invoice = make_invoice(db_session, [line(1, "10.00", vat=0, line_base="10.01")])
        strict = InvoiceAuditor(db_session, Settings(audit_tolerance=Decimal("0.01")))
        lenient = InvoiceAuditor(db_session, Settings(audit_tolerance=Decimal("0.02")))
        assert failed(strict.audit(invoice), LINE_BASE)
        assert not failed(lenient.audit(invoice), LINE_BASE)

    @pytest.mark.parametrize("rate", [21, 10, 4])
    def test_standard_vat_rates_are_valid(self, db_session, auditor, rate):
        invoice = make_invoice(db_session, [line(1, "100.00", vat=rate)])
        report = auditor.audit(invoice)
        assert not failed(report, VAT_RATE)
        assert report.status is AuditStatus.VALIDATED

    @pytest.mark.parametrize("rate", ["19", "7", "5", "22", "21.5"])
    def test_unknown_vat_rate_is_an_error(self, db_session, auditor, rate):
        invoice = make_invoice(db_session, [line(1, "100.00", vat=rate)])
        report = auditor.audit(invoice)
        [check] = failed(report, VAT_RATE)
        assert check.severity is Severity.ERROR
        assert check.line_number == 1
        assert check.actual_value == rate
        assert "21%, 10%, 4%, 0%" in check.message
        assert report.status is AuditStatus.DISCREPANCY

    def test_zero_vat_is_a_warning_about_exemption(self, db_session, auditor):
        invoice = make_invoice(db_session, [line(1, "100.00", vat=0), line(1, "50.00")])
        report = auditor.audit(invoice)
        [check] = failed(report, VAT_RATE)
        assert check.severity is Severity.WARNING
        assert check.line_number == 1
        assert "exenta o no sujeta" in check.message
        assert report.status is AuditStatus.VALIDATED
        assert report.warning_count == 1

    def test_zero_vat_is_an_error_when_not_allowed(self, db_session):
        settings = Settings(allowed_vat_rates=[Decimal(21), Decimal(10), Decimal(4)])
        invoice = make_invoice(db_session, [line(1, "100.00", vat=0)])
        report = InvoiceAuditor(db_session, settings).audit(invoice)
        [check] = failed(report, VAT_RATE)
        assert check.severity is Severity.ERROR

    def test_line_vat_mismatch_message(self, db_session, auditor):
        invoice = make_invoice(db_session, [line(1, "200.00", vat_amount="40.00")])
        report = auditor.audit(invoice)
        [check] = failed(report, LINE_VAT)
        assert check.line_number == 1
        assert check.expected_value == "42.00"
        assert check.actual_value == "40.00"
        assert check.message == (
            "Línea 1: la cuota de IVA debería ser 42.00 € (200.00 € × 21%) y figura 40.00 € (diferencia 2.00 €)"
        )

    def test_line_vat_uses_declared_base(self, db_session, auditor):
        invoice = make_invoice(db_session, [line(2, "50.00", line_base="90.00", vat_amount="18.90")])
        report = auditor.audit(invoice)
        assert failed(report, LINE_BASE)
        assert not failed(report, LINE_VAT)

    @pytest.mark.parametrize(
        ("base", "rate", "expected"),
        [
            ("0.50", 21, "0.11"),  # 0.105 rounds half up
            ("0.05", 10, "0.01"),  # 0.005
            ("10.50", 10, "1.05"),
            ("33.33", 21, "7.00"),  # 6.9993
            ("1.19", 21, "0.25"),  # 0.2499
        ],
    )
    def test_line_vat_rounds_half_up(self, db_session, auditor, base, rate, expected):
        spec = line(1, base, vat=rate)
        assert spec["vat_amount"] == Decimal(expected)
        assert not failed(auditor.audit(make_invoice(db_session, [spec])), LINE_VAT)

    def test_line_vat_half_cent_rejects_banker_rounding(self, db_session, auditor):
        invoice = make_invoice(db_session, [line(1, "0.50", vat_amount="0.10")])
        [check] = failed(auditor.audit(invoice), LINE_VAT)
        assert check.expected_value == "0.11"

    @pytest.mark.parametrize("rate", [0, 1, 2, 7, 15, 19])
    def test_allowed_irpf_rates(self, db_session, auditor, rate):
        invoice = make_invoice(db_session, [line(1, "1000.00", irpf=rate)])
        report = auditor.audit(invoice)
        assert not failed(report, IRPF_RATE)
        assert not failed(report, LINE_IRPF)
        assert report.status is AuditStatus.VALIDATED

    def test_unknown_irpf_rate_is_an_error(self, db_session, auditor):
        invoice = make_invoice(db_session, [line(1, "1000.00", irpf=5)])
        report = auditor.audit(invoice)
        [check] = failed(report, IRPF_RATE)
        assert check.severity is Severity.ERROR
        assert check.line_number == 1
        assert check.actual_value == "5"

    def test_line_irpf_mismatch_message(self, db_session, auditor):
        invoice = make_invoice(db_session, [line(1, "1000.00", irpf=15, irpf_amount="140.00")])
        [check] = failed(auditor.audit(invoice), LINE_IRPF)
        assert check.message == (
            "Línea 1: la retención de IRPF debería ser 150.00 € (1000.00 € × 15%) y figura 140.00 € "
            "(diferencia 10.00 €)"
        )

    def test_missing_irpf_amount_with_rate_is_an_error(self, db_session, auditor):
        invoice = make_invoice(db_session, [line(1, "1000.00", irpf=15, irpf_amount="0.00")])
        assert failed(auditor.audit(invoice), LINE_IRPF)

    def test_errors_are_reported_for_every_offending_line(self, db_session, auditor):
        specs = [
            line(1, "10.00", line_base="11.00", vat_amount="2.31"),
            line(1, "20.00"),
            line(1, "30.00", line_base="31.00", vat_amount="6.51"),
        ]
        report = auditor.audit(make_invoice(db_session, specs))
        assert [c.line_number for c in failed(report, LINE_BASE)] == [1, 3]

    def test_professional_invoice_with_irpf(self, db_session, auditor):
        invoice = make_invoice(db_session, [line(1, "1000.00", irpf=15), line(2, "250.00", irpf=15)])
        assert invoice.total_irpf == Decimal("225.00")
        assert invoice.total_amount == Decimal("1500.00") + Decimal("315.00") - Decimal("225.00")
        report = auditor.audit(invoice)
        assert report.status is AuditStatus.VALIDATED
        assert report.warning_count == 0


class TestTotals:
    def test_tax_base_mismatch(self, db_session, auditor):
        invoice = make_invoice(db_session, tax_base=Decimal("310.00"), total_amount=Decimal("373.00"))
        report = auditor.audit(invoice)
        [check] = failed(report, TAX_BASE_SUM)
        assert check.expected_value == "300.00"
        assert check.actual_value == "310.00"
        assert check.message == (
            "La base imponible debería ser 300.00 € (suma de las bases de las líneas) "
            "y figura 310.00 € (diferencia 10.00 €)"
        )
        assert not failed(report, GRAND_TOTAL)

    def test_tax_base_one_cent_off(self, db_session, auditor):
        invoice = make_invoice(db_session, tax_base=Decimal("300.01"), total_amount=Decimal("363.01"))
        assert failed(auditor.audit(invoice), TAX_BASE_SUM)

    def test_vat_total_mismatch(self, db_session, auditor):
        invoice = make_invoice(db_session, total_vat=Decimal("60.00"), total_amount=Decimal("360.00"))
        report = auditor.audit(invoice)
        [check] = failed(report, VAT_TOTAL)
        assert check.expected_value == "63.00"
        assert check.actual_value == "60.00"
        assert check.message == "El IVA total debería ser 63.00 € y figura 60.00 € (diferencia 3.00 €)"
        assert not failed(report, GRAND_TOTAL)

    def test_irpf_total_mismatch(self, db_session, auditor):
        invoice = make_invoice(
            db_session,
            [line(1, "1000.00", irpf=15)],
            total_irpf=Decimal("100.00"),
            total_amount=Decimal("1110.00"),
        )
        report = auditor.audit(invoice)
        [check] = failed(report, IRPF_TOTAL)
        assert check.expected_value == "150.00"
        assert "retención de IRPF total" in check.message
        assert not failed(report, GRAND_TOTAL)

    def test_grand_total_mismatch_message(self, db_session, auditor):
        invoice = make_invoice(db_session, total_amount=Decimal("1000.00"))
        report = auditor.audit(invoice)
        [check] = failed(report, GRAND_TOTAL)
        assert check.expected_value == "363.00"
        assert check.actual_value == "1000.00"
        assert check.message == (
            "El importe total debería ser 363.00 € (300.00 € + 63.00 € de IVA - 0.00 € de IRPF) "
            "y figura 1000.00 € (diferencia 637.00 €)"
        )
        assert report.status is AuditStatus.DISCREPANCY

    def test_grand_total_subtracts_irpf(self, db_session, auditor):
        invoice = make_invoice(db_session, [line(1, "1000.00", irpf=15)])
        assert invoice.total_amount == Decimal("1060.00")
        assert not failed(auditor.audit(invoice), GRAND_TOTAL)

        wrong = make_invoice(db_session, [line(1, "1000.00", irpf=15)], total_amount=Decimal("1360.00"))
        [check] = failed(auditor.audit(wrong), GRAND_TOTAL)
        assert check.expected_value == "1060.00"

    def test_grand_total_one_cent_off(self, db_session, auditor):
        invoice = make_invoice(db_session, total_amount=Decimal("363.01"))
        [check] = failed(auditor.audit(invoice), GRAND_TOTAL)
        assert "diferencia 0.01 €" in check.message

    def test_mixed_vat_rates_exact_totals(self, db_session, auditor):
        specs = [line(1, "100.00", vat=21), line(2, "50.00", vat=10), line(3, "25.00", vat=4)]
        invoice = make_invoice(db_session, specs)
        assert invoice.total_vat == Decimal("21.00") + Decimal("10.00") + Decimal("3.00")
        report = auditor.audit(invoice)
        assert report.status is AuditStatus.VALIDATED
        assert report.warning_count == 0


class TestRoundingStrategies:
    """Per-rate-group and per-line VAT rounding may differ by a cent; both are valid on a Spanish invoice."""

    @staticmethod
    def mixed_specs():
        # 21%: two lines of 0.50 -> 0.11 each (0.22 per line) but 1.00 * 21% = 0.21 per group.
        return [line(1, "0.50"), line(1, "0.50"), line(1, "100.00", vat=10), line(1, "25.00", vat=4)]

    def test_fixture_data_really_differs(self):
        specs = self.mixed_specs()
        per_line = sum(s["vat_amount"] for s in specs)
        per_group = q(Decimal("1.00") * 21 / 100) + q(Decimal("100.00") * 10 / 100) + q(Decimal("25.00") * 4 / 100)
        assert (per_line, per_group) == (Decimal("11.22"), Decimal("11.21"))

    def test_per_line_rounding_is_accepted(self, db_session, auditor):
        invoice = make_invoice(db_session, self.mixed_specs())
        assert invoice.total_vat == Decimal("11.22")
        report = auditor.audit(invoice)
        assert not failed(report, VAT_TOTAL)
        assert report.status is AuditStatus.VALIDATED

    def test_per_group_rounding_is_accepted(self, db_session, auditor):
        invoice = make_invoice(
            db_session,
            self.mixed_specs(),
            total_vat=Decimal("11.21"),
            total_amount=Decimal("126.00") + Decimal("11.21"),
        )
        report = auditor.audit(invoice)
        assert not failed(report, VAT_TOTAL)
        assert not failed(report, GRAND_TOTAL)
        assert report.status is AuditStatus.VALIDATED

    def test_neither_strategy_is_rejected_and_message_lists_both(self, db_session, auditor):
        invoice = make_invoice(
            db_session,
            self.mixed_specs(),
            total_vat=Decimal("11.23"),
            total_amount=Decimal("126.00") + Decimal("11.23"),
        )
        report = auditor.audit(invoice)
        [check] = failed(report, VAT_TOTAL)
        assert "11.21 € (redondeo por tipo impositivo)" in check.message
        assert "11.22 € (suma de las líneas)" in check.message
        assert "figura 11.23 €" in check.message
        assert report.status is AuditStatus.DISCREPANCY

    def test_irpf_total_accepts_both_strategies(self, db_session, auditor):
        # 7%: two lines of 0.50 -> 0.04 each (0.08 per line) but 1.00 * 7% = 0.07 per group.
        specs = [line(1, "0.50", irpf=7), line(1, "0.50", irpf=7)]
        assert sum(s["irpf_amount"] for s in specs) == Decimal("0.08")

        per_line = make_invoice(db_session, specs)
        assert not failed(auditor.audit(per_line), IRPF_TOTAL)

        per_group = make_invoice(
            db_session,
            specs,
            total_irpf=Decimal("0.07"),
            total_amount=Decimal("1.00") + per_line.total_vat - Decimal("0.07"),
        )
        report = auditor.audit(per_group)
        assert not failed(report, IRPF_TOTAL)
        assert not failed(report, GRAND_TOTAL)

        wrong = make_invoice(
            db_session,
            specs,
            total_irpf=Decimal("0.09"),
            total_amount=Decimal("1.00") + per_line.total_vat - Decimal("0.09"),
        )
        assert failed(auditor.audit(wrong), IRPF_TOTAL)

    def test_vat_total_is_computed_per_rate_not_overall(self, db_session, auditor):
        # Overall base * a blended rate must not be accepted: 21% on 100 and 10% on 100 -> 31.00.
        invoice = make_invoice(
            db_session,
            [line(1, "100.00", vat=21), line(1, "100.00", vat=10)],
            total_vat=Decimal("30.00"),
            total_amount=Decimal("230.00"),
        )
        assert failed(auditor.audit(invoice), VAT_TOTAL)


class TestDuplicates:
    def test_same_supplier_and_number_is_duplicate(self, db_session, auditor):
        first = make_invoice(db_session, invoice_number="2024-001")
        assert auditor.audit(first).status is AuditStatus.VALIDATED

        second = make_invoice(db_session, invoice_number="2024-001")
        report = auditor.audit(second)

        assert report.status is AuditStatus.DUPLICATE
        assert report.duplicate_of == [first.id]
        [check] = failed(report, DUPLICATE)
        assert check.severity is Severity.ERROR
        assert f"#{first.id}" in check.message
        assert "2024-001" in check.message
        assert second.audit_status is AuditStatus.DUPLICATE
        assert second.error_count == 1

    def test_original_stays_valid_when_a_later_copy_exists(self, db_session, auditor):
        first = make_invoice(db_session, invoice_number="A-1")
        second = make_invoice(db_session, invoice_number="A-1")
        report_first = auditor.audit(first)
        assert report_first.status is AuditStatus.VALIDATED
        assert report_first.duplicate_of == []
        report_second = auditor.audit(second)
        assert report_second.status is AuditStatus.DUPLICATE
        assert report_second.duplicate_of == [first.id]

    def test_series_and_number_are_normalized(self, db_session, auditor):
        first = make_invoice(db_session, invoice_series="F", invoice_number="2024/001")
        second = make_invoice(db_session, invoice_series=None, invoice_number="f-2024-001")
        report = auditor.audit(second)
        assert report.status is AuditStatus.DUPLICATE
        assert report.duplicate_of == [first.id]

    def test_different_number_is_not_duplicate(self, db_session, auditor):
        make_invoice(db_session, invoice_number="2024-001")
        second = make_invoice(db_session, invoice_number="2024-002")
        report = auditor.audit(second)
        assert report.status is AuditStatus.VALIDATED
        assert report.duplicate_of == []

    def test_different_series_is_not_duplicate(self, db_session, auditor):
        make_invoice(db_session, invoice_series="A", invoice_number="1")
        second = make_invoice(db_session, invoice_series="B", invoice_number="1")
        assert auditor.audit(second).status is AuditStatus.VALIDATED

    def test_same_number_from_another_supplier_is_not_duplicate(self, db_session, auditor):
        make_invoice(db_session, invoice_number="2024-001")
        other = make_invoice(db_session, supplier_tax_id="A58818501", invoice_number="2024-001")
        report = auditor.audit(other)
        assert report.status is AuditStatus.VALIDATED
        assert report.duplicate_of == []

    def test_same_tax_id_with_different_formatting_is_duplicate(self, db_session, auditor):
        first = make_invoice(db_session, invoice_number="X-9")
        other_supplier = get_supplier(db_session, "ESB-65410011")
        second = make_invoice(db_session, invoice_number="X-9", supplier=other_supplier, supplier_tax_id="ES B65410011")
        report = auditor.audit(second)
        assert report.status is AuditStatus.DUPLICATE
        assert report.duplicate_of == [first.id]

    def test_all_duplicates_are_listed(self, db_session, auditor):
        ids = [make_invoice(db_session, invoice_number="DUP").id for _ in range(3)]
        report = auditor.audit(make_invoice(db_session, invoice_number="DUP"))
        assert report.duplicate_of == ids
        [check] = failed(report, DUPLICATE)
        assert all(f"#{i}" in check.message for i in ids)

    def test_same_file_hash_is_duplicate(self, db_session, auditor):
        digest = "a" * 64
        first = make_invoice(db_session, invoice_number="H-1", source_file_hash=digest)
        second = make_invoice(db_session, invoice_number="H-2", source_file_hash=digest)
        report = auditor.audit(second)

        assert report.status is AuditStatus.DUPLICATE
        assert report.duplicate_of == [first.id]
        [check] = failed(report, DUPLICATE)
        assert check.field == "source_file_hash"
        assert f"#{first.id}" in check.message
        assert any(c.passed and c.field == "invoice_number" for c in checks_for(report, DUPLICATE))

    def test_same_file_hash_across_suppliers_is_duplicate(self, db_session, auditor):
        digest = "b" * 64
        first = make_invoice(db_session, supplier_tax_id="A58818501", source_file_hash=digest)
        second = make_invoice(db_session, source_file_hash=digest)
        assert auditor.audit(second).duplicate_of == [first.id]

    def test_different_hash_is_not_duplicate(self, db_session, auditor):
        make_invoice(db_session, source_file_hash="a" * 64)
        report = auditor.audit(make_invoice(db_session, source_file_hash="c" * 64))
        assert report.status is AuditStatus.VALIDATED
        assert all(c.passed for c in checks_for(report, DUPLICATE))

    def test_missing_hash_never_matches(self, db_session, auditor):
        make_invoice(db_session, source_file_hash=None)
        report = auditor.audit(make_invoice(db_session, source_file_hash=None))
        assert report.status is AuditStatus.VALIDATED
        assert {c.field for c in checks_for(report, DUPLICATE)} == {"invoice_number"}

    def test_number_and_hash_duplicates_are_merged(self, db_session, auditor):
        by_number = make_invoice(db_session, invoice_number="N-1")
        by_hash = make_invoice(db_session, invoice_number="N-2", source_file_hash="d" * 64)
        target = make_invoice(db_session, invoice_number="N-1", source_file_hash="d" * 64)
        report = auditor.audit(target)
        assert report.duplicate_of == sorted([by_number.id, by_hash.id])
        assert len(failed(report, DUPLICATE)) == 2
        assert report.error_count == 2

    def test_invoice_is_not_a_duplicate_of_itself(self, db_session, auditor):
        invoice = make_invoice(db_session, source_file_hash="e" * 64)
        report = auditor.audit(invoice)
        assert report.duplicate_of == []
        assert report.status is AuditStatus.VALIDATED


class TestCurrencyAndDir3:
    @pytest.mark.parametrize("currency", ["USD", "GBP", "eur "])
    def test_currency_other_than_eur(self, db_session, auditor, currency):
        invoice = make_invoice(db_session, currency=currency)
        report = auditor.audit(invoice)
        if currency.strip().upper() == "EUR":
            assert not failed(report, CURRENCY)
            return
        [check] = failed(report, CURRENCY)
        assert check.severity is Severity.WARNING
        assert check.expected_value == "EUR"
        assert report.status is AuditStatus.VALIDATED

    @pytest.mark.parametrize(
        ("field", "label"),
        [
            ("dir3_accounting_office", "oficina contable"),
            ("dir3_managing_body", "órgano gestor"),
            ("dir3_processing_unit", "unidad tramitadora"),
        ],
    )
    def test_missing_single_dir3_code(self, db_session, auditor, field, label):
        invoice = make_invoice(db_session, **{field: None})
        report = auditor.audit(invoice)
        [check] = failed(report, FACE_DIR3)
        assert check.severity is Severity.WARNING
        assert label in check.message
        assert "no se podrá enviar a FACe sin códigos DIR3" in check.message
        assert report.status is AuditStatus.VALIDATED
        assert report.warning_count == 1

    def test_all_dir3_codes_missing(self, db_session, auditor):
        invoice = make_invoice(
            db_session, dir3_accounting_office="", dir3_managing_body="  ", dir3_processing_unit=None
        )
        [check] = failed(auditor.audit(invoice), FACE_DIR3)
        for label in ("oficina contable", "órgano gestor", "unidad tramitadora"):
            assert label in check.message


class TestStatusPriority:
    def test_warnings_only_is_validated(self, db_session, auditor):
        invoice = make_invoice(db_session, currency="USD", buyer_tax_id=None, dir3_managing_body=None)
        report = auditor.audit(invoice)
        assert report.status is AuditStatus.VALIDATED
        assert report.error_count == 0
        assert report.warning_count == 3
        assert report.summary.startswith("Factura validada con 3 advertencia(s)")

    def test_error_is_discrepancy(self, db_session, auditor):
        report = auditor.audit(make_invoice(db_session, total_amount=Decimal("1.00")))
        assert report.status is AuditStatus.DISCREPANCY
        assert report.summary.startswith("Se han detectado discrepancias")

    def test_duplicate_outranks_discrepancy(self, db_session, auditor):
        make_invoice(db_session, invoice_number="P-1")
        invoice = make_invoice(db_session, invoice_number="P-1", total_amount=Decimal("1.00"))
        report = auditor.audit(invoice)
        assert failed(report, GRAND_TOTAL)
        assert failed(report, DUPLICATE)
        assert report.status is AuditStatus.DUPLICATE
        assert report.error_count == 2
        assert report.summary.startswith("Posible factura duplicada")

    def test_discrepancy_outranks_validated(self, db_session, auditor):
        invoice = make_invoice(db_session, currency="USD", total_amount=Decimal("1.00"))
        report = auditor.audit(invoice)
        assert report.status is AuditStatus.DISCREPANCY
        assert report.warning_count == 1
        assert report.error_count == 1

    def test_duplicate_warning_does_not_apply(self, db_session, auditor):
        # Only DUPLICATE errors lead to the DUPLICATE status.
        make_invoice(db_session, invoice_number="Q-1")
        invoice = make_invoice(db_session, invoice_number="Q-2", total_amount=Decimal("1.00"))
        assert auditor.audit(invoice).status is AuditStatus.DISCREPANCY

    def test_summary_lists_every_failed_check(self, db_session, auditor):
        invoice = make_invoice(
            db_session,
            supplier_tax_id="B65410012",
            total_amount=Decimal("1.00"),
            currency="USD",
        )
        report = auditor.audit(invoice)
        failed_checks = [c for c in report.checks if not c.passed]
        assert len(failed_checks) == 3
        assert all(c.message in report.summary for c in failed_checks)
        assert "2 error(es) y 1 advertencia(s)" in report.summary

    def test_counts_match_persisted_logs(self, db_session, auditor):
        invoice = make_invoice(db_session, currency="USD", total_amount=Decimal("1.00"))
        report = auditor.audit(invoice)
        logs = [log for log in invoice.validation_logs if log.run_id == report.run_id]
        assert sum(1 for log in logs if not log.passed and log.severity is Severity.ERROR) == invoice.error_count
        assert sum(1 for log in logs if not log.passed and log.severity is Severity.WARNING) == invoice.warning_count


class TestReaudit:
    def test_rerun_creates_new_run_and_resets_counts(self, db_session, auditor):
        invoice = make_invoice(db_session, total_amount=Decimal("1.00"), currency="USD")
        first = auditor.audit(invoice)
        assert first.status is AuditStatus.DISCREPANCY
        assert (invoice.error_count, invoice.warning_count) == (1, 1)

        invoice.total_amount = Decimal("363.00")
        invoice.currency = "EUR"
        second = auditor.audit(invoice)

        assert second.run_id != first.run_id
        assert second.status is AuditStatus.VALIDATED
        assert (second.error_count, second.warning_count) == (0, 0)
        assert (invoice.error_count, invoice.warning_count) == (0, 0)
        assert invoice.audit_status is AuditStatus.VALIDATED
        assert invoice.last_audit_run_id == second.run_id
        assert invoice.last_audited_at >= first.audited_at

    def test_rerun_keeps_history_and_current_run_is_filterable(self, db_session, auditor):
        invoice = make_invoice(db_session)
        first = auditor.audit(invoice)
        second = auditor.audit(invoice)

        total = db_session.scalar(select(func.count()).select_from(ValidationLog))
        assert total == len(first.checks) + len(second.checks)
        current = [log for log in invoice.validation_logs if log.run_id == invoice.last_audit_run_id]
        assert len(current) == len(second.checks)
        assert {log.run_id for log in invoice.validation_logs} == {first.run_id, second.run_id}

    def test_rerun_after_duplicate_resolved(self, db_session, auditor):
        original = make_invoice(db_session, invoice_number="R-1")
        copy = make_invoice(db_session, invoice_number="R-1")
        assert auditor.audit(copy).status is AuditStatus.DUPLICATE

        db_session.delete(original)
        db_session.flush()
        report = auditor.audit(copy)
        assert report.status is AuditStatus.VALIDATED
        assert report.duplicate_of == []
        assert copy.error_count == 0

    def test_run_ids_are_unique_uuids(self, db_session, auditor):
        invoice = make_invoice(db_session)
        run_ids = {auditor.audit(invoice).run_id for _ in range(3)}
        assert len(run_ids) == 3
        assert all(len(run_id) == 36 for run_id in run_ids)


class TestFacePartyData:
    def test_complete_parties_pass(self, db_session, auditor):
        report = auditor.audit(make_invoice(db_session))
        [check] = [c for c in report.checks if c.rule_code == FACE_PARTY_DATA]
        assert check.passed

    def test_missing_buyer_address_is_a_warning(self, db_session, auditor):
        invoice = make_invoice(db_session, buyer_address=None, buyer_postal_code="8002")
        report = auditor.audit(invoice)
        [check] = failed(report, FACE_PARTY_DATA)
        assert check.severity is Severity.WARNING
        assert "dirección" in check.message
        assert "'8002' no tiene 5 dígitos" in check.message
        assert report.status is AuditStatus.VALIDATED
