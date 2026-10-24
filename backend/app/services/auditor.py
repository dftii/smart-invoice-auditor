"""Invoice auditing engine: arithmetic, tax, identity, date and duplicate checks with a persisted audit trail."""

import re
import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.money import HUNDRED, round_money, to_decimal
from app.models import AuditStatus, Invoice, InvoiceLine, Severity, ValidationLog
from app.schemas import AuditCheck, AuditReport
from app.services.tax_id import normalize_tax_id, validate_tax_id

SUPPLIER_TAX_ID = "SUPPLIER_TAX_ID"
BUYER_TAX_ID = "BUYER_TAX_ID"
DATES = "DATES"
HAS_LINES = "HAS_LINES"
LINE_BASE = "LINE_BASE"
VAT_RATE = "VAT_RATE"
LINE_VAT = "LINE_VAT"
IRPF_RATE = "IRPF_RATE"
LINE_IRPF = "LINE_IRPF"
TAX_BASE_SUM = "TAX_BASE_SUM"
VAT_TOTAL = "VAT_TOTAL"
IRPF_TOTAL = "IRPF_TOTAL"
GRAND_TOTAL = "GRAND_TOTAL"
DUPLICATE = "DUPLICATE"
CURRENCY = "CURRENCY"
FACE_DIR3 = "FACE_DIR3"
FACE_PARTY_DATA = "FACE_PARTY_DATA"
SYNTHETIC_DATA = "SYNTHETIC_DATA"

FACE_CURRENCY = "EUR"
DIR3_FIELDS = (
    ("dir3_accounting_office", "oficina contable"),
    ("dir3_managing_body", "órgano gestor"),
    ("dir3_processing_unit", "unidad tramitadora"),
)
# Facturae requires a full postal address in Spain for both parties.
PARTY_FIELDS = (
    ("address", "dirección"),
    ("postal_code", "código postal"),
    ("city", "población"),
    ("province", "provincia"),
)
_SPANISH_POSTAL_CODE = re.compile(r"^\d{5}$")
_NON_ALNUM = re.compile(r"[^0-9A-Za-z]")


_dec = to_decimal
_round = round_money


def _eur(value: Decimal) -> str:
    return f"{_round(value):.2f} €"


def _num(value: Decimal, min_decimals: int = 0) -> str:
    """Plain decimal text without trailing zeros, padded to at least `min_decimals` places."""
    text = format(value.normalize(), "f")
    decimals = len(text.split(".")[1]) if "." in text else 0
    if decimals < min_decimals:
        text = f"{value:.{min_decimals}f}"
    return text


def _today() -> date:
    """Current date in Spain (UTC when the tz database is unavailable)."""
    try:
        return datetime.now(ZoneInfo("Europe/Madrid")).date()
    except ZoneInfoNotFoundError:
        return datetime.now(UTC).date()


def _normalize_number(series: str | None, number: str | None) -> str:
    return _NON_ALNUM.sub("", f"{series or ''}{number or ''}").upper()


def find_related_invoice_ids(
    db: Session,
    *,
    supplier_id: int | None,
    supplier_tax_id: str | None,
    series: str | None,
    number: str | None,
    file_hash: str | None,
    exclude_id: int | None = None,
) -> list[int]:
    """Ids of the other invoices that share the (supplier tax id, series+number) key or the source file hash.

    Their audit result depends on this invoice (duplicate detection), so they must be re-audited when it
    is edited or deleted. An empty supplier tax id never matches by number.
    """
    tax_id = normalize_tax_id(supplier_tax_id or "")
    normalized_number = _normalize_number(series, number)
    conditions = []
    if tax_id:
        conditions.append(or_(Invoice.supplier_tax_id == tax_id, Invoice.supplier_tax_id == (supplier_tax_id or "")))
        if supplier_id is not None:
            conditions.append(Invoice.supplier_id == supplier_id)
    if file_hash:
        conditions.append(Invoice.source_file_hash == file_hash)
    if not conditions:
        return []
    statement = select(
        Invoice.id, Invoice.supplier_tax_id, Invoice.invoice_series, Invoice.invoice_number, Invoice.source_file_hash
    ).where(or_(*conditions))
    if exclude_id is not None:
        statement = statement.where(Invoice.id != exclude_id)
    related = []
    for row in db.execute(statement.order_by(Invoice.id)):
        same_hash = bool(file_hash) and row.source_file_hash == file_hash
        same_number = (
            bool(tax_id)
            and normalize_tax_id(row.supplier_tax_id) == tax_id
            and _normalize_number(row.invoice_series, row.invoice_number) == normalized_number
        )
        if same_hash or same_number:
            related.append(row.id)
    return related


def _ok(rule_code: str, message: str, *, field: str | None = None) -> AuditCheck:
    return AuditCheck(rule_code=rule_code, severity=Severity.INFO, passed=True, message=message, field=field)


def _fail(
    rule_code: str,
    severity: Severity,
    message: str,
    *,
    field: str | None = None,
    line_number: int | None = None,
    expected: str | None = None,
    actual: str | None = None,
) -> AuditCheck:
    return AuditCheck(
        rule_code=rule_code,
        severity=severity,
        passed=False,
        message=message,
        field=field,
        line_number=line_number,
        expected_value=expected,
        actual_value=actual,
    )


class InvoiceAuditor:
    """Runs every audit rule against an invoice and persists the result as a ValidationLog trail.

    The session is flushed but never committed: the caller owns the transaction.
    """

    def __init__(self, db: Session, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()

    def audit(self, invoice: Invoice) -> AuditReport:
        if invoice not in self.db:
            self.db.add(invoice)
        self.db.flush()

        checks: list[AuditCheck] = []
        checks += self._check_supplier(invoice)
        checks += self._check_buyer(invoice)
        checks += self._check_dates(invoice)
        lines = list(invoice.lines)
        checks += self._check_has_lines(lines)
        checks += self._check_lines(lines)
        checks += self._check_totals(invoice, lines)
        duplicate_checks, duplicate_ids = self._check_duplicates(invoice)
        checks += duplicate_checks
        checks += self._check_currency(invoice)
        checks += self._check_dir3(invoice)
        checks += self._check_party_data(invoice)
        checks += self._check_synthetic(invoice)

        errors = [c for c in checks if not c.passed and c.severity is Severity.ERROR]
        warnings = [c for c in checks if not c.passed and c.severity is Severity.WARNING]
        if any(c.rule_code == DUPLICATE for c in errors):
            status = AuditStatus.DUPLICATE
        elif errors:
            status = AuditStatus.DISCREPANCY
        else:
            status = AuditStatus.VALIDATED

        run_id = str(uuid.uuid4())
        audited_at = datetime.now(UTC)
        summary = self._build_summary(status, checks, errors, warnings)

        for check in checks:
            invoice.validation_logs.append(ValidationLog(run_id=run_id, **check.model_dump()))
        invoice.audit_status = status
        invoice.audit_summary = summary
        invoice.error_count = len(errors)
        invoice.warning_count = len(warnings)
        invoice.last_audited_at = audited_at
        invoice.last_audit_run_id = run_id
        self.db.flush()

        return AuditReport(
            invoice_id=invoice.id,
            run_id=run_id,
            status=status,
            error_count=len(errors),
            warning_count=len(warnings),
            summary=summary,
            checks=checks,
            duplicate_of=duplicate_ids,
            audited_at=audited_at,
        )

    # ------------------------------------------------------------------ identity

    def _check_supplier(self, invoice: Invoice) -> list[AuditCheck]:
        result = validate_tax_id(invoice.supplier_tax_id)
        supplier = invoice.supplier
        if supplier is not None:
            own = result if normalize_tax_id(supplier.tax_id) == result.normalized else validate_tax_id(supplier.tax_id)
            supplier.tax_id_type = own.tax_id_type
            supplier.tax_id_valid = own.is_valid
            supplier.person_type = own.person_type

        if result.is_valid:
            return [
                _ok(
                    SUPPLIER_TAX_ID,
                    f"El identificador fiscal del emisor es válido ({result.tax_id_type.value} {result.normalized})",
                    field="supplier_tax_id",
                )
            ]
        return [
            _fail(
                SUPPLIER_TAX_ID,
                Severity.ERROR,
                f"El identificador fiscal del emisor '{invoice.supplier_tax_id}' no es válido: {result.reason}",
                field="supplier_tax_id",
                actual=invoice.supplier_tax_id,
            )
        ]

    def _check_buyer(self, invoice: Invoice) -> list[AuditCheck]:
        raw = (invoice.buyer_tax_id or "").strip()
        if not raw:
            return [
                _fail(
                    BUYER_TAX_ID,
                    Severity.WARNING,
                    "No se ha indicado el identificador fiscal del receptor",
                    field="buyer_tax_id",
                )
            ]
        result = validate_tax_id(raw)
        if result.is_valid:
            return [
                _ok(
                    BUYER_TAX_ID,
                    f"El identificador fiscal del receptor es válido ({result.tax_id_type.value} {result.normalized})",
                    field="buyer_tax_id",
                )
            ]
        return [
            _fail(
                BUYER_TAX_ID,
                Severity.ERROR,
                f"El identificador fiscal del receptor '{raw}' no es válido: {result.reason}",
                field="buyer_tax_id",
                actual=raw,
            )
        ]

    # --------------------------------------------------------------------- dates

    def _check_dates(self, invoice: Invoice) -> list[AuditCheck]:
        checks: list[AuditCheck] = []
        issue, due, operation = invoice.issue_date, invoice.due_date, invoice.operation_date

        today = _today()
        if issue > today:
            checks.append(
                _fail(
                    DATES,
                    Severity.ERROR,
                    f"La fecha de expedición ({issue:%d/%m/%Y}) es posterior a la fecha actual ({today:%d/%m/%Y})",
                    field="issue_date",
                    expected=f"<= {today.isoformat()}",
                    actual=issue.isoformat(),
                )
            )
        else:
            checks.append(_ok(DATES, "La fecha de expedición no es futura", field="issue_date"))

        if due is not None:
            if due < issue:
                checks.append(
                    _fail(
                        DATES,
                        Severity.ERROR,
                        f"La fecha de vencimiento ({due:%d/%m/%Y}) es anterior a la fecha de expedición "
                        f"({issue:%d/%m/%Y}) en {(issue - due).days} día(s)",
                        field="due_date",
                        expected=f">= {issue.isoformat()}",
                        actual=due.isoformat(),
                    )
                )
            else:
                checks.append(
                    _ok(DATES, "La fecha de vencimiento es posterior o igual a la de expedición", field="due_date")
                )

        if operation is not None:
            if operation > issue:
                checks.append(
                    _fail(
                        DATES,
                        Severity.WARNING,
                        f"La fecha de la operación ({operation:%d/%m/%Y}) es posterior a la fecha de expedición "
                        f"({issue:%d/%m/%Y}) en {(operation - issue).days} día(s)",
                        field="operation_date",
                        expected=f"<= {issue.isoformat()}",
                        actual=operation.isoformat(),
                    )
                )
            else:
                checks.append(
                    _ok(DATES, "La fecha de la operación no es posterior a la de expedición", field="operation_date")
                )
        return checks

    # --------------------------------------------------------------------- lines

    @staticmethod
    def _check_has_lines(lines: list[InvoiceLine]) -> list[AuditCheck]:
        if lines:
            return [_ok(HAS_LINES, f"La factura contiene {len(lines)} línea(s)", field="lines")]
        return [_fail(HAS_LINES, Severity.ERROR, "La factura no contiene ninguna línea de detalle", field="lines")]

    def _check_lines(self, lines: list[InvoiceLine]) -> list[AuditCheck]:
        """Per-line rules. Failures are reported per line; a rule with no failure gets one aggregate pass entry."""
        if not lines:
            return []
        tolerance = self.settings.audit_tolerance
        allowed_vat = self.settings.allowed_vat_rates
        allowed_irpf = self.settings.allowed_irpf_rates
        by_rule: dict[str, list[AuditCheck]] = {
            code: [] for code in (LINE_BASE, VAT_RATE, LINE_VAT, IRPF_RATE, LINE_IRPF)
        }

        for line in lines:
            n = line.line_number
            quantity, price = _dec(line.quantity), _dec(line.unit_price)
            discount = _dec(line.discount_rate)
            declared_base = _dec(line.line_base)
            vat_rate, irpf_rate = _dec(line.vat_rate), _dec(line.irpf_rate)

            expected_base = _round(quantity * price * (HUNDRED - discount) / HUNDRED)
            if not _same(expected_base, declared_base, tolerance):
                detail = f"{_num(quantity)} × {_num(price, 2)} €"
                if discount:
                    detail += f" - {_num(discount)}% de descuento"
                by_rule[LINE_BASE].append(
                    _fail(
                        LINE_BASE,
                        Severity.ERROR,
                        f"Línea {n}: la base imponible debería ser {_eur(expected_base)} ({detail}) "
                        f"y figura {_eur(declared_base)} (diferencia {_eur(abs(expected_base - declared_base))})",
                        field="line_base",
                        line_number=n,
                        expected=f"{expected_base:.2f}",
                        actual=f"{_round(declared_base):.2f}",
                    )
                )

            if vat_rate not in allowed_vat:
                by_rule[VAT_RATE].append(
                    _fail(
                        VAT_RATE,
                        Severity.ERROR,
                        f"Línea {n}: el tipo de IVA {_num(vat_rate)}% no es válido; "
                        f"los tipos admitidos son {', '.join(_num(r) + '%' for r in allowed_vat)}",
                        field="vat_rate",
                        line_number=n,
                        expected=", ".join(_num(r) for r in allowed_vat),
                        actual=_num(vat_rate),
                    )
                )
            elif vat_rate == 0:
                by_rule[VAT_RATE].append(
                    _fail(
                        VAT_RATE,
                        Severity.WARNING,
                        f"Línea {n}: tipo de IVA 0%; la operación figura como exenta o no sujeta, "
                        "compruebe que consta la causa de exención",
                        field="vat_rate",
                        line_number=n,
                        actual="0",
                    )
                )

            expected_vat = _round(declared_base * vat_rate / HUNDRED)
            declared_vat = _dec(line.vat_amount)
            if not _same(expected_vat, declared_vat, tolerance):
                by_rule[LINE_VAT].append(
                    _fail(
                        LINE_VAT,
                        Severity.ERROR,
                        f"Línea {n}: la cuota de IVA debería ser {_eur(expected_vat)} "
                        f"({_eur(declared_base)} × {_num(vat_rate)}%) y figura {_eur(declared_vat)} "
                        f"(diferencia {_eur(abs(expected_vat - declared_vat))})",
                        field="vat_amount",
                        line_number=n,
                        expected=f"{expected_vat:.2f}",
                        actual=f"{_round(declared_vat):.2f}",
                    )
                )

            if irpf_rate not in allowed_irpf:
                by_rule[IRPF_RATE].append(
                    _fail(
                        IRPF_RATE,
                        Severity.ERROR,
                        f"Línea {n}: el tipo de retención IRPF {_num(irpf_rate)}% no es válido; "
                        f"los tipos admitidos son {', '.join(_num(r) + '%' for r in allowed_irpf)}",
                        field="irpf_rate",
                        line_number=n,
                        expected=", ".join(_num(r) for r in allowed_irpf),
                        actual=_num(irpf_rate),
                    )
                )

            expected_irpf = _round(declared_base * irpf_rate / HUNDRED)
            declared_irpf = _dec(line.irpf_amount)
            if not _same(expected_irpf, declared_irpf, tolerance):
                by_rule[LINE_IRPF].append(
                    _fail(
                        LINE_IRPF,
                        Severity.ERROR,
                        f"Línea {n}: la retención de IRPF debería ser {_eur(expected_irpf)} "
                        f"({_eur(declared_base)} × {_num(irpf_rate)}%) y figura {_eur(declared_irpf)} "
                        f"(diferencia {_eur(abs(expected_irpf - declared_irpf))})",
                        field="irpf_amount",
                        line_number=n,
                        expected=f"{expected_irpf:.2f}",
                        actual=f"{_round(declared_irpf):.2f}",
                    )
                )

        passed_messages = {
            LINE_BASE: (
                "line_base",
                "La base imponible de todas las líneas coincide con cantidad × precio - descuento",
            ),
            VAT_RATE: ("vat_rate", "Los tipos de IVA de todas las líneas son válidos"),
            LINE_VAT: ("vat_amount", "La cuota de IVA de todas las líneas es correcta"),
            IRPF_RATE: ("irpf_rate", "Los tipos de retención IRPF de todas las líneas son válidos"),
            LINE_IRPF: ("irpf_amount", "La retención de IRPF de todas las líneas es correcta"),
        }
        checks: list[AuditCheck] = []
        for code, (field, message) in passed_messages.items():
            results = by_rule[code]
            checks += results if results else [_ok(code, message, field=field)]
        return checks

    # -------------------------------------------------------------------- totals

    def _check_totals(self, invoice: Invoice, lines: list[InvoiceLine]) -> list[AuditCheck]:
        tolerance = self.settings.audit_tolerance
        checks: list[AuditCheck] = []
        tax_base = _dec(invoice.tax_base)
        total_vat = _dec(invoice.total_vat)
        total_irpf = _dec(invoice.total_irpf)
        total_amount = _dec(invoice.total_amount)

        bases = [_dec(line.line_base) for line in lines]
        base_sum = _round(sum(bases, Decimal(0)))
        if _same(base_sum, tax_base, tolerance):
            checks.append(
                _ok(
                    TAX_BASE_SUM,
                    f"La suma de las bases de las líneas ({_eur(base_sum)}) coincide con la base imponible",
                    field="tax_base",
                )
            )
        else:
            checks.append(
                _fail(
                    TAX_BASE_SUM,
                    Severity.ERROR,
                    f"La base imponible debería ser {_eur(base_sum)} (suma de las bases de las líneas) "
                    f"y figura {_eur(tax_base)} (diferencia {_eur(abs(base_sum - tax_base))})",
                    field="tax_base",
                    expected=f"{base_sum:.2f}",
                    actual=f"{_round(tax_base):.2f}",
                )
            )

        checks.append(
            self._check_tax_total(
                VAT_TOTAL,
                label="El IVA total",
                field="total_vat",
                declared=total_vat,
                rate_groups=_group_by_rate((line.vat_rate, line.line_base, line.vat_amount) for line in lines),
                tolerance=tolerance,
            )
        )
        checks.append(
            self._check_tax_total(
                IRPF_TOTAL,
                label="La retención de IRPF total",
                field="total_irpf",
                declared=total_irpf,
                rate_groups=_group_by_rate((line.irpf_rate, line.line_base, line.irpf_amount) for line in lines),
                tolerance=tolerance,
            )
        )

        expected_total = _round(tax_base + total_vat - total_irpf)
        if _same(expected_total, total_amount, tolerance):
            checks.append(
                _ok(
                    GRAND_TOTAL,
                    f"El importe total ({_eur(total_amount)}) es coherente con base + IVA - IRPF",
                    field="total_amount",
                )
            )
        else:
            checks.append(
                _fail(
                    GRAND_TOTAL,
                    Severity.ERROR,
                    f"El importe total debería ser {_eur(expected_total)} "
                    f"({_eur(tax_base)} + {_eur(total_vat)} de IVA - {_eur(total_irpf)} de IRPF) "
                    f"y figura {_eur(total_amount)} (diferencia {_eur(abs(expected_total - total_amount))})",
                    field="total_amount",
                    expected=f"{expected_total:.2f}",
                    actual=f"{_round(total_amount):.2f}",
                )
            )
        return checks

    @staticmethod
    def _check_tax_total(
        rule_code: str,
        *,
        label: str,
        field: str,
        declared: Decimal,
        rate_groups: dict[Decimal, tuple[Decimal, Decimal]],
        tolerance: Decimal,
    ) -> AuditCheck:
        """Accepts either per-rate-group rounding or the sum of the already rounded per-line amounts."""
        by_group = _round(sum((_round(base * rate / HUNDRED) for rate, (base, _) in rate_groups.items()), Decimal(0)))
        by_lines = _round(sum((amount for _, amount in rate_groups.values()), Decimal(0)))

        if _same(by_group, declared, tolerance) or _same(by_lines, declared, tolerance):
            return _ok(rule_code, f"{label} ({_eur(declared)}) coincide con el calculado", field=field)

        if by_group == by_lines:
            reference = f"debería ser {_eur(by_group)}"
        else:
            reference = (
                f"debería ser {_eur(by_group)} (redondeo por tipo impositivo) o {_eur(by_lines)} (suma de las líneas)"
            )
        return _fail(
            rule_code,
            Severity.ERROR,
            f"{label} {reference} y figura {_eur(declared)} (diferencia {_eur(abs(by_group - declared))})",
            field=field,
            expected=f"{by_group:.2f}",
            actual=f"{_round(declared):.2f}",
        )

    # ---------------------------------------------------------------- duplicates

    def _check_duplicates(self, invoice: Invoice) -> tuple[list[AuditCheck], list[int]]:
        supplier_tax_id = normalize_tax_id(invoice.supplier_tax_id)
        number = _normalize_number(invoice.invoice_series, invoice.invoice_number)
        label = invoice.full_number

        same_number: list[int] = []
        if supplier_tax_id:
            rows = self.db.execute(
                select(
                    Invoice.id,
                    Invoice.supplier_tax_id,
                    Invoice.invoice_series,
                    Invoice.invoice_number,
                ).where(
                    Invoice.id < invoice.id,
                    or_(
                        Invoice.supplier_id == invoice.supplier_id,
                        Invoice.supplier_tax_id.in_({invoice.supplier_tax_id, supplier_tax_id}),
                    ),
                )
            ).all()
            same_number = sorted(
                row.id
                for row in rows
                if normalize_tax_id(row.supplier_tax_id) == supplier_tax_id
                and _normalize_number(row.invoice_series, row.invoice_number) == number
            )

        checks: list[AuditCheck] = []
        if not supplier_tax_id:
            checks.append(
                _ok(
                    DUPLICATE,
                    "No se ha podido comprobar la duplicidad por número: la factura no tiene identificador fiscal "
                    "del emisor",
                    field="invoice_number",
                )
            )
        elif same_number:
            checks.append(
                _fail(
                    DUPLICATE,
                    Severity.ERROR,
                    f"Factura duplicada: el emisor {supplier_tax_id} ya tiene registrada la factura número "
                    f"'{label}' (facturas {_join_ids(same_number)})",
                    field="invoice_number",
                    actual=label,
                )
            )
        else:
            checks.append(
                _ok(DUPLICATE, f"No existe otra factura del emisor con el número '{label}'", field="invoice_number")
            )

        same_hash: list[int] = []
        if invoice.source_file_hash:
            same_hash = list(
                self.db.scalars(
                    select(Invoice.id)
                    .where(Invoice.id < invoice.id, Invoice.source_file_hash == invoice.source_file_hash)
                    .order_by(Invoice.id)
                )
            )
            if same_hash:
                checks.append(
                    _fail(
                        DUPLICATE,
                        Severity.ERROR,
                        "Archivo duplicado: ya se ha cargado un documento idéntico "
                        f"(huella {invoice.source_file_hash[:12]}…, facturas {_join_ids(same_hash)})",
                        field="source_file_hash",
                        actual=invoice.source_file_hash,
                    )
                )
            else:
                checks.append(
                    _ok(DUPLICATE, "El archivo origen no se ha cargado anteriormente", field="source_file_hash")
                )

        return checks, sorted(set(same_number) | set(same_hash))

    # --------------------------------------------------------------- FACe-related

    @staticmethod
    def _check_currency(invoice: Invoice) -> list[AuditCheck]:
        currency = (invoice.currency or "").strip().upper()
        if currency == FACE_CURRENCY:
            return [_ok(CURRENCY, "La moneda de la factura es EUR", field="currency")]
        return [
            _fail(
                CURRENCY,
                Severity.WARNING,
                f"La moneda de la factura es '{currency or 'no indicada'}' y FACe requiere {FACE_CURRENCY}",
                field="currency",
                expected=FACE_CURRENCY,
                actual=currency or None,
            )
        ]

    @staticmethod
    def _check_dir3(invoice: Invoice) -> list[AuditCheck]:
        missing = [label for attr, label in DIR3_FIELDS if not (getattr(invoice, attr) or "").strip()]
        if not missing:
            return [_ok(FACE_DIR3, "Constan los tres códigos DIR3 necesarios para FACe", field="dir3")]
        return [
            _fail(
                FACE_DIR3,
                Severity.WARNING,
                f"Faltan códigos DIR3 ({', '.join(missing)}): no se podrá enviar a FACe sin códigos DIR3",
                field="dir3",
            )
        ]

    @staticmethod
    def _check_party_data(invoice: Invoice) -> list[AuditCheck]:
        supplier = invoice.supplier
        seller = {attr: getattr(supplier, attr, None) for attr, _ in PARTY_FIELDS}
        seller["name"] = getattr(supplier, "name", None)
        buyer = {attr: getattr(invoice, f"buyer_{attr}") for attr, _ in PARTY_FIELDS}
        buyer["name"] = invoice.buyer_name

        problems: list[str] = []
        for party_label, data in (("emisor", seller), ("receptor", buyer)):
            missing = [label for attr, label in (("name", "nombre"), *PARTY_FIELDS) if not (data.get(attr) or "").strip()]
            if missing:
                problems.append(f"{party_label}: faltan {', '.join(missing)}")
            postal_code = (data.get("postal_code") or "").strip()
            if postal_code and not _SPANISH_POSTAL_CODE.match(postal_code):
                problems.append(f"{party_label}: código postal '{postal_code}' no tiene 5 dígitos")

        if not problems:
            return [_ok(FACE_PARTY_DATA, "Emisor y receptor tienen los datos completos exigidos por Facturae")]
        return [
            _fail(
                FACE_PARTY_DATA,
                Severity.WARNING,
                f"Datos incompletos para generar Facturae ({'; '.join(problems)})",
                field="parties",
            )
        ]

    @staticmethod
    def _check_synthetic(invoice: Invoice) -> list[AuditCheck]:
        """Fabricated demo data never corresponds to the document, so it can never be validated."""
        if not invoice.is_synthetic:
            return []
        return [
            _fail(
                SYNTHETIC_DATA,
                Severity.ERROR,
                "Datos sintéticos de demostración: no corresponden al documento",
                field="is_synthetic",
                expected="false",
                actual="true",
            )
        ]

    # ------------------------------------------------------------------- summary

    @staticmethod
    def _build_summary(
        status: AuditStatus, checks: list[AuditCheck], errors: list[AuditCheck], warnings: list[AuditCheck]
    ) -> str:
        if not errors and not warnings:
            return f"Factura validada: se superaron correctamente las {len(checks)} comprobaciones."
        details = " | ".join(c.message for c in errors + warnings)
        counts = f"{len(errors)} error(es) y {len(warnings)} advertencia(s)"
        if status is AuditStatus.VALIDATED:
            return f"Factura validada con {len(warnings)} advertencia(s): {details}"
        if status is AuditStatus.DUPLICATE:
            return f"Posible factura duplicada ({counts}): {details}"
        return f"Se han detectado discrepancias ({counts}): {details}"


def _same(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    """Equality at cent level; `tolerance` (one cent by default) means any difference below it is no difference."""
    diff = abs(_round(a) - _round(b))
    return diff == 0 or diff < tolerance


def _group_by_rate(rows: Iterable[tuple[object, object, object]]) -> dict[Decimal, tuple[Decimal, Decimal]]:
    """Group (rate, base, amount) rows by rate into (sum of bases, sum of declared amounts)."""
    bases: dict[Decimal, Decimal] = defaultdict(Decimal)
    amounts: dict[Decimal, Decimal] = defaultdict(Decimal)
    for rate, base, amount in rows:
        key = _dec(rate).normalize()
        bases[key] += _dec(base)
        amounts[key] += _dec(amount)
    return {rate: (bases[rate], amounts[rate]) for rate in bases}


def _join_ids(ids: list[int]) -> str:
    return ", ".join(f"#{i}" for i in ids)
