"""Spanish tax identifier validation: NIF (DNI and K/L/M), NIE and CIF with their control algorithms."""

import re
from dataclasses import dataclass

from app.models import PersonType, TaxIdType

NIF_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
CIF_CONTROL_LETTERS = "JABCDEFGHI"
CIF_ORG_LETTERS = "ABCDEFGHJNPQRSUVW"
# Entities whose control character must be a letter / must be a digit; every other organisation accepts both.
CIF_LETTER_ONLY = frozenset("PQRSNW")
CIF_DIGIT_ONLY = frozenset("ABEH")
NIE_PREFIX = {"X": "0", "Y": "1", "Z": "2"}
SPECIAL_NIF_PREFIXES = "KLM"

_NON_ALNUM = re.compile(r"[^0-9A-Za-z]")
_DNI_RE = re.compile(r"^(\d{8})([A-Z])$")
_SPECIAL_NIF_RE = re.compile(r"^([KLM])(\d{7})([A-Z])$")
_NIE_RE = re.compile(r"^([XYZ])(\d{7})([A-Z])$")
_CIF_RE = re.compile(rf"^([{CIF_ORG_LETTERS}])(\d{{7}})([0-9A-J])$")


@dataclass(frozen=True)
class TaxIdValidation:
    normalized: str
    tax_id_type: TaxIdType
    is_valid: bool
    reason: str | None
    person_type: PersonType


def normalize_tax_id(value: str) -> str:
    """Uppercase, strip separators and the optional 'ES' country prefix."""
    cleaned = _NON_ALNUM.sub("", value or "").upper()
    if len(cleaned) == 11 and cleaned.startswith("ES"):
        cleaned = cleaned[2:]
    return cleaned


def compute_nif_letter(number: int) -> str:
    return NIF_LETTERS[number % 23]


def compute_cif_control(org_letter: str, digits7: str) -> tuple[str, str]:
    """Return the (digit, letter) control characters of a CIF; which one applies depends on the entity type."""
    if len(digits7) != 7 or not digits7.isdigit():
        raise ValueError("digits7 must contain exactly 7 digits")
    if org_letter.upper() not in CIF_ORG_LETTERS:
        raise ValueError(f"Invalid CIF organisation letter: {org_letter!r}")
    even_sum = sum(int(d) for d in digits7[1::2])
    odd_sum = sum(sum(divmod(int(d) * 2, 10)) for d in digits7[0::2])
    digit = (10 - (even_sum + odd_sum) % 10) % 10
    return str(digit), CIF_CONTROL_LETTERS[digit]


def _nif_error(normalized: str) -> str | None:
    if m := _DNI_RE.match(normalized):
        number, found = int(m.group(1)), m.group(2)
    elif m := _SPECIAL_NIF_RE.match(normalized):
        number, found = int(m.group(2)), m.group(3)
    else:
        return "Formato de NIF no válido: se esperan 8 dígitos y una letra de control (o K/L/M + 7 dígitos + letra)"
    expected = compute_nif_letter(number)
    if expected != found:
        return f"Letra de control incorrecta: se esperaba '{expected}' y se encontró '{found}'"
    return None


def _nie_error(normalized: str) -> str | None:
    m = _NIE_RE.match(normalized)
    if not m:
        return "Formato de NIE no válido: se esperan X, Y o Z, 7 dígitos y una letra de control"
    expected = compute_nif_letter(int(NIE_PREFIX[m.group(1)] + m.group(2)))
    if expected != m.group(3):
        return f"Letra de control incorrecta: se esperaba '{expected}' y se encontró '{m.group(3)}'"
    return None


def _cif_error(normalized: str) -> str | None:
    m = _CIF_RE.match(normalized)
    if not m:
        return "Formato de CIF no válido: se esperan una letra de entidad, 7 dígitos y un carácter de control"
    org, digits, found = m.groups()
    digit, letter = compute_cif_control(org, digits)
    if org in CIF_LETTER_ONLY:
        if found != letter:
            return f"Letra de control incorrecta: se esperaba '{letter}' y se encontró '{found}'"
        return None
    if org in CIF_DIGIT_ONLY:
        if found != digit:
            return f"Dígito de control incorrecto: se esperaba '{digit}' y se encontró '{found}'"
        return None
    if found not in (digit, letter):
        return f"Carácter de control incorrecto: se esperaba '{digit}' o '{letter}' y se encontró '{found}'"
    return None


def validate_nif(value: str) -> bool:
    return _nif_error(normalize_tax_id(value)) is None


def validate_nie(value: str) -> bool:
    return _nie_error(normalize_tax_id(value)) is None


def validate_cif(value: str) -> bool:
    return _cif_error(normalize_tax_id(value)) is None


def _detect_type(normalized: str) -> TaxIdType:
    if not normalized:
        return TaxIdType.UNKNOWN
    first = normalized[0]
    if first.isdigit() or first in SPECIAL_NIF_PREFIXES:
        return TaxIdType.NIF
    if first in NIE_PREFIX:
        return TaxIdType.NIE
    if first in CIF_ORG_LETTERS:
        return TaxIdType.CIF
    return TaxIdType.UNKNOWN


def validate_tax_id(value: str | None) -> TaxIdValidation:
    """Classify and validate a Spanish tax id (NIF, NIE or CIF), explaining in Spanish why it is invalid."""
    normalized = normalize_tax_id(value or "")
    if not normalized:
        return TaxIdValidation(
            normalized="",
            tax_id_type=TaxIdType.UNKNOWN,
            is_valid=False,
            reason="No se ha indicado ningún NIF/NIE/CIF",
            person_type=PersonType.LEGAL_ENTITY,
        )

    tax_id_type = _detect_type(normalized)
    if tax_id_type is TaxIdType.UNKNOWN:
        return TaxIdValidation(
            normalized=normalized,
            tax_id_type=tax_id_type,
            is_valid=False,
            reason="Formato no reconocido: no corresponde a un NIF, NIE ni CIF español",
            person_type=PersonType.LEGAL_ENTITY,
        )

    error = {
        TaxIdType.NIF: _nif_error,
        TaxIdType.NIE: _nie_error,
        TaxIdType.CIF: _cif_error,
    }[tax_id_type](normalized)
    person_type = PersonType.LEGAL_ENTITY if tax_id_type is TaxIdType.CIF else PersonType.INDIVIDUAL
    return TaxIdValidation(
        normalized=normalized,
        tax_id_type=tax_id_type,
        is_valid=error is None,
        reason=error,
        person_type=person_type,
    )
