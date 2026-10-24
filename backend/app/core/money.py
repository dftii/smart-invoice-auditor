"""Decimal helpers shared by the extraction, audit, persistence and export layers."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
HUNDRED = Decimal(100)
QUANTITY_STEP = Decimal("0.0001")
UNIT_PRICE_STEP = Decimal("0.000001")
# Numeric(14, 2): amounts must stay below this magnitude to be stored in every supported database.
MAX_ABS_AMOUNT = Decimal(10) ** 12


def to_decimal(value: object) -> Decimal:
    """Coerce DB/ORM values (Decimal, int, float, str, None) to Decimal; None means zero."""
    if value is None:
        return Decimal(0)
    return value if isinstance(value, Decimal) else Decimal(str(value))


def quantize(value: Decimal, step: Decimal) -> Decimal:
    """Round half up to `step`. Raises decimal.InvalidOperation when the value does not fit the precision."""
    return value.quantize(step, rounding=ROUND_HALF_UP)


def round_money(value: Decimal) -> Decimal:
    return quantize(value, CENT)


def percent_of(base: Decimal, rate: Decimal) -> Decimal:
    return round_money(base * rate / HUNDRED)


def line_base(quantity: Decimal, unit_price: Decimal, discount_rate: Decimal) -> Decimal:
    return round_money(quantity * unit_price * (HUNDRED - discount_rate) / HUNDRED)
