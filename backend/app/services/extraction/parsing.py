"""Text and number parsing helpers shared by the AI result mapper and the offline text parser."""

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation


def fold(text: str) -> str:
    """Strip accents keeping string length, so match offsets stay valid on the original text."""
    return "".join(unicodedata.normalize("NFD", ch)[0] for ch in text)


def parse_decimal(raw: str) -> Decimal:
    """Parse Spanish ("1.234,56") and plain ("1234.56") decimal notations, tolerating €, % and a leading sign."""
    text = raw.strip().replace("−", "-").replace("–", "-").replace(" ", "")
    text = re.sub(r"(?i)€|eur\b|%|\s", "", text)
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = text.strip("()+-")
    if not re.fullmatch(r"\d[\d.,]*", text):
        raise InvalidOperation(f"not a number: {raw!r}")
    last_dot, last_comma = text.rfind("."), text.rfind(",")
    decimal_sep: str | None
    if last_dot >= 0 and last_comma >= 0:
        decimal_sep = "." if last_dot > last_comma else ","
    elif last_comma >= 0:
        decimal_sep = "," if text.count(",") == 1 else None
    elif last_dot >= 0:
        int_part = text[:last_dot]
        three_decimals = len(text) - last_dot - 1 == 3 and int_part not in ("0", "") and len(int_part) <= 3
        decimal_sep = None if text.count(".") > 1 or three_decimals else "."
    else:
        decimal_sep = None
    if decimal_sep is None:
        value = Decimal(re.sub(r"[.,]", "", text))
    else:
        int_text, frac = text.rsplit(decimal_sep, 1)
        value = Decimal(f"{re.sub('[.,]', '', int_text) or '0'}.{frac}")
    return -value if negative else value


MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
DATE_RE = re.compile(
    r"(?<!\d)(?:(\d{4})-(\d{1,2})-(\d{1,2})|(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})"
    r"|(\d{1,2})\s+de\s+([a-z]+)\s+(?:de\s+)?(\d{4}))(?!\d)",
    re.IGNORECASE,
)


def first_date(folded_lower: str) -> date | None:
    for match in DATE_RE.finditer(folded_lower):
        parsed = date_from_match(match)
        if parsed:
            return parsed
    return None


def date_from_match(match: re.Match[str]) -> date | None:
    groups = match.groups()
    try:
        if groups[0]:
            return date(int(groups[0]), int(groups[1]), int(groups[2]))
        if groups[3]:
            return date(int(groups[5]), int(groups[4]), int(groups[3]))
        month = MONTHS.get(groups[7].lower())
        return date(int(groups[8]), month, int(groups[6])) if month else None
    except ValueError:
        return None
