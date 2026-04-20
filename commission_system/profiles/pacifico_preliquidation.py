from __future__ import annotations

import re
from decimal import Decimal

from ..models import ParseContext, ParsedDocument
from ..utils import build_validation, clean_lines, find_next_numeric_line, find_prefixed_value, next_non_empty_line, to_decimal_flexible
from .base import BaseProfile


DETAIL_RE = re.compile(
    r"^(?P<item>\d+)\s+(?P<producto>\S+)\s+(?P<poliza>\S+)\s+(?P<avcob>[\d/Il]+)\s+(?P<ram>\S+)\s+"
    r"(?P<doc>[\d/Il]+)\s+(?P<concepto>.+?)\s+(?P<fecha_pago>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<prima_comercial>-?[\d,]+\.\d{2})\s+(?P<derecho_emision>-?[\d,]+\.\d{2})\s+"
    r"(?P<prima_afecta>-?[\d,]+\.\d{2})\s+(?P<pct_comision>-?[\d,]+\.\d{2})\s+"
    r"(?P<monto_comision>-?[\d,]+\.\d{2})$"
)


class PacificoPreliquidationProfile(BaseProfile):
    profile_id = "pacifico_preliquidation"
    insurer = "PACIFICO"
    display_name = "Pacifico Preliquidacion"
    keywords = ("PACIFICO", "PRELIQUIDACION DE COMISIONES", "MONTO IMPONIBLE", "NETO A PAGAR")
    priority = 80

    def parse(self, text: str, context: ParseContext) -> ParsedDocument:
        lines = clean_lines(text)
        document_number = self._extract_document_number(text, context.file_path.stem)
        broker = find_prefixed_value(lines, "Agente / Broker")
        currency = find_prefixed_value(lines, "Moneda")
        generated_at = self._extract_generated_at(text)
        detail_rows, warnings = self._extract_detail_rows(lines)
        reported_totals = self._extract_totals(lines)
        validations = self._build_validations(detail_rows, reported_totals)

        return ParsedDocument(
            source_file=context.file_path.name,
            source_stem=context.file_path.stem,
            detected_insurer=self.insurer,
            detected_profile=self.display_name,
            document_number=document_number,
            document_type="Preliquidacion de Comisiones",
            broker=broker,
            currency=currency,
            generated_at=generated_at,
            input_mode=context.input_mode,
            extracted_char_count=context.extracted_char_count,
            page_count=context.page_count,
            metadata={"cod_sbs": find_prefixed_value(lines, "Cod.SBS") or ""},
            detail_rows=detail_rows,
            reported_totals=reported_totals,
            validations=validations,
            warnings=warnings,
        )

    def _extract_document_number(self, text: str, fallback: str) -> str | None:
        match = re.search(r"PRELIQUIDACION\s+DE\s+COMISIONES\s+NRO\.?\s*([0-9]+)", text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        fallback_match = re.search(r"([0-9]{6,})", fallback)
        return fallback_match.group(1) if fallback_match else fallback

    def _extract_generated_at(self, text: str) -> str | None:
        match = re.search(r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}\s*[ap]\.m\.)", text, flags=re.IGNORECASE)
        return match.group(1) if match else None

    def _extract_detail_rows(self, lines: list[str]) -> tuple[list[dict], list[str]]:
        rows: list[dict] = []
        warnings: list[str] = []
        buffer = ""
        for line in lines:
            if self._skip_line(line):
                continue
            if any(label in line.upper() for label in ("SALDO ANTERIOR", "MONTO IMPONIBLE", "IMPUESTO GENERAL", "NETO A PAGAR")):
                buffer = ""
                continue
            candidate = f"{buffer} {line}".strip() if buffer else line
            parsed = self._parse_detail_line(candidate)
            if parsed is None:
                if re.match(r"^\d+\s+\S+", line):
                    if buffer:
                        warnings.append(f"Fila PACIFICO no parseada: {buffer}")
                    buffer = line
                elif buffer:
                    buffer = f"{buffer} {line}".strip()
                continue
            rows.append(parsed)
            buffer = ""
        if buffer:
            warnings.append(f"Fila PACIFICO no parseada: {buffer}")
        return rows, warnings

    def _skip_line(self, line: str) -> bool:
        upper = line.upper()
        return any(
            token in upper
            for token in (
                "PRELIQUIDACION DE COMISIONES",
                "AGENTE / BROKER",
                "DIRECCION",
                "UBICACION",
                "TELEFONO",
                "PACIFICO",
                "FECHA Y HORA",
                "NUMERO DE PAGINA",
                "USUARIO",
                "PRIMA COMERCIAL",
                "DERECHO DE",
                "EMISION PRIMA",
                "RECIBIMOS DE",
            )
        )

    def _parse_detail_line(self, line: str) -> dict | None:
        normalized = line.replace("  ", " ").replace("111", "1/1").replace("Il", "1/1")
        match = DETAIL_RE.match(normalized)
        if not match:
            return None
        payload = match.groupdict()
        return {
            "item": int(payload["item"]),
            "producto": payload["producto"],
            "poliza": payload["poliza"],
            "avcob": payload["avcob"].replace("I", "1").replace("l", "1"),
            "ram": payload["ram"],
            "document": payload["doc"].replace("I", "1").replace("l", "1"),
            "concepto": payload["concepto"].strip(),
            "fecha_pago": payload["fecha_pago"],
            "prima_comercial": to_decimal_flexible(payload["prima_comercial"]),
            "derecho_emision": to_decimal_flexible(payload["derecho_emision"]),
            "prima_afecta": to_decimal_flexible(payload["prima_afecta"]),
            "pct_comision": to_decimal_flexible(payload["pct_comision"]),
            "monto_comision": to_decimal_flexible(payload["monto_comision"]),
            "raw_line": normalized,
        }

    def _extract_totals(self, lines: list[str]) -> list[dict]:
        totals: list[dict] = []
        labels = {
            "SALDO ANTERIOR": "saldo_anterior",
            "MONTO IMPONIBLE": "monto_imponible",
            "IMPUESTO GENERAL A LAS VENTAS": "igv",
            "NETO A PAGAR": "neto_a_pagar",
        }
        for index, line in enumerate(lines):
            upper = line.upper().rstrip(":")
            if upper not in labels:
                continue
            value_line = find_next_numeric_line(lines, index)
            if not value_line:
                continue
            number_match = re.search(r"-?[\d,]+\.\d{2}", value_line)
            if not number_match:
                continue
            totals.append(
                {
                    "scope": "DOCUMENTO",
                    "metric": labels[upper],
                    "value": to_decimal_flexible(number_match.group(0)),
                }
            )
        return totals

    def _build_validations(self, detail_rows: list[dict], reported_totals: list[dict]) -> list[dict]:
        validations: list[dict] = []
        calculated_commission = sum((row["monto_comision"] for row in detail_rows), start=Decimal("0"))
        reported_lookup = {row["metric"]: row["value"] for row in reported_totals}
        if "saldo_anterior" in reported_lookup:
            validations.append(
                build_validation(
                    scope="DOCUMENTO",
                    metric="saldo_anterior",
                    expected=reported_lookup["saldo_anterior"],
                    calculated=calculated_commission,
                )
            )
        if "monto_imponible" in reported_lookup:
            validations.append(
                build_validation(
                    scope="DOCUMENTO",
                    metric="monto_imponible",
                    expected=reported_lookup["monto_imponible"],
                    calculated=calculated_commission,
                )
            )
        return validations
