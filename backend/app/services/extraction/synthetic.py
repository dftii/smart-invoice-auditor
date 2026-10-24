"""Deterministic synthetic invoice generator for the demo mode.

Everything produced here is FABRICATED: results are flagged ``is_synthetic`` so they can never be
validated or exported.
"""

from __future__ import annotations

import hashlib
import random
import re
from datetime import date, timedelta
from decimal import Decimal

from app.core.money import CENT, HUNDRED, line_base, percent_of
from app.models import ExtractionMethod
from app.schemas import ExtractedLine, ExtractedParty, ExtractionResult
from app.services.extraction.parsing import fold
from app.services.tax_id import CIF_CONTROL_LETTERS, CIF_LETTER_ONLY, NIF_LETTERS, compute_cif_control

_COMPANY_PREFIXES = (
    "Servicios",
    "Soluciones",
    "Consultoría",
    "Distribuciones",
    "Talleres",
    "Construcciones",
    "Suministros",
    "Ingeniería",
    "Logística",
    "Gráficas",
    "Instalaciones",
    "Tecnología",
)
_COMPANY_CORES = (
    "Levante",
    "Ibérica",
    "Mediterránea",
    "Castellana",
    "Atlántica",
    "del Norte",
    "del Tajo",
    "Cantábrica",
    "Andaluza",
    "Meseta",
    "Costa Azahar",
    "Sierra Nevada",
    "del Ebro",
    "Galaico",
    "Manchega",
)
_COMPANY_SUFFIXES = ("S.L.", "S.A.", "S.L.U.")
_FIRST_NAMES = ("María", "José", "Antonio", "Carmen", "Manuel", "Laura", "Javier", "Elena", "Francisco", "Marta")
_SURNAMES = ("García", "Fernández", "López", "Martínez", "Sánchez", "Pérez", "Gómez", "Ruiz", "Díaz", "Moreno")
_LOCATIONS = (
    ("Madrid", "Madrid", "28"),
    ("Barcelona", "Barcelona", "08"),
    ("Valencia", "Valencia", "46"),
    ("Sevilla", "Sevilla", "41"),
    ("Zaragoza", "Zaragoza", "50"),
    ("Málaga", "Málaga", "29"),
    ("Bilbao", "Bizkaia", "48"),
    ("Murcia", "Murcia", "30"),
    ("Alicante", "Alicante", "03"),
    ("Valladolid", "Valladolid", "47"),
    ("Granada", "Granada", "18"),
    ("Vigo", "Pontevedra", "36"),
)
_STREETS = (
    "Calle Mayor",
    "Avenida de la Constitución",
    "Calle de Alcalá",
    "Paseo de la Castellana",
    "Calle San Fernando",
    "Avenida de Andalucía",
    "Calle Real",
    "Gran Vía",
    "Calle del Carmen",
    "Avenida del Mediterráneo",
)
_PUBLIC_BODIES = (
    ("Ayuntamiento de {city}", "P"),
    ("Diputación Provincial de {province}", "P"),
    ("Universidad de {city}", "Q"),
)

# (concept, qty range, unit price range, VAT %)
_BUSINESS_CATALOG = (
    ("Servicios de consultoría informática (horas)", (8, 60), (45, 95), 21),
    ("Licencia anual de software de gestión", (1, 10), (120, 480), 21),
    ("Mantenimiento de equipos informáticos", (1, 6), (60, 150), 21),
    ("Material de oficina y papelería", (5, 40), (3, 25), 21),
    ("Campaña de marketing digital", (1, 2), (600, 2500), 21),
    ("Transporte y logística de mercancías", (1, 8), (90, 380), 21),
    ("Menús de empresa (servicio de catering)", (10, 60), (9, 14), 10),
    ("Alojamiento hotelero (noches)", (2, 12), (70, 140), 10),
    ("Obras de reforma y rehabilitación", (1, 1), (1800, 9000), 10),
    ("Pan y productos de panadería", (20, 200), (1, 3), 4),
    ("Libros y material didáctico impreso", (3, 50), (8, 35), 4),
    ("Medicamentos de uso humano", (5, 40), (4, 30), 4),
)
_PROFESSIONAL_CATALOG = (
    ("Honorarios de asesoría fiscal y contable", (1, 3), (250, 900), 21),
    ("Honorarios de asesoramiento jurídico (horas)", (2, 15), (80, 160), 21),
    ("Servicios de diseño gráfico y maquetación", (1, 5), (180, 650), 21),
    ("Traducción jurada de documentación", (1, 10), (60, 220), 21),
)
_DISCREPANCY_KINDS = ("total_amount", "line_base", "line_vat", "supplier_tax_id", "due_date", "vat_rate")



def _make_cif(org_letter: str, digits: str) -> str:
    digit, letter = compute_cif_control(org_letter, digits)
    return f"{org_letter}{digits}{letter if org_letter in CIF_LETTER_ONLY else digit}"


def _make_nif(number: int) -> str:
    return f"{number:08d}{NIF_LETTERS[number % 23]}"


def _corrupt_tax_id(tax_id: str) -> str:
    """Replace the control character with one that is guaranteed to be wrong for the real algorithm."""
    body, control = tax_id[:-1], tax_id[-1]
    if tax_id[0].isdigit():
        return body + next(letter for letter in NIF_LETTERS if letter != control)
    if tax_id[0] in CIF_LETTER_ONLY:
        return body + next(letter for letter in CIF_CONTROL_LETTERS if letter != control)
    return body + str((int(control) + 1) % 10)


def synthetic_discrepancy_kind(content: bytes) -> str | None:
    """Discrepancy deliberately injected into the synthetic invoice of this content (None if it is consistent)."""
    digest = hashlib.sha256(content).digest()
    if int.from_bytes(digest[:4], "big") % 5 != 0:
        return None
    return _DISCREPANCY_KINDS[digest[4] % len(_DISCREPANCY_KINDS)]


class SyntheticInvoiceFactory:
    """Deterministic generator of realistic, arithmetically valid Spanish invoices seeded by content hash."""

    def __init__(self, content: bytes) -> None:
        digest = hashlib.sha256(content).digest()
        self._rng = random.Random(int.from_bytes(digest, "big"))
        self.discrepancy = synthetic_discrepancy_kind(content)

    def _digits(self, count: int) -> str:
        return "".join(str(self._rng.randint(0, 9)) for _ in range(count))

    def _location(self) -> tuple[str, str, str]:
        city, province, prefix = self._rng.choice(_LOCATIONS)
        return city, province, f"{prefix}{self._rng.randint(1, 999):03d}"

    def _company(self) -> ExtractedParty:
        city, province, postal = self._location()
        name = f"{self._rng.choice(_COMPANY_PREFIXES)} {self._rng.choice(_COMPANY_CORES)}, {self._rng.choice(_COMPANY_SUFFIXES)}"
        org = self._rng.choice("ABABACDG")
        slug = re.sub(r"[^a-z0-9]", "", fold(name.split(",")[0]).lower())
        return ExtractedParty(
            name=name,
            tax_id=_make_cif(org, str(self._rng.randint(1, 9)) + self._digits(6)),
            address=f"{self._rng.choice(_STREETS)} {self._rng.randint(1, 120)}",
            postal_code=postal,
            city=city,
            province=province,
            email=f"facturacion@{slug}.example",
            phone=f"9{self._rng.randint(1, 8)}{self._digits(7)}",
        )

    def _professional(self) -> ExtractedParty:
        city, province, postal = self._location()
        name = f"{self._rng.choice(_FIRST_NAMES)} {self._rng.choice(_SURNAMES)} {self._rng.choice(_SURNAMES)}"
        slug = re.sub(r"[^a-z0-9]", "", fold(name).lower())
        return ExtractedParty(
            name=name,
            tax_id=_make_nif(self._rng.randint(10_000_000, 99_999_999)),
            address=f"{self._rng.choice(_STREETS)} {self._rng.randint(1, 120)}, {self._rng.randint(1, 6)}º",
            postal_code=postal,
            city=city,
            province=province,
            email=f"{slug}@correo.example",
            phone=f"6{self._digits(8)}",
        )

    def _public_body(self) -> tuple[ExtractedParty, tuple[str, str, str]]:
        city, province, postal = self._location()
        template, org = self._rng.choice(_PUBLIC_BODIES)
        party = ExtractedParty(
            name=template.format(city=city, province=province),
            tax_id=_make_cif(org, "28" + self._digits(5)),
            address=f"Plaza de España {self._rng.randint(1, 15)}",
            postal_code=postal,
            city=city,
            province=province,
        )
        base = f"L01{postal[:2]}{self._digits(4)}"
        return party, (base, f"L01{postal[:2]}{self._digits(4)}", f"L01{postal[:2]}{self._digits(4)}")

    def build(self) -> ExtractionResult:
        rng = self._rng
        professional = rng.random() < 0.25
        supplier = self._professional() if professional else self._company()
        public_buyer = rng.random() < 0.5
        dir3: tuple[str, str, str] | tuple[None, None, None] = (None, None, None)
        if public_buyer:
            buyer, dir3 = self._public_body()
        else:
            buyer = self._company()

        catalog = _PROFESSIONAL_CATALOG if professional else _BUSINESS_CATALOG
        irpf_rate = Decimal(15) if professional else Decimal(0)
        lines: list[ExtractedLine] = []
        for concept, qty_range, price_range, vat in rng.sample(catalog, rng.randint(1, min(4, len(catalog)))):
            quantity = Decimal(rng.randint(*qty_range))
            unit_price = Decimal(rng.randint(price_range[0] * 100, price_range[1] * 100)) / HUNDRED
            discount = Decimal(rng.choice((5, 10))) if rng.random() < 0.2 else Decimal(0)
            base = line_base(quantity, unit_price, discount)
            lines.append(
                ExtractedLine(
                    concept=concept,
                    quantity=quantity,
                    unit_price=unit_price.quantize(CENT),
                    discount_rate=discount,
                    vat_rate=Decimal(vat),
                    irpf_rate=irpf_rate,
                    line_base=base,
                    vat_amount=percent_of(base, Decimal(vat)),
                    irpf_amount=percent_of(base, irpf_rate),
                )
            )

        issue = date(2024, 1, 1) + timedelta(days=rng.randint(0, 700))
        due = issue + timedelta(days=rng.choice((15, 30, 45, 60)))
        series = rng.choice(("A", "F", None))
        number = f"{issue.year}-{rng.randint(1, 999):04d}"
        tax_id = supplier.tax_id
        total_adjustment = Decimal(0)

        if self.discrepancy is not None:
            index = rng.randrange(len(lines))
            target = lines[index]
            if self.discrepancy == "line_base":
                new_base = target.line_base + Decimal(rng.choice((5, 10, 25)))
                lines[index] = target.model_copy(
                    update={
                        "line_base": new_base,
                        "vat_amount": percent_of(new_base, target.vat_rate),
                        "irpf_amount": percent_of(new_base, target.irpf_rate),
                    }
                )
            elif self.discrepancy == "line_vat":
                lines[index] = target.model_copy(
                    update={"vat_amount": target.vat_amount + Decimal(rng.choice((1, 2, 5)))}
                )
            elif self.discrepancy == "vat_rate":
                lines[index] = target.model_copy(
                    update={"vat_rate": Decimal(19), "vat_amount": percent_of(target.line_base, Decimal(19))}
                )
            elif self.discrepancy == "supplier_tax_id" and tax_id:
                tax_id = _corrupt_tax_id(tax_id)
            elif self.discrepancy == "due_date":
                due = issue - timedelta(days=rng.choice((5, 10, 20)))
            elif self.discrepancy == "total_amount":
                total_adjustment = Decimal(rng.choice((10, 25, 50, 100)))

        tax_base = sum((line.line_base for line in lines), Decimal(0))
        total_vat = sum((line.vat_amount for line in lines), Decimal(0))
        total_irpf = sum((line.irpf_amount for line in lines), Decimal(0))
        return ExtractionResult(
            method=ExtractionMethod.DEMO,
            strategy_name="demo-synthetic-generator",
            confidence=0.3,
            is_synthetic=True,
            supplier=supplier.model_copy(update={"tax_id": tax_id}),
            buyer=buyer,
            invoice_number=number,
            invoice_series=series,
            issue_date=issue,
            operation_date=issue if rng.random() < 0.3 else None,
            due_date=due,
            tax_base=tax_base,
            total_vat=total_vat,
            total_irpf=total_irpf,
            total_amount=tax_base + total_vat - total_irpf + total_adjustment,
            lines=lines,
            dir3_accounting_office=dir3[0],
            dir3_managing_body=dir3[1],
            dir3_processing_unit=dir3[2],
        )

