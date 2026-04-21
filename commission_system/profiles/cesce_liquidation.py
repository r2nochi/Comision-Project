from __future__ import annotations

import re
from decimal import Decimal

from ..models import ParseContext, ParsedDocument
from ..utils import build_validation, clean_lines, normalize_spaces, to_decimal_flexible
from .base import BaseProfile


DETAIL_RE = re.compile(
    r"^(?P<prefix>.+?)\s+(?P<tipo_doc>NCREDITO|FACTURA)\s+(?P<nro_doc>[A-Z0-9-]+)\s+"
    r"(?P<fecha_pago>\d{2}/\d{2}/\d{4})\s+(?P<pct>-?[\d.]+)\s+(?P<moneda>\S+)\s+"
    r"(?P<prima_neta>-?[\d,]+\.\d{2})\s+(?P<comision_total>-?[\d,]+\.\d{2})\s+"
    r"(?P<comision_pagar>-?[\d,]+\.\d{2})$",
    flags=re.IGNORECASE,
)


class CesceLiquidationProfile(BaseProfile):
    profile_id = "cesce_liquidation"
    insurer = "CESCE"
    display_name = "Cesce Liquidacion"
    keywords = ("CESCE", "LIQUIDACIONES DE COMISIONES NRO", "VALOR VENTA", "COMI.PAGAR")
    priority = 65

    def parse(self, text: str, context: ParseContext) -> ParsedDocument:
        lines = clean_lines(text)
        detail_rows, warnings = self._extract_detail_rows(lines)
        reported_totals = self._extract_totals(lines)
        validations = self._build_validations(detail_rows, reported_totals)
        document_match = re.search(r"LIQUIDACIONES\s+DE\s+COMISIONES\s+NRO:\s*([0-9]+)", text, flags=re.IGNORECASE)

        return ParsedDocument(
            source_file=context.file_path.name,
            source_stem=context.file_path.stem,
            detected_insurer=self.insurer,
            detected_profile=self.display_name,
            document_number=document_match.group(1) if document_match else context.file_path.stem,
            document_type="Liquidaciones de Comisiones",
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
        for line in lines:
            if self._skip_line(line):
                continue
            normalized = normalize_spaces(line)
            normalized = re.sub(r"\b(?:e|ile)\b\s+(NCREDITO|FACTURA)\b", r"\1", normalized, flags=re.IGNORECASE)
            match = DETAIL_RE.match(normalized)
            if not match:
                continue
            payload = match.groupdict()
            cliente, poliza = self._split_prefix(payload["prefix"])
            rows.append(
                {
                    "cliente": cliente,
                    "poliza": poliza,
                    "tipo_doc": payload["tipo_doc"],
                    "nro_doc": payload["nro_doc"],
                    "fecha_pago": payload["fecha_pago"],
                    "pct_comision": to_decimal_flexible(payload["pct"]),
                    "moneda": payload["moneda"],
                    "prima_neta": to_decimal_flexible(payload["prima_neta"]),
                    "comision_total": to_decimal_flexible(payload["comision_total"]),
                    "comision_pagar": to_decimal_flexible(payload["comision_pagar"]),
                    "raw_line": normalized,
                }
            )
        if not rows:
            warnings.append("No se detectaron filas CESCE con OCR suficiente.")
        return rows, warnings

    def _split_prefix(self, prefix: str) -> tuple[str, str]:
        tokens = prefix.split()
        if not tokens:
            return prefix, ""
        candidate = tokens[-1]
        if re.fullmatch(r"[A-Z0-9-]{2,20}", candidate, flags=re.IGNORECASE):
            cliente = " ".join(tokens[:-1]).strip()
            return (cliente or prefix, candidate)
        return prefix, ""

    def _skip_line(self, line: str) -> bool:
        upper = line.upper()
        return any(
            token in upper
            for token in (
                "LIQUIDACIONES DE COMISIONES NRO",
                "CORREDOR:",
                "DIRECCION:",
                "REG.SBS:",
                "MONEDA:",
                "CLIENTE POLIZA",
                "VALOR VENTA",
                "PENDIENTES DE FACTURA",
            )
        )

    def _extract_totals(self, lines: list[str]) -> list[dict]:
        totals: list[dict] = []
        for line in lines:
            for label, metric in (("VALOR VENTA", "valor_venta"), ("VALOR IGV", "igv"), ("VALOR TOTAL", "valor_total")):
                match = re.match(rf"^{label}:\s*\S+\s*(-?[\d,]+\.\d{{2}})$", line, flags=re.IGNORECASE)
                if match:
                    totals.append({"scope": "DOCUMENTO", "metric": metric, "value": to_decimal_flexible(match.group(1))})
        return totals

    def _build_validations(self, detail_rows: list[dict], reported_totals: list[dict]) -> list[dict]:
        validations: list[dict] = []
        reported_lookup = {row["metric"]: row["value"] for row in reported_totals}
        calculated = sum((row["comision_pagar"] for row in detail_rows), start=Decimal("0"))
        if "valor_venta" in reported_lookup:
            validations.append(
                build_validation(
                    scope="DOCUMENTO",
                    metric="valor_venta",
                    expected=reported_lookup["valor_venta"],
                    calculated=calculated,
                )
            )
        return validations
