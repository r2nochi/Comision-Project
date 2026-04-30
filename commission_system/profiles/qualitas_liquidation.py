from __future__ import annotations

import re
from decimal import Decimal

from ..models import ParseContext, ParsedDocument
from ..utils import (
    build_validation,
    clean_lines,
    normalize_code_like_field,
    normalize_for_match,
    normalize_spaces,
    replace_ocr_o_with_zero_in_numeric_segments,
    to_decimal_flexible,
)
from .base import BaseProfile


DETAIL_RE = re.compile(
    r"^(?P<tipo>\S*)\s*(?P<poliza>\d+)\s+(?P<endoso>\d+)\s+(?P<recibo>\d+)\s+(?P<orden>\S+)\s+"
    r"(?P<fecha_pago>\d{2}/\d{2}/\d{2})\s+(?P<remesa>\d+)\s+(?P<asegurado>.+?)\s+"
    r"(?P<prima_neta>-?[\d,]+\.\d{2})\s+(?P<pct>-?[\d,]+\.\d{2})\s*%\s+"
    r"(?P<comision>-?[\d,]+\.\d{2})\s+(?P<igv>-?[\d,]+\.\d{2})\s+(?P<cargo>-?[\d,]+\.\d{2})\s+"
    r"(?P<pago_comision>-?[\d,]+\.\d{2})$"
)


class QualitasLiquidationProfile(BaseProfile):
    profile_id = "qualitas_liquidation"
    insurer = "QUALITAS"
    display_name = "Qualitas Liquidacion"
    keywords = ("QUALITAS", "FOLIO:", "LIQUIDACION DE COMISIONES", "SALDO ACTUAL NETO")
    priority = 70

    def parse(self, text: str, context: ParseContext) -> ParsedDocument:
        lines = clean_lines(text)
        detail_rows, warnings = self._extract_detail_rows(lines)
        reported_totals = self._extract_totals(lines)
        if context.input_mode == "scan":
            reported_totals = [
                row
                for row in reported_totals
                if row.get("metric") not in {"comision_total_periodo_resumen", "saldo_actual_neto_resumen", "igv_resumen"}
            ]
        validations = self._build_validations(detail_rows, reported_totals)
        folio_match = re.search(r"FOLIO:\s*([0-9O]+)", text, flags=re.IGNORECASE)
        period_match = re.search(r"PERIODO\s+DEL\s+(.+?)\n", text, flags=re.IGNORECASE)

        return ParsedDocument(
            source_file=context.file_path.name,
            source_stem=context.file_path.stem,
            detected_insurer=self.insurer,
            detected_profile=self.display_name,
            document_number=normalize_code_like_field(folio_match.group(1), allowed="A-Z0-9") if folio_match else context.file_path.stem,
            document_type="Liquidacion de Comisiones",
            broker="LA PROTECTORA CORREDORES DE SEGUROS S.A.",
            currency="DLS",
            generated_at=None,
            input_mode=context.input_mode,
            extracted_char_count=context.extracted_char_count,
            page_count=context.page_count,
            metadata={"periodo": period_match.group(1).strip() if period_match else "N/D"},
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
            raw_candidate = f"{buffer} {line}".strip() if buffer else line
            candidate = replace_ocr_o_with_zero_in_numeric_segments(raw_candidate)
            match = DETAIL_RE.match(candidate)
            if not match:
                if re.match(r"^(AUTO|\d{10})", line):
                    if buffer:
                        warnings.append(f"Fila QUALITAS no parseada: {buffer}")
                    buffer = line
                elif buffer:
                    buffer = candidate
                continue
            payload = match.groupdict()
            rows.append(
                {
                    "tipo": payload["tipo"],
                    "poliza": normalize_code_like_field(payload["poliza"], allowed="A-Z0-9"),
                    "endoso": normalize_code_like_field(payload["endoso"], allowed="A-Z0-9"),
                    "recibo": normalize_code_like_field(payload["recibo"], allowed="A-Z0-9"),
                    "orden_pago": payload["orden"],
                    "fecha_pago": payload["fecha_pago"],
                    "remesa": normalize_code_like_field(payload["remesa"], allowed="A-Z0-9"),
                    "asegurado_concepto": self._normalize_asegurado_concepto(payload["asegurado"]),
                    "prima_neta": to_decimal_flexible(payload["prima_neta"]),
                    "pct_comision": to_decimal_flexible(payload["pct"]),
                    "comision": to_decimal_flexible(payload["comision"]),
                    "igv": to_decimal_flexible(payload["igv"]),
                    "cargo": to_decimal_flexible(payload["cargo"]),
                    "pago_comision": to_decimal_flexible(payload["pago_comision"]),
                    "raw_line": candidate,
                }
            )
            buffer = ""
        if buffer:
            warnings.append(f"Fila QUALITAS no parseada: {buffer}")
        return rows, warnings

    def _normalize_asegurado_concepto(self, value: str) -> str:
        normalized = normalize_spaces(value)
        return re.sub(r"\bOP\s+(?P<number>\d{6,})\b", r"OP #\g<number>", normalized, flags=re.IGNORECASE)

    def _skip_line(self, line: str) -> bool:
        upper = line.upper()
        return any(
            token in upper
            for token in (
                "NOMBRE Y DOMICILIO",
                "PERIODO DEL",
                "CODIGO SBS",
                "OFICINA",
                "TIPO POLIZA",
                "COMISION TOTAL PERIODO",
                "CUALQUIER ACLARACION",
                "RESUMEN",
            )
        )

    def _extract_totals(self, lines: list[str]) -> list[dict]:
        totals_by_metric: dict[str, dict] = {}
        in_summary = False
        for line in lines:
            normalized = normalize_for_match(line)
            amount = self._extract_last_amount(line)
            if normalized == "RESUMEN":
                in_summary = True
                continue
            if amount is None:
                continue

            metric = self._resolve_total_metric(normalized, in_summary=in_summary)
            if not metric:
                continue
            totals_by_metric[metric] = {
                "scope": "DOCUMENTO",
                "metric": metric,
                "value": to_decimal_flexible(amount),
            }

        return list(totals_by_metric.values())

    def _resolve_total_metric(self, normalized: str, *, in_summary: bool) -> str | None:
        if "SALDO ANTERIOR" in normalized:
            return "saldo_anterior"
        if "COMISION TOTAL PERIODO" in normalized:
            return "comision_total_periodo_resumen"
        if any(label in normalized for label in ("OTROS CARGOS", "DTROS CARGOS", "TROS CARGOS")):
            return "otros_cargos"
        if "OTROS ABONOS" in normalized:
            return "otros_abonos"
        if "PAGO COMISIONES PERIODO ANTERIOR" in normalized:
            return "pago_comisiones_periodo_anterior"
        if "PAGO DETRACCIONES PERIODO ANTERIOR" in normalized:
            return "pago_detracciones_periodo_anterior"
        if "SALDO ACTUAL TOTAL" in normalized:
            return "saldo_actual_total"
        if "SALDO ACTUAL NETO" in normalized:
            return "saldo_actual_neto_resumen"
        if any(label in normalized for label in ("IGV.PAG.", "LG.V.")):
            return "igv"
        if "I.G.V." in normalized and in_summary:
            return "igv_resumen"
        if "IMPORTE" in normalized and not in_summary:
            return "saldo_actual_neto"
        if "TOTAL" in normalized and not in_summary:
            return "comision_total_periodo"
        return None

    def _extract_last_amount(self, value: str) -> str | None:
        matches = re.findall(r"-?[\d,]+\.\d{2}", value)
        return matches[-1] if matches else None

    def _build_validations(self, detail_rows: list[dict], reported_totals: list[dict]) -> list[dict]:
        validations: list[dict] = []
        reported_lookup = {row["metric"]: row["value"] for row in reported_totals}
        calculated = sum((row["pago_comision"] for row in detail_rows), start=Decimal("0"))
        expected = reported_lookup.get("comision_total_periodo_resumen") or reported_lookup.get("saldo_actual_total")
        if expected is not None:
            validations.append(
                build_validation(
                    scope="DOCUMENTO",
                    metric="comision_total_periodo",
                    expected=expected,
                    calculated=calculated,
                )
            )
        return validations
