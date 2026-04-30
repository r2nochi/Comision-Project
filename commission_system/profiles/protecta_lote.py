from __future__ import annotations

import calendar
import re
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pytesseract
from PIL import ImageFilter, ImageOps

from ..models import ParseContext, ParsedDocument
from ..ocr import ensure_tesseract
from ..utils import build_validation, clean_lines, normalize_for_match, normalize_spaces, to_decimal_flexible
from .base import BaseProfile


MONTH_TOTAL_RE = re.compile(r"TOTAL\s+MES\s+(?P<month>[A-ZÁÉÍÓÚ]+)", flags=re.IGNORECASE)
DATE_RE = re.compile(r"\d{1,2}/\d{2}/\d{4}")


class ProtectaLoteProfile(BaseProfile):
    profile_id = "protecta_lote"
    insurer = "PROTECTA"
    display_name = "Protecta Lote"
    keywords = ("PROTECTA", "DETALLE DE LOTE DE COMISIONES", "NUMERO DE LOTE", "MONTO TOTAL")
    priority = 75

    def parse(self, text: str, context: ParseContext) -> ParsedDocument:
        lines = clean_lines(text)
        detail_rows, monthly_totals, warnings = self._extract_table_rows(context.file_path)
        document_totals = self._extract_document_totals(lines)
        reported_totals = [*monthly_totals, *document_totals]
        validations = self._build_validations(detail_rows, document_totals)
        lot_match = re.search(r"NUMERO DE LOTE\s+([0-9]+)", text, flags=re.IGNORECASE)

        return ParsedDocument(
            source_file=context.file_path.name,
            source_stem=context.file_path.stem,
            detected_insurer=self.insurer,
            detected_profile=self.display_name,
            document_number=lot_match.group(1) if lot_match else context.file_path.stem,
            document_type="Detalle de Lote de Comisiones",
            broker="LA PROTECTORA CORREDORES DE SEGUROS SA",
            currency="S/",
            generated_at=None,
            input_mode=context.input_mode,
            extracted_char_count=context.extracted_char_count,
            page_count=context.page_count,
            detail_rows=detail_rows,
            reported_totals=reported_totals,
            validations=validations,
            warnings=warnings,
        )

    def _extract_table_rows(self, file_path: Path) -> tuple[list[dict], list[dict], list[str]]:
        ensure_tesseract()
        detail_rows: list[dict] = []
        monthly_totals: list[dict] = []
        warnings: list[str] = []
        pdf = pdfium.PdfDocument(str(file_path))

        try:
            for page_number in range(len(pdf)):
                page = pdf[page_number]
                bitmap = page.render(scale=2.2)
                try:
                    image = bitmap.to_pil().copy()
                finally:
                    bitmap.close()
                    page.close()

                try:
                    row_boxes, column_edges = self._detect_table_grid(image)
                except ValueError as error:
                    warnings.append(f"No se pudo detectar la grilla de la pagina {page_number + 1}: {error}")
                    image.close()
                    continue

                for row_index, (top, bottom) in enumerate(row_boxes, start=1):
                    row_height = bottom - top
                    if row_height < 25 or row_height > 90:
                        continue

                    merged_label = self._ocr_cell(image, column_edges[0], top, column_edges[6], bottom)
                    if not merged_label:
                        continue

                    month_match = MONTH_TOTAL_RE.search(normalize_for_match(merged_label))
                    if month_match:
                        month = self._normalize_month(month_match.group("month"))
                        pct_text = self._ocr_cell(image, column_edges[7], top, column_edges[8], bottom)
                        comision = self._parse_decimal(self._ocr_cell(image, column_edges[8], top, column_edges[9], bottom))
                        igv = self._parse_decimal(self._ocr_cell(image, column_edges[9], top, column_edges[10], bottom))
                        total = self._parse_decimal(self._ocr_cell(image, column_edges[10], top, column_edges[11], bottom))
                        comision, igv, total = self._reconcile_components(comision, igv, total)
                        monthly_totals.append(
                            {
                                "scope": "MES",
                                "label": f"TOTAL MES {month}",
                                "month": month,
                                "prima": self._parse_decimal(self._ocr_cell(image, column_edges[6], top, column_edges[7], bottom)),
                                "pct_comision": self._parse_optional_decimal(pct_text),
                                "comision": comision,
                                "igv": igv,
                                "total": total,
                                "raw_line": merged_label,
                            }
                        )
                        continue

                    date_text = self._ocr_cell(image, column_edges[3], top, column_edges[4], bottom)
                    if not DATE_RE.search(date_text):
                        continue

                    pct_text = self._ocr_cell(image, column_edges[7], top, column_edges[8], bottom)
                    comision = self._parse_decimal(self._ocr_cell(image, column_edges[8], top, column_edges[9], bottom))
                    igv = self._parse_decimal(self._ocr_cell(image, column_edges[9], top, column_edges[10], bottom))
                    total = self._parse_decimal(self._ocr_cell(image, column_edges[10], top, column_edges[11], bottom))
                    comision, igv, total = self._reconcile_components(comision, igv, total)
                    row = {
                        "ramo": self._normalize_cell_text(
                            self._ocr_cell(image, column_edges[0], top, column_edges[1], bottom)
                        ),
                        "poliza": self._normalize_policy(
                            self._ocr_cell(image, column_edges[1], top, column_edges[2], bottom)
                        ),
                        "contratante": self._normalize_contratante(
                            self._ocr_cell(image, column_edges[2], top, column_edges[3], bottom)
                        ),
                        "fecha_emision": self._normalize_cell_text(date_text),
                        "estado": self._normalize_cell_text(
                            self._ocr_cell(image, column_edges[4], top, column_edges[5], bottom)
                        ),
                        "nro_factura": self._normalize_invoice(
                            self._ocr_cell(image, column_edges[5], top, column_edges[6], bottom)
                        ),
                        "prima": self._parse_decimal(
                            self._ocr_cell(image, column_edges[6], top, column_edges[7], bottom)
                        ),
                        "pct_comision": self._parse_optional_decimal(pct_text) or self._infer_pct_from_ramo(
                            self._ocr_cell(image, column_edges[0], top, column_edges[1], bottom)
                        ),
                        "comision": comision,
                        "igv": igv,
                        "total": total,
                    }
                    row["raw_line"] = " | ".join(
                        [
                            row["ramo"],
                            row["poliza"],
                            row["contratante"],
                            row["fecha_emision"],
                            row["estado"],
                            row["nro_factura"],
                        ]
                    )
                    detail_rows.append(row)

                image.close()
        finally:
            pdf.close()

        if not detail_rows:
            warnings.append("No se pudieron reconstruir filas de detalle con OCR tabular.")

        return detail_rows, monthly_totals, warnings

    def _detect_table_grid(self, image) -> tuple[list[tuple[int, int]], list[int]]:
        gray = np.array(image.convert("L"))
        horizontal_slice = gray[:, 30 : image.width - 40]
        horizontal_counts = (horizontal_slice < 180).sum(axis=1)
        horizontal_threshold = max(700, int(horizontal_counts.max() * 0.75))
        horizontal_lines = self._cluster_positions(np.where(horizontal_counts > horizontal_threshold)[0])
        row_boxes = [(horizontal_lines[index], horizontal_lines[index + 1]) for index in range(len(horizontal_lines) - 1)]
        row_boxes = [box for box in row_boxes if box[1] - box[0] >= 20]
        if not row_boxes:
            raise ValueError("sin lineas horizontales")

        top, bottom = row_boxes[0][0], row_boxes[-1][1]
        vertical_slice = gray[top:bottom, :]
        vertical_counts = (vertical_slice < 180).sum(axis=0)
        vertical_threshold = max(120, int(vertical_counts.max() * 0.55))
        column_edges = self._cluster_positions(np.where(vertical_counts > vertical_threshold)[0])
        if len(column_edges) < 12:
            raise ValueError(f"lineas verticales insuficientes ({len(column_edges)})")

        return row_boxes, column_edges[:12]

    def _cluster_positions(self, indices) -> list[int]:
        positions: list[int] = []
        start = None
        previous = None
        for value in indices:
            integer = int(value)
            if start is None:
                start = previous = integer
                continue
            if integer == previous + 1:
                previous = integer
                continue
            positions.append(round((start + previous) / 2))
            start = previous = integer
        if start is not None:
            positions.append(round((start + previous) / 2))
        return positions

    def _ocr_cell(self, image, left: int, top: int, right: int, bottom: int) -> str:
        crop = image.crop((left + 2, top + 2, right - 2, bottom - 2))
        try:
            processed = ImageOps.autocontrast(crop.convert("L")).filter(ImageFilter.SHARPEN)
            text = pytesseract.image_to_string(processed, lang="spa", config="--psm 6")
        finally:
            crop.close()
        return self._normalize_cell_text(text)

    def _normalize_cell_text(self, value: str) -> str:
        normalized = normalize_spaces(value.replace("\n", " ").replace("§", "S").replace("$", "S"))
        normalized = normalized.replace("FO14", "F014").replace("FC14", "FC14").replace("FOT4", "F014")
        normalized = normalized.replace("E.!.R.L.", "E.I.R.L.").replace("S$.A.C.", "S.A.C.")
        return normalized

    def _normalize_contratante(self, value: str) -> str:
        normalized = self._normalize_cell_text(value)
        normalized = re.sub(r"\bA\s+8\s+CL\b", "A & CL", normalized, flags=re.IGNORECASE)
        return normalized

    def _normalize_policy(self, value: str) -> str:
        normalized = re.sub(r"[^0-9]", "", value.upper().replace("O", "0").replace("I", "1").replace("L", "1"))
        return normalized

    def _normalize_invoice(self, value: str) -> str:
        normalized = value.upper().replace("O", "0").replace("I", "1").replace("L", "1")
        normalized = normalized.replace("FO14", "F014").replace("FOT4", "F014")
        normalized = normalized.replace("F0014", "F014")
        return re.sub(r"[^A-Z0-9-]", "", normalized)

    def _parse_decimal(self, value: str):
        cleaned = self._normalize_numeric_text(value)
        if not cleaned:
            return to_decimal_flexible("0")
        return to_decimal_flexible(cleaned)

    def _parse_optional_decimal(self, value: str):
        cleaned = self._normalize_numeric_text(value)
        if not cleaned or not re.search(r"\d", cleaned):
            return None
        parsed = to_decimal_flexible(cleaned)
        return parsed if parsed != 0 else None

    def _infer_pct_from_ramo(self, value: str):
        normalized = normalize_for_match(value)
        if "VIDA LEY" in normalized:
            return None
        return None

    def _reconcile_components(self, comision, igv, total):
        calculated_total = comision + igv
        if abs(calculated_total - total) <= to_decimal_flexible("0.02"):
            return comision, igv, total
        if calculated_total != 0 and abs(total) > abs(calculated_total) * 5:
            total = calculated_total
        else:
            corrected_igv = total - comision
            if abs(corrected_igv) < abs(igv) or abs(igv) > abs(total) * 2:
                igv = corrected_igv
            else:
                total = calculated_total
        return comision, igv, total

    def _normalize_numeric_text(self, value: str) -> str:
        normalized = normalize_spaces(str(value).upper())
        normalized = normalized.replace("SI", "S/").replace("SS", "S/").replace("$/", "S/")
        normalized = normalized.replace("O", "0").replace("I", "1").replace("L", "1")
        normalized = re.sub(r"(?<=\d)\s+(?=\d{1,2}\b)", "", normalized)
        normalized = re.sub(r"(?<=\d),(\d)(?!\d)", lambda match: f",{match.group(1)}0", normalized)
        matches = re.findall(r"-?\s*[\d,.]+", normalized)
        if not matches:
            return ""
        token = max(matches, key=lambda item: len(item.replace(" ", "")))
        return normalize_spaces(token)

    def _extract_document_totals(self, lines: list[str]) -> list[dict]:
        totals: list[dict] = []
        for line in lines:
            for label, metric in (("MONTO NETO", "monto_neto"), ("IGV %", "igv"), ("MONTO TOTAL", "monto_total")):
                match = re.search(rf"{label}:\s*(-?[\d,]+\.\d{{2}})", line, flags=re.IGNORECASE)
                if match:
                    totals.append({"scope": "DOCUMENTO", "metric": metric, "value": to_decimal_flexible(match.group(1))})
        return totals

    def _normalize_month(self, value: str) -> str:
        normalized = normalize_for_match(value)
        replacements = {
            "SETIEMBRE": "SEPTIEMBRE",
            "SEPTIEMBRE": "SEPTIEMBRE",
        }
        return replacements.get(normalized, normalized)

    def _build_validations(self, detail_rows: list[dict], reported_totals: list[dict]) -> list[dict]:
        validations: list[dict] = []
        reported_lookup = {row["metric"]: row["value"] for row in reported_totals}
        calculated = sum((row["total"] for row in detail_rows), start=to_decimal_flexible("0"))
        if "monto_total" in reported_lookup:
            validations.append(
                build_validation(
                    scope="DOCUMENTO",
                    metric="monto_total",
                    expected=reported_lookup["monto_total"],
                    calculated=calculated,
                )
            )
        return validations

    def _month_name(self, month_number: int) -> str:
        months = {
            1: "ENERO",
            2: "FEBRERO",
            3: "MARZO",
            4: "ABRIL",
            5: "MAYO",
            6: "JUNIO",
            7: "JULIO",
            8: "AGOSTO",
            9: "SEPTIEMBRE",
            10: "OCTUBRE",
            11: "NOVIEMBRE",
            12: "DICIEMBRE",
        }
        return months.get(month_number, calendar.month_name[month_number].upper())
