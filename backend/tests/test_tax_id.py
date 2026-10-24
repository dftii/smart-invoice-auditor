"""Tests for Spanish NIF / NIE / CIF validation.

Expected control characters come from a reference implementation written independently of the production code
(Horner's rule for the mod-23 letter, an explicit position loop for the CIF digit) and from hand-computed constants.
"""

import string

import pytest

from app.models import PersonType, TaxIdType
from app.services.tax_id import (
    _detect_type,
    compute_cif_control,
    compute_nif_letter,
    normalize_tax_id,
    validate_cif,
    validate_nie,
    validate_nif,
    validate_tax_id,
)

NIF_FORMAT_REASON = (
    "Formato de NIF no válido: se esperan 8 dígitos y una letra de control (o K/L/M + 7 dígitos + letra)"
)
NIE_FORMAT_REASON = "Formato de NIE no válido: se esperan X, Y o Z, 7 dígitos y una letra de control"
CIF_FORMAT_REASON = "Formato de CIF no válido: se esperan una letra de entidad, 7 dígitos y un carácter de control"

# --------------------------------------------------------------------------- Independent reference

NIF_TABLE = [
    "T",
    "R",
    "W",
    "A",
    "G",
    "M",
    "Y",
    "F",
    "P",
    "D",
    "X",
    "B",
    "N",
    "J",
    "Z",
    "S",
    "Q",
    "V",
    "H",
    "L",
    "C",
    "K",
    "E",
]
CIF_LETTER_TABLE = ["J", "A", "B", "C", "D", "E", "F", "G", "H", "I"]
ALL_CIF_CONTROLS = list("0123456789") + list("ABCDEFGHIJ")

# Entity letters per control rule, as published by the AEAT (hard-coded on purpose, not imported from the app).
LETTER_ONLY_ORGS = "PQRSNW"
DIGIT_ONLY_ORGS = "ABEH"
EITHER_ORGS = "CDFGJUV"
VALID_ORGS = LETTER_ONLY_ORGS + DIGIT_ONLY_ORGS + EITHER_ORGS
NIF_PREFIXES = "KLM"
NIE_PREFIXES = "XYZ"
# Letters that start no Spanish tax id at all (no CIF entity, not K/L/M, not X/Y/Z).
UNASSIGNED_LETTERS = "IOT"


def reference_nif_letter(digits: str) -> str:
    remainder = 0
    for char in digits:
        remainder = (remainder * 10 + int(char)) % 23
    return NIF_TABLE[remainder]


def reference_cif_control(digits7: str) -> tuple[str, str]:
    total = 0
    for position, char in enumerate(digits7, start=1):
        value = int(char)
        if position % 2 == 1:
            doubled = value * 2
            total += doubled // 10 + doubled % 10
        else:
            total += value
    units = total % 10
    digit = 0 if units == 0 else 10 - units
    return str(digit), CIF_LETTER_TABLE[digit]


def _wrong_nie_letters() -> list[tuple[str, str]]:
    pairs = []
    for prefix, prefix_digit in zip(NIE_PREFIXES, "012", strict=True):
        correct = reference_nif_letter(prefix_digit + "1234567")
        pairs += [(prefix, letter) for letter in string.ascii_uppercase if letter != correct]
    return pairs


WRONG_NIE_LETTERS = _wrong_nie_letters()


def accepted_controls(org: str, digits7: str = "1234567") -> set[str]:
    digit, letter = reference_cif_control(digits7)
    if org in LETTER_ONLY_ORGS:
        return {letter}
    if org in DIGIT_ONLY_ORGS:
        return {digit}
    return {digit, letter}


# DNI numbers 0..22 cover every possible remainder, i.e. each of the 23 letters exactly once.
ALL_23_DNIS = [(f"{n:08d}", letter) for n, letter in enumerate("TRWAGMYFPDXBNJZSQVHLCKE")]
# Hand-computed: 87654321 = 23 * 3811057 + 10 -> X; 11111111 = 23 * 483091 + 18 -> H.
KNOWN_DNIS = ["12345678Z", "87654321X", "11111111H", "00000000T", "99999999R", "00000001R"]
KNOWN_SPECIAL_NIFS = ["K1234567L", "L1234567L", "M1234567L", "K0000000T", "L0000001R", "M0000022E"]
# 10000000 = 23 * 434782 + 14 -> Z; 20000000 = 23 * 869565 + 5 -> M; 9999999 = 23 * 434782 + 13 -> J.
KNOWN_NIES = ["X1234567L", "Y1234567X", "Z1234567R", "X0000000T", "Y0000000Z", "Z0000000M", "X9999999J", "Y9999999G"]
# Public CIFs of well-known companies / bodies, plus hand-computed examples (B1234567 -> sum 26 -> 4 / D).
KNOWN_CIFS = [
    "A28017895",  # El Corte Inglés
    "A39000013",  # Banco Santander
    "A48265169",  # BBVA
    "A82018474",  # Telefónica Móviles
    "A58818501",
    "B65410011",
    "B12345674",
    "Q2826000H",
    "P0800000B",
]


class TestReferenceImplementation:
    """The reference helpers must agree with hand-computed constants before they are trusted as an oracle."""

    @pytest.mark.parametrize(
        ("digits", "letter"),
        [
            ("12345678", "Z"),
            ("87654321", "X"),
            ("11111111", "H"),
            ("00000000", "T"),
            ("99999999", "R"),
            ("1234567", "L"),
        ],
    )
    def test_reference_nif_letter(self, digits, letter):
        assert reference_nif_letter(digits) == letter

    @pytest.mark.parametrize(
        ("digits7", "expected"),
        [
            ("1234567", ("4", "D")),  # 2 + 2 + 6 + 4 + 1 + 6 + 5 = 26 -> 10 - 6
            ("5881850", ("1", "A")),  # 1 + 8 + 7 + 1 + 7 + 5 + 0 = 29 -> 10 - 9
            ("0000000", ("0", "J")),  # sum 0 -> control 0, not 10
            ("9090909", ("4", "D")),  # four doubled 9s give 9 each (36) + zeros -> 10 - 6
        ],
    )
    def test_reference_cif_control(self, digits7, expected):
        assert reference_cif_control(digits7) == expected

    def test_known_cifs_satisfy_the_reference_rule(self):
        for cif in KNOWN_CIFS:
            digit, letter = reference_cif_control(cif[1:8])
            assert cif[8] in accepted_controls(cif[0], cif[1:8]), cif
            assert cif[8] in (digit, letter), cif


class TestComputeFunctions:
    @pytest.mark.parametrize(("digits", "letter"), ALL_23_DNIS)
    def test_nif_letter_for_each_remainder(self, digits, letter):
        assert compute_nif_letter(int(digits)) == letter

    def test_all_23_letters_are_distinct_and_complete(self):
        letters = [compute_nif_letter(n) for n in range(23)]
        assert len(set(letters)) == 23
        assert letters == NIF_TABLE
        assert set(letters).isdisjoint("IÑOU")

    @pytest.mark.parametrize(("number", "letter"), [(23, "T"), (24, "R"), (1234567, "L"), (99999999, "R")])
    def test_nif_letter_wraps_modulo_23(self, number, letter):
        assert compute_nif_letter(number) == letter
        assert compute_nif_letter(number + 23) == letter

    @pytest.mark.parametrize(
        ("org", "digits", "expected"),
        [
            ("B", "1234567", ("4", "D")),
            ("A", "5881850", ("1", "A")),
            ("B", "6541001", ("1", "A")),
            ("Q", "2826000", ("8", "H")),
            ("P", "0800000", ("2", "B")),
            ("B", "0000000", ("0", "J")),
            ("B", "9090909", ("4", "D")),
            ("a", "5881850", ("1", "A")),
        ],
    )
    def test_cif_control(self, org, digits, expected):
        assert compute_cif_control(org, digits) == expected

    @pytest.mark.parametrize("digits7", ["0000000", "1234567", "9999999", "7654321", "2468024", "1357913", "0000001"])
    def test_cif_control_matches_reference(self, digits7):
        assert compute_cif_control("B", digits7) == reference_cif_control(digits7)

    def test_cif_control_matches_reference_on_a_dense_sample(self):
        for number in range(0, 10_000_000, 7_919):
            digits7 = f"{number:07d}"
            assert compute_cif_control("C", digits7) == reference_cif_control(digits7), digits7

    @pytest.mark.parametrize(
        "digits7",
        ["12345", "123456", "12345678", "12345AB", "1234 67", "", "123456-"],
    )
    def test_cif_control_rejects_bad_digits(self, digits7):
        with pytest.raises(ValueError, match="7 digits"):
            compute_cif_control("B", digits7)

    @pytest.mark.parametrize("org", ["X", "I", "O", "T", "K", "1", "Ñ"])
    def test_cif_control_rejects_bad_org_letter(self, org):
        with pytest.raises(ValueError, match="organisation letter"):
            compute_cif_control(org, "1234567")

    @pytest.mark.parametrize("org", ["", "AB", "ABC"])
    def test_cif_control_rejects_non_single_letter_org(self, org):
        with pytest.raises(ValueError):
            compute_cif_control(org, "1234567")


class TestNormalize:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("12345678z", "12345678Z"),
            (" 12.345.678-Z ", "12345678Z"),
            ("12 345 678 Z", "12345678Z"),
            ("12/345/678_Z", "12345678Z"),
            ("\t12345678Z\n", "12345678Z"),
            ("ES B65410011", "B65410011"),
            ("ESB-65410011", "B65410011"),
            ("es12345678z", "12345678Z"),
            ("ES-12.345.678-Z", "12345678Z"),
            ("ES X1234567L", "X1234567L"),
            ("B 65410011", "B65410011"),
            ("b.654.100.11", "B65410011"),
            ("", ""),
            ("   ", ""),
            ("--.. ", ""),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_tax_id(raw) == expected

    def test_none_is_empty(self):
        assert normalize_tax_id(None) == ""

    @pytest.mark.parametrize("raw", ["ES1234567", "ES123456789012", "ESB654100112", "ES"])
    def test_es_prefix_is_only_stripped_from_eleven_character_ids(self, raw):
        assert normalize_tax_id(raw) == raw

    def test_non_ascii_characters_are_dropped_as_separators(self):
        assert normalize_tax_id("12345678Z€") == "12345678Z"
        assert normalize_tax_id("B·65410011") == "B65410011"

    def test_is_idempotent(self):
        once = normalize_tax_id(" es b-65410011 ")
        assert normalize_tax_id(once) == once == "B65410011"


class TestNif:
    @pytest.mark.parametrize(("digits", "letter"), ALL_23_DNIS)
    def test_each_of_the_23_letters_is_accepted_for_its_number(self, digits, letter):
        assert validate_nif(digits + letter)

    @pytest.mark.parametrize("value", KNOWN_DNIS + KNOWN_SPECIAL_NIFS)
    def test_known_valid(self, value):
        assert validate_nif(value)

    @pytest.mark.parametrize("wrong", [letter for letter in string.ascii_uppercase if letter != "Z"])
    def test_every_other_letter_is_rejected_for_12345678(self, wrong):
        assert not validate_nif(f"12345678{wrong}")

    @pytest.mark.parametrize(
        "value",
        [
            "1234567Z",  # 7 digits
            "123456789Z",  # 9 digits
            "12345678",  # no control letter
            "12345678ZZ",  # two letters
            "1234A678Z",  # letter inside the body
            "ABCDEFGHI",
            "123456781",  # digit instead of control letter
            "K123456L",  # K + 6 digits
            "K12345678L",  # K + 8 digits
            "K1234567",  # K without control letter
            "KL234567L",  # non-digit body
            "KK234567L",
            "",
            "   ",
            None,
        ],
    )
    def test_malformed_is_rejected(self, value):
        assert not validate_nif(value)

    @pytest.mark.parametrize("prefix", list(NIF_PREFIXES))
    def test_special_nifs_use_the_seven_digits_for_the_letter(self, prefix):
        assert validate_nif(f"{prefix}1234567L")
        assert not validate_nif(f"{prefix}1234567A")
        assert not validate_nif(f"{prefix}1234567Z")

    @pytest.mark.parametrize(
        "value",
        ["12.345.678-z", "ES12345678Z", "es 12345678 z", " 12345678Z ", "ES-12.345.678-Z", "k-1234567-l"],
    )
    def test_accepts_formatting_noise(self, value):
        assert validate_nif(value)

    @pytest.mark.parametrize("value", ["X1234567L", "B65410011", "Y1234567X", "A58818501"])
    def test_other_id_kinds_are_not_nifs(self, value):
        assert not validate_nif(value)


class TestNie:
    @pytest.mark.parametrize("value", KNOWN_NIES)
    def test_known_valid(self, value):
        assert validate_nie(value)

    @pytest.mark.parametrize("prefix", list(NIE_PREFIXES))
    @pytest.mark.parametrize("digits", ["0000000", "1234567", "7654321", "9999999", "0000023"])
    def test_letter_is_computed_from_prefix_digit_plus_seven_digits(self, prefix, digits):
        number = "012"[NIE_PREFIXES.index(prefix)] + digits
        assert validate_nie(prefix + digits + reference_nif_letter(number))

    @pytest.mark.parametrize(("prefix", "wrong"), WRONG_NIE_LETTERS)
    def test_wrong_control_letter_is_rejected_for_1234567(self, prefix, wrong):
        assert not validate_nie(f"{prefix}1234567{wrong}")

    @pytest.mark.parametrize(
        "value",
        [
            "X1234567A",  # wrong letter
            "A1234567L",  # not an NIE prefix
            "T1234567L",
            "X123456L",  # 6 digits
            "X12345678L",  # 8 digits
            "X1234567",  # no letter
            "X12B4567L",  # non-digit body
            "1234567L",
            "XX234567L",
            "",
            None,
        ],
    )
    def test_invalid(self, value):
        assert not validate_nie(value)

    def test_prefix_changes_the_letter(self):
        assert validate_nie("X1234567L")
        assert not validate_nie("Y1234567L")
        assert not validate_nie("Z1234567L")

    def test_formatting_noise_and_country_prefix(self):
        assert validate_nie("ES X-1234567-L")
        assert validate_nie("x1234567l")

    @pytest.mark.parametrize("value", ["12345678Z", "K1234567L", "B65410011"])
    def test_other_id_kinds_are_not_nies(self, value):
        assert not validate_nie(value)


class TestCif:
    @pytest.mark.parametrize("value", KNOWN_CIFS)
    def test_known_valid(self, value):
        assert validate_cif(value)

    @pytest.mark.parametrize("org", list(VALID_ORGS))
    def test_accepted_control_characters_per_entity_type(self, org):
        accepted = {c for c in ALL_CIF_CONTROLS if validate_cif(f"{org}1234567{c}")}
        assert accepted == accepted_controls(org)

    @pytest.mark.parametrize("org", list(LETTER_ONLY_ORGS))
    def test_letter_only_entities(self, org):
        digit, letter = reference_cif_control("1234567")
        assert validate_cif(f"{org}1234567{letter}")
        assert not validate_cif(f"{org}1234567{digit}")

    @pytest.mark.parametrize("org", list(DIGIT_ONLY_ORGS))
    def test_digit_only_entities(self, org):
        digit, letter = reference_cif_control("1234567")
        assert validate_cif(f"{org}1234567{digit}")
        assert not validate_cif(f"{org}1234567{letter}")

    @pytest.mark.parametrize("org", list(EITHER_ORGS))
    def test_other_entities_accept_both(self, org):
        digit, letter = reference_cif_control("1234567")
        assert validate_cif(f"{org}1234567{digit}")
        assert validate_cif(f"{org}1234567{letter}")

    @pytest.mark.parametrize("org", list(VALID_ORGS))
    @pytest.mark.parametrize("digits7", ["0000000", "5881850", "9999999", "2826000", "0800000"])
    def test_every_entity_with_several_bodies(self, org, digits7):
        accepted = {c for c in ALL_CIF_CONTROLS if validate_cif(f"{org}{digits7}{c}")}
        assert accepted == accepted_controls(org, digits7)

    def test_control_zero_maps_to_digit_0_and_letter_j(self):
        # Sum divisible by 10 -> control 0 (never 10): digit '0' / letter 'J', and 'I' (=9) is wrong.
        assert validate_cif("B00000000")
        assert validate_cif("G0000000J")
        assert validate_cif("G00000000")
        assert validate_cif("P0000000J")
        assert not validate_cif("P00000000")
        assert not validate_cif("B0000000J")
        assert not validate_cif("G0000000I")

    @pytest.mark.parametrize("wrong", ["B65410012", "A58818502", "B65410010", "B6541001A", "B65410019"])
    def test_wrong_control_is_rejected(self, wrong):
        assert not validate_cif(wrong)

    @pytest.mark.parametrize("letter", [c for c in string.ascii_uppercase if c not in VALID_ORGS])
    def test_invalid_entity_letters_are_rejected(self, letter):
        digit, control_letter = reference_cif_control("1234567")
        assert not validate_cif(f"{letter}1234567{digit}")
        assert not validate_cif(f"{letter}1234567{control_letter}")

    @pytest.mark.parametrize(
        "value",
        [
            "Q2826000",  # missing control
            "B1234567",
            "B123456744",  # too long
            "B12345",  # too short
            "12345674B",  # letter at the end
            "B12A45674",  # non-digit body
            "BB2345674",
            "B1234567K",  # control letter outside A-J
            "B1234567Z",
            "",
            None,
        ],
    )
    def test_bad_format(self, value):
        assert not validate_cif(value)

    @pytest.mark.parametrize("value", ["12345678Z", "X1234567L", "K1234567L"])
    def test_other_id_kinds_are_not_cifs(self, value):
        assert not validate_cif(value)

    def test_formatting_noise_and_country_prefix(self):
        assert validate_cif("ES B-65410011")
        assert validate_cif("b 6541 0011")
        assert validate_cif("esq2826000h")


class TestValidateTaxId:
    @pytest.mark.parametrize("value", KNOWN_DNIS + KNOWN_SPECIAL_NIFS)
    def test_nif(self, value):
        result = validate_tax_id(value)
        assert result.is_valid
        assert result.reason is None
        assert result.tax_id_type is TaxIdType.NIF
        assert result.person_type is PersonType.INDIVIDUAL
        assert result.normalized == value

    @pytest.mark.parametrize("value", KNOWN_NIES)
    def test_nie(self, value):
        result = validate_tax_id(value)
        assert result.is_valid
        assert result.reason is None
        assert result.tax_id_type is TaxIdType.NIE
        assert result.person_type is PersonType.INDIVIDUAL
        assert result.normalized == value

    @pytest.mark.parametrize("value", KNOWN_CIFS)
    def test_cif(self, value):
        result = validate_tax_id(value)
        assert result.is_valid
        assert result.reason is None
        assert result.tax_id_type is TaxIdType.CIF
        assert result.person_type is PersonType.LEGAL_ENTITY
        assert result.normalized == value

    @pytest.mark.parametrize(
        ("raw", "normalized", "tax_id_type"),
        [
            ("ES-b 65410011", "B65410011", TaxIdType.CIF),
            ("es 12.345.678-z", "12345678Z", TaxIdType.NIF),
            ("ES X-1234567-L", "X1234567L", TaxIdType.NIE),
            ("  k1234567l\n", "K1234567L", TaxIdType.NIF),
        ],
    )
    def test_normalizes_and_strips_country_prefix(self, raw, normalized, tax_id_type):
        result = validate_tax_id(raw)
        assert result.is_valid
        assert result.normalized == normalized
        assert result.tax_id_type is tax_id_type

    def test_nif_wrong_letter_message(self):
        result = validate_tax_id("12345678A")
        assert not result.is_valid
        assert result.tax_id_type is TaxIdType.NIF
        assert result.person_type is PersonType.INDIVIDUAL
        assert result.reason == "Letra de control incorrecta: se esperaba 'Z' y se encontró 'A'"

    def test_special_nif_wrong_letter_message(self):
        result = validate_tax_id("K1234567A")
        assert result.tax_id_type is TaxIdType.NIF
        assert result.reason == "Letra de control incorrecta: se esperaba 'L' y se encontró 'A'"

    def test_nie_wrong_letter_message(self):
        result = validate_tax_id("X1234567A")
        assert not result.is_valid
        assert result.tax_id_type is TaxIdType.NIE
        assert result.person_type is PersonType.INDIVIDUAL
        assert result.reason == "Letra de control incorrecta: se esperaba 'L' y se encontró 'A'"

    def test_cif_wrong_digit_message(self):
        result = validate_tax_id("B12345678")
        assert not result.is_valid
        assert result.tax_id_type is TaxIdType.CIF
        assert result.person_type is PersonType.LEGAL_ENTITY
        assert result.reason == "Dígito de control incorrecto: se esperaba '4' y se encontró '8'"

    def test_cif_digit_where_letter_is_required_message(self):
        result = validate_tax_id("P08000002")
        assert result.reason == "Letra de control incorrecta: se esperaba 'B' y se encontró '2'"

    def test_cif_letter_where_digit_is_required_message(self):
        result = validate_tax_id("B1234567D")
        assert result.reason == "Dígito de control incorrecto: se esperaba '4' y se encontró 'D'"

    def test_cif_wrong_letter_message(self):
        result = validate_tax_id("P0800000C")
        assert not result.is_valid
        assert result.reason == "Letra de control incorrecta: se esperaba 'B' y se encontró 'C'"

    def test_cif_flexible_control_message(self):
        result = validate_tax_id("G12345675")
        assert not result.is_valid
        assert result.reason == "Carácter de control incorrecto: se esperaba '4' o 'D' y se encontró '5'"

    @pytest.mark.parametrize(
        ("value", "tax_id_type", "reason"),
        [
            (
                "1234",
                TaxIdType.NIF,
                NIF_FORMAT_REASON,
            ),
            (
                "1234567Z",
                TaxIdType.NIF,
                NIF_FORMAT_REASON,
            ),
            (
                "K12345678",
                TaxIdType.NIF,
                NIF_FORMAT_REASON,
            ),
            (
                "X123456L",
                TaxIdType.NIE,
                NIE_FORMAT_REASON,
            ),
            (
                "Z12B4567L",
                TaxIdType.NIE,
                NIE_FORMAT_REASON,
            ),
            (
                "B1234567",
                TaxIdType.CIF,
                CIF_FORMAT_REASON,
            ),
            (
                "B1234567Z",
                TaxIdType.CIF,
                CIF_FORMAT_REASON,
            ),
            (
                "A12B4567C",
                TaxIdType.CIF,
                CIF_FORMAT_REASON,
            ),
        ],
    )
    def test_bad_format_messages(self, value, tax_id_type, reason):
        result = validate_tax_id(value)
        assert not result.is_valid
        assert result.tax_id_type is tax_id_type
        assert result.reason == reason

    @pytest.mark.parametrize("letter", list(UNASSIGNED_LETTERS))
    def test_unassigned_first_letter_is_unknown(self, letter):
        result = validate_tax_id(f"{letter}1234567L")
        assert not result.is_valid
        assert result.tax_id_type is TaxIdType.UNKNOWN
        assert result.person_type is PersonType.LEGAL_ENTITY
        assert result.normalized == f"{letter}1234567L"
        assert result.reason == "Formato no reconocido: no corresponde a un NIF, NIE ni CIF español"

    @pytest.mark.parametrize(
        ("value", "tax_id_type"),
        [
            ("12345678Z", TaxIdType.NIF),
            ("K1234567L", TaxIdType.NIF),
            ("X1234567L", TaxIdType.NIE),
            ("B65410011", TaxIdType.CIF),
        ],
    )
    def test_type_is_detected_from_the_first_character(self, value, tax_id_type):
        assert validate_tax_id(value).tax_id_type is tax_id_type

    @pytest.mark.parametrize("letter", list("KLM"))
    def test_k_l_m_are_nifs_not_cifs(self, letter):
        result = validate_tax_id(f"{letter}1234567L")
        assert result.tax_id_type is TaxIdType.NIF
        assert result.person_type is PersonType.INDIVIDUAL

    @pytest.mark.parametrize("value", [None, "", "   ", "--", "..."])
    def test_missing(self, value):
        result = validate_tax_id(value)
        assert not result.is_valid
        assert result.tax_id_type is TaxIdType.UNKNOWN
        assert result.person_type is PersonType.LEGAL_ENTITY
        assert result.normalized == ""
        assert result.reason == "No se ha indicado ningún NIF/NIE/CIF"

    def test_lone_es_is_not_treated_as_missing(self):
        # Too short to be a country prefix, so it is read as a (malformed) CIF starting with entity letter E.
        result = validate_tax_id("ES")
        assert result.normalized == "ES"
        assert result.tax_id_type is TaxIdType.CIF
        assert result.reason == CIF_FORMAT_REASON

    def test_detect_type_of_empty_string(self):
        assert _detect_type("") is TaxIdType.UNKNOWN

    def test_result_is_immutable(self):
        result = validate_tax_id("12345678Z")
        with pytest.raises(AttributeError):
            result.is_valid = False

    def test_validate_functions_agree_with_validate_tax_id(self):
        for value in KNOWN_DNIS + KNOWN_NIES + KNOWN_CIFS + ["12345678A", "X1234567A", "B65410012", "Q2826000"]:
            result = validate_tax_id(value)
            by_type = {TaxIdType.NIF: validate_nif, TaxIdType.NIE: validate_nie, TaxIdType.CIF: validate_cif}
            assert by_type[result.tax_id_type](value) is result.is_valid, value
