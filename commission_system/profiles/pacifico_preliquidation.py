from __future__ import annotations

import re
from decimal import Decimal

from ..models import ParseContext, ParsedDocument
from ..utils import build_validation, clean_lines, find_prefixed_value, normalize_for_match, to_decimal_flexible
from .base import BaseProfile


DETAIL_RE = re.compile(
    r"^(?P<item>\d+)\s+(?P<producto>\S+)\s+(?P<poliza>\S+)\s+(?P<avcob>[\d/Il]+)\s+(?P<ram>\S+)\s+"
    r"(?P<doc>[\d/Il]+)\s+(?P<concepto>.+?)\s+(?P<fecha_pago>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<prima_comercial>-?[\d,]+\.\d{2})\s+(?P<derecho_emision>-?[\d,]+\.\d{2})\s+"
    r"(?P<prima_afecta>-?[\d,]+\.\d{2})\s+(?P<pct_comision>-?[\d,]+\.\d{2})\s+"
    r"(?P<monto_comision>-?[\d,]+\.\d{2})$"
)

DETAIL_START_RE = re.compile(r"^\d{1,3}\s+[A-Z0-9]{3,6}\s+\d{6,}\b")

TOTAL_LABELS = (
    ("SALDO ANTERIOR", "saldo_anterior"),
    ("MONTO IMPONIBLE", "monto_imponible"),
    ("IMPUESTO GENERAL A LAS VENTAS", "igv"),
    ("NETO A PAGAR", "neto_a_pagar"),
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
        reported_totals, reconciliation_warnings = self._reconcile_totals(detail_rows, reported_totals)
        warnings.extend(reconciliation_warnings)
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
            if self._is_total_line(line):
                rows, warnings = self._flush_buffer(rows, warnings, buffer)
                buffer = ""
                continue
            if self._looks_like_detail_start(line):
                rows, warnings = self._flush_buffer(rows, warnings, buffer)
                buffer = line
                continue
            if buffer:
                if self._parse_detail_line(buffer) is not None:
                    rows, warnings = self._flush_buffer(rows, warnings, buffer)
                    buffer = ""
                    continue
                buffer = f"{buffer} {line}".strip()
        if buffer:
            rows, warnings = self._flush_buffer(rows, warnings, buffer)
        return rows, warnings

    def _skip_line(self, line: str) -> bool:
        upper = line.upper()
        return bool(re.match(r"^\d+\s+DE\s+\d+$", upper)) or upper == "ADMSGCOM" or any(
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

    def _is_total_line(self, line: str) -> bool:
        upper = normalize_for_match(line)
        return any(upper.startswith(label) for label, _ in TOTAL_LABELS)

    def _looks_like_detail_start(self, line: str) -> bool:
        return bool(DETAIL_START_RE.match(line))

    def _flush_buffer(self, rows: list[dict], warnings: list[str], buffer: str) -> tuple[list[dict], list[str]]:
        if not buffer:
            return rows, warnings
        parsed = self._parse_detail_line(buffer)
        if parsed is None:
            warnings.append(f"Fila PACIFICO no parseada: {buffer}")
        else:
            rows.append(parsed)
        return rows, warnings

    def _parse_detail_line(self, line: str) -> dict | None:
        normalized = re.sub(r"\s+", " ", line).strip()
        normalized = normalized.replace("—", " ").replace("–", " ").replace(" ?", " ").replace("? ", " ")
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
            "document": self._normalize_doc_token(payload["doc"]),
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
        totals_map: dict[str, Decimal] = {}
        pending_metrics: list[str] = []
        capture_numeric_block = False

        for line in lines:
            normalized = normalize_for_match(line).rstrip(":")
            metric = next((value for label, value in TOTAL_LABELS if normalized.startswith(label)), None)
            if metric:
                capture_numeric_block = True
                amounts = re.findall(r"-?\d[\d,]*\.\d{2}", line)
                if amounts:
                    totals_map[metric] = to_decimal_flexible(amounts[-1])
                else:
                    pending_metrics.append(metric)
                continue

            if not capture_numeric_block or not pending_metrics:
                continue

            stripped = line.strip()
            if not re.fullmatch(r"-?\d[\d,]*\.\d{2}", stripped):
                continue

            metric = pending_metrics.pop(0)
            totals_map[metric] = to_decimal_flexible(stripped)

        return [
            {"scope": "DOCUMENTO", "metric": metric, "value": totals_map[metric]}
            for _, metric in TOTAL_LABELS
            if metric in totals_map
        ]

    def _reconcile_totals(self, detail_rows: list[dict], reported_totals: list[dict]) -> tuple[list[dict], list[str]]:
        warnings: list[str] = []
        if not detail_rows or not reported_totals:
            return reported_totals, warnings

        calculated_commission = sum((row["monto_comision"] for row in detail_rows), start=Decimal("0"))
        totals_lookup = {row["metric"]: row for row in reported_totals}
        saldo = totals_lookup.get("saldo_anterior", {}).get("value")
        monto = totals_lookup.get("monto_imponible", {}).get("value")
        igv = totals_lookup.get("igv", {}).get("value")
        neto = totals_lookup.get("neto_a_pagar", {}).get("value")

        derived_imponible = None
        if igv is not None and neto is not None:
            derived_imponible = neto - igv

        reference = None
        if saldo is not None and abs(saldo - calculated_commission) <= Decimal("0.01"):
            reference = saldo
        elif derived_imponible is not None and abs(derived_imponible - calculated_commission) <= Decimal("0.01"):
            reference = derived_imponible

        if reference is not None and monto is not None and abs(monto - reference) > Decimal("0.01"):
            totals_lookup["monto_imponible"]["value"] = reference
            warnings.append(
                "Se ajusto monto_imponible usando la consistencia aritmetica del documento y la suma del detalle."
            )

        return reported_totals, warnings

    def _normalize_doc_token(self, value: str) -> str:
        normalized = value.upper().replace("I", "1").replace("L", "1")
        normalized = re.sub(r"[^0-9/]", "", normalized)
        replacements = {
            "111": "1/1",
            "1110": "1/10",
            "1112": "1/12",
            "011": "0/1",
            "0/11": "0/1",
            "1/11": "1/1",
        }
        normalized = replacements.get(normalized, normalized)
        if normalized in {"0/1", "1/1", "1/4", "1/10", "1/12"}:
            return normalized
        if normalized.isdigit():
            normalized = replacements.get(normalized, normalized)
            if normalized in {"0/1", "1/1", "1/4", "1/10", "1/12"}:
                return normalized
        if "/" in normalized:
            left, right = normalized.split("/", 1)
            if left in {"0", "1"} and right in {"1", "4", "10", "12"}:
                return f"{left}/{right}"
        return normalized

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
