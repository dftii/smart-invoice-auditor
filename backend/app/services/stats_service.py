"""Dashboard aggregates computed in SQL (portable across SQLite and MySQL)."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

from app.models import AuditStatus, Invoice, InvoiceLine, Supplier, ValidationLog
from app.schemas import (
    DashboardStats,
    MonthlyTotal,
    RuleFailureCount,
    StatusCount,
    TopSupplier,
    VatBreakdown,
)
from app.services.extractor import get_extractor
from app.services.invoice_service import ERROR_STATUSES

TOP_SUPPLIERS_LIMIT = 5
TOP_RULES_LIMIT = 10
CENT = Decimal("0.01")


def _dec(value: Any) -> Decimal:
    return Decimal(value if value is not None else 0).quantize(CENT, rounding=ROUND_HALF_UP)


def _by_status(db: Session) -> list[StatusCount]:
    rows = db.execute(
        select(Invoice.audit_status, func.count(Invoice.id), func.sum(Invoice.total_amount)).group_by(
            Invoice.audit_status
        )
    ).all()
    found = {status: (count, amount) for status, count, amount in rows}
    return [
        StatusCount(status=status, count=found.get(status, (0, 0))[0], total_amount=_dec(found.get(status, (0, 0))[1]))
        for status in AuditStatus
    ]


def _monthly(db: Session) -> list[MonthlyTotal]:
    year = extract("year", Invoice.issue_date)
    month = extract("month", Invoice.issue_date)
    rows = db.execute(
        select(
            year,
            month,
            func.count(Invoice.id),
            func.sum(Invoice.tax_base),
            func.sum(Invoice.total_vat),
            func.sum(Invoice.total_irpf),
            func.sum(Invoice.total_amount),
        )
        .group_by(year, month)
        .order_by(year, month)
    ).all()
    return [
        MonthlyTotal(
            month=f"{int(y):04d}-{int(m):02d}",
            invoice_count=count,
            tax_base=_dec(base),
            total_vat=_dec(vat),
            total_irpf=_dec(irpf),
            total_amount=_dec(total),
        )
        for y, m, count, base, vat, irpf, total in rows
    ]


def _top_suppliers(db: Session) -> list[TopSupplier]:
    total = func.sum(Invoice.total_amount)
    rows = db.execute(
        select(Supplier.id, Supplier.name, Supplier.tax_id, func.count(Invoice.id), total)
        .join(Invoice, Invoice.supplier_id == Supplier.id)
        .group_by(Supplier.id, Supplier.name, Supplier.tax_id)
        .order_by(total.desc(), Supplier.id)
        .limit(TOP_SUPPLIERS_LIMIT)
    ).all()
    return [
        TopSupplier(supplier_id=sid, name=name, tax_id=tax_id, invoice_count=count, total_amount=_dec(amount))
        for sid, name, tax_id, count, amount in rows
    ]


def _top_failed_rules(db: Session) -> list[RuleFailureCount]:
    """Failed checks of each invoice's latest audit run only (older runs are history, not current state)."""
    count = func.count(ValidationLog.id)
    rows = db.execute(
        select(ValidationLog.rule_code, count)
        .join(Invoice, (Invoice.id == ValidationLog.invoice_id) & (Invoice.last_audit_run_id == ValidationLog.run_id))
        .where(ValidationLog.passed.is_(False))
        .group_by(ValidationLog.rule_code)
        .order_by(count.desc(), ValidationLog.rule_code)
        .limit(TOP_RULES_LIMIT)
    ).all()
    return [RuleFailureCount(rule_code=rule, count=n) for rule, n in rows]


def _vat_breakdown(db: Session) -> list[VatBreakdown]:
    rows = db.execute(
        select(InvoiceLine.vat_rate, func.sum(InvoiceLine.line_base), func.sum(InvoiceLine.vat_amount))
        .group_by(InvoiceLine.vat_rate)
        .order_by(InvoiceLine.vat_rate.desc())
    ).all()
    return [VatBreakdown(vat_rate=_dec(rate), tax_base=_dec(base), vat_amount=_dec(vat)) for rate, base, vat in rows]


def compute_dashboard_stats(db: Session) -> DashboardStats:
    """Aggregate every stored invoice (duplicates included, so status counts add up to the total)."""
    count, base, vat, irpf, total = db.execute(
        select(
            func.count(Invoice.id),
            func.sum(Invoice.tax_base),
            func.sum(Invoice.total_vat),
            func.sum(Invoice.total_irpf),
            func.sum(Invoice.total_amount),
        )
    ).one()
    validated = db.scalar(select(func.count(Invoice.id)).where(Invoice.audit_status == AuditStatus.VALIDATED)) or 0
    suppliers = db.scalar(select(func.count(Supplier.id))) or 0
    with_errors = (
        db.scalar(
            select(func.count(Invoice.id)).where(
                Invoice.audit_status.in_(ERROR_STATUSES)
            )
        )
        or 0
    )
    exported = db.scalar(select(func.count(Invoice.id)).where(Invoice.facturae_exported_at.is_not(None))) or 0
    ready = (
        db.scalar(
            select(func.count(Invoice.id)).where(
                Invoice.audit_status == AuditStatus.VALIDATED,
                Invoice.warning_count == 0,
                Invoice.facturae_exported_at.is_(None),
            )
        )
        or 0
    )
    rate = float((Decimal(validated) * 100 / Decimal(count)).quantize(CENT, rounding=ROUND_HALF_UP)) if count else 0.0

    return DashboardStats(
        total_invoices=count,
        total_suppliers=suppliers,
        total_tax_base=_dec(base),
        total_vat=_dec(vat),
        total_irpf=_dec(irpf),
        total_amount=_dec(total),
        validation_rate=rate,
        invoices_with_errors=with_errors,
        exported_to_face=exported,
        ready_for_face=ready,
        by_status=_by_status(db),
        monthly=_monthly(db),
        top_suppliers=_top_suppliers(db),
        top_failed_rules=_top_failed_rules(db),
        vat_breakdown=_vat_breakdown(db),
        extraction_mode=get_extractor().mode,
    )
