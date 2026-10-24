"""Facturae 3.2.2 export (FACe-ready XML) with optional XAdES-EPES enveloped signature."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from lxml import etree

from app.core.config import Settings, get_settings
from app.core.money import to_decimal
from app.models import AuditStatus, Invoice, InvoiceLine, PersonType
from app.services.iban import is_valid_iban, normalize_iban
from app.services.tax_id import normalize_tax_id

if TYPE_CHECKING:  # cryptography is only needed for signing and is imported lazily
    from cryptography import x509

logger = logging.getLogger(__name__)

FACTURAE_NAMESPACE = "http://www.facturae.gob.es/formato/Versiones/Facturaev3_2_2.xml"
XMLDSIG_NAMESPACE = "http://www.w3.org/2000/09/xmldsig#"
SCHEMA_VERSION = "3.2.2"

SIGNATURE_POLICY_IDENTIFIER = (
    "http://www.facturae.es/politica_de_firma_formato_facturae/politica_de_firma_formato_facturae_v3_1.pdf"
)
SIGNATURE_POLICY_DESCRIPTION = "Política de Firma FacturaE v3.1"
# Base64 SHA-1 of the published policy document (the digest FACe expects in SigPolicyHash).
SIGNATURE_POLICY_DIGEST_SHA1 = "Ohixl6upD6av8N7pEvDABhEL6hM="

TAX_TYPE_VAT = "01"
TAX_TYPE_IRPF = "04"
PAYMENT_MEANS_TRANSFER = "04"
SPECIAL_EVENT_EXEMPT = "01"
ROLE_ACCOUNTING_OFFICE = "01"
ROLE_MANAGING_BODY = "02"
ROLE_PROCESSING_UNIT = "03"

_CENT = Decimal("0.01")
_MICRO = Decimal("0.000001")
_HUNDRED = Decimal(100)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPANISH_SURNAME_PARTICLES = frozenset(
    {"de", "del", "la", "las", "los", "el", "y", "e", "van", "von", "da", "do", "dos"}
)
_INDIVIDUAL_TAX_ID = re.compile(r"^(\d{8}[A-Z]|[XYZKLM]\d{7}[A-Z])$")

# Maximum lengths imposed by the Facturae 3.2.2 XSD (see tests/xsd/Facturaev3_2_2.xsd).
MAX_ADDRESS = 80
MAX_TOWN = 50
MAX_PROVINCE = 20
MAX_CORPORATE_NAME = 80
MAX_INDIVIDUAL_NAME = 40
MAX_POST_CODE_AND_TOWN = 50


class FacturaeExportError(Exception):
    """Raised when an invoice cannot be exported (or signed) as Facturae."""


@dataclass(frozen=True)
class _TaxGroup:
    rate: Decimal
    base: Decimal
    amount: Decimal


@dataclass(frozen=True)
class _PartyData:
    role: str
    tax_id: str
    person_type: str
    name: str
    address: str | None
    postal_code: str | None
    city: str | None
    province: str | None
    country_code: str


_dec = to_decimal


def _fmt(value: Decimal, quantum: Decimal) -> str:
    return format(value.quantize(quantum, rounding=ROUND_HALF_UP) + Decimal(0), "f")


def _money(value: Decimal) -> str:
    return _fmt(value, _CENT)


def _micro(value: Decimal) -> str:
    return _fmt(value, _MICRO)


def _quantity(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _clean(value: str | None, max_len: int, *, multiline: bool = False) -> str:
    text = _CONTROL_CHARS.sub("", value or "")
    text = text.strip() if multiline else re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def _infer_person_type(tax_id: str) -> str:
    return PersonType.INDIVIDUAL.value if _INDIVIDUAL_TAX_ID.match(tax_id) else PersonType.LEGAL_ENTITY.value


def _split_trailing_surname(tokens: list[str]) -> tuple[str, list[str]]:
    """Pop the last surname (with its leading particles such as 'de la') from a token list."""
    start = len(tokens) - 1
    while start > 0 and tokens[start - 1].lower() in _SPANISH_SURNAME_PARTICLES:
        start -= 1
    return " ".join(tokens[start:]), tokens[:start]


def _length_problems(party: _PartyData) -> list[str]:
    """Fields longer than the XSD allows: they are reported instead of silently truncated."""
    limits = [
        ("dirección", _clean(party.address, 10_000), MAX_ADDRESS),
        ("población", _clean(party.city, 10_000), MAX_TOWN),
        ("provincia", _clean(party.province, 10_000), MAX_PROVINCE),
    ]
    if party.person_type != PersonType.INDIVIDUAL.value:
        limits.append(("nombre", _clean(party.name, 10_000), MAX_CORPORATE_NAME))
    if party.country_code != "ESP":
        postal_and_town = _clean(f"{_clean(party.postal_code, 10_000)} {_clean(party.city, 10_000)}", 10_000)
        limits.append(("código postal y población", postal_and_town, MAX_POST_CODE_AND_TOWN))
    return [
        f"{party.role}: {label} «{value}» tiene {len(value)} caracteres y Facturae admite como máximo {limit}"
        for label, value, limit in limits
        if len(value) > limit
    ]


def split_individual_name(full_name: str) -> tuple[str, str, str | None]:
    """Split a Spanish personal name into (name, first surname, second surname).

    Accepts "Apellido1 Apellido2, Nombre" or "Nombre Apellido1 Apellido2"; compound surnames
    introduced by particles ("de la Cruz") are kept together.
    """
    text = _clean(full_name, 200)
    if "," in text:
        surnames_part, _, given = text.partition(",")
        given_name = given.strip()
        surname_tokens = surnames_part.split()
        groups: list[str] = []
        pending: list[str] = []
        for token in surname_tokens:
            pending.append(token)
            if token.lower() not in _SPANISH_SURNAME_PARTICLES:
                groups.append(" ".join(pending))
                pending = []
        if pending:
            groups.append(" ".join(pending))
        if given_name and groups:
            return given_name, groups[0], " ".join(groups[1:]) or None
    tokens = text.split()
    if len(tokens) < 2:
        raise FacturaeExportError(
            f"No se pueden separar nombre y apellidos de la persona física «{text}»; "
            "Facturae exige nombre y primer apellido."
        )
    if len(tokens) == 2:
        return tokens[0], tokens[1], None
    second, rest = _split_trailing_surname(tokens)
    first, rest = _split_trailing_surname(rest) if rest else ("", [])
    if not rest:
        raise FacturaeExportError(f"No se pueden separar nombre y apellidos de la persona física «{text}».")
    return " ".join(rest), first, second


class FacturaeExporter:
    """Builds Facturae 3.2.2 documents from audited invoices."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ public API

    def filename_for(self, invoice: Invoice) -> str:
        tax_id = normalize_tax_id(invoice.supplier_tax_id) or "SIN-NIF"
        raw = f"facturae_{tax_id}_{invoice.full_number}"
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
        return f"{safe}.xml"

    def build_tree(self, invoice: Invoice) -> etree._Element:
        self._ensure_exportable(invoice)
        lines = list(invoice.lines)
        vat_groups = self._vat_groups(lines, _dec(invoice.total_vat))
        irpf_groups = self._irpf_groups(lines, _dec(invoice.total_irpf))
        self._ensure_totals(invoice, lines)

        seller = self._seller_data(invoice)
        buyer = self._buyer_data(invoice)
        self._ensure_party_data(seller, buyer)

        root = etree.Element(
            f"{{{FACTURAE_NAMESPACE}}}Facturae", nsmap={"fe": FACTURAE_NAMESPACE, "ds": XMLDSIG_NAMESPACE}
        )
        self._add_file_header(root, invoice, seller)
        parties = etree.SubElement(root, "Parties")
        self._add_party(etree.SubElement(parties, "SellerParty"), seller)
        self._add_party(etree.SubElement(parties, "BuyerParty"), buyer, centres=self._centres(invoice))
        invoices = etree.SubElement(root, "Invoices")
        self._add_invoice(invoices, invoice, lines, vat_groups, irpf_groups)
        return root

    def to_xml(self, invoice: Invoice, sign: bool | None = None) -> bytes:
        root = self.build_tree(invoice)
        etree.indent(root, space="  ")
        should_sign = self.settings.facturae_cert_path is not None if sign is None else sign
        if should_sign:
            root = self._sign(root)
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=False)

    # ------------------------------------------------------------------ validation helpers

    @staticmethod
    def _ensure_exportable(invoice: Invoice) -> None:
        if invoice.is_synthetic:
            raise FacturaeExportError(
                "Los datos de esta factura son sintéticos (modo demostración) y no corresponden al documento: "
                "no se puede exportar a Facturae."
            )
        if invoice.audit_status != AuditStatus.VALIDATED:
            current = invoice.audit_status.value if invoice.audit_status else "desconocido"
            raise FacturaeExportError(
                f"Solo se pueden exportar a Facturae las facturas validadas (estado actual: {current})."
            )
        if not invoice.lines:
            raise FacturaeExportError("La factura no tiene líneas y no se puede exportar a Facturae.")
        if _dec(invoice.total_amount) < 0 or _dec(invoice.tax_base) < 0:
            raise FacturaeExportError("Las facturas rectificativas no están soportadas todavía.")
        if (invoice.currency or "").upper() != "EUR":
            raise FacturaeExportError(f"FACe solo admite facturas en EUR (la factura está en {invoice.currency}).")
        if len(invoice.invoice_number or "") > 20 or len(invoice.invoice_series or "") > 20:
            raise FacturaeExportError("Facturae limita el número y la serie de factura a 20 caracteres cada uno.")
        if invoice.payment_iban and not is_valid_iban(invoice.payment_iban):
            raise FacturaeExportError("El IBAN de la factura no es válido.")

    @staticmethod
    def _ensure_totals(invoice: Invoice, lines: list[InvoiceLine]) -> None:
        base_sum = sum((_dec(line.line_base) for line in lines), Decimal(0))
        if base_sum != _dec(invoice.tax_base):
            raise FacturaeExportError(
                f"La suma de bases de las líneas ({_money(base_sum)} €) no coincide con la base imponible "
                f"de la factura ({_money(_dec(invoice.tax_base))} €)."
            )
        expected = _dec(invoice.tax_base) + _dec(invoice.total_vat) - _dec(invoice.total_irpf)
        if expected != _dec(invoice.total_amount):
            raise FacturaeExportError(
                f"El total de la factura ({_money(_dec(invoice.total_amount))} €) no coincide con "
                f"base + IVA - IRPF ({_money(expected)} €)."
            )

    @staticmethod
    def _group_taxes(
        label: str, lines: list[InvoiceLine], rate_of, amount_of, declared_total: Decimal, *, include_zero: bool
    ) -> list[_TaxGroup]:
        """Group lines by rate and pick the rounding (per group or per line) that matches the declared total."""
        by_rate: dict[Decimal, list[InvoiceLine]] = {}
        for line in lines:
            rate = _dec(rate_of(line)).quantize(_CENT)
            if rate > 0 or include_zero:
                by_rate.setdefault(rate, []).append(line)
        if not by_rate:
            if declared_total != 0:
                raise FacturaeExportError(
                    f"La factura declara {label} por {_money(declared_total)} € pero ninguna línea lo aplica."
                )
            return []

        grouped_rounding: list[_TaxGroup] = []
        per_line_rounding: list[_TaxGroup] = []
        for rate in sorted(by_rate, reverse=True):
            group_lines = by_rate[rate]
            base = sum((_dec(line.line_base) for line in group_lines), Decimal(0))
            grouped_rounding.append(_TaxGroup(rate, base, (base * rate / _HUNDRED).quantize(_CENT, ROUND_HALF_UP)))
            per_line_rounding.append(
                _TaxGroup(rate, base, sum((_dec(amount_of(line)) for line in group_lines), Decimal(0)))
            )
        for candidate in (grouped_rounding, per_line_rounding):
            if sum((group.amount for group in candidate), Decimal(0)) == declared_total:
                return candidate
        raise FacturaeExportError(
            f"El total de {label} declarado ({_money(declared_total)} €) no coincide con el calculado "
            f"por grupos de tipo ({_money(sum((g.amount for g in grouped_rounding), Decimal(0)))} €) "
            f"ni por líneas ({_money(sum((g.amount for g in per_line_rounding), Decimal(0)))} €)."
        )

    def _vat_groups(self, lines: list[InvoiceLine], declared_total: Decimal) -> list[_TaxGroup]:
        return self._group_taxes(
            "IVA", lines, lambda ln: ln.vat_rate, lambda ln: ln.vat_amount, declared_total, include_zero=True
        )

    def _irpf_groups(self, lines: list[InvoiceLine], declared_total: Decimal) -> list[_TaxGroup]:
        return self._group_taxes(
            "IRPF", lines, lambda ln: ln.irpf_rate, lambda ln: ln.irpf_amount, declared_total, include_zero=False
        )

    # ------------------------------------------------------------------ parties

    @staticmethod
    def _seller_data(invoice: Invoice) -> _PartyData:
        supplier = invoice.supplier
        tax_id = normalize_tax_id(invoice.supplier_tax_id or supplier.tax_id)
        return _PartyData(
            role="emisor",
            tax_id=tax_id,
            person_type=supplier.person_type.value if supplier.person_type else _infer_person_type(tax_id),
            name=supplier.name,
            address=supplier.address,
            postal_code=supplier.postal_code,
            city=supplier.city,
            province=supplier.province,
            country_code=(supplier.country_code or "ESP").upper(),
        )

    @staticmethod
    def _buyer_data(invoice: Invoice) -> _PartyData:
        tax_id = normalize_tax_id(invoice.buyer_tax_id)
        return _PartyData(
            role="receptor",
            tax_id=tax_id,
            person_type=_infer_person_type(tax_id),
            name=invoice.buyer_name or "",
            address=invoice.buyer_address,
            postal_code=invoice.buyer_postal_code,
            city=invoice.buyer_city,
            province=invoice.buyer_province,
            country_code=(invoice.buyer_country_code or "ESP").upper(),
        )

    @staticmethod
    def _ensure_party_data(*parties: _PartyData) -> None:
        problems: list[str] = []
        for party in parties:
            fields = {
                "NIF/CIF": party.tax_id,
                "nombre": party.name,
                "dirección": party.address,
                "código postal": party.postal_code,
                "población": party.city,
                "provincia": party.province,
            }
            missing = [label for label, value in fields.items() if not (value or "").strip()]
            if missing:
                problems.append(f"{party.role}: falta {', '.join(missing)}")
            problems.extend(_length_problems(party))
            if len(party.country_code) != 3 or not party.country_code.isalpha():
                problems.append(f"{party.role}: el código de país «{party.country_code}» debe ser ISO 3166-1 alfa-3")
            elif party.country_code == "ESP" and not re.fullmatch(r"\d{5}", (party.postal_code or "").strip()):
                problems.append(f"{party.role}: el código postal español debe tener 5 dígitos")
        if problems:
            raise FacturaeExportError("Datos insuficientes para generar Facturae: " + "; ".join(problems) + ".")

    @staticmethod
    def _centres(invoice: Invoice) -> list[tuple[str, str, str]]:
        candidates = [
            (invoice.dir3_accounting_office, ROLE_ACCOUNTING_OFFICE, "Oficina contable"),
            (invoice.dir3_managing_body, ROLE_MANAGING_BODY, "Órgano gestor"),
            (invoice.dir3_processing_unit, ROLE_PROCESSING_UNIT, "Unidad tramitadora"),
        ]
        centres = [(code.strip(), role, label) for code, role, label in candidates if code and code.strip()]
        for code, _role, label in centres:
            if len(code) > 10:
                raise FacturaeExportError(f"El código DIR3 de {label} ({code}) supera los 10 caracteres de Facturae.")
        return centres

    def _add_party(
        self, parent: etree._Element, party: _PartyData, centres: list[tuple[str, str, str]] | None = None
    ) -> None:
        is_spain = party.country_code == "ESP"
        identification = etree.SubElement(parent, "TaxIdentification")
        self._text(identification, "PersonTypeCode", party.person_type)
        self._text(identification, "ResidenceTypeCode", "R" if is_spain else "E")
        self._text(identification, "TaxIdentificationNumber", _clean(party.tax_id, 30))

        if centres:
            wrapper = etree.SubElement(parent, "AdministrativeCentres")
            for code, role, description in centres:
                centre = etree.SubElement(wrapper, "AdministrativeCentre")
                self._text(centre, "CentreCode", code)
                self._text(centre, "RoleTypeCode", role)
                self._add_address(centre, party)
                self._text(centre, "CentreDescription", description)

        if party.person_type == PersonType.INDIVIDUAL.value:
            name, first_surname, second_surname = split_individual_name(party.name)
            for label, part in (("nombre", name), ("primer apellido", first_surname), ("segundo apellido", second_surname)):
                if part and len(_clean(part, 10_000)) > MAX_INDIVIDUAL_NAME:
                    raise FacturaeExportError(
                        f"{party.role}: el {label} «{part}» supera los {MAX_INDIVIDUAL_NAME} caracteres de Facturae."
                    )
            individual = etree.SubElement(parent, "Individual")
            self._text(individual, "Name", _clean(name, 40))
            self._text(individual, "FirstSurname", _clean(first_surname, 40))
            if second_surname:
                self._text(individual, "SecondSurname", _clean(second_surname, 40))
            self._add_address(individual, party)
        else:
            legal = etree.SubElement(parent, "LegalEntity")
            self._text(legal, "CorporateName", _clean(party.name, 80))
            self._add_address(legal, party)

    def _add_address(self, parent: etree._Element, party: _PartyData) -> None:
        address = _clean(party.address, 80)
        postal_code = _clean(party.postal_code, 10)
        city = _clean(party.city, 50)
        province = _clean(party.province, 20)
        if party.country_code == "ESP":
            node = etree.SubElement(parent, "AddressInSpain")
            self._text(node, "Address", address)
            self._text(node, "PostCode", postal_code)
            self._text(node, "Town", city)
        else:
            node = etree.SubElement(parent, "OverseasAddress")
            self._text(node, "Address", address)
            self._text(node, "PostCodeAndTown", _clean(f"{postal_code} {city}", 50))
        self._text(node, "Province", province)
        self._text(node, "CountryCode", party.country_code)

    # ------------------------------------------------------------------ document sections

    @staticmethod
    def _text(parent: etree._Element, tag: str, value: str) -> etree._Element:
        node = etree.SubElement(parent, tag)
        node.text = value
        return node

    def _amount(self, parent: etree._Element, tag: str, value: Decimal) -> None:
        wrapper = etree.SubElement(parent, tag)
        self._text(wrapper, "TotalAmount", _money(value))

    def _add_file_header(self, root: etree._Element, invoice: Invoice, seller: _PartyData) -> None:
        total = _dec(invoice.total_amount)
        header = etree.SubElement(root, "FileHeader")
        self._text(header, "SchemaVersion", SCHEMA_VERSION)
        self._text(header, "Modality", "I")
        self._text(header, "InvoiceIssuerType", "EM")
        batch = etree.SubElement(header, "Batch")
        identifier = f"{seller.tax_id}{invoice.invoice_series or ''}{invoice.invoice_number}"
        self._text(batch, "BatchIdentifier", _clean(identifier, 70))
        self._text(batch, "InvoicesCount", "1")
        self._amount(batch, "TotalInvoicesAmount", total)
        self._amount(batch, "TotalOutstandingAmount", total)
        self._amount(batch, "TotalExecutableAmount", total)
        self._text(batch, "InvoiceCurrencyCode", "EUR")

    def _add_invoice(
        self,
        parent: etree._Element,
        invoice: Invoice,
        lines: list[InvoiceLine],
        vat_groups: list[_TaxGroup],
        irpf_groups: list[_TaxGroup],
    ) -> None:
        node = etree.SubElement(parent, "Invoice")

        header = etree.SubElement(node, "InvoiceHeader")
        self._text(header, "InvoiceNumber", _clean(invoice.invoice_number, 20))
        if invoice.invoice_series:
            self._text(header, "InvoiceSeriesCode", _clean(invoice.invoice_series, 20))
        self._text(header, "InvoiceDocumentType", "FC")
        self._text(header, "InvoiceClass", "OO")

        issue = etree.SubElement(node, "InvoiceIssueData")
        self._text(issue, "IssueDate", self._iso(invoice.issue_date))
        if invoice.operation_date:
            self._text(issue, "OperationDate", self._iso(invoice.operation_date))
        self._text(issue, "InvoiceCurrencyCode", "EUR")
        self._text(issue, "TaxCurrencyCode", "EUR")
        self._text(issue, "LanguageName", "es")

        outputs = etree.SubElement(node, "TaxesOutputs")
        for group in vat_groups:
            self._add_tax(outputs, TAX_TYPE_VAT, group.rate, group.base, group.amount)
        if irpf_groups:
            withheld = etree.SubElement(node, "TaxesWithheld")
            for group in irpf_groups:
                self._add_tax(withheld, TAX_TYPE_IRPF, group.rate, group.base, group.amount)

        self._add_totals(node, invoice)

        items = etree.SubElement(node, "Items")
        for line in lines:
            self._add_line(items, line)

        # A credit transfer without the account to credit is invalid for FACe, so payment data is only
        # written when both the due date and the IBAN are known.
        iban = normalize_iban(invoice.payment_iban)
        if invoice.due_date and iban:
            installment = etree.SubElement(etree.SubElement(node, "PaymentDetails"), "Installment")
            self._text(installment, "InstallmentDueDate", self._iso(invoice.due_date))
            self._text(installment, "InstallmentAmount", _money(_dec(invoice.total_amount)))
            self._text(installment, "PaymentMeans", PAYMENT_MEANS_TRANSFER)
            self._text(etree.SubElement(installment, "AccountToBeCredited"), "IBAN", iban)

    @staticmethod
    def _iso(value: date) -> str:
        return value.isoformat()

    def _add_tax(self, parent: etree._Element, tax_type: str, rate: Decimal, base: Decimal, amount: Decimal) -> None:
        tax = etree.SubElement(parent, "Tax")
        self._text(tax, "TaxTypeCode", tax_type)
        self._text(tax, "TaxRate", _money(rate))
        self._amount(tax, "TaxableBase", base)
        self._amount(tax, "TaxAmount", amount)

    def _add_totals(self, parent: etree._Element, invoice: Invoice) -> None:
        base = _dec(invoice.tax_base)
        total = _dec(invoice.total_amount)
        totals = etree.SubElement(parent, "InvoiceTotals")
        self._text(totals, "TotalGrossAmount", _money(base))
        self._text(totals, "TotalGrossAmountBeforeTaxes", _money(base))
        self._text(totals, "TotalTaxOutputs", _money(_dec(invoice.total_vat)))
        self._text(totals, "TotalTaxesWithheld", _money(_dec(invoice.total_irpf)))
        self._text(totals, "InvoiceTotal", _money(total))
        self._text(totals, "TotalOutstandingAmount", _money(total))
        self._text(totals, "TotalExecutableAmount", _money(total))

    def _add_line(self, parent: etree._Element, line: InvoiceLine) -> None:
        quantity = _dec(line.quantity)
        unit_price = _dec(line.unit_price)
        base = _dec(line.line_base)
        total_cost = (quantity * unit_price).quantize(_MICRO, ROUND_HALF_UP)
        unit = line.unit_of_measure if re.fullmatch(r"(0[1-9]|[12]\d|3[0-6])", line.unit_of_measure or "") else "01"

        node = etree.SubElement(parent, "InvoiceLine")
        self._text(node, "ItemDescription", _clean(line.concept, 2500, multiline=True))
        self._text(node, "Quantity", _quantity(quantity))
        self._text(node, "UnitOfMeasure", unit)
        self._text(node, "UnitPriceWithoutTax", _micro(unit_price))
        self._text(node, "TotalCost", _micro(total_cost))
        discount_rate = _dec(line.discount_rate)
        if discount_rate > 0:
            discount = etree.SubElement(etree.SubElement(node, "DiscountsAndRebates"), "Discount")
            self._text(discount, "DiscountReason", "Descuento")
            self._text(discount, "DiscountRate", _money(discount_rate))
            self._text(discount, "DiscountAmount", _micro(total_cost - base))
        self._text(node, "GrossAmount", _money(base))

        irpf_rate = _dec(line.irpf_rate)
        if irpf_rate > 0:
            withheld = etree.SubElement(node, "TaxesWithheld")
            self._add_tax(withheld, TAX_TYPE_IRPF, irpf_rate, base, _dec(line.irpf_amount))
        outputs = etree.SubElement(node, "TaxesOutputs")
        vat_rate = _dec(line.vat_rate)
        self._add_tax(outputs, TAX_TYPE_VAT, vat_rate, base, _dec(line.vat_amount))
        if vat_rate == 0:
            event = etree.SubElement(node, "SpecialTaxableEvent")
            self._text(event, "SpecialTaxableEventCode", SPECIAL_EVENT_EXEMPT)
            self._text(event, "SpecialTaxableEventReason", "Operación exenta de IVA")

    # ------------------------------------------------------------------ signing

    def _load_credentials(self) -> tuple[object, x509.Certificate, list[x509.Certificate]]:
        cert_path = self.settings.facturae_cert_path
        if cert_path is None:
            raise FacturaeExportError(
                "No se puede firmar la factura: no hay certificado configurado (FACTURAE_CERT_PATH)."
            )
        from cryptography.hazmat.primitives.serialization import pkcs12

        path = Path(cert_path)
        if not path.is_file():
            logger.error("Facturae signing certificate not found: %s", path)
            raise FacturaeExportError("No se puede firmar la factura: el certificado configurado no existe en el servidor.")
        password = self.settings.secret("facturae_cert_password")
        try:
            key, certificate, extra = pkcs12.load_key_and_certificates(
                path.read_bytes(), password.encode("utf-8") if password else None
            )
        except (ValueError, TypeError, OSError) as exc:
            logger.error("Could not open the PKCS#12 certificate %s: %s", path.name, exc)
            raise FacturaeExportError(
                "No se pudo abrir el certificado PKCS#12 configurado (¿contraseña incorrecta?)."
            ) from exc
        if key is None or certificate is None:
            raise FacturaeExportError("El certificado configurado no contiene clave privada y certificado.")
        return key, certificate, list(extra)

    def _sign(self, root: etree._Element) -> etree._Element:
        try:
            from signxml import DigestAlgorithm, SignatureMethod, methods
            from signxml.xades import XAdESSignaturePolicy, XAdESSigner
        except ImportError as exc:
            raise FacturaeExportError(
                "No se puede firmar la factura: la instalación de signxml no incluye soporte XAdES."
            ) from exc

        from cryptography.hazmat.primitives.asymmetric import ec, rsa

        key, certificate, extra = self._load_credentials()
        if isinstance(key, rsa.RSAPrivateKey):
            signature_method = SignatureMethod.RSA_SHA256
        elif isinstance(key, ec.EllipticCurvePrivateKey):
            signature_method = SignatureMethod.ECDSA_SHA256
        else:
            raise FacturaeExportError("Tipo de clave del certificado no soportado: se requiere RSA o ECDSA.")

        signer_class = _build_signer_class(XAdESSigner)
        policy = XAdESSignaturePolicy(
            Identifier=SIGNATURE_POLICY_IDENTIFIER,
            Description=SIGNATURE_POLICY_DESCRIPTION,
            DigestMethod=DigestAlgorithm.SHA1,
            DigestValue=SIGNATURE_POLICY_DIGEST_SHA1,
        )
        signer = signer_class(
            signature_policy=policy,
            method=methods.enveloped,
            signature_algorithm=signature_method,
            digest_algorithm=DigestAlgorithm.SHA256,
            c14n_algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315",
        )
        try:
            return signer.sign(root, key=key, cert=[certificate, *extra])
        except Exception as exc:  # signxml/cryptography raise heterogeneous errors; surface them uniformly
            logger.error("XAdES-EPES signing failed: %s", exc)
            raise FacturaeExportError("Error al firmar la factura con XAdES-EPES.") from exc


def _build_signer_class(base: type) -> type:
    """Return an XAdES signer emitting the XAdES 1.3.2 `SigningCertificate` (v1) block required by Facturae."""
    from base64 import b64encode

    from cryptography import x509
    from lxml.etree import SubElement
    from signxml.util import ds_tag, xades_tag

    class FacturaeXAdESSigner(base):  # type: ignore[misc, valid-type]
        def add_signing_certificate(self, signed_signature_properties, sig_root, signing_settings):
            from cryptography.hazmat.primitives.serialization import Encoding

            container = SubElement(signed_signature_properties, xades_tag("SigningCertificate"), nsmap=self.namespaces)
            certificate = signing_settings.cert_chain[0]
            if not isinstance(certificate, x509.Certificate):
                certificate = x509.load_pem_x509_certificate(
                    certificate if isinstance(certificate, bytes) else str(certificate).encode()
                )
            node = SubElement(container, xades_tag("Cert"), nsmap=self.namespaces)
            digest = SubElement(node, xades_tag("CertDigest"), nsmap=self.namespaces)
            SubElement(digest, ds_tag("DigestMethod"), nsmap=self.namespaces, Algorithm=self.digest_alg.value)
            value = SubElement(digest, ds_tag("DigestValue"), nsmap=self.namespaces)
            value.text = b64encode(self._get_digest(certificate.public_bytes(Encoding.DER), self.digest_alg)).decode()
            issuer_serial = SubElement(node, xades_tag("IssuerSerial"), nsmap=self.namespaces)
            SubElement(
                issuer_serial, ds_tag("X509IssuerName"), nsmap=self.namespaces
            ).text = certificate.issuer.rfc4514_string()
            SubElement(issuer_serial, ds_tag("X509SerialNumber"), nsmap=self.namespaces).text = str(
                certificate.serial_number
            )

    return FacturaeXAdESSigner
