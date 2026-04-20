from __future__ import annotations

import re
from decimal import Decimal

from ..models import ParseContext, ParsedDocument
from ..utils import build_validation, clean_lines, normalize_for_match, normalize_spaces, to_decimal_flexible
from .base import BaseProfile


TAIL_RE = re.compile(
    r"^(?P<fecha_inicio>\d{2}/\d{2}/\d{4})\s+(?P<prefix>.+?)\s+(?P<monto_comision>-?[\d,]+\.\d{2})\s+"
    r"\((?P<pct>[\d.]+)\s*%\)\s+(?:RUC\s*[=-]\s*)?(?P<identificacion>\d{8,14})\s+(?P<cliente>.+)$",
    flags=re.IGNORECASE,
)


class GenericLiquidationProfile(BaseProfile):
    def __init__(self, *, profile_id: str, insurer: str, display_name: str, keywords: tuple[str, ...]) -> None:
        self.profile_id = profile_id
        self.insurer = insurer
        self.display_name = display_name
        self.keywords = keywords
        self.priority = 55

    def parse(self, text: str, context: ParseContext) -> ParsedDocument:
        lines = clean_lines(text)
        detail_rows, warnings = self._extract_detail_rows(lines)
        reported_totals = self._extract_totals(lines)
        validations = self._build_validations(detail_rows, reported_totals)
        document_number = self._extract_document_number(text, context.file_path.stem)
        generated_at = self._extract_generated_at(text)

        return ParsedDocument(
            source_file=context.file_path.name,
            source_stem=context.file_path.stem,
            detected_insurer=self.insurer,
            detected_profile=self.display_name,
            document_number=document_number,
            document_type="Liquidacion de Comisiones",
            broker="LA PROTECTORA CORREDORES DE SEGUROS SA",
            currency="S/",
            generated_at=generated_at,
            input_mode=context.input_mode,
            extracted_char_count=context.extracted_char_count,
            page_count=context.page_count,
            detail_rows=detail_rows,
            reported_totals=reported_totals,
            validations=validations,
            warnings=warnings,
        )

    def _extract_document_number(self, text: str, fallback: str) -> str:
        match = re.search(r"LIQUIDACION\s+NUMERO:\s*(LIQ-[0-9]+)", text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        fallback_match = re.search(r"(LIQ-[0-9]+)", fallback, flags=re.IGNORECASE)
        return fallback_match.group(1) if fallback_match else fallback

    def _extract_generated_at(self, text: str) -> str | None:
        match = re.search(r"FECHA Y HORA:\s*([0-9/:\sapm\.\-]+)", text, flags=re.IGNORECASE)
        return normalize_spaces(match.group(1)) if match else None

    def _extract_detail_rows(self, lines: list[str]) -> tuple[list[dict], list[str]]:
        rows: list[dict] = []
        warnings: list[str] = []
        buffer: list[str] = []
        for line in lines:
            if self._skip_line(line):
                continue
            if self._is_total_line(line):
                if buffer:
                    parsed = self._parse_buffer(buffer)
                    if parsed:
                        rows.append(parsed)
                    else:
                        warnings.append(f"Fila {self.insurer} no parseada: {' '.join(buffer)}")
                    buffer = []
                continue
            if re.match(r"^\d{2}/\d{2}/\d{4}\b", line) and buffer:
                parsed = self._parse_buffer(buffer)
                if parsed:
                    rows.append(parsed)
                else:
                    warnings.append(f"Fila {self.insurer} no parseada: {' '.join(buffer)}")
                buffer = [line]
            else:
                buffer.append(line)
        if buffer:
            parsed = self._parse_buffer(buffer)
            if parsed:
                rows.append(parsed)
            else:
                warnings.append(f"Fila {self.insurer} no parseada: {' '.join(buffer)}")
        return rows, warnings

    def _skip_line(self, line: str) -> bool:
        upper = normalize_for_match(line)
        return any(
            token in upper
            for token in (
                "LIQUIDACION NUMERO:",
                "BROKER:",
                "LIQUIDACION FECHA:",
                "FECHA Y HORA:",
                "TIPO DE DOCUMENTO",
                "NRO. DOCUMENTO",
                "DOC.",
                "MONTO DOC.",
                "MONTO COMISION",
                "NRO DE",
                "IDENTIFICACION",
                "TOTAL SIN IMPUESTOS",
                "TOTAL IGV",
                "TOTAL A COBRAR",
                "PAGINA",
                "CLIENTE",
            )
        )

    def _is_total_line(self, line: str) -> bool:
        upper = normalize_for_match(line)
        return upper.startswith("TOTALES") or upper.startswith("TOTAL SIN IMPUESTOS") or upper.startswith("TOTAL IGV") or upper.startswith("TOTAL A COBRAR")

    def _parse_buffer(self, buffer: list[str]) -> dict | None:
        candidate = normalize_spaces(" ".join(buffer))
        tail_match = TAIL_RE.match(candidate)
        if not tail_match:
            return None
        payload = tail_match.groupdict()
        prefix_tokens = payload["prefix"].split()
        document_legal = prefix_tokens[-1] if prefix_tokens else ""
        document_number = ""
        if len(prefix_tokens) >= 2 and re.search(r"[\d/-]", prefix_tokens[-2]):
            document_number = prefix_tokens[-2]
            description = " ".join(prefix_tokens[:-2]).strip()
        else:
            description = " ".join(prefix_tokens[:-1]).strip()
        description = description or payload["prefix"]
        return {
            "fecha_inicio": payload["fecha_inicio"],
            "descripcion": description,
            "document_number": document_number,
            "document_legal": document_legal,
            "monto_comision": to_decimal_flexible(payload["monto_comision"]),
            "pct_comision": to_decimal_flexible(payload["pct"]),
            "identificacion": payload["identificacion"],
            "cliente": payload["cliente"],
            "raw_line": candidate,
        }

    def _extract_totals(self, lines: list[str]) -> list[dict]:
        totals: list[dict] = []
        for line in lines:
            match = re.match(r"^TOTAL SIN IMPUESTOS:\s*S?/?\.?\s*(-?[\d,]+\.\d{2})$", line, flags=re.IGNORECASE)
            if match:
                totals.append({"scope": "DOCUMENTO", "metric": "total_sin_impuestos", "value": to_decimal_flexible(match.group(1))})
            match = re.match(r"^TOTAL IGV:\s*S?/?\.?\s*(-?[\d,]+\.\d{2})$", line, flags=re.IGNORECASE)
            if match:
                totals.append({"scope": "DOCUMENTO", "metric": "igv", "value": to_decimal_flexible(match.group(1))})
            match = re.match(r"^TOTAL A COBRAR:\s*S?/?\.?\s*(-?[\d,]+\.\d{2})$", line, flags=re.IGNORECASE)
            if match:
                totals.append({"scope": "DOCUMENTO", "metric": "total_a_cobrar", "value": to_decimal_flexible(match.group(1))})
            match = re.match(r"^TOTALES\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})$", line, flags=re.IGNORECASE)
            if match:
                totals.append({"scope": "DOCUMENTO", "metric": "total_documento", "value": to_decimal_flexible(match.group(1))})
                totals.append({"scope": "DOCUMENTO", "metric": "total_sin_impuestos_detalle", "value": to_decimal_flexible(match.group(2))})
        return totals

    def _build_validations(self, detail_rows: list[dict], reported_totals: list[dict]) -> list[dict]:
        validations: list[dict] = []
        reported_lookup = {row["metric"]: row["value"] for row in reported_totals}
        calculated = sum((row["monto_comision"] for row in detail_rows), start=Decimal("0"))
        expected = reported_lookup.get("total_sin_impuestos") or reported_lookup.get("total_sin_impuestos_detalle")
        if expected is not None:
            validations.append(
                build_validation(
                    scope="DOCUMENTO",
                    metric="total_sin_impuestos",
                    expected=expected,
                    calculated=calculated,
                )
            )
        return validations
