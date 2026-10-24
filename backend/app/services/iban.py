"""IBAN normalisation and validation (ISO 13616 structure and mod-97 checksum)."""

import re

_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{1,30}$")


def normalize_iban(value: str | None) -> str:
    """Uppercase and remove spaces and common separators."""
    return re.sub(r"[\s.\-]", "", value or "").upper()


def is_valid_iban(value: str | None) -> bool:
    iban = normalize_iban(value)
    if not (5 <= len(iban) <= 34) or not _IBAN_RE.match(iban):
        return False
    if iban.startswith("ES") and len(iban) != 24:
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1
