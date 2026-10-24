"""Tests for Spanish NIF / NIE / CIF validation."""

import pytest

from app.models import PersonType, TaxIdType
from app.services.tax_id import (
    compute_cif_control,
    compute_nif_letter,
    normalize_tax_id,
    validate_cif,
    validate_nie,
    validate_nif,
    validate_tax_id,
)

VALID_DNI = ["12345678Z", "00000000T", "99999999R", "00000001R"]
VALID_SPECIAL_NIF = ["K1234567L", "L1234567L", "M1234567L"]
VALID_NIE = ["X1234567L", "Y1234567X", "Z1234567R"]
# Published / hand-verified examples. P0800000B and Q2826000H are public-body CIFs (letter control),
# A58818501 and B65410011 use a digit control.
VALID_CIF = ["A58818501", "B65410011", "Q2826000H", "P0800000B", "B12345674"]


class TestComputeFunctions:
    @pytest.mark.parametrize(
        ("number", "letter"),
        [(0, "T"), (1, "R"), (22, "E"), (23, "T"), (12345678, "Z"), (99999999, "R"), (1234567, "L")],
    )
    def test_nif_letter(self, number, letter):
        assert compute_nif_letter(number) == letter

    @pytest.mark.parametrize(
        ("org", "digits", "expected"),
        [
            ("B", "1234567", ("4", "D")),
            ("A", "5881850", ("1", "A")),
            ("B", "6541001", ("1", "A")),
            ("Q", "2826000", ("8", "H")),
            ("P", "0800000", ("2", "B")),
            ("B", "0000000", ("0", "J")),
        ],
    )
    def test_cif_control(self, org, digits, expected):
        assert compute_cif_control(org, digits) == expected

    def test_cif_control_rejects_bad_input(self):
        with pytest.raises(ValueError):
            compute_cif_control("B", "12345")
        with pytest.raises(ValueError):
            compute_cif_control("B", "12345AB")
        with pytest.raises(ValueError):
            compute_cif_control("X", "1234567")

    def test_cif_digit_doubling_sums_digits(self):
        # Odd positions 9,9,9,9 double to 18 -> 1+8 = 9 each (36); even positions 0,0,0 add nothing -> control 4.
        assert compute_cif_control("B", "9090909") == ("4", "D")


class TestNormalize:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("12345678z", "12345678Z"),
            (" 12.345.678-Z ", "12345678Z"),
            ("ES B65410011", "B65410011"),
            ("ESB-65410011", "B65410011"),
            ("es12345678z", "12345678Z"),
            ("B 65410011", "B65410011"),
            ("", ""),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_tax_id(raw) == expected

    def test_es_prefix_is_not_stripped_from_nine_char_ids(self):
        # "E" is a valid CIF organisation letter; a 9-char id starting with "ES" is not prefixed.
        assert normalize_tax_id("ES1234567") == "ES1234567"


class TestNif:
    @pytest.mark.parametrize("value", VALID_DNI + VALID_SPECIAL_NIF)
    def test_valid(self, value):
        assert validate_nif(value)

    @pytest.mark.parametrize("value", ["12345678A", "12345678", "1234567Z", "123456789Z", "ABCDEFGHI", "K1234567A", ""])
    def test_invalid(self, value):
        assert not validate_nif(value)

    def test_accepts_formatting_noise(self):
        assert validate_nif("12.345.678-z")
        assert validate_nif("ES12345678Z")


class TestNie:
    @pytest.mark.parametrize("value", VALID_NIE)
    def test_valid(self, value):
        assert validate_nie(value)

    @pytest.mark.parametrize("value", ["X1234567A", "A1234567L", "X123456L", "1234567L", "T1234567L"])
    def test_invalid(self, value):
        assert not validate_nie(value)

    def test_prefix_changes_the_letter(self):
        assert validate_nie("X1234567L")
        assert not validate_nie("Y1234567L")


class TestCif:
    @pytest.mark.parametrize("value", VALID_CIF)
    def test_valid(self, value):
        assert validate_cif(value)

    def test_wrong_digit(self):
        assert not validate_cif("B65410012")
        assert not validate_cif("A58818502")

    @pytest.mark.parametrize("value", ["Q2826000", "I12345674", "O12345674", "B1234567", "B123456744", "12345674B"])
    def test_bad_format(self, value):
        assert not validate_cif(value)

    @pytest.mark.parametrize("org", list("PQRSNW"))
    def test_letter_only_entities_reject_digit_control(self, org):
        digit, letter = compute_cif_control(org, "1234567")
        assert validate_cif(f"{org}1234567{letter}")
        assert not validate_cif(f"{org}1234567{digit}")

    @pytest.mark.parametrize("org", list("ABEH"))
    def test_digit_only_entities_reject_letter_control(self, org):
        digit, letter = compute_cif_control(org, "1234567")
        assert validate_cif(f"{org}1234567{digit}")
        assert not validate_cif(f"{org}1234567{letter}")

    @pytest.mark.parametrize("org", list("CDFGJUV"))
    def test_other_entities_accept_both(self, org):
        digit, letter = compute_cif_control(org, "1234567")
        assert validate_cif(f"{org}1234567{digit}")
        assert validate_cif(f"{org}1234567{letter}")

    def test_control_letter_j_for_zero_digit(self):
        assert validate_cif("G0000000J")
        assert validate_cif("G00000000")
        assert not validate_cif("P00000000")
        assert validate_cif("P0000000J")


class TestValidateTaxId:
    @pytest.mark.parametrize("value", VALID_DNI + VALID_SPECIAL_NIF)
    def test_nif(self, value):
        result = validate_tax_id(value)
        assert result.is_valid
        assert result.reason is None
        assert result.tax_id_type is TaxIdType.NIF
        assert result.person_type is PersonType.INDIVIDUAL
        assert result.normalized == value

    @pytest.mark.parametrize("value", VALID_NIE)
    def test_nie(self, value):
        result = validate_tax_id(value)
        assert result.is_valid
        assert result.tax_id_type is TaxIdType.NIE
        assert result.person_type is PersonType.INDIVIDUAL

    @pytest.mark.parametrize("value", VALID_CIF)
    def test_cif(self, value):
        result = validate_tax_id(value)
        assert result.is_valid
        assert result.reason is None
        assert result.tax_id_type is TaxIdType.CIF
        assert result.person_type is PersonType.LEGAL_ENTITY

    def test_normalizes_and_strips_country_prefix(self):
        result = validate_tax_id("ES-b 65410011")
        assert result.is_valid
        assert result.normalized == "B65410011"

    def test_nif_wrong_letter_message(self):
        result = validate_tax_id("12345678A")
        assert not result.is_valid
        assert result.tax_id_type is TaxIdType.NIF
        assert result.reason == "Letra de control incorrecta: se esperaba 'Z' y se encontró 'A'"

    def test_nie_wrong_letter_message(self):
        result = validate_tax_id("X1234567A")
        assert not result.is_valid
        assert result.tax_id_type is TaxIdType.NIE
        assert result.reason == "Letra de control incorrecta: se esperaba 'L' y se encontró 'A'"

    def test_cif_wrong_digit_message(self):
        result = validate_tax_id("B12345678")
        assert not result.is_valid
        assert result.tax_id_type is TaxIdType.CIF
        assert result.person_type is PersonType.LEGAL_ENTITY
        assert result.reason == "Dígito de control incorrecto: se esperaba '4' y se encontró '8'"

    def test_cif_wrong_letter_message(self):
        result = validate_tax_id("P0800000C")
        assert not result.is_valid
        assert result.reason == "Letra de control incorrecta: se esperaba 'B' y se encontró 'C'"

    def test_cif_flexible_control_message(self):
        result = validate_tax_id("G12345675")
        assert not result.is_valid
        assert result.reason == "Carácter de control incorrecto: se esperaba '4' o 'D' y se encontró '5'"

    def test_bad_format(self):
        result = validate_tax_id("1234")
        assert not result.is_valid
        assert result.tax_id_type is TaxIdType.NIF
        assert "Formato" in result.reason

    @pytest.mark.parametrize("value", ["T1234567L", "I12345674"])
    def test_unknown_type(self, value):
        result = validate_tax_id(value)
        assert not result.is_valid
        assert result.tax_id_type in (TaxIdType.UNKNOWN,)
        assert result.reason

    @pytest.mark.parametrize("value", [None, "", "   ", "--"])
    def test_missing(self, value):
        result = validate_tax_id(value)
        assert not result.is_valid
        assert result.tax_id_type is TaxIdType.UNKNOWN
        assert result.normalized == ""
        assert "No se ha indicado" in result.reason

    def test_result_is_immutable(self):
        result = validate_tax_id("12345678Z")
        with pytest.raises(AttributeError):
            result.is_valid = False
