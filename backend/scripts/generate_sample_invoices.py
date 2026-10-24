"""Generate sample Spanish invoices (PDF + PNG) used to demo and test the extraction and audit pipeline.

Run from backend/:  python scripts/generate_sample_invoices.py [output_dir]

All tax ids are valid under the real NIF/CIF control algorithms, except the one in the "invalid CIF" sample, whose
control character is deliberately wrong.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

SAMPLES_DIR = BACKEND_DIR / "samples"
CENT = Decimal("0.01")
HUNDRED = Decimal(100)


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Party:
    name: str
    tax_id: str
    address: str
    postal_code: str
    city: str
    province: str
    email: str | None = None
    phone: str | None = None
    printed_tax_id: str | None = None  # how the id is printed (prefix / dashes); defaults to tax_id

    @property
    def tax_id_label(self) -> str:
        return "NIF" if self.tax_id[:1].isdigit() else "CIF"


@dataclass(frozen=True)
class Line:
    concept: str
    quantity: Decimal
    unit_price: Decimal
    vat_rate: Decimal
    irpf_rate: Decimal = Decimal(0)
    printed_base: Decimal | None = None  # set only to print a deliberately wrong line amount

    @property
    def base(self) -> Decimal:
        return self.printed_base if self.printed_base is not None else _money(self.quantity * self.unit_price)

    @property
    def vat_amount(self) -> Decimal:
        return _money(self.base * self.vat_rate / HUNDRED)

    @property
    def irpf_amount(self) -> Decimal:
        return _money(self.base * self.irpf_rate / HUNDRED)


@dataclass(frozen=True)
class SampleInvoice:
    filename: str
    description: str
    supplier: Party
    buyer: Party
    number: str
    series: str | None
    issue_date: date
    due_date: date
    lines: tuple[Line, ...]
    operation_date: date | None = None
    dir3: tuple[str, str, str] | None = None  # (oficina contable, órgano gestor, unidad tramitadora)
    decimal_style: str = "es"  # "es" -> 1.234,56   "plain" -> 1234.56
    date_style: str = "dmy"  # "dmy" -> 15/03/2024   "iso" -> 2024-03-15
    currency_suffix: str = " €"
    iban: str = "ES91 2100 0418 4502 0005 1332"
    expected_issues: tuple[str, ...] = field(default=())

    @property
    def has_irpf(self) -> bool:
        return any(line.irpf_rate > 0 for line in self.lines)

    @property
    def tax_base(self) -> Decimal:
        return sum((line.base for line in self.lines), Decimal(0))

    @property
    def vat_by_rate(self) -> dict[Decimal, Decimal]:
        grouped: dict[Decimal, Decimal] = {}
        for line in self.lines:
            grouped[line.vat_rate] = grouped.get(line.vat_rate, Decimal(0)) + line.vat_amount
        return dict(sorted(grouped.items(), reverse=True))

    @property
    def total_vat(self) -> Decimal:
        return sum(self.vat_by_rate.values(), Decimal(0))

    @property
    def total_irpf(self) -> Decimal:
        return sum((line.irpf_amount for line in self.lines), Decimal(0))

    @property
    def total_amount(self) -> Decimal:
        return self.tax_base + self.total_vat - self.total_irpf


def _d(value: str) -> Decimal:
    return Decimal(value)


SAMPLES: tuple[SampleInvoice, ...] = (
    SampleInvoice(
        filename="01_factura_valida_iva21.pdf",
        description="Factura válida, un único tipo de IVA (21 %)",
        supplier=Party(
            "Desarrollos Web Mediterráneo, S.L.",
            "B46271896",
            "Calle Gran Vía 45",
            "46005",
            "Valencia",
            "Valencia",
            "facturas@dwmediterraneo.example",
            "961 234 567",
            printed_tax_id="ES-B46271896",
        ),
        buyer=Party("Comercial Ribera, S.A.", "A28730547", "Avenida de América 112", "28028", "Madrid", "Madrid"),
        number="2024-0001",
        series="A",
        issue_date=date(2024, 3, 15),
        due_date=date(2024, 4, 14),
        lines=(
            Line("Desarrollo de tienda online (fase 1)", _d("1"), _d("2400.00"), _d("21")),
            Line("Mantenimiento mensual del servidor", _d("3"), _d("85.50"), _d("21")),
        ),
    ),
    SampleInvoice(
        filename="02_factura_iva_mixto_21_10_4.pdf",
        description="Factura válida con IVA mixto 21 % / 10 % / 4 % (decimales con punto y fechas ISO)",
        supplier=Party(
            "Suministros Integrales del Norte, S.A.",
            "A79156022",
            "Polígono Industrial Los Olmos 7",
            "47012",
            "Valladolid",
            "Valladolid",
            "administracion@sinorte.example",
            "983 456 789",
            printed_tax_id="A-79156022",
        ),
        buyer=Party("Hostelería Castellana, S.L.", "B83504175", "Calle Mayor 23", "09003", "Burgos", "Burgos"),
        number="2024/0153",
        series=None,
        issue_date=date(2024, 5, 20),
        operation_date=date(2024, 5, 18),
        due_date=date(2024, 6, 19),
        lines=(
            Line("Material de oficina (papel y toner)", _d("10"), _d("12.40"), _d("21")),
            Line("Menu de empresa (catering)", _d("20"), _d("11.00"), _d("10")),
            Line("Pan y productos de panaderia", _d("50"), _d("1.10"), _d("4")),
            Line("Libros tecnicos", _d("4"), _d("29.95"), _d("4")),
        ),
        decimal_style="plain",
        date_style="iso",
        currency_suffix=" EUR",
    ),
    SampleInvoice(
        filename="03_factura_profesional_irpf15.pdf",
        description="Factura de profesional autónomo con retención de IRPF del 15 %",
        supplier=Party(
            "María Pilar Fernández Soto",
            "34567891H",
            "Calle del Carmen 18, 2º B",
            "41003",
            "Sevilla",
            "Sevilla",
            "mpfernandez@asesoria.example",
            "954 321 098",
            printed_tax_id="34567891-H",
        ),
        buyer=Party(
            "Gestión Patrimonial del Tajo, S.L.", "B19283746", "Paseo de la Castellana 200", "28046", "Madrid", "Madrid"
        ),
        number="2024-012",
        series="P",
        issue_date=date(2024, 4, 2),
        due_date=date(2024, 4, 17),
        lines=(
            Line("Honorarios de asesoría fiscal 1T 2024", _d("1"), _d("1200.00"), _d("21"), _d("15")),
            Line("Elaboración de declaraciones", _d("4"), _d("75.00"), _d("21"), _d("15")),
        ),
    ),
    SampleInvoice(
        filename="04_factura_error_calculo_linea.pdf",
        description="Factura con un error de cálculo en una línea (2 x 150,00 impreso como 330,00)",
        supplier=Party(
            "Talleres Hermanos Vidal, S.L.",
            "B58120932",
            "Calle Real 9",
            "08205",
            "Sabadell",
            "Barcelona",
            "taller@hermanosvidal.example",
            "937 654 321",
        ),
        buyer=Party(
            "Transportes Rápidos Levante, S.A.", "A40367120", "Avenida del Puerto 50", "46023", "Valencia", "Valencia"
        ),
        number="2024-0087",
        series="T",
        issue_date=date(2024, 7, 8),
        due_date=date(2024, 8, 7),
        lines=(
            Line(
                "Sustitución de rodamientos y mano de obra", _d("2"), _d("150.00"), _d("21"), printed_base=_d("330.00")
            ),
            Line("Revisión general del vehículo", _d("1"), _d("90.00"), _d("21")),
        ),
        expected_issues=("LINE_BASE",),
    ),
    SampleInvoice(
        filename="05_factura_cif_proveedor_invalido.pdf",
        description="Factura con CIF de proveedor inválido (dígito de control incorrecto)",
        supplier=Party(
            "Reformas Integrales Costa Blanca, S.L.",
            "B03456788",
            "Avenida de Elche 77",
            "03008",
            "Alicante",
            "Alicante",
            "info@reformascostablanca.example",
            "965 111 222",
        ),
        buyer=Party("Comercial Ribera, S.A.", "A28730547", "Avenida de América 112", "28028", "Madrid", "Madrid"),
        number="2024-0301",
        series="R",
        issue_date=date(2024, 9, 12),
        due_date=date(2024, 10, 12),
        lines=(
            Line("Reforma de baño completa (vivienda)", _d("1"), _d("3200.00"), _d("10")),
            Line("Suministro de materiales varios", _d("1"), _d("780.50"), _d("21")),
        ),
        expected_issues=("SUPPLIER_TAX_ID",),
    ),
    SampleInvoice(
        filename="06_factura_face_dir3.pdf",
        description="Factura a una administración pública con códigos DIR3 para FACe",
        supplier=Party(
            "Servicios Informáticos Castilla, S.L.",
            "B19283746",
            "Calle San Fernando 31",
            "28805",
            "Alcalá de Henares",
            "Madrid",
            "facturacion@sicastilla.example",
            "918 765 432",
        ),
        buyer=Party(
            "Ayuntamiento de Alcalá de Henares",
            "P2800500G",
            "Plaza de Cervantes 12",
            "28801",
            "Alcalá de Henares",
            "Madrid",
        ),
        number="FAC/2024/0457",
        series=None,
        issue_date=date(2024, 11, 4),
        due_date=date(2024, 12, 4),
        lines=(
            Line("Licencias de software de gestión (anual)", _d("25"), _d("48.00"), _d("21")),
            Line("Soporte técnico y mantenimiento", _d("12"), _d("60.00"), _d("21")),
            Line("Formación presencial para el personal", _d("1"), _d("350.00"), _d("21")),
        ),
        dir3=("L01280058", "L01280059", "L01280060"),
    ),
)

# --------------------------------------------------------------------------- Formatting


def format_money(value: Decimal, style: str) -> str:
    text = f"{value:,.2f}"
    if style == "es":
        text = text.replace(",", "\0").replace(".", ",").replace("\0", ".")
    else:
        text = text.replace(",", "")
    return text


def format_quantity(value: Decimal, style: str) -> str:
    text = f"{value.normalize():f}"
    return text.replace(".", ",") if style == "es" else text


def format_rate(value: Decimal) -> str:
    return f"{value.normalize():f} %"


def format_date(value: date, style: str) -> str:
    return value.strftime("%d/%m/%Y") if style == "dmy" else value.isoformat()


# --------------------------------------------------------------------------- PDF rendering

BLUE = (0.10, 0.20, 0.38)
GREY = (0.93, 0.94, 0.96)
LEFT = 40
RIGHT = 555


class _PdfWriter:
    def __init__(self, invoice: SampleInvoice, path: Path) -> None:
        self.invoice = invoice
        self.pdf = canvas.Canvas(str(path), pagesize=A4)
        self.pdf.setTitle(f"Factura {invoice.number}")
        self.pdf.setAuthor(invoice.supplier.name)
        self.y = 0.0

    def render(self) -> None:
        self._header()
        self._party("EMISOR", self.invoice.supplier, with_dir3=False)
        self._party("CLIENTE", self.invoice.buyer, with_dir3=True)
        self._table()
        self._totals()
        self._footer()
        self.pdf.showPage()
        self.pdf.save()

    def _text(self, x: float, text: str, font: str = "Helvetica", size: int = 10, step: float = 14) -> None:
        self.pdf.setFont(font, size)
        self.pdf.drawString(x, self.y, text)
        self.y -= step

    def _header(self) -> None:
        invoice = self.invoice
        self.pdf.setFillColorRGB(*BLUE)
        self.pdf.rect(0, 782, 595, 60, stroke=0, fill=1)
        self.pdf.setFillColorRGB(1, 1, 1)
        self.pdf.setFont("Helvetica-Bold", 24)
        self.pdf.drawString(LEFT, 803, "FACTURA")
        self.pdf.setFillColorRGB(0, 0, 0)
        self.y = 758
        style = invoice.date_style
        if invoice.series:
            self._text(LEFT, f"Serie: {invoice.series}")
        self._text(LEFT, f"Factura nº: {invoice.number}", "Helvetica-Bold", 11, 15)
        self._text(LEFT, f"Fecha de emisión: {format_date(invoice.issue_date, style)}")
        if invoice.operation_date:
            self._text(LEFT, f"Fecha de operación: {format_date(invoice.operation_date, style)}")
        self._text(LEFT, f"Fecha de vencimiento: {format_date(invoice.due_date, style)}", step=22)

    def _party(self, title: str, party: Party, with_dir3: bool) -> None:
        self.pdf.setFillColorRGB(*BLUE)
        self._text(LEFT, title, "Helvetica-Bold", 11, 15)
        self.pdf.setFillColorRGB(0, 0, 0)
        self._text(LEFT, party.name, "Helvetica-Bold", 10)
        self._text(LEFT, f"{party.tax_id_label}: {party.printed_tax_id or party.tax_id}")
        self._text(LEFT, party.address)
        self._text(LEFT, f"{party.postal_code} {party.city} ({party.province})")
        contact = []
        if party.email:
            contact.append(f"Email: {party.email}")
        if party.phone:
            contact.append(f"Tel: {party.phone}")
        for entry in contact:
            self._text(LEFT, entry)
        if with_dir3 and self.invoice.dir3:
            office, body, unit = self.invoice.dir3
            self._text(LEFT, f"Oficina contable (DIR3): {office}")
            self._text(LEFT, f"Órgano gestor (DIR3): {body}")
            self._text(LEFT, f"Unidad tramitadora (DIR3): {unit}")
        self.y -= 10

    def _columns(self) -> list[tuple[str, str, float]]:
        if self.invoice.has_irpf:
            return [
                ("Cant.", "qty", 300),
                ("Precio unit.", "price", 385),
                ("IVA %", "vat", 435),
                ("IRPF %", "irpf", 490),
                ("Importe", "amount", RIGHT),
            ]
        return [
            ("Cant.", "qty", 320),
            ("Precio unit.", "price", 410),
            ("IVA %", "vat", 470),
            ("Importe", "amount", RIGHT),
        ]

    def _table(self) -> None:
        invoice = self.invoice
        style, suffix = invoice.decimal_style, invoice.currency_suffix
        columns = self._columns()
        self.pdf.setFillColorRGB(*GREY)
        self.pdf.rect(LEFT - 4, self.y - 4, RIGHT - LEFT + 8, 18, stroke=0, fill=1)
        self.pdf.setFillColorRGB(0, 0, 0)
        self.pdf.setFont("Helvetica-Bold", 9)
        self.pdf.drawString(LEFT, self.y, "Concepto")
        for label, _, x in columns:
            self.pdf.drawRightString(x, self.y, label)
        self.y -= 20
        self.pdf.setFont("Helvetica", 9)
        for line in invoice.lines:
            if stringWidth(line.concept, "Helvetica", 9) > columns[0][2] - LEFT - 95:
                raise ValueError(f"Concept too long for the layout: {line.concept}")
            cells = {
                "qty": format_quantity(line.quantity, style),
                "price": format_money(line.unit_price, style) + suffix,
                "vat": format_rate(line.vat_rate),
                "irpf": format_rate(line.irpf_rate),
                "amount": format_money(line.base, style) + suffix,
            }
            self.pdf.drawString(LEFT, self.y, line.concept)
            for _, key, x in columns:
                self.pdf.drawRightString(x, self.y, cells[key])
            self.y -= 16
        self.pdf.setStrokeColorRGB(*BLUE)
        self.pdf.line(LEFT - 4, self.y + 8, RIGHT + 4, self.y + 8)
        self.y -= 14

    def _totals(self) -> None:
        invoice = self.invoice
        style, suffix = invoice.decimal_style, invoice.currency_suffix

        def row(label: str, value: Decimal, bold: bool = False) -> None:
            self.pdf.setFont("Helvetica-Bold" if bold else "Helvetica", 11 if bold else 10)
            self.pdf.drawString(370, self.y, label)
            self.pdf.drawRightString(RIGHT, self.y, format_money(value, style) + suffix)
            self.y -= 16

        row("Base imponible:", invoice.tax_base)
        for rate, amount in invoice.vat_by_rate.items():
            row(f"IVA {rate.normalize():f}%:", amount)
        if len(invoice.vat_by_rate) > 1:
            row("Total IVA:", invoice.total_vat)
        if invoice.has_irpf:
            rate = max(line.irpf_rate for line in invoice.lines)
            row(f"Retención IRPF {rate.normalize():f}%:", -invoice.total_irpf)
        self.y -= 4
        row("TOTAL FACTURA:", invoice.total_amount, bold=True)

    def _footer(self) -> None:
        self.y = 90
        self._text(LEFT, "Forma de pago: transferencia bancaria", size=9, step=12)
        self._text(LEFT, f"IBAN: {self.invoice.iban}", size=9)


def write_pdf(invoice: SampleInvoice, path: Path) -> None:
    _PdfWriter(invoice, path).render()


# --------------------------------------------------------------------------- PNG rendering


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def write_png(invoice: SampleInvoice, path: Path) -> None:
    """Rasterised invoice, as if photographed: the demo mode has no OCR, so it takes the synthetic-data path."""
    image = Image.new("RGB", (1240, 1000), (250, 250, 247))
    draw = ImageDraw.Draw(image)
    title_font, body_font = _load_font(44), _load_font(26)
    style, suffix = invoice.decimal_style, invoice.currency_suffix
    draw.text((60, 50), "FACTURA", font=title_font, fill=(25, 50, 95))
    rows = [
        f"Factura nº: {invoice.number}    Fecha: {format_date(invoice.issue_date, style)}",
        f"Emisor: {invoice.supplier.name}  ({invoice.supplier.tax_id_label} {invoice.supplier.tax_id})",
        f"Cliente: {invoice.buyer.name}  ({invoice.buyer.tax_id_label} {invoice.buyer.tax_id})",
        "",
        *[
            f"{line.concept}  x{format_quantity(line.quantity, style)}  {format_money(line.base, style)}{suffix}"
            for line in invoice.lines
        ],
        "",
        f"Base imponible: {format_money(invoice.tax_base, style)}{suffix}",
        f"IVA: {format_money(invoice.total_vat, style)}{suffix}",
        f"TOTAL: {format_money(invoice.total_amount, style)}{suffix}",
    ]
    y = 150
    for row in rows:
        draw.text((60, y), row, font=body_font, fill=(30, 30, 30))
        y += 48
    image.save(path, format="PNG")


# --------------------------------------------------------------------------- Entry point

IMAGE_SAMPLE_FILENAME = "07_factura_imagen_demo.png"


def generate_all(output_dir: Path = SAMPLES_DIR) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for invoice in SAMPLES:
        path = output_dir / invoice.filename
        write_pdf(invoice, path)
        written.append(path)
    image_path = output_dir / IMAGE_SAMPLE_FILENAME
    write_png(SAMPLES[0], image_path)
    written.append(image_path)
    return written


def main(argv: list[str]) -> int:
    output_dir = Path(argv[1]) if len(argv) > 1 else SAMPLES_DIR
    paths = generate_all(output_dir)
    by_name = {invoice.filename: invoice for invoice in SAMPLES}
    for path in paths:
        invoice = by_name.get(path.name)
        if invoice is None:
            print(f"{path.name:<45} imagen (el modo demo no tiene OCR: genera datos sintéticos)")
            continue
        print(
            f"{path.name:<45} base {invoice.tax_base:>9} IVA {invoice.total_vat:>8} "
            f"IRPF {invoice.total_irpf:>7} total {invoice.total_amount:>9}  {invoice.description}"
        )
    print(f"\n{len(paths)} archivos generados en {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
