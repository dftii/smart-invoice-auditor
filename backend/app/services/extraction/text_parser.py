"""Heuristic parser for the text of Spanish invoices (offline / demo mode)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.core.money import HUNDRED, line_base, percent_of, round_money
from app.models import ExtractionMethod
from app.schemas import ExtractedLine, ExtractedParty, ExtractionResult
from app.services.extraction.base import MAX_RAW_TEXT_CHARS
from app.services.extraction.parsing import DATE_RE, date_from_match, fold, parse_decimal

_AMOUNT_RE = re.compile(r"(?<![\w.,])[-−–]?\d+(?:[.,]\d+)*")
_NUMERIC_CELL_RE = re.compile(r"[-−–]?\d+(?:[.,]\d+)*%?")
_RATE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_PHONE_RE = re.compile(r"(?:tel(?:efono|\.|f\.?)?|movil|phone)\s*[:.]?\s*(\+?\d[\d\s.\-]{7,}\d)", re.IGNORECASE)
_POSTAL_RE = re.compile(
    r"(?<!\d)(\d{5})(?!\d)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ.' \-]*?)\s*(?:\(\s*([^)]+?)\s*\))?\s*(?:[,;]|$)"
)
_TAX_ID_BODY = r"[A-Z0-9][\s\-.]?\d{7}[\s\-.]?[A-Z0-9]"
_LABELLED_TAX_ID_RE = re.compile(
    r"(?:\bC\.?I\.?F\.?|\bN\.?I\.?F\.?|\bN\.?I\.?E\.?|\bD\.?N\.?I\.?|\bVAT\b)\s*(?:n[º°o]\.?)?\s*[:.\-]?\s*"
    rf"(?:ES[\s\-]?)?({_TAX_ID_BODY})(?![A-Z0-9])",
    re.IGNORECASE,
)
_BARE_TAX_ID_RE = re.compile(
    r"(?<![A-Z0-9])(?:ES[\s\-]?)?([A-Z][\s\-]?\d{7}[\s\-]?[0-9A-Z]|\d{8}[\s\-]?[A-Z])(?![A-Z0-9])", re.IGNORECASE
)
_TAX_ID_SHAPE_RE = re.compile(r"[XYZA-HJ-NP-SUVW]\d{7}[0-9A-Z]|\d{8}[A-Z]")

_NUMBER_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"factura\s*(?:n[º°o]\.?|num(?:ero|\.)?|#)\s*[:.\-]?\s*([A-Z0-9][A-Z0-9\-/._]*)",
        r"n[º°]\.?\s*(?:de\s*)?factura\s*[:.\-]?\s*([A-Z0-9][A-Z0-9\-/._]*)",
        r"num(?:ero|\.)?\s*(?:de\s*)?factura\s*[:.\-]?\s*([A-Z0-9][A-Z0-9\-/._]*)",
        r"invoice\s*(?:n[oº°.]|number|#)\s*[:.\-]?\s*([A-Z0-9][A-Z0-9\-/._]*)",
        r"^factura\s*:\s*([A-Z0-9][A-Z0-9\-/._]*)",
        r"\bnumero\s*[:.\-]\s*([A-Z0-9][A-Z0-9\-/._]*)",
    )
]
_SERIES_RE = re.compile(r"\bserie\s*[:.\-]\s*([A-Z0-9]{1,10})\b", re.IGNORECASE)
_SUPPLIER_MARKER_RE = re.compile(
    r"^(?:datos\s+(?:del\s+)?)?(?:(?:emisor|proveedor|vendedor|expedidor|acreedor)\b|de(?=\s*:))\s*[:\-]?\s*(.*)$",
    re.IGNORECASE,
)
_BUYER_MARKER_RE = re.compile(
    r"^(?:datos\s+(?:del\s+)?)?(?:(?:cliente|destinatario|comprador|receptor|adquirente|facturar\s+a|deudor)\b"
    r"|para(?=\s*:))\s*[:\-]?\s*(.*)$",
    re.IGNORECASE,
)
_NAME_LABEL_RE = re.compile(r"^(?:nombre|razon\s+social|denominacion|empresa|titular)\s*[:\-]\s*(.+)$", re.IGNORECASE)
_LABEL_LINE_RE = re.compile(
    r"^(?:cif|nif|nie|dni|vat|n\.?i\.?f|c\.?i\.?f|direccion|domicilio|dir\b|tel|telf|telefono|movil|email|e-mail|"
    r"correo|web|cp\b|c\.p|oficina|organo|unidad|fecha|factura|serie|iban|forma de pago|ref|n[º°])(?![a-z])",
    re.IGNORECASE,
)
_ADDRESS_START_RE = re.compile(
    r"^(?:calle|c/|cl\b|avda|avenida|av\.|plaza|pza|paseo|camino|carretera|ctra|pol\.|poligono|travesia|ronda|via\b|"
    r"glorieta|urbanizacion|rua|carrer)",
    re.IGNORECASE,
)
_TABLE_COLUMN_KEYS = (
    ("qty", re.compile(r"\b(?:cant\w*|uds?\.?|unidades|qty)(?!\w)", re.IGNORECASE)),
    ("price", re.compile(r"\b(?:precio|p\.?\s*unit\w*|p/u|importe\s+unit\w*)", re.IGNORECASE)),
    ("discount", re.compile(r"\b(?:dto|descuento|desc)\b", re.IGNORECASE)),
    ("vat", re.compile(r"\bi\.?v\.?a\b", re.IGNORECASE)),
    ("irpf", re.compile(r"\b(?:irpf|ret\.?|retencion)", re.IGNORECASE)),
    ("amount", re.compile(r"\b(?:importe|total|base|subtotal)\b", re.IGNORECASE)),
)
_TABLE_CONCEPT_RE = re.compile(r"\b(?:concepto|descripcion|detalle|articulo|producto|servicio)\b", re.IGNORECASE)
_TOTALS_START_RE = re.compile(
    r"^(?:base\s+imponible|total\s+base|subtotal|importe\s+neto|total\b|importe\s+total|iva\b|i\.v\.a|cuota|"
    r"retencion|irpf|a\s+pagar)",
    re.IGNORECASE,
)
_DROP_ORDER = ("irpf", "discount", "vat", "qty")
_DEFAULT_COLUMNS = ["qty", "price", "vat", "irpf", "amount"]
_DIR3_LABELS = {
    "dir3_accounting_office": r"oficina\s+contable",
    "dir3_managing_body": r"organo\s+gestor",
    "dir3_processing_unit": r"unidad\s+tramitadora",
}
_CONFIDENCE_CAP = 0.95






def _line_amounts(text: str) -> list[Decimal]:
    """Monetary figures of a line, skipping percentages ("21%")."""
    amounts: list[Decimal] = []
    for match in _AMOUNT_RE.finditer(text):
        if re.match(r"\s*%", text[match.end() :]):
            continue
        try:
            amounts.append(parse_decimal(match.group()))
        except InvalidOperation:
            continue
    return amounts


def _rate_in(text: str) -> Decimal | None:
    match = _RATE_RE.search(text)
    if not match:
        return None
    try:
        return parse_decimal(match.group(1))
    except InvalidOperation:
        return None


@dataclass
class _Totals:
    base: Decimal | None = None
    vat: Decimal | None = None
    irpf: Decimal | None = None
    irpf_rate: Decimal | None = None
    total: Decimal | None = None
    vat_rates: tuple[Decimal, ...] = ()


class SpanishInvoiceTextParser:
    """Heuristic parser for text extracted from Spanish invoices. Returns None when the text is not an invoice."""

    def parse(self, text: str) -> ExtractionResult | None:
        lines = [cleaned for cleaned in (self._clean(raw) for raw in text.splitlines()) if cleaned]
        if len(lines) < 4:
            return None
        folded = [fold(line) for line in lines]
        warnings: list[str] = []

        number, series = self._find_number(folded)
        issue_date, operation_date, due_date = self._find_dates(folded)
        dir3 = self._find_dir3(folded)
        header_idx = self._find_table_header(folded)
        totals_idx = self._find_totals_start(folded, header_idx + 1 if header_idx is not None else 0)
        party_end = header_idx if header_idx is not None else (totals_idx if totals_idx is not None else len(lines))
        supplier, buyer = self._find_parties(lines, folded, party_end)

        totals = self._parse_totals(folded[totals_idx:]) if totals_idx is not None else _Totals()
        row_start = header_idx + 1 if header_idx is not None else 0
        row_end = totals_idx if totals_idx is not None else len(lines)
        columns = self._header_columns(folded[header_idx]) if header_idx is not None else list(_DEFAULT_COLUMNS)
        lines_out = self._parse_rows(lines[row_start:row_end], columns, totals, warnings, strict=header_idx is None)

        if not (number and issue_date and supplier.tax_id and supplier.name):
            return None
        if not lines_out and (totals.base is None or totals.total is None):
            return None
        if not lines_out:
            lines_out = [self._single_line_from_totals(totals, warnings)]

        tax_base = totals.base
        if tax_base is None:
            tax_base = sum((line.line_base for line in lines_out), Decimal(0))
            warnings.append("No se encontró la base imponible; se ha calculado sumando las líneas.")
        total_vat = totals.vat
        if total_vat is None:
            total_vat = sum((line.vat_amount for line in lines_out), Decimal(0))
            warnings.append("No se encontró la cuota de IVA total; se ha calculado sumando las líneas.")
        total_irpf = totals.irpf
        if total_irpf is None:
            total_irpf = sum((line.irpf_amount for line in lines_out), Decimal(0))
        total_amount = totals.total
        if total_amount is None:
            total_amount = tax_base + total_vat - total_irpf
            warnings.append("No se encontró el total de la factura; se ha calculado.")

        found = [
            number,
            issue_date,
            due_date,
            supplier.name,
            supplier.tax_id,
            supplier.address,
            buyer.name,
            buyer.tax_id,
            lines_out,
            totals.base,
            totals.vat,
            totals.total,
        ]
        confidence = round(min(_CONFIDENCE_CAP, sum(1 for item in found if item) / len(found)), 2)

        return ExtractionResult(
            method=ExtractionMethod.DEMO,
            strategy_name="demo-regex-parser",
            confidence=confidence,
            supplier=supplier,
            buyer=buyer,
            invoice_number=number,
            invoice_series=series,
            issue_date=issue_date,
            operation_date=operation_date,
            due_date=due_date,
            currency=self._find_currency(folded),
            tax_base=round_money(tax_base),
            total_vat=round_money(total_vat),
            total_irpf=round_money(total_irpf),
            total_amount=round_money(total_amount),
            lines=lines_out,
            dir3_accounting_office=dir3.get("dir3_accounting_office"),
            dir3_managing_body=dir3.get("dir3_managing_body"),
            dir3_processing_unit=dir3.get("dir3_processing_unit"),
            warnings=warnings,
            raw_text=text[:MAX_RAW_TEXT_CHARS],
        )

    @staticmethod
    def _clean(raw: str) -> str:
        text = raw.replace(" ", " ").replace("\t", "  ").rstrip()
        return re.sub(r"[ ]{3,}", "  ", text.strip())

    # ---- header fields

    @staticmethod
    def _find_number(folded: list[str]) -> tuple[str | None, str | None]:
        number: str | None = None
        for line in folded:
            for pattern in _NUMBER_PATTERNS:
                for match in pattern.finditer(line):
                    token = match.group(1).strip(".,;:-/")
                    if re.search(r"\d", token):
                        number = token
                        break
                if number:
                    break
            if number:
                break
        series: str | None = None
        for line in folded:
            match = _SERIES_RE.search(line)
            if match:
                series = match.group(1).upper()
                break
        return number, series

    @staticmethod
    def _find_dates(folded: list[str]) -> tuple[date | None, date | None, date | None]:
        issue: date | None = None
        operation: date | None = None
        due: date | None = None
        unlabelled: date | None = None
        for line in folded:
            lowered = line.lower()
            previous_end = 0
            for match in DATE_RE.finditer(lowered):
                parsed = date_from_match(match)
                label = lowered[max(previous_end, match.start() - 45) : match.start()]
                previous_end = match.end()
                if parsed is None:
                    continue
                if re.search(r"venc|vence|pago", label):
                    due = due or parsed
                elif re.search(r"operacion|entrega|devengo|prestacion", label):
                    operation = operation or parsed
                elif re.search(r"emision|expedicion|factura|fecha|date", label):
                    issue = issue or parsed
                else:
                    unlabelled = unlabelled or parsed
        return issue or unlabelled, operation, due

    @staticmethod
    def _find_dir3(folded: list[str]) -> dict[str, str]:
        found: dict[str, str] = {}
        for field_name, label in _DIR3_LABELS.items():
            pattern = re.compile(label + r"[^:\n]*?:\s*([A-Z0-9]{5,12})\b", re.IGNORECASE)
            fallback = re.compile(label + r"\s+([A-Z]{1,3}\d{5,9})\b", re.IGNORECASE)
            for line in folded:
                match = pattern.search(line) or fallback.search(line)
                if match and re.search(r"\d", match.group(1)):
                    found[field_name] = match.group(1).upper()
                    break
        return found

    @staticmethod
    def _find_currency(folded: list[str]) -> str:
        joined = " ".join(folded)
        if re.search(r"\bUSD\b|\$", joined):
            return "USD"
        if re.search(r"\bGBP\b|£", joined):
            return "GBP"
        return "EUR"

    # ---- parties

    def _find_parties(self, lines: list[str], folded: list[str], end: int) -> tuple[ExtractedParty, ExtractedParty]:
        supplier_idx = buyer_idx = None
        supplier_inline = buyer_inline = ""
        for index in range(end):
            if supplier_idx is None and (m := _SUPPLIER_MARKER_RE.match(folded[index])):
                supplier_idx, supplier_inline = index, lines[index][len(folded[index]) - len(m.group(1)) :]
            elif buyer_idx is None and (m := _BUYER_MARKER_RE.match(folded[index])):
                buyer_idx, buyer_inline = index, lines[index][len(folded[index]) - len(m.group(1)) :]

        if supplier_idx is None and buyer_idx is None:
            return self._parties_without_markers(lines, folded, end)

        def block(start: int | None, inline: str, other: int | None) -> list[str]:
            if start is None:
                return []
            stop = end
            if other is not None and other > start:
                stop = min(stop, other)
            inline_text = inline.strip()
            rows = [inline_text] if inline_text and not _LABEL_LINE_RE.match(fold(inline_text)) else []
            return rows + lines[start + 1 : stop]

        supplier = self._party_from_block(block(supplier_idx, supplier_inline, buyer_idx))
        buyer = self._party_from_block(block(buyer_idx, buyer_inline, supplier_idx))
        return supplier, buyer

    def _parties_without_markers(
        self, lines: list[str], folded: list[str], end: int
    ) -> tuple[ExtractedParty, ExtractedParty]:
        hits: list[int] = [i for i in range(end) if self._tax_id_in(folded[i])]
        parties: list[ExtractedParty] = []
        for position, index in enumerate(hits[:2]):
            start = max(0, index - 3) if position == 0 else hits[0] + 1
            parties.append(self._party_from_block(lines[start : index + 3]))
        while len(parties) < 2:
            parties.append(ExtractedParty())
        return parties[0], parties[1]

    @staticmethod
    def _tax_id_in(text: str) -> str | None:
        match = _LABELLED_TAX_ID_RE.search(text)
        if match:
            return re.sub(r"[\s\-.]", "", match.group(1)).upper()
        for candidate in _BARE_TAX_ID_RE.finditer(text):
            cleaned = re.sub(r"[\s\-.]", "", candidate.group(1)).upper()
            if _TAX_ID_SHAPE_RE.fullmatch(cleaned):
                return cleaned
        return None

    def _party_from_block(self, rows: list[str]) -> ExtractedParty:
        rows = [row for row in rows if row.strip()]
        tax_id = None
        for row in rows:
            tax_id = self._tax_id_in(fold(row))
            if tax_id:
                break

        name = address = postal = city = province = email = phone = None
        for row in rows:
            labelled = _NAME_LABEL_RE.match(fold(row))
            if labelled and name is None:
                name = row[len(row) - len(labelled.group(1)) :].strip()
        for row in rows:
            fold_row = fold(row)
            if email is None and (m := _EMAIL_RE.search(row)):
                email = m.group()
            if phone is None and (m := _PHONE_RE.search(fold_row)):
                phone = m.group(1).strip()
            if address is None:
                labelled_address = re.match(r"(?i)^(?:direccion|domicilio|dir\.?)\s*[:\-]\s*(.+)$", fold_row)
                if labelled_address:
                    address = row[len(row) - len(labelled_address.group(1)) :].strip()
                elif _ADDRESS_START_RE.match(fold_row):
                    address = row.strip()
            if (
                name is None
                and not _LABEL_LINE_RE.match(fold_row)
                and not _ADDRESS_START_RE.match(fold_row)
                and re.search(r"[A-Za-z]{3}", row)
                and not _EMAIL_RE.search(row)
                and not _POSTAL_RE.match(row)
                and not self._tax_id_in(fold_row)
            ):
                name = row.strip()

        for row in rows:
            match = _POSTAL_RE.search(row)
            if match:
                postal, city = match.group(1), match.group(2).strip(" .")
                province = match.group(3).strip() if match.group(3) else None
                if address and match.group(0).strip(" ,;") in address:
                    address = address.replace(match.group(0), "").strip(" ,;")
                break
        if address:
            address = re.sub(r",?\s*$", "", address)
        return ExtractedParty(
            name=name,
            tax_id=tax_id,
            address=address,
            postal_code=postal,
            city=city,
            province=province,
            email=email,
            phone=phone,
        )

    # ---- line table

    @staticmethod
    def _find_table_header(folded: list[str]) -> int | None:
        for index, line in enumerate(folded):
            if not _TABLE_CONCEPT_RE.search(line):
                continue
            keys = {key for key, pattern in _TABLE_COLUMN_KEYS if pattern.search(line)}
            if len(keys) >= 2:
                return index
        return None

    @staticmethod
    def _find_totals_start(folded: list[str], start: int) -> int | None:
        for index in range(start, len(folded)):
            if _TOTALS_START_RE.match(folded[index].lower()):
                return index
        return None

    @staticmethod
    def _header_columns(header: str) -> list[str]:
        price_spans = [m.span() for m in _TABLE_COLUMN_KEYS[1][1].finditer(header)]
        found: list[tuple[int, str]] = []
        for key, pattern in _TABLE_COLUMN_KEYS:
            for match in pattern.finditer(header):
                overlaps_price = key == "amount" and any(s < match.end() and match.start() < e for s, e in price_spans)
                if overlaps_price:
                    continue
                found.append((match.start(), key))
                if key != "amount":
                    break
        ordered: list[str] = []
        for _, key in sorted(found):
            if key == "amount":
                ordered = [k for k in ordered if k != "amount"]
            if key not in ordered:
                ordered.append(key)
        return ordered or list(_DEFAULT_COLUMNS)

    @staticmethod
    def _split_row(line: str, max_numbers: int) -> tuple[str, list[str]] | None:
        cleaned = re.sub(r"\s*(?:€|EUR\b)", "", line)
        cleaned = re.sub(r"(\d)\s+%", r"\1%", cleaned).strip()
        cells = re.split(r"\s{2,}", cleaned)
        if len(cells) >= 2 and not _NUMERIC_CELL_RE.fullmatch(cells[0]):
            numeric = cells[1:]
            if numeric and all(_NUMERIC_CELL_RE.fullmatch(cell) for cell in numeric) and len(numeric) <= max_numbers:
                return cells[0].strip(), numeric
        tokens = cleaned.split()
        numbers: list[str] = []
        while tokens and len(numbers) < max_numbers and _NUMERIC_CELL_RE.fullmatch(tokens[-1]):
            numbers.insert(0, tokens.pop())
        concept = " ".join(tokens).strip()
        if len(numbers) < 1 or not concept or not re.search(r"[A-Za-z]{2}", concept):
            return None
        return concept, numbers

    @staticmethod
    def _assign_columns(columns: list[str], count: int) -> list[str]:
        keys = list(columns)
        for drop in _DROP_ORDER:
            if len(keys) <= count:
                break
            if drop in keys:
                keys.remove(drop)
        return keys[-count:] if len(keys) > count else keys

    def _parse_rows(
        self, rows: list[str], columns: list[str], totals: _Totals, warnings: list[str], strict: bool
    ) -> list[ExtractedLine]:
        result: list[ExtractedLine] = []
        default_vat = totals.vat_rates[0] if len(totals.vat_rates) == 1 else None
        assumed_vat = False
        for row in rows:
            folded_row = fold(row).lower()
            if _TOTALS_START_RE.match(folded_row):
                continue
            if strict and ":" in row:
                continue
            split = self._split_row(row, len(columns))
            if split is None:
                if result and (row[:1].islower() or row[:1] == "(") and not _LABEL_LINE_RE.match(folded_row):
                    last = result[-1]
                    result[-1] = last.model_copy(update={"concept": f"{last.concept} {row.strip()}"})
                continue
            concept, cells = split
            if strict and len(cells) < 2:
                continue
            values: dict[str, Decimal] = {}
            try:
                for key, cell in zip(self._assign_columns(columns, len(cells)), cells, strict=False):
                    values[key] = parse_decimal(cell)
            except InvalidOperation:
                continue
            if "price" not in values and "amount" not in values:
                continue
            quantity = values.get("qty", Decimal(1))
            if quantity <= 0:
                continue
            discount = values.get("discount", Decimal(0))
            price = values.get("price")
            amount = values.get("amount")
            if price is None:
                price = (amount / quantity).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            base = amount if amount is not None else line_base(quantity, price, discount)
            if "vat" in values:
                vat_rate = values["vat"]
            elif default_vat is not None:
                vat_rate = default_vat
            else:
                vat_rate, assumed_vat = Decimal(21), True
            irpf_rate = values["irpf"] if "irpf" in values else (totals.irpf_rate or Decimal(0))
            result.append(
                ExtractedLine(
                    concept=concept,
                    quantity=quantity,
                    unit_price=price,
                    discount_rate=discount,
                    vat_rate=vat_rate,
                    irpf_rate=irpf_rate,
                    line_base=round_money(base),
                    vat_amount=percent_of(base, vat_rate),
                    irpf_amount=percent_of(base, irpf_rate),
                )
            )
        if assumed_vat:
            warnings.append("Alguna línea no indicaba el tipo de IVA; se ha supuesto el 21 %.")
        return result

    @staticmethod
    def _single_line_from_totals(totals: _Totals, warnings: list[str]) -> ExtractedLine:
        base = totals.base or Decimal(0)
        vat_rate = Decimal(0)
        if base and totals.vat is not None:
            ratio = totals.vat / base * HUNDRED
            vat_rate = min((Decimal(r) for r in (21, 10, 4, 0)), key=lambda candidate: abs(candidate - ratio))
        warnings.append("No se pudo leer el detalle de líneas; se ha creado una línea única a partir de los totales.")
        irpf_rate = totals.irpf_rate or Decimal(0)
        return ExtractedLine(
            concept="Servicios según factura",
            quantity=Decimal(1),
            unit_price=base,
            vat_rate=vat_rate,
            irpf_rate=irpf_rate,
            line_base=round_money(base),
            vat_amount=percent_of(base, vat_rate),
            irpf_amount=percent_of(base, irpf_rate),
        )

    # ---- totals

    @staticmethod
    def _parse_totals(folded: list[str]) -> _Totals:
        totals = _Totals()
        base_parts: list[Decimal] = []
        vat_parts: list[tuple[Decimal | None, Decimal]] = []
        irpf_parts: list[Decimal] = []
        explicit_vat: Decimal | None = None
        for line in folded:
            lowered = line.lower()
            amounts = _line_amounts(line)
            if not amounts:
                continue
            amount = amounts[-1]
            is_total_line = re.match(r"\s*(?:total|importe\s+total|a\s+pagar|liquido)", lowered) and not re.match(
                r"\s*total\s+(?:base|iva|i\.v\.a|irpf|retenc|impuestos)", lowered
            )
            if re.search(r"base\s+imponible|total\s+base|subtotal|base\s+total|importe\s+neto", lowered):
                if _rate_in(line) is not None:
                    base_parts.append(amount)
                elif totals.base is None:
                    totals.base = amount
            elif is_total_line:
                totals.total = amount
            elif re.search(r"\birpf\b|retencion", lowered):
                irpf_parts.append(abs(amount))
                totals.irpf_rate = totals.irpf_rate or _rate_in(line)
            elif re.search(r"\bi\.?v\.?a\b|cuota", lowered):
                if re.search(r"total\s+(?:cuota\s+)?i\.?v\.?a|cuota\s+total|total\s+impuestos", lowered):
                    explicit_vat = amount
                else:
                    vat_parts.append((_rate_in(line), amount))
        if totals.base is None and base_parts:
            totals.base = sum(base_parts, Decimal(0))
        totals.vat = (
            explicit_vat
            if explicit_vat is not None
            else (sum((a for _, a in vat_parts), Decimal(0)) if vat_parts else None)
        )
        totals.vat_rates = tuple(dict.fromkeys(rate for rate, _ in vat_parts if rate is not None))
        if irpf_parts:
            totals.irpf = sum(irpf_parts, Decimal(0))
            if totals.irpf_rate is None and totals.base:
                ratio = totals.irpf / totals.base * HUNDRED
                nearest = min((Decimal(r) for r in (1, 2, 7, 15, 19)), key=lambda candidate: abs(candidate - ratio))
                totals.irpf_rate = nearest if abs(nearest - ratio) < Decimal("0.5") else None
        return totals

