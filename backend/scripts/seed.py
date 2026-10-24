"""Seed the database with 25 demo Spanish invoices that are audited by the real InvoiceAuditor.

Run from backend/:  python scripts/seed.py [--reset] [--database-url URL]

Every invoice is described by a `Scenario` (supplier, buyer, lines, optional `mutate` step that injects a specific
error). Amounts are computed correctly with ROUND_HALF_UP first; the error of each scenario is then introduced
explicitly. The audit status is never set by hand: it is whatever `InvoiceAuditor` returns, and the seed fails loudly
when it differs from the scenario's expectation. Tax ids are generated with the official NIF/CIF control algorithms.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.db.database import SessionLocal, build_engine, init_db
from app.db.database import engine as default_engine
from app.models import AuditStatus, Invoice, PersonType, Severity, Supplier
from app.schemas import AuditReport, InvoiceCreate, InvoiceLineCreate, SupplierCreate
from app.services.auditor import (
    BUYER_TAX_ID,
    DATES,
    DUPLICATE,
    FACE_DIR3,
    FACE_PARTY_DATA,
    GRAND_TOTAL,
    IRPF_TOTAL,
    LINE_BASE,
    LINE_IRPF,
    LINE_VAT,
    SUPPLIER_TAX_ID,
    TAX_BASE_SUM,
    VAT_RATE,
    VAT_TOTAL,
    InvoiceAuditor,
)
from app.services.face_exporter import FacturaeExporter, FacturaeExportError
from app.services.invoice_service import create_invoice_manual
from app.services.tax_id import (
    CIF_CONTROL_LETTERS,
    CIF_LETTER_ONLY,
    NIF_LETTERS,
    compute_cif_control,
    compute_nif_letter,
    validate_tax_id,
)

MARKER = "Datos de demostración (seed)"
CENT = Decimal("0.01")
HUNDRED = Decimal(100)
EXPECTED_INVOICE_COUNT = 25


class SeedError(RuntimeError):
    """The seed produced something other than what its scenarios declare."""


class AlreadySeededError(SeedError):
    """Seeded invoices are already present in the database."""


# --------------------------------------------------------------------------- Tax ids


def make_nif(number: int) -> str:
    return f"{number:08d}{compute_nif_letter(number)}"


def make_cif(org: str, digits7: str) -> str:
    digit, letter = compute_cif_control(org, digits7)
    return f"{org}{digits7}{letter if org in CIF_LETTER_ONLY else digit}"


def corrupt_tax_id(tax_id: str) -> str:
    """Return the same id with a wrong control character."""
    if tax_id[0].isdigit():
        wrong = NIF_LETTERS[(NIF_LETTERS.index(tax_id[-1]) + 1) % len(NIF_LETTERS)]
    elif tax_id[0] in CIF_LETTER_ONLY:
        wrong = CIF_CONTROL_LETTERS[(CIF_CONTROL_LETTERS.index(tax_id[-1]) + 1) % len(CIF_CONTROL_LETTERS)]
    else:
        wrong = str((int(tax_id[-1]) + 1) % 10)
    corrupted = tax_id[:-1] + wrong
    if validate_tax_id(corrupted).is_valid:
        raise SeedError(f"No se pudo invalidar el identificador {tax_id}")
    return corrupted


# --------------------------------------------------------------------------- Parties


@dataclass(frozen=True)
class SupplierSpec:
    name: str
    tax_id: str
    person_type: PersonType
    address: str
    postal_code: str
    city: str
    province: str
    email: str
    phone: str
    prefix: str = ""  # invoice number prefix
    series: str | None = None
    first_number: int = 1


@dataclass(frozen=True)
class BuyerSpec:
    name: str
    tax_id: str | None
    address: str | None
    postal_code: str
    city: str
    province: str
    dir3: tuple[str, str, str] | None = None  # oficina contable, órgano gestor, unidad tramitadora


LEGAL, INDIVIDUAL = PersonType.LEGAL_ENTITY, PersonType.INDIVIDUAL

LEVANTE = SupplierSpec(
    "Suministros Industriales Levante, S.L.", make_cif("B", "4612345"), LEGAL,
    "Avenida del Cid, 112, bajo", "46018", "Valencia", "Valencia",
    "facturacion@suministroslevante.es", "963 842 117", prefix="FV-", first_number=1180,
)  # fmt: skip
IBERIA = SupplierSpec(
    "Consultoría Tecnológica Iberia, S.A.", make_cif("A", "2806714"), LEGAL,
    "Paseo de la Castellana, 141, planta 9", "28046", "Madrid", "Madrid",
    "administracion@ctiberia.es", "915 703 228", series="IB", first_number=84,
)  # fmt: skip
NORTE = SupplierSpec(
    "Limpiezas Integrales del Norte, S.L.", make_cif("B", "4809621"), LEGAL,
    "Alameda de Urquijo, 36, 2.º izda.", "48011", "Bilbao", "Bizkaia",
    "contabilidad@limpiezasnorte.es", "944 235 019", prefix="LN", first_number=2310,
)  # fmt: skip
MEDITERRANEO = SupplierSpec(
    "Construcciones y Reformas Mediterráneo, S.L.", make_cif("B", "0319854"), LEGAL,
    "Calle Gerona, 18, entresuelo", "03001", "Alicante", "Alicante",
    "obras@reformasmediterraneo.es", "965 120 774", prefix="OB-", first_number=412,
)  # fmt: skip
ANDALUZA = SupplierSpec(
    "Informática y Redes Andaluza, S.L.", make_cif("B", "4106238"), LEGAL,
    "Calle Leonardo da Vinci, 18, Isla de la Cartuja", "41092", "Sevilla", "Sevilla",
    "ventas@informaticaandaluza.es", "954 460 335", prefix="IRA", first_number=697,
)  # fmt: skip
FERRER = SupplierSpec(
    "Catering Hermanos Ferrer, S.L.", make_cif("B", "5009147"), LEGAL,
    "Calle Don Jaime I, 40, 1.º", "50001", "Zaragoza", "Zaragoza",
    "eventos@cateringferrer.es", "976 391 842", prefix="CF", first_number=265,
)  # fmt: skip
ATLANTICO = SupplierSpec(
    "Editorial Pensamiento Atlántico, S.L.", make_cif("B", "1570832"), LEGAL,
    "Rúa do Franco, 24, 1.º", "15705", "Santiago de Compostela", "A Coruña",
    "pedidos@pensamientoatlantico.es", "981 580 463", prefix="EA", first_number=1530,
)  # fmt: skip
LUCIA = SupplierSpec(
    "Martínez Soler, Lucía", make_nif(34827561), INDIVIDUAL,
    "Gran Vía Escultor Salzillo, 9, 4.º B", "30004", "Murcia", "Murcia",
    "lucia.martinez@arquitectamartinez.es", "968 211 906", series="ARQ", first_number=31,
)  # fmt: skip
JAVIER = SupplierSpec(
    "Ortega Vidal, Javier", make_nif(24915378), INDIVIDUAL,
    "Calle Recogidas, 21, 2.º C", "18002", "Granada", "Granada",
    "javier.ortega@ortegaabogados.es", "958 265 740", prefix="J", first_number=52,
)  # fmt: skip
MARTA = SupplierSpec(
    "Pons Riera, Marta", make_nif(47362915), INDIVIDUAL,
    "Carrer de Provença, 288, 2n 1a", "08008", "Barcelona", "Barcelona",
    "marta@ponsriera.design", "932 154 877", series="MP", first_number=7,
)  # fmt: skip
REPROGRAFIA = SupplierSpec(  # CIF with a wrong control digit
    "Reprografía Express Palma, S.L.", corrupt_tax_id(make_cif("B", "0742619")), LEGAL,
    "Carrer de Sant Miquel, 71", "07002", "Palma", "Illes Balears",
    "pedidos@reprografiaexpress.es", "971 724 508", prefix="RE", first_number=903,
)  # fmt: skip
GIL = SupplierSpec(  # NIF with a wrong control letter
    "Gil Torres, Andrés", corrupt_tax_id(make_nif(12958347)), INDIVIDUAL,
    "Calle Santiago, 9, 1.º", "47001", "Valladolid", "Valladolid",
    "andres.gil@traduccionesgil.es", "983 305 612", prefix="TG", first_number=18,
)  # fmt: skip

AYTO_ALCALA = BuyerSpec(
    "Ayuntamiento de Alcalá de Henares", make_cif("P", "2800721"),
    "Plaza de Cervantes, 12", "28801", "Alcalá de Henares", "Madrid",
    ("L01280058", "L01280058", "L01280058"),
)  # fmt: skip
DIPUTACION_VALENCIA = BuyerSpec(
    "Diputación Provincial de Valencia", make_cif("P", "4600015"),
    "Calle Corona, 36", "46003", "Valencia", "Valencia",
    ("L02000046", "L02000046", "L02000046"),
)  # fmt: skip
UNIVERSIDAD_MURCIA = BuyerSpec(
    "Universidad de Murcia", make_cif("Q", "3018205"),
    "Avenida Teniente Flomesta, 5", "30003", "Murcia", "Murcia",
    ("U00700001", "U00700001", "U00700045"),
)  # fmt: skip
AYTO_GIJON = BuyerSpec(
    "Ayuntamiento de Gijón", make_cif("P", "3302406"),
    "Calle Cabrales, 2", "33201", "Gijón", "Asturias",
    ("L01330241", "L01330241", "L01330241"),
)  # fmt: skip
CONSEJERIA_MADRID = BuyerSpec(
    "Comunidad de Madrid. Consejería de Cultura, Turismo y Deporte", make_cif("S", "2800310"),
    "Calle Alcalá, 31", "28014", "Madrid", "Madrid",
    ("A13002908", "A13002908", "A13018337"),
)  # fmt: skip
HOTEL_COSTA_BLANCA = BuyerSpec(
    "Grupo Hotelero Costa Blanca, S.A.", make_cif("A", "0328417"),
    "Avenida de Niza, 22", "03503", "Benidorm", "Alicante",
)  # fmt: skip
GESTORIA_ALMAGRO = BuyerSpec(
    "Gestoría Administrativa Almagro, S.L.", make_cif("B", "2810466"),
    "Calle de Almagro, 15, 1.º", "28010", "Madrid", "Madrid",
)  # fmt: skip
COMUNIDAD_ALMENDROS = BuyerSpec(  # no tax id on the document
    "Comunidad de Propietarios Residencial Los Almendros", None,
    "Calle de los Almendros, 8", "03540", "Alicante", "Alicante",
)  # fmt: skip


# --------------------------------------------------------------------------- Amounts


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class LineSpec:
    concept: str
    quantity: Decimal
    unit_price: Decimal
    vat_rate: Decimal = Decimal(21)
    irpf_rate: Decimal = Decimal(0)
    discount_rate: Decimal = Decimal(0)
    unit_of_measure: str = "01"  # Facturae: 01 unidades, 02 horas


def line(
    concept: str, quantity: str, unit_price: str, *, vat: str = "21", irpf: str = "0", disc: str = "0", unit: str = "01"
) -> LineSpec:
    return LineSpec(concept, Decimal(quantity), Decimal(unit_price), Decimal(vat), Decimal(irpf), Decimal(disc), unit)


@dataclass
class DraftLine:
    spec: LineSpec
    line_base: Decimal
    vat_amount: Decimal
    irpf_amount: Decimal


@dataclass
class Draft:
    """Declared amounts of an invoice. They start out correct; a scenario's `mutate` step may corrupt them."""

    lines: list[DraftLine]
    tax_base: Decimal
    total_vat: Decimal
    total_irpf: Decimal
    total_amount: Decimal


def retotal(draft: Draft) -> None:
    """Recompute the totals from the declared line amounts (per-line VAT/IRPF rounding)."""
    draft.tax_base = sum((item.line_base for item in draft.lines), Decimal(0))
    draft.total_vat = sum((item.vat_amount for item in draft.lines), Decimal(0))
    draft.total_irpf = sum((item.irpf_amount for item in draft.lines), Decimal(0))
    draft.total_amount = draft.tax_base + draft.total_vat - draft.total_irpf


def build_draft(specs: Sequence[LineSpec]) -> Draft:
    items = []
    for spec in specs:
        base = money(spec.quantity * spec.unit_price * (HUNDRED - spec.discount_rate) / HUNDRED)
        items.append(
            DraftLine(spec, base, money(base * spec.vat_rate / HUNDRED), money(base * spec.irpf_rate / HUNDRED))
        )
    draft = Draft(items, Decimal(0), Decimal(0), Decimal(0), Decimal(0))
    retotal(draft)
    return draft


# Error injections: each one breaks exactly one audit rule.


def vat_total_one_cent_high(draft: Draft) -> None:
    """The VAT total is one cent above the VAT of the lines and the grand total includes that error."""
    draft.total_vat += Decimal("0.01")
    draft.total_amount += Decimal("0.01")


def grand_total_five_cents_high(draft: Draft) -> None:
    """Base + VAT are right but the printed invoice total is 0.05 EUR too high."""
    draft.total_amount += Decimal("0.05")


def discount_not_applied(draft: Draft) -> None:
    """The discounted line shows quantity x price without the discount; VAT and totals follow the wrong base."""
    target = next(item for item in draft.lines if item.spec.discount_rate)
    target.line_base = money(target.spec.quantity * target.spec.unit_price)
    target.vat_amount = money(target.line_base * target.spec.vat_rate / HUNDRED)
    retotal(draft)


def line_vat_three_cents_high(draft: Draft) -> None:
    """The VAT of the first line is rounded up by 0.03 EUR and the VAT total adds up the lines."""
    draft.lines[0].vat_amount += Decimal("0.03")
    retotal(draft)


def irpf_applied_over_base_plus_vat(draft: Draft) -> None:
    """Classic mistake: the withholding is computed over base + VAT instead of over the base."""
    for item in draft.lines:
        item.irpf_amount = money((item.line_base + item.vat_amount) * item.spec.irpf_rate / HUNDRED)
    retotal(draft)


def tax_base_three_cents_high(draft: Draft) -> None:
    """The invoice tax base is 0.03 EUR above the sum of the line bases (transcription error)."""
    draft.tax_base += Decimal("0.03")
    draft.total_amount += Decimal("0.03")


def irpf_total_omitted(draft: Draft) -> None:
    """The lines carry IRPF but the invoice total_irpf is 0 and the total was not reduced by the withholding."""
    draft.total_irpf = Decimal("0.00")
    draft.total_amount = draft.tax_base + draft.total_vat


# --------------------------------------------------------------------------- Scenarios


@dataclass(frozen=True)
class Scenario:
    key: str
    description: str
    supplier: SupplierSpec
    buyer: BuyerSpec
    days_ago: int
    lines: tuple[LineSpec, ...]
    expected_status: AuditStatus = AuditStatus.VALIDATED
    expected_errors: tuple[str, ...] = ()  # rule codes of the failed ERROR checks
    expected_warnings: tuple[str, ...] = ()  # rule codes of the failed WARNING checks
    mutate: Callable[[Draft], None] | None = None
    payment_days: int = 30
    operation_offset_days: int | None = None  # operation date relative to the issue date (negative = before)
    duplicate_of: str | None = None
    issue_date: date | None = None  # resolved by build_scenarios
    invoice_number: str = ""  # resolved by build_scenarios

    @property
    def is_ready_for_face(self) -> bool:
        return self.expected_status is AuditStatus.VALIDATED and not self.expected_warnings

    def to_payload(self) -> InvoiceCreate:
        if self.issue_date is None:
            raise SeedError(f"El escenario {self.key} no tiene fecha resuelta")
        draft = build_draft(self.lines)
        if self.mutate is not None:
            self.mutate(draft)
        supplier, buyer = self.supplier, self.buyer
        operation_date = (
            self.issue_date + timedelta(days=self.operation_offset_days)
            if self.operation_offset_days is not None
            else None
        )
        dir3 = buyer.dir3 or (None, None, None)
        return InvoiceCreate(
            supplier=SupplierCreate(
                name=supplier.name,
                tax_id=supplier.tax_id,
                person_type=supplier.person_type,
                address=supplier.address,
                postal_code=supplier.postal_code,
                city=supplier.city,
                province=supplier.province,
                email=supplier.email,
                phone=supplier.phone,
            ),
            invoice_number=self.invoice_number,
            invoice_series=supplier.series,
            issue_date=self.issue_date,
            operation_date=operation_date,
            due_date=self.issue_date + timedelta(days=self.payment_days),
            currency="EUR",
            buyer_name=buyer.name,
            buyer_tax_id=buyer.tax_id,
            buyer_address=buyer.address,
            buyer_postal_code=buyer.postal_code,
            buyer_city=buyer.city,
            buyer_province=buyer.province,
            dir3_accounting_office=dir3[0],
            dir3_managing_body=dir3[1],
            dir3_processing_unit=dir3[2],
            tax_base=draft.tax_base,
            total_vat=draft.total_vat,
            total_irpf=draft.total_irpf,
            total_amount=draft.total_amount,
            notes=f"{MARKER}: {self.description}",
            lines=[
                InvoiceLineCreate(
                    concept=item.spec.concept,
                    quantity=item.spec.quantity,
                    unit_of_measure=item.spec.unit_of_measure,
                    unit_price=item.spec.unit_price,
                    discount_rate=item.spec.discount_rate,
                    vat_rate=item.spec.vat_rate,
                    irpf_rate=item.spec.irpf_rate,
                    line_base=item.line_base,
                    vat_amount=item.vat_amount,
                    irpf_amount=item.irpf_amount,
                )
                for item in draft.lines
            ],
        )


VALIDATED, DISCREPANCY, DUPLICATE_STATUS = AuditStatus.VALIDATED, AuditStatus.DISCREPANCY, AuditStatus.DUPLICATE


def _scenario_definitions() -> list[Scenario]:
    """All 25 scenarios in definition order (dates and invoice numbers are resolved by build_scenarios)."""
    ready = [
        Scenario(
            "r1",
            "Lista para FACe: material de oficina con descuento",
            LEVANTE,
            AYTO_ALCALA,
            410,
            (
                line("Papel multifunción A4 80 g/m², caja de 5 paquetes de 500 hojas", "40", "24.90"),
                line("Tóner negro compatible HP 59A (CF259A)", "12", "38.75", disc="10"),
                line("Archivador de palanca folio, lomo ancho", "150", "2.35"),
            ),
            operation_offset_days=-5,
        ),
        Scenario(
            "r2",
            "Lista para FACe: limpieza de edificios universitarios",
            NORTE,
            UNIVERSIDAD_MURCIA,
            352,
            (
                line(
                    "Servicio de limpieza de edificios universitarios, mes de octubre (horas)",
                    "320",
                    "14.80",
                    unit="02",
                ),
                line("Reposición de productos de limpieza y consumibles", "1", "245.60"),
            ),
        ),
        Scenario(
            "r3",
            "Lista para FACe: rehabilitación con IVA reducido y alquiler de maquinaria",
            MEDITERRANEO,
            DIPUTACION_VALENCIA,
            318,
            (
                line(
                    "Sustitución de carpintería exterior de aluminio en sede administrativa (m²)",
                    "42.5",
                    "218.40",
                    vat="10",
                ),
                line("Reparación de cubierta y tratamiento de impermeabilización (m²)", "120", "46.30", vat="10"),
                line("Alquiler de andamio y plataforma elevadora (días)", "12", "96.50"),
            ),
            payment_days=60,
        ),
        Scenario(
            "r4",
            "Lista para FACe: libros con IVA superreducido y envío",
            ATLANTICO,
            UNIVERSIDAD_MURCIA,
            281,
            (
                line(
                    "Libros de la colección Manuales Universitarios, lote para biblioteca",
                    "85",
                    "27.40",
                    vat="4",
                    disc="15",
                ),
                line("Gastos de envío y manipulación", "1", "38.00"),
            ),
        ),
        Scenario(
            "r5",
            "Lista para FACe: honorarios de arquitecta con retención del 15 %",
            LUCIA,
            AYTO_GIJON,
            227,
            (
                line(
                    "Redacción de proyecto básico y de ejecución de rehabilitación de local municipal",
                    "1",
                    "4150.00",
                    irpf="15",
                ),
                line("Memoria de accesibilidad y cumplimiento del CTE-DB-SUA", "1", "640.00", irpf="15"),
            ),
            payment_days=45,
        ),
        Scenario(
            "r6",
            "Lista para FACe: diseñadora con retención reducida del 7 %",
            MARTA,
            DIPUTACION_VALENCIA,
            173,
            (
                line("Diseño de identidad gráfica para campaña informativa", "1", "1850.00", irpf="7"),
                line("Maquetación de folletos y cartelería (horas)", "18.5", "42.00", irpf="7", unit="02"),
            ),
        ),
        Scenario(
            "r7",
            "Lista para FACe: catering con IVA del 10 % y descuento",
            FERRER,
            UNIVERSIDAD_MURCIA,
            104,
            (
                line("Menús para la jornada de bienvenida de estudiantes", "220", "14.50", vat="10", disc="5"),
                line("Servicio de café y pausa de media mañana", "220", "3.80", vat="10"),
                line("Alquiler de menaje y mobiliario", "1", "380.00"),
            ),
        ),
        Scenario(
            "r8",
            "Lista para FACe: consultoría y licencias con descuento",
            IBERIA,
            CONSEJERIA_MADRID,
            58,
            (
                line("Consultoría de arquitectura de sistemas (jornadas)", "22", "640.00"),
                line("Licencia anual de software de monitorización", "3", "1290.50", disc="8"),
            ),
            payment_days=60,
        ),
    ]
    warnings = [
        Scenario(
            "w1",
            "Validada con advertencia: receptor privado sin códigos DIR3",
            ANDALUZA,
            HOTEL_COSTA_BLANCA,
            392,
            (
                line("Switch gestionable de 24 puertos PoE", "4", "312.50"),
                line("Instalación y configuración de red (horas)", "8", "55.00", unit="02"),
            ),
            expected_warnings=(FACE_DIR3,),
        ),
        Scenario(
            "w2",
            "Validada con advertencia: abogado con retención facturando a una empresa sin DIR3",
            JAVIER,
            GESTORIA_ALMAGRO,
            262,
            (line("Honorarios por asesoramiento mercantil y revisión de contratos", "1", "1400.00", irpf="15"),),
            expected_warnings=(FACE_DIR3,),
        ),
        Scenario(
            "w3",
            "Validada con advertencia: curso de formación exento (IVA 0 %)",
            IBERIA,
            UNIVERSIDAD_MURCIA,
            191,
            (
                line("Soporte evolutivo del portal de administración electrónica", "1", "1150.00"),
                line("Curso de formación en ciberseguridad (operación exenta)", "1", "1800.00", vat="0"),
            ),
            expected_warnings=(VAT_RATE,),
        ),
        Scenario(
            "w4",
            "Validada con advertencia: falta la dirección del receptor",
            NORTE,
            replace(DIPUTACION_VALENCIA, address=None),
            137,
            (line("Servicio de limpieza de oficinas, mes de mayo (horas)", "240", "14.80", unit="02"),),
            expected_warnings=(FACE_PARTY_DATA,),
        ),
        Scenario(
            "w5",
            "Validada con advertencias: comunidad de propietarios sin NIF ni DIR3",
            MEDITERRANEO,
            COMUNIDAD_ALMENDROS,
            74,
            (
                line("Pintura plástica en fachada del edificio (m²)", "380", "11.60", vat="10"),
                line("Limpieza final de obra", "1", "410.00"),
            ),
            expected_warnings=(BUYER_TAX_ID, FACE_DIR3),
        ),
        Scenario(
            "w6",
            "Validada con advertencia: fecha de la operación posterior a la de expedición",
            LUCIA,
            AYTO_GIJON,
            21,
            (line("Informe de valoración de inmueble (factura anticipada)", "1", "1275.00", irpf="15"),),
            operation_offset_days=12,
            expected_warnings=(DATES,),
        ),
    ]
    math_errors = [
        Scenario(
            "m1",
            "Error: el IVA total supera en 0,01 € la suma de las cuotas",
            IBERIA,
            UNIVERSIDAD_MURCIA,
            371,
            (
                line("Desarrollo de módulo de administración electrónica (jornadas)", "15", "580.00"),
                line("Soporte evolutivo mensual", "1", "1150.00"),
            ),
            DISCREPANCY,
            (VAT_TOTAL,),
            mutate=vat_total_one_cent_high,
        ),
        Scenario(
            "m2",
            "Error: el importe total difiere 0,05 € de base + IVA",
            FERRER,
            DIPUTACION_VALENCIA,
            299,
            (line("Menús para jornada técnica provincial", "140", "16.20", vat="10"),),
            DISCREPANCY,
            (GRAND_TOTAL,),
            mutate=grand_total_five_cents_high,
        ),
        Scenario(
            "m3",
            "Error: línea con descuento sin aplicar en la base",
            LEVANTE,
            AYTO_GIJON,
            244,
            (
                line("Sillas ergonómicas de oficina con reposabrazos", "30", "112.40", disc="12"),
                line("Mesas auxiliares de reunión", "6", "87.50"),
            ),
            DISCREPANCY,
            (LINE_BASE,),
            mutate=discount_not_applied,
        ),
        Scenario(
            "m4",
            "Error: cuota de IVA de una línea redondeada 0,03 € por encima",
            ANDALUZA,
            UNIVERSIDAD_MURCIA,
            209,
            (
                line("Ordenador portátil 14 pulgadas, 16 GB de RAM", "18", "689.00"),
                line("Licencia de ofimática anual", "18", "74.90"),
                line("Monitor de 24 pulgadas", "18", "129.99"),
            ),
            DISCREPANCY,
            (LINE_VAT,),
            mutate=line_vat_three_cents_high,
        ),
        Scenario(
            "m5",
            "Error: retención de IRPF calculada sobre base más IVA",
            LUCIA,
            AYTO_ALCALA,
            155,
            (line("Informe pericial y valoración de inmueble", "1", "2275.00", irpf="15"),),
            DISCREPANCY,
            (LINE_IRPF,),
            mutate=irpf_applied_over_base_plus_vat,
        ),
        Scenario(
            "m6",
            "Error: la base imponible no coincide con la suma de las líneas",
            MEDITERRANEO,
            AYTO_GIJON,
            88,
            (
                line("Pavimentación de acera con baldosa hidráulica (m²)", "210", "38.40", vat="10"),
                line("Señalización y balizamiento de obra", "1", "520.00"),
            ),
            DISCREPANCY,
            (TAX_BASE_SUM,),
            mutate=tax_base_three_cents_high,
        ),
        Scenario(
            "m7",
            "Error: retención de IRPF omitida en el total de la factura",
            JAVIER,
            AYTO_ALCALA,
            39,
            (
                line(
                    "Asesoramiento jurídico en procedimiento de contratación (horas)",
                    "14",
                    "95.00",
                    irpf="15",
                    unit="02",
                ),
                line("Dictamen jurídico sobre la adjudicación del contrato", "1", "850.00", irpf="15"),
            ),
            DISCREPANCY,
            (IRPF_TOTAL,),
            mutate=irpf_total_omitted,
        ),
    ]
    invalid_ids = [
        Scenario(
            "x1",
            "Error: CIF del emisor con dígito de control incorrecto",
            REPROGRAFIA,
            AYTO_GIJON,
            336,
            (
                line("Impresión y encuadernación de memorias anuales", "600", "8.75"),
                line("Cartelería en gran formato", "25", "34.90"),
            ),
            DISCREPANCY,
            (SUPPLIER_TAX_ID,),
        ),
        Scenario(
            "x2",
            "Error: NIF del emisor con letra de control incorrecta",
            GIL,
            CONSEJERIA_MADRID,
            120,
            (line("Traducción jurada inglés-español de documentación (palabras)", "12000", "0.085", irpf="15"),),
            DISCREPANCY,
            (SUPPLIER_TAX_ID,),
        ),
    ]
    by_key = {item.key: item for item in ready + warnings}
    duplicates = [
        replace(
            by_key["r2"],
            key="d1",
            description="Duplicada: reenvío de la factura de limpieza ya registrada",
            days_ago=by_key["r2"].days_ago - 7,
            expected_status=DUPLICATE_STATUS,
            expected_errors=(DUPLICATE,),
            duplicate_of="r2",
        ),
        replace(
            by_key["w1"],
            key="d2",
            description="Duplicada: la misma factura de redes recibida de nuevo",
            days_ago=by_key["w1"].days_ago - 26,
            expected_status=DUPLICATE_STATUS,
            expected_errors=(DUPLICATE,),
            duplicate_of="w1",
        ),
    ]
    return ready + warnings + math_errors + invalid_ids + duplicates


def build_scenarios(today: date | None = None) -> list[Scenario]:
    """Scenarios sorted oldest first, with issue dates relative to `today` and per-supplier invoice numbers."""
    today = today or date.today()
    ordered = sorted(_scenario_definitions(), key=lambda item: -item.days_ago)
    counters: dict[str, int] = {}
    numbers: dict[str, str] = {}
    resolved = []
    for item in ordered:
        issue_date = today - timedelta(days=item.days_ago)
        if item.duplicate_of:
            number = numbers[item.duplicate_of]
        else:
            counter = counters.get(item.supplier.tax_id, item.supplier.first_number)
            counters[item.supplier.tax_id] = counter + 1
            number = f"{item.supplier.prefix}{issue_date.year % 100:02d}/{counter:04d}"
        numbers[item.key] = number
        resolved.append(replace(item, issue_date=issue_date, invoice_number=number))
    if len(resolved) != EXPECTED_INVOICE_COUNT:
        raise SeedError(f"Se esperaban {EXPECTED_INVOICE_COUNT} escenarios y hay {len(resolved)}")
    return resolved


# --------------------------------------------------------------------------- Seeding


def is_seeded(db: Session) -> bool:
    return db.scalar(select(Invoice.id).where(Invoice.notes.startswith(MARKER)).limit(1)) is not None


def reset(db: Session) -> tuple[int, int]:
    """Delete seeded invoices (with lines and logs) and the suppliers left without invoices."""
    invoices = list(db.scalars(select(Invoice).where(Invoice.notes.startswith(MARKER))))
    supplier_ids = {invoice.supplier_id for invoice in invoices}
    for invoice in invoices:
        db.delete(invoice)
    db.flush()
    orphans = (
        list(db.scalars(select(Supplier).where(Supplier.id.in_(supplier_ids), ~Supplier.invoices.any())))
        if supplier_ids
        else []
    )
    for supplier in orphans:
        db.delete(supplier)
    db.flush()
    return len(invoices), len(orphans)


def failed_rules(report: AuditReport, severity: Severity) -> list[str]:
    return sorted(check.rule_code for check in report.checks if not check.passed and check.severity is severity)


def _verify(
    scenario: Scenario,
    invoice: Invoice,
    report: AuditReport,
    created: dict[str, Invoice],
    exporter: FacturaeExporter,
) -> None:
    problems = []
    if report.status is not scenario.expected_status:
        problems.append(f"estado {report.status.value} en lugar de {scenario.expected_status.value}")
    if (errors := failed_rules(report, Severity.ERROR)) != sorted(scenario.expected_errors):
        problems.append(f"errores {errors} en lugar de {sorted(scenario.expected_errors)}")
    if (warnings := failed_rules(report, Severity.WARNING)) != sorted(scenario.expected_warnings):
        problems.append(f"advertencias {warnings} en lugar de {sorted(scenario.expected_warnings)}")
    if scenario.duplicate_of and created[scenario.duplicate_of].id not in report.duplicate_of:
        problems.append(f"no referencia a la factura original {scenario.duplicate_of}")
    if problems:
        raise SeedError(f"Escenario {scenario.key} ({invoice.full_number}): " + "; ".join(problems))
    if scenario.is_ready_for_face:
        try:
            exporter.to_xml(invoice, sign=False)
        except FacturaeExportError as exc:
            raise SeedError(
                f"La factura {invoice.full_number} ({scenario.key}) no se exporta a Facturae: {exc}"
            ) from exc


def seed(db: Session, today: date | None = None) -> list[Invoice]:
    """Create and audit the 25 demo invoices (flush only: the caller owns the transaction)."""
    if is_seeded(db):
        raise AlreadySeededError("Ya existen facturas de demostración; use --reset para recrearlas")
    auditor = InvoiceAuditor(db)
    exporter = FacturaeExporter()
    created: dict[str, Invoice] = {}
    invoices: list[Invoice] = []
    for scenario in build_scenarios(today):
        invoice = create_invoice_manual(db, scenario.to_payload())
        report = auditor.audit(invoice)
        _verify(scenario, invoice, report, created, exporter)
        created[scenario.key] = invoice
        invoices.append(invoice)
    db.flush()
    return invoices


# --------------------------------------------------------------------------- Output

STATUS_LABELS = {
    AuditStatus.VALIDATED: "Validada",
    AuditStatus.DISCREPANCY: "Discrepancia",
    AuditStatus.DUPLICATE: "Duplicada",
    AuditStatus.PENDING: "Pendiente",
}


def _eur_es(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".") + " €"


def first_failing_rule(invoice: Invoice) -> str:
    logs = [log for log in invoice.validation_logs if log.run_id == invoice.last_audit_run_id]
    return next((log.rule_code for log in logs if not log.passed and log.severity is Severity.ERROR), "-")


def summarize(invoices: Sequence[Invoice]) -> str:
    rows = [
        (
            invoice.full_number,
            invoice.supplier.name[:38],
            _eur_es(invoice.total_amount),
            STATUS_LABELS[invoice.audit_status],
            str(invoice.warning_count),
            first_failing_rule(invoice),
        )
        for invoice in invoices
    ]
    headers = ("Número", "Proveedor", "Total", "Estado", "Adv.", "Primera regla fallida")
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    right = {2, 4}

    def render(row: Sequence[str]) -> str:
        return "  ".join(cell.rjust(widths[i]) if i in right else cell.ljust(widths[i]) for i, cell in enumerate(row))

    status = Counter(invoice.audit_status for invoice in invoices)
    validated = [invoice for invoice in invoices if invoice.audit_status is AuditStatus.VALIDATED]
    ready = sum(1 for invoice in validated if invoice.warning_count == 0)
    bad_ids = sum(1 for invoice in invoices if first_failing_rule(invoice) == SUPPLIER_TAX_ID)
    lines = [render(headers), "  ".join("-" * width for width in widths), *(render(row) for row in rows), ""]
    lines += [
        f"Total de facturas: {len(invoices)}",
        f"  Listas para FACe (validadas sin advertencias): {ready}",
        f"  Validadas con advertencias: {len(validated) - ready}",
        f"  Error matemático (discrepancias): {status[AuditStatus.DISCREPANCY] - bad_ids}",
        f"  NIF/CIF del emisor inválido (discrepancias): {bad_ids}",
        f"  Duplicadas: {status[AuditStatus.DUPLICATE]}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Carga 25 facturas de demostración auditadas con InvoiceAuditor.")
    parser.add_argument("--reset", action="store_true", help="elimina antes las facturas de demostración existentes")
    parser.add_argument(
        "--database-url", help="URL de la base de datos (por defecto, DATABASE_URL de la configuración)"
    )
    return parser.parse_args(argv)


def _prefer_lossless_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _prefer_lossless_output()
    args = _parse_args(argv)
    if args.database_url:
        engine = build_engine(args.database_url)
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        label = make_url(args.database_url).render_as_string(hide_password=True)
    else:
        engine, factory = default_engine, SessionLocal
        label = engine.url.render_as_string(hide_password=True)

    try:
        init_db(engine)
        print(f"Base de datos: {label}")
        with factory() as db:
            try:
                if args.reset:
                    removed, orphans = reset(db)
                    print(f"Eliminadas {removed} facturas de demostración y {orphans} proveedores sin facturas.")
                elif is_seeded(db):
                    print(
                        "Ya existen facturas de demostración; no se ha cambiado nada. "
                        "Use --reset para eliminarlas y volver a crearlas."
                    )
                    return 0
                invoices = seed(db)
                db.commit()
            except Exception as exc:  # noqa: BLE001 - any failure must roll the whole seed back
                db.rollback()
                print(f"Error: {exc}. Se han revertido todos los cambios.", file=sys.stderr)
                return 1
            print(summarize(invoices))
    finally:
        if args.database_url:
            engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
