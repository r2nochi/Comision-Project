from __future__ import annotations

import re
from decimal import Decimal

from ..models import ParseContext, ParsedDocument
from ..utils import build_validation, clean_lines, normalize_spaces, to_decimal_flexible
from .base import BaseProfile


DETAIL_RE = re.compile(
    r"^(?P<ramo>.+?)\s+(?P<poliza>\d{7,})\s+(?P<contratante>.+?)\s+(?P<fecha_emision>\d{1,2}/\d{2}/\d{4})\s+"
    r"(?P<estado>POR COBRAR)\s+(?P<nro_factura>[A-Z0-9-]+)\s+(?P<prima>-?[\d,]+(?:\.\d+)?)\s+"
    r"(?:(?P<pct>-?[\d,]+(?:\.\d+)?)\s+)?(?P<comision>-?[\d,]+(?:\.\d+)?)\s+"
    r"(?P<igv>-?[\d,]+(?:\.\d+)?)\s+(?P<total>-?[\d,]+(?:\.\d+)?)$",
    flags=re.IGNORECASE,
)


class ProtectaLoteProfile(BaseProfile):
    profile_id = "protecta_lote"
    insurer = "PROTECTA"
    display_name = "Protecta Lote"
    keywords = ("PROTECTA", "DETALLE DE LOTE DE COMISIONES", "NUMERO DE LOTE", "MONTO TOTAL")
    priority = 75

    def parse(self, text: str, context: ParseContext) -> ParsedDocument:
        lines = clean_lines(text)
        detail_rows, warnings = self._extract_detail_rows(lines)
        reported_totals = self._extract_totals(lines)
        validations = self._build_validations(detail_rows, reported_totals)
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

    def _extract_detail_rows(self, lines: list[str]) -> tuple[list[dict], list[str]]:
        rows: list[dict] = []
        warnings: list[str] = []
        buffer = ""
        for line in lines:
            if self._skip_line(line):
                continue
            candidate = f"{buffer} {line}".strip() if buffer else line
            parsed = self._parse_candidate(candidate)
            if parsed is None:
                if any(token in line.upper() for token in ("VIDA LEY", "TRABAJADORES", "SALUD")):
                    if buffer:
                        warnings.append(f"Fila PROTECTA LOTE no parseada: {buffer}")
                    buffer = line
                elif buffer:
                    buffer = candidate
                continue
            rows.append(parsed)
            buffer = ""
        if buffer:
            warnings.append(f"Fila PROTECTA LOTE no parseada: {buffer}")
        return rows, warnings

    def _parse_candidate(self, candidate: str) -> dict | None:
        normalized = candidate.replace("|", " ").replace("S/", " ")
        normalized = normalized.replace("FO14", "F014").replace("FC14", "F014")
        normalized = normalize_spaces(normalized)
        match = DETAIL_RE.match(normalized)
        if not match:
            return None
        payload = match.groupdict()
        return {
            "ramo": payload["ramo"],
            "poliza": payload["poliza"],
            "contratante": payload["contratante"],
            "fecha_emision": payload["fecha_emision"],
            "estado": payload["estado"],
            "nro_factura": payload["nro_factura"],
            "prima": to_decimal_flexible(payload["prima"]),
            "pct_comision": to_decimal_flexible(payload["pct"] or "0"),
            "comision": to_decimal_flexible(payload["comision"]),
            "igv": to_decimal_flexible(payload["igv"]),
            "total": to_decimal_flexible(payload["total"]),
            "raw_line": normalized,
        }

    def _skip_line(self, line: str) -> bool:
        upper = line.upper()
        return any(
            token in upper
            for token in (
                "DETALLE DE LOTE DE COMISIONES",
                "NUMERO DE LOTE",
                "NRO. SERIE",
                "NRO. FACTURA:",
                "RAMO POLIZA CONTRATANTE",
                "TOTAL MES",
                "PROTECTA SECURITY",
            )
        )

    def _extract_totals(self, lines: list[str]) -> list[dict]:
        totals: list[dict] = []
        for line in lines:
            for label, metric in (("MONTO NETO", "monto_neto"), ("IGV %", "igv"), ("MONTO TOTAL", "monto_total")):
                match = re.search(rf"{label}:\s*(-?[\d,]+\.\d{{2}})", line, flags=re.IGNORECASE)
                if match:
                    totals.append({"scope": "DOCUMENTO", "metric": metric, "value": to_decimal_flexible(match.group(1))})
        return totals

    def _build_validations(self, detail_rows: list[dict], reported_totals: list[dict]) -> list[dict]:
        validations: list[dict] = []
        reported_lookup = {row["metric"]: row["value"] for row in reported_totals}
        calculated = sum((row["total"] for row in detail_rows), start=Decimal("0"))
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
