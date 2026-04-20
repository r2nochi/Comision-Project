from __future__ import annotations

import re
from decimal import Decimal

from ..models import ParseContext, ParsedDocument
from ..utils import build_validation, clean_lines, find_prefixed_value, to_decimal_flexible
from .base import BaseProfile


DETAIL_RE = re.compile(
    r"^(?P<tomador>.+?)\s+(?P<poliza>\d+)\s+(?P<fecha>\d{2}-\d{2}-\d{4})\s+"
    r"(?P<moneda>S/|US\$)\s+(?P<base>[\d.,]+)\s+(?P<pct>[\d.]+)%\s+(?P<monto>[\d.,]+)$"
)


class AvlaLiquidationProfile(BaseProfile):
    profile_id = "avla_liquidation"
    insurer = "AVLA"
    display_name = "AVLA Liquidacion"
    keywords = ("AVLA", "LIQUIDACION DE COMISIONES", "RESUMEN DE PAGO", "N. LIQUID")
    priority = 60

    def parse(self, text: str, context: ParseContext) -> ParsedDocument:
        lines = clean_lines(text)
        document_number = self._extract_document_number(lines, context.file_path.stem)
        detail_rows = self._extract_detail_rows(lines)
        reported_totals = self._extract_totals(lines)
        validations = self._build_validations(detail_rows, reported_totals)

        return ParsedDocument(
            source_file=context.file_path.name,
            source_stem=context.file_path.stem,
            detected_insurer=self.insurer,
            detected_profile=self.display_name,
            document_number=document_number,
            document_type="Liquidacion de Comisiones",
            broker=find_prefixed_value(lines, "CORREDOR"),
            currency="S/",
            generated_at=None,
            input_mode=context.input_mode,
            extracted_char_count=context.extracted_char_count,
            page_count=context.page_count,
            metadata={
                "ruc_corredor": find_prefixed_value(lines, "RUC") or "",
                "cod_sbs": find_prefixed_value(lines, "COD. SBS") or "",
                "negocio": find_prefixed_value(lines, "NEGOCIO") or "",
                "semana": find_prefixed_value(lines, "SEMANA") or "",
            },
            detail_rows=detail_rows,
            reported_totals=reported_totals,
            validations=validations,
        )

    def _extract_document_number(self, lines: list[str], fallback: str) -> str | None:
        value = find_prefixed_value(lines, "N. LIQUID")
        if value:
            return value.replace(" ", "")
        match = re.search(r"([A-Z]{2}\d{4,})", fallback, flags=re.IGNORECASE)
        return match.group(1) if match else fallback

    def _extract_detail_rows(self, lines: list[str]) -> list[dict]:
        rows: list[dict] = []
        for line in lines:
            match = DETAIL_RE.match(line)
            if not match:
                continue
            payload = match.groupdict()
            rows.append(
                {
                    "tomador": payload["tomador"],
                    "poliza": payload["poliza"],
                    "fecha": payload["fecha"],
                    "moneda": payload["moneda"],
                    "base": to_decimal_flexible(payload["base"]),
                    "pct_comision": to_decimal_flexible(payload["pct"]),
                    "monto_comision": to_decimal_flexible(payload["monto"]),
                    "raw_line": line,
                }
            )
        return rows

    def _extract_totals(self, lines: list[str]) -> list[dict]:
        labels = {
            "COMISIN DEL PERODO": "total_comision",
            "I.G.V. (18%)": "igv",
            "TOTAL A PAGAR": "total_a_pagar",
        }
        totals: list[dict] = []
        for index, line in enumerate(lines):
            upper = line.upper()
            if upper not in labels:
                continue
            if index + 1 >= len(lines):
                continue
            amount_line = lines[index + 1]
            match = re.search(r"[\d.,]+", amount_line)
            if not match:
                continue
            totals.append(
                {
                    "scope": "DOCUMENTO",
                    "metric": labels[upper],
                    "value": to_decimal_flexible(match.group(0)),
                }
            )
        return totals

    def _build_validations(self, detail_rows: list[dict], reported_totals: list[dict]) -> list[dict]:
        validations: list[dict] = []
        reported_lookup = {row["metric"]: row["value"] for row in reported_totals}
        calculated_commission = sum((row["monto_comision"] for row in detail_rows), start=Decimal("0"))
        if "total_comision" in reported_lookup:
            validations.append(
                build_validation(
                    scope="DOCUMENTO",
                    metric="total_comision",
                    expected=reported_lookup["total_comision"],
                    calculated=calculated_commission,
                )
            )
        return validations
