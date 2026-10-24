"""Tests for the Facturae 3.2.2 exporter: structure, amounts, parties, filename and XAdES signing."""

import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from lxml import etree

from app.core.config import Settings
from app.models import AuditStatus, Invoice, InvoiceLine, PersonType, Supplier, TaxIdType
from app.services.face_exporter import (
    FACTURAE_NAMESPACE,
    SIGNATURE_POLICY_IDENTIFIER,
    FacturaeExporter,
    FacturaeExportError,
    split_individual_name,
)

NS = {"fe": FACTURAE_NAMESPACE, "ds": "http://www.w3.org/2000/09/xmldsig#"}
XSD_PATH = Path(__file__).parent / "xsd" / "Facturaev3_2_2.xsd"
CERT_PASSWORD = "clave-de-prueba"


def _line(
    number: int, concept: str, qty: str, price: str, vat: str, base: str, vat_amount: str, **extra
) -> InvoiceLine:
    return InvoiceLine(
        line_number=number,
        concept=concept,
        quantity=Decimal(qty),
        unit_of_measure="01",
        unit_price=Decimal(price),
        discount_rate=Decimal(extra.get("discount", "0")),
        vat_rate=Decimal(vat),
        irpf_rate=Decimal(extra.get("irpf", "0")),
        line_base=Decimal(base),
        vat_amount=Decimal(vat_amount),
        irpf_amount=Decimal(extra.get("irpf_amount", "0")),
    )


def _supplier(**overrides) -> Supplier:
    data = {
        "name": "Servicios Integrales Mediterráneo S.L.",
        "tax_id": "B12345678",
        "tax_id_type": TaxIdType.CIF,
        "tax_id_valid": True,
        "person_type": PersonType.LEGAL_ENTITY,
        "address": "Calle Mayor 12, 2º B",
        "postal_code": "46001",
        "city": "Valencia",
        "province": "Valencia",
        "country_code": "ESP",
    }
    data.update(overrides)
    return Supplier(**data)


def _invoice(supplier: Supplier, **overrides) -> Invoice:
    data = {
        "supplier": supplier,
        "invoice_number": "2024-001",
        "invoice_series": "F",
        "issue_date": date(2024, 3, 15),
        "operation_date": date(2024, 3, 10),
        "due_date": date(2024, 4, 14),
        "currency": "EUR",
        "payment_iban": "ES9121000418450200051332",
        "supplier_tax_id": supplier.tax_id,
        "buyer_name": "Ayuntamiento de Valencia",
        "buyer_tax_id": "P4625200C",
        "buyer_address": "Plaza del Ayuntamiento 1",
        "buyer_postal_code": "46002",
        "buyer_city": "Valencia",
        "buyer_province": "Valencia",
        "buyer_country_code": "ESP",
        "dir3_accounting_office": "L01462508",
        "dir3_managing_body": "L01462509",
        "dir3_processing_unit": "L01462510",
        "tax_base": Decimal("280.00"),
        "total_vat": Decimal("33.60"),
        "total_irpf": Decimal("42.00"),
        "total_amount": Decimal("271.60"),
        "audit_status": AuditStatus.VALIDATED,
    }
    data.update(overrides)
    invoice = Invoice(**data)
    invoice.lines = [
        _line(1, "Consultoría técnica", "2", "50.00", "21", "100.00", "21.00", irpf="15", irpf_amount="15.00"),
        _line(2, "Material de oficina", "3", "30.00", "10", "90.00", "9.00", irpf="15", irpf_amount="13.50"),
        _line(
            3,
            "Licencia anual",
            "1",
            "100.00",
            "4",
            "90.00",
            "3.60",
            discount="10",
            irpf="15",
            irpf_amount="13.50",
        ),
    ]
    return invoice


@pytest.fixture()
def invoice(db_session) -> Invoice:
    supplier = _supplier()
    db_session.add(supplier)
    inv = _invoice(supplier)
    db_session.add(inv)
    db_session.commit()
    return inv


@pytest.fixture(scope="module")
def xsd() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(XSD_PATH)))


def _local(element: etree._Element) -> str:
    return etree.QName(element).localname


def _children(element: etree._Element) -> list[str]:
    return [_local(child) for child in element]


def _find(root: etree._Element, path: str) -> etree._Element:
    node = root.find(path, NS)
    assert node is not None, f"missing element {path}"
    return node


def _make_pkcs12(tmp_path: Path, password: str = CERT_PASSWORD) -> tuple[Path, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "ES"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Servicios Integrales Mediterraneo"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Facturacion"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Certificado de prueba"),
        ]
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(secrets.randbits(63) + 1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    blob = pkcs12.serialize_key_and_certificates(
        b"test", key, certificate, None, serialization.BestAvailableEncryption(password.encode())
    )
    path = tmp_path / "cert.p12"
    path.write_bytes(blob)
    return path, certificate


def _settings(cert_path: Path | None = None, password: str | None = None) -> Settings:
    return Settings(facturae_cert_path=cert_path, facturae_cert_password=password, anthropic_api_key=None)


# ---------------------------------------------------------------- structure


@pytest.mark.parametrize("status", [AuditStatus.PENDING, AuditStatus.DISCREPANCY, AuditStatus.DUPLICATE])
def test_rejects_non_validated_invoice(invoice, status):
    invoice.audit_status = status
    with pytest.raises(FacturaeExportError, match="validadas"):
        FacturaeExporter(_settings()).build_tree(invoice)
    with pytest.raises(FacturaeExportError):
        FacturaeExporter(_settings()).to_xml(invoice)


def test_root_namespace_and_version(invoice):
    root = FacturaeExporter(_settings()).build_tree(invoice)
    assert root.tag == f"{{{FACTURAE_NAMESPACE}}}Facturae"
    assert root.nsmap["fe"] == FACTURAE_NAMESPACE
    assert root.nsmap["ds"] == NS["ds"]
    assert _find(root, "FileHeader/SchemaVersion").text == "3.2.2"
    assert _find(root, "FileHeader/Modality").text == "I"
    assert _find(root, "FileHeader/InvoiceIssuerType").text == "EM"


def test_top_level_and_invoice_section_order(invoice):
    root = FacturaeExporter(_settings()).build_tree(invoice)
    assert _children(root) == ["FileHeader", "Parties", "Invoices"]
    assert _children(_find(root, "Parties")) == ["SellerParty", "BuyerParty"]
    assert _children(_find(root, "Invoices/Invoice")) == [
        "InvoiceHeader",
        "InvoiceIssueData",
        "TaxesOutputs",
        "TaxesWithheld",
        "InvoiceTotals",
        "Items",
        "PaymentDetails",
    ]
    assert _children(_find(root, "Invoices/Invoice/InvoiceTotals")) == [
        "TotalGrossAmount",
        "TotalGrossAmountBeforeTaxes",
        "TotalTaxOutputs",
        "TotalTaxesWithheld",
        "InvoiceTotal",
        "TotalOutstandingAmount",
        "TotalExecutableAmount",
    ]


def test_file_header_batch(invoice):
    root = FacturaeExporter(_settings()).build_tree(invoice)
    batch = _find(root, "FileHeader/Batch")
    assert _children(batch) == [
        "BatchIdentifier",
        "InvoicesCount",
        "TotalInvoicesAmount",
        "TotalOutstandingAmount",
        "TotalExecutableAmount",
        "InvoiceCurrencyCode",
    ]
    assert _find(batch, "BatchIdentifier").text == "B12345678F2024-001"
    assert _find(batch, "InvoicesCount").text == "1"
    assert _find(batch, "TotalInvoicesAmount/TotalAmount").text == "271.60"
    assert _find(batch, "InvoiceCurrencyCode").text == "EUR"


def test_invoice_header_and_dates(invoice):
    root = FacturaeExporter(_settings()).build_tree(invoice)
    header = _find(root, "Invoices/Invoice/InvoiceHeader")
    assert _children(header) == ["InvoiceNumber", "InvoiceSeriesCode", "InvoiceDocumentType", "InvoiceClass"]
    assert _find(header, "InvoiceNumber").text == "2024-001"
    assert _find(header, "InvoiceSeriesCode").text == "F"
    assert _find(header, "InvoiceDocumentType").text == "FC"
    assert _find(header, "InvoiceClass").text == "OO"
    issue = _find(root, "Invoices/Invoice/InvoiceIssueData")
    assert _children(issue) == [
        "IssueDate",
        "OperationDate",
        "InvoiceCurrencyCode",
        "TaxCurrencyCode",
        "LanguageName",
    ]
    assert _find(issue, "IssueDate").text == "2024-03-15"
    assert _find(issue, "OperationDate").text == "2024-03-10"
    assert _find(issue, "LanguageName").text == "es"


def test_tax_groups_and_totals(invoice):
    root = FacturaeExporter(_settings()).build_tree(invoice)
    outputs = root.findall("Invoices/Invoice/TaxesOutputs/Tax", NS)
    groups = [
        (
            _find(tax, "TaxTypeCode").text,
            _find(tax, "TaxRate").text,
            _find(tax, "TaxableBase/TotalAmount").text,
            _find(tax, "TaxAmount/TotalAmount").text,
        )
        for tax in outputs
    ]
    assert groups == [
        ("01", "21.00", "100.00", "21.00"),
        ("01", "10.00", "90.00", "9.00"),
        ("01", "4.00", "90.00", "3.60"),
    ]
    withheld = root.findall("Invoices/Invoice/TaxesWithheld/Tax", NS)
    assert len(withheld) == 1
    assert _find(withheld[0], "TaxTypeCode").text == "04"
    assert _find(withheld[0], "TaxRate").text == "15.00"
    assert _find(withheld[0], "TaxableBase/TotalAmount").text == "280.00"
    assert _find(withheld[0], "TaxAmount/TotalAmount").text == "42.00"

    totals = _find(root, "Invoices/Invoice/InvoiceTotals")
    expected = {
        "TotalGrossAmount": "280.00",
        "TotalGrossAmountBeforeTaxes": "280.00",
        "TotalTaxOutputs": "33.60",
        "TotalTaxesWithheld": "42.00",
        "InvoiceTotal": "271.60",
        "TotalOutstandingAmount": "271.60",
        "TotalExecutableAmount": "271.60",
    }
    assert {_local(child): child.text for child in totals} == expected


def test_lines_and_decimal_formatting(invoice):
    root = FacturaeExporter(_settings()).build_tree(invoice)
    lines = root.findall("Invoices/Invoice/Items/InvoiceLine", NS)
    assert len(lines) == 3
    first, _, third = lines
    assert _children(first) == [
        "ItemDescription",
        "Quantity",
        "UnitOfMeasure",
        "UnitPriceWithoutTax",
        "TotalCost",
        "GrossAmount",
        "TaxesWithheld",
        "TaxesOutputs",
    ]
    assert _find(first, "Quantity").text == "2"
    assert _find(first, "UnitPriceWithoutTax").text == "50.000000"
    assert _find(first, "TotalCost").text == "100.000000"
    assert _find(first, "GrossAmount").text == "100.00"
    assert _find(first, "TaxesOutputs/Tax/TaxRate").text == "21.00"
    assert _find(first, "TaxesWithheld/Tax/TaxAmount/TotalAmount").text == "15.00"

    assert _children(third)[5:8] == ["DiscountsAndRebates", "GrossAmount", "TaxesWithheld"]
    discount = _find(third, "DiscountsAndRebates/Discount")
    assert _find(discount, "DiscountRate").text == "10.00"
    assert _find(discount, "DiscountAmount").text == "10.000000"
    assert _find(third, "TotalCost").text == "100.000000"
    assert _find(third, "GrossAmount").text == "90.00"


def test_dir3_administrative_centres(invoice):
    root = FacturaeExporter(_settings()).build_tree(invoice)
    buyer = _find(root, "Parties/BuyerParty")
    assert _children(buyer) == ["TaxIdentification", "AdministrativeCentres", "LegalEntity"]
    centres = buyer.findall("AdministrativeCentres/AdministrativeCentre", NS)
    assert [(_find(c, "RoleTypeCode").text, _find(c, "CentreCode").text) for c in centres] == [
        ("01", "L01462508"),
        ("02", "L01462509"),
        ("03", "L01462510"),
    ]
    assert _children(centres[0]) == ["CentreCode", "RoleTypeCode", "AddressInSpain", "CentreDescription"]


def test_dir3_centres_omitted_when_codes_missing(invoice):
    invoice.dir3_accounting_office = invoice.dir3_managing_body = invoice.dir3_processing_unit = None
    root = FacturaeExporter(_settings()).build_tree(invoice)
    assert root.find("Parties/BuyerParty/AdministrativeCentres", NS) is None


def test_payment_details_only_with_due_date_and_iban(invoice):
    root = FacturaeExporter(_settings()).build_tree(invoice)
    installment = _find(root, "Invoices/Invoice/PaymentDetails/Installment")
    assert _children(installment) == ["InstallmentDueDate", "InstallmentAmount", "PaymentMeans", "AccountToBeCredited"]
    assert _find(installment, "AccountToBeCredited/IBAN").text == "ES9121000418450200051332"
    assert _find(installment, "InstallmentDueDate").text == "2024-04-14"
    assert _find(installment, "InstallmentAmount").text == "271.60"
    assert _find(installment, "PaymentMeans").text == "04"

    invoice.due_date = None
    root = FacturaeExporter(_settings()).build_tree(invoice)
    assert root.find("Invoices/Invoice/PaymentDetails", NS) is None


def test_no_payment_details_without_iban(invoice):
    """A credit transfer without the account to credit is invalid, so nothing is emitted."""
    invoice.payment_iban = None
    root = FacturaeExporter(_settings()).build_tree(invoice)
    assert root.find("Invoices/Invoice/PaymentDetails", NS) is None


def test_synthetic_invoice_is_never_exported(invoice):
    invoice.is_synthetic = True
    with pytest.raises(FacturaeExportError, match="sintéticos"):
        FacturaeExporter(_settings()).build_tree(invoice)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("province", "Santa Cruz de Tenerife", "provincia"),
        ("city", "C" * 51, "población"),
        ("address", "A" * 81, "dirección"),
    ],
)
def test_overlong_supplier_fields_are_rejected_not_truncated(invoice, field, value, expected):
    setattr(invoice.supplier, field, value)
    with pytest.raises(FacturaeExportError, match=expected) as error:
        FacturaeExporter(_settings()).build_tree(invoice)
    assert "emisor" in str(error.value)


def test_length_constants_match_the_xsd():
    """The limits used for the pre-export checks are the ones declared in the XSD."""
    import re

    from app.services import face_exporter

    text = XSD_PATH.read_text(encoding="utf-8")
    max_of = {
        name: int(value)
        for name, value in re.findall(r'<xs:simpleType name="(TextMax\d+Type)">\s*<xs:restriction[^>]*>\s*<xs:maxLength value="(\d+)"', text)
    }
    declared = dict(re.findall(r'<xs:element name="(Address|Town|Province|CorporateName|PostCodeAndTown)" type="(TextMax\d+Type)"', text))
    assert max_of[declared["Address"]] == face_exporter.MAX_ADDRESS
    assert max_of[declared["Town"]] == face_exporter.MAX_TOWN
    assert max_of[declared["Province"]] == face_exporter.MAX_PROVINCE
    assert max_of[declared["CorporateName"]] == face_exporter.MAX_CORPORATE_NAME
    assert max_of[declared["PostCodeAndTown"]] == face_exporter.MAX_POST_CODE_AND_TOWN


def test_no_irpf_section_without_withholding(db_session):
    supplier = _supplier()
    inv = _invoice(
        supplier,
        tax_base=Decimal("100.00"),
        total_vat=Decimal("21.00"),
        total_irpf=Decimal("0.00"),
        total_amount=Decimal("121.00"),
    )
    inv.lines = [_line(1, "Servicio", "1", "100.00", "21", "100.00", "21.00")]
    db_session.add(inv)
    db_session.commit()
    root = FacturaeExporter(_settings()).build_tree(inv)
    invoice_node = _find(root, "Invoices/Invoice")
    assert "TaxesWithheld" not in _children(invoice_node)
    assert _find(invoice_node, "InvoiceTotals/TotalTaxesWithheld").text == "0.00"


def test_vat_exempt_line_gets_special_taxable_event(db_session):
    supplier = _supplier()
    inv = _invoice(
        supplier,
        tax_base=Decimal("50.00"),
        total_vat=Decimal("0.00"),
        total_irpf=Decimal("0.00"),
        total_amount=Decimal("50.00"),
    )
    inv.lines = [_line(1, "Formación exenta", "1", "50.00", "0", "50.00", "0.00")]
    db_session.add(inv)
    db_session.commit()
    root = FacturaeExporter(_settings()).build_tree(inv)
    line = _find(root, "Invoices/Invoice/Items/InvoiceLine")
    assert _children(line)[-2:] == ["TaxesOutputs", "SpecialTaxableEvent"]
    assert _find(line, "TaxesOutputs/Tax/TaxRate").text == "0.00"


def test_per_line_vat_rounding_is_accepted(db_session):
    """Declared VAT equals the sum of per-line VAT (not the per-group recomputation): the group uses line sums."""
    supplier = _supplier()
    inv = _invoice(
        supplier,
        tax_base=Decimal("0.30"),
        total_vat=Decimal("0.06"),
        total_irpf=Decimal("0.00"),
        total_amount=Decimal("0.36"),
    )
    inv.lines = [
        _line(1, "A", "1", "0.10", "21", "0.10", "0.02"),
        _line(2, "B", "1", "0.10", "21", "0.10", "0.02"),
        _line(3, "C", "1", "0.10", "21", "0.10", "0.02"),
    ]
    db_session.add(inv)
    db_session.commit()
    root = FacturaeExporter(_settings()).build_tree(inv)
    amount = _find(root, "Invoices/Invoice/TaxesOutputs/Tax/TaxAmount/TotalAmount")
    assert amount.text == "0.06"


def test_inconsistent_vat_total_is_rejected(invoice):
    invoice.total_vat = Decimal("40.00")
    invoice.total_amount = Decimal("278.00")
    with pytest.raises(FacturaeExportError, match="IVA"):
        FacturaeExporter(_settings()).build_tree(invoice)


def test_missing_party_data_is_rejected(invoice):
    invoice.buyer_address = None
    with pytest.raises(FacturaeExportError) as exc_info:
        FacturaeExporter(_settings()).build_tree(invoice)
    assert "receptor" in str(exc_info.value) and "dirección" in str(exc_info.value)


def test_invalid_spanish_postal_code_is_rejected(invoice):
    invoice.buyer_postal_code = "4600"
    with pytest.raises(FacturaeExportError, match="5 dígitos"):
        FacturaeExporter(_settings()).build_tree(invoice)


def test_non_eur_currency_is_rejected(invoice):
    invoice.currency = "USD"
    with pytest.raises(FacturaeExportError, match="EUR"):
        FacturaeExporter(_settings()).build_tree(invoice)


# ---------------------------------------------------------------- parties


def test_legal_entity_seller(invoice):
    root = FacturaeExporter(_settings()).build_tree(invoice)
    seller = _find(root, "Parties/SellerParty")
    assert _children(seller) == ["TaxIdentification", "LegalEntity"]
    identification = _find(seller, "TaxIdentification")
    assert _find(identification, "PersonTypeCode").text == "J"
    assert _find(identification, "ResidenceTypeCode").text == "R"
    assert _find(identification, "TaxIdentificationNumber").text == "B12345678"
    legal = _find(seller, "LegalEntity")
    assert _children(legal) == ["CorporateName", "AddressInSpain"]
    assert _children(_find(legal, "AddressInSpain")) == ["Address", "PostCode", "Town", "Province", "CountryCode"]
    assert _find(legal, "AddressInSpain/CountryCode").text == "ESP"


def test_individual_seller_splits_name_and_surnames(db_session):
    supplier = _supplier(
        name="María Pérez García",
        tax_id="12345678Z",
        tax_id_type=TaxIdType.NIF,
        person_type=PersonType.INDIVIDUAL,
    )
    inv = _invoice(supplier)
    db_session.add(inv)
    db_session.commit()
    root = FacturaeExporter(_settings()).build_tree(inv)
    seller = _find(root, "Parties/SellerParty")
    assert _children(seller) == ["TaxIdentification", "Individual"]
    assert _find(seller, "TaxIdentification/PersonTypeCode").text == "F"
    individual = _find(seller, "Individual")
    assert _children(individual) == ["Name", "FirstSurname", "SecondSurname", "AddressInSpain"]
    assert _find(individual, "Name").text == "María"
    assert _find(individual, "FirstSurname").text == "Pérez"
    assert _find(individual, "SecondSurname").text == "García"
    assert seller.find("LegalEntity", NS) is None


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        ("Juan García", ("Juan", "García", None)),
        ("Juan García López", ("Juan", "García", "López")),
        ("Ana María de la Cruz Soto", ("Ana María", "de la Cruz", "Soto")),
        ("García López, Juan Carlos", ("Juan Carlos", "García", "López")),
        ("de la Fuente Ruiz, Pilar", ("Pilar", "de la Fuente", "Ruiz")),
    ],
)
def test_split_individual_name(full_name, expected):
    assert split_individual_name(full_name) == expected


def test_split_individual_name_requires_surname():
    with pytest.raises(FacturaeExportError, match="apellidos"):
        split_individual_name("Madonna")


def test_overseas_seller_uses_overseas_address(db_session):
    supplier = _supplier(name="Acme GmbH", tax_id="DE123456789", country_code="DEU", postal_code="10115", city="Berlin")
    inv = _invoice(supplier)
    db_session.add(inv)
    db_session.commit()
    root = FacturaeExporter(_settings()).build_tree(inv)
    seller = _find(root, "Parties/SellerParty")
    assert _find(seller, "TaxIdentification/ResidenceTypeCode").text == "E"
    overseas = _find(seller, "LegalEntity/OverseasAddress")
    assert _children(overseas) == ["Address", "PostCodeAndTown", "Province", "CountryCode"]
    assert _find(overseas, "PostCodeAndTown").text == "10115 Berlin"


def test_spanish_vat_prefix_is_stripped_from_tax_id(db_session):
    supplier = _supplier()
    inv = _invoice(supplier, supplier_tax_id="ES B-12345678")
    db_session.add(inv)
    db_session.commit()
    root = FacturaeExporter(_settings()).build_tree(inv)
    assert _find(root, "Parties/SellerParty/TaxIdentification/TaxIdentificationNumber").text == "B12345678"


# ---------------------------------------------------------------- filename and serialization


def test_filename_sanitization(invoice):
    exporter = FacturaeExporter(_settings())
    assert exporter.filename_for(invoice) == "facturae_B12345678_F-2024-001.xml"
    invoice.invoice_series = None
    invoice.invoice_number = "A/2024 0001ñ"
    invoice.supplier_tax_id = "b-1234.5678"
    assert exporter.filename_for(invoice) == "facturae_B12345678_A_2024_0001.xml"
    invoice.invoice_number = "../../etc/passwd"
    name = exporter.filename_for(invoice)
    assert "/" not in name and "\\" not in name and ".." not in name
    assert name.startswith("facturae_B12345678_") and name.endswith(".xml")


def test_to_xml_is_pretty_utf8_and_well_formed(invoice):
    xml = FacturaeExporter(_settings()).to_xml(invoice)
    assert xml.startswith(b"<?xml version='1.0' encoding='UTF-8'?>")
    text = xml.decode("utf-8")
    assert "\n  <FileHeader>" in text
    assert "Servicios Integrales Mediterráneo S.L." in text
    root = etree.fromstring(xml)
    assert root.tag == f"{{{FACTURAE_NAMESPACE}}}Facturae"
    assert root.find("ds:Signature", NS) is None


def test_sign_none_follows_certificate_setting(invoice, tmp_path):
    cert_path, _ = _make_pkcs12(tmp_path)
    unsigned = FacturaeExporter(_settings()).to_xml(invoice)
    assert b"Signature" not in unsigned
    signed = FacturaeExporter(_settings(cert_path, CERT_PASSWORD)).to_xml(invoice)
    assert b"<ds:Signature" in signed
    assert b"Signature" not in FacturaeExporter(_settings(cert_path, CERT_PASSWORD)).to_xml(invoice, sign=False)
    assert b"<ds:Signature" in FacturaeExporter(_settings(cert_path, CERT_PASSWORD)).to_xml(invoice)


# ---------------------------------------------------------------- XSD validation


def test_unsigned_xml_validates_against_official_xsd(invoice, xsd):
    root = etree.fromstring(FacturaeExporter(_settings()).to_xml(invoice))
    assert xsd.validate(root), xsd.error_log


def test_individual_and_overseas_variants_validate_against_xsd(db_session, xsd):
    individual = _supplier(
        name="Juan García López", tax_id="12345678Z", tax_id_type=TaxIdType.NIF, person_type=PersonType.INDIVIDUAL
    )
    foreign = _supplier(name="Acme GmbH", tax_id="DE123456789", country_code="DEU", postal_code="10115", city="Berlin")
    for index, supplier in enumerate((individual, foreign)):
        inv = _invoice(supplier, invoice_number=f"V{index}")
        db_session.add(inv)
        db_session.commit()
        root = etree.fromstring(FacturaeExporter(_settings()).to_xml(inv))
        assert xsd.validate(root), xsd.error_log


def test_exempt_and_no_irpf_variants_validate_against_xsd(db_session, xsd):
    inv = _invoice(
        _supplier(),
        tax_base=Decimal("150.00"),
        total_vat=Decimal("21.00"),
        total_irpf=Decimal("0.00"),
        total_amount=Decimal("171.00"),
        due_date=None,
        operation_date=None,
    )
    inv.lines = [
        _line(1, "Servicio gravado", "1", "100.00", "21", "100.00", "21.00"),
        _line(2, "Servicio exento", "1", "50.00", "0", "50.00", "0.00"),
    ]
    db_session.add(inv)
    db_session.commit()
    root = etree.fromstring(FacturaeExporter(_settings()).to_xml(inv))
    assert xsd.validate(root), xsd.error_log


# ---------------------------------------------------------------- signing


def test_signed_xml_has_verifiable_xades_epes_signature(invoice, tmp_path, xsd):
    pytest.importorskip("signxml.xades", reason="signxml build without XAdES support")
    from signxml import DigestAlgorithm, SignatureConfiguration, XMLVerifier
    from signxml.xades import XAdESSignatureConfiguration, XAdESSignaturePolicy, XAdESVerifier

    cert_path, certificate = _make_pkcs12(tmp_path)
    xml = FacturaeExporter(_settings(cert_path, CERT_PASSWORD)).to_xml(invoice, sign=True)
    root = etree.fromstring(xml)

    assert _children(root) == ["FileHeader", "Parties", "Invoices", "Signature"]
    signature = _find(root, "ds:Signature")
    assert signature.getparent() is root
    assert xsd.validate(root), xsd.error_log

    policy = signature.find(".//{http://uri.etsi.org/01903/v1.3.2#}SigPolicyId/{*}Identifier")
    assert policy is not None and policy.text == SIGNATURE_POLICY_IDENTIFIER
    assert signature.find(".//{http://uri.etsi.org/01903/v1.3.2#}SigningCertificate") is not None

    expected_policy = XAdESSignaturePolicy(
        Identifier=SIGNATURE_POLICY_IDENTIFIER,
        Description="Política de Firma FacturaE v3.1",
        DigestMethod=DigestAlgorithm.SHA1,
        DigestValue="Ohixl6upD6av8N7pEvDABhEL6hM=",
    )
    results = XAdESVerifier().verify(
        xml,
        x509_cert=certificate,
        expect_signature_policy=expected_policy,
        expect_config=XAdESSignatureConfiguration(),
    )
    assert len(results) == 3

    plain = XMLVerifier().verify(xml, x509_cert=certificate, expect_config=SignatureConfiguration(expect_references=3))
    assert plain[0].signed_xml is not None


def test_tampering_with_signed_xml_breaks_verification(invoice, tmp_path):
    pytest.importorskip("signxml.xades", reason="signxml build without XAdES support")
    from signxml.exceptions import InvalidSignature
    from signxml.xades import XAdESVerifier

    cert_path, certificate = _make_pkcs12(tmp_path)
    xml = FacturaeExporter(_settings(cert_path, CERT_PASSWORD)).to_xml(invoice, sign=True)
    tampered = xml.replace(b"<InvoiceTotal>271.60<", b"<InvoiceTotal>1.00<")
    assert tampered != xml
    with pytest.raises(InvalidSignature):
        XAdESVerifier().verify(tampered, x509_cert=certificate)


def test_signing_without_certificate_raises(invoice):
    with pytest.raises(FacturaeExportError, match="certificado"):
        FacturaeExporter(_settings()).to_xml(invoice, sign=True)


def test_signing_with_missing_certificate_file_raises(invoice, tmp_path):
    with pytest.raises(FacturaeExportError, match="no existe"):
        FacturaeExporter(_settings(tmp_path / "missing.p12", CERT_PASSWORD)).to_xml(invoice, sign=True)


def test_signing_with_wrong_password_raises(invoice, tmp_path):
    cert_path, _ = _make_pkcs12(tmp_path)
    with pytest.raises(FacturaeExportError, match="PKCS#12"):
        FacturaeExporter(_settings(cert_path, "otra-clave")).to_xml(invoice, sign=True)
