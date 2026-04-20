from __future__ import annotations

import re
from decimal import Decimal

from ..models import ParseContext, ParsedDocument
from ..utils import build_validation, clean_lines, normalize_for_match, to_decimal_flexible
from .base import BaseProfile


DETAIL_RE = re.compile(
    r"^(?P<fecha_inicio>\d{2}/\d{2}/\d{4})\s+(?P<producto>.+?)\s+"
    r"(?P<vigencia>\d{2}/\d{2}/\d{4}\s*-\s*\d{2}/\d{2}/\d{4})\s+"
    r"(?P<tipo_documento>[A-Z]{3})\s+(?P<contrato>\d+)\s+(?P<nro_documento>EPS-\d+)\s+"
    r"(?P<doc_legal>[BF]\d{3}-\d+)\s+(?P<monto_doc>-?[\d,]+\.\d{3})\s+"
    r"(?P<monto_comision>-?[\d,]+\.\d{3})\s+\((?P<pct>[\d.]+)\s*%\)\s+"
    r"(?P<identificacion>\d{8,14})\s+(?P<cliente>.+)$"
)


class SanitasEpsProfile(BaseProfile):
    profile_id = "sanitas_eps"
    insurer = "SANITAS"
    display_name = "Sanitas EPS"
    keywords = ("SANITAS", "LIQ-700", "VIGENCIA", "TOTAL A COBRAR")
    priority = 75

    def parse(self, text: str, context: ParseContext) -> ParsedDocument:
        lines = clean_lines(text)
        rows, warnings = self._extract_detail_rows(lines)
        reported_totals = self._extract_totals(lines)
        validations = self._build_validations(rows, reported_totals)
        liquidacion_match = re.search(r"LIQUIDACION\s+NUMERO:\s*(LIQ-[0-9]+)", text, flags=re.IGNORECASE)
        fecha_match = re.search(r"LIQUIDACION\s+FECHA:\s*([0-9/]+)", text, flags=re.IGNORECASE)

        return ParsedDocument(
            source_file=context.file_path.name,
            source_stem=context.file_path.stem,
            detected_insurer=self.insurer,
            detected_profile=self.display_name,
            document_number=liquidacion_match.group(1) if liquidacion_match else context.file_path.stem,
            document_type="Liquidacion EPS",
            broker="LA PROTECTORA CORREDORES DE SEGUROS SA",
            currency="S/",
            generated_at=fecha_match.group(1) if fecha_match else None,
            input_mode=context.input_mode,
            extracted_char_count=context.extracted_char_count,
            page_count=context.page_count,
            detail_rows=rows,
            reported_totals=reported_totals,
            validations=validations,
            warnings=warnings,
        )

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
                        warnings.append(f"Fila SANITAS no parseada: {' '.join(buffer)}")
                    buffer = []
                continue
            is_row_start = bool(re.match(r"^\d{2}/\d{2}/\d{4}\b(?!\s*-)", line))
            if is_row_start and buffer and not buffer[-1].endswith("-"):
                parsed = self._parse_buffer(buffer)
                if parsed:
                    rows.append(parsed)
                else:
                    warnings.append(f"Fila SANITAS no parseada: {' '.join(buffer)}")
                buffer = [line]
            else:
                buffer.append(line)
        if buffer:
            parsed = self._parse_buffer(buffer)
            if parsed:
                rows.append(parsed)
            else:
                warnings.append(f"Fila SANITAS no parseada: {' '.join(buffer)}")
        return rows, warnings

    def _parse_buffer(self, buffer: list[str]) -> dict | None:
        candidate = " ".join(buffer)
        candidate = re.sub(r"EPS-\s+(?=\d)", "EPS-", candidate)
        candidate = re.sub(r"([BF]\d{3}-?)\s+(\d+)", lambda match: f"{match.group(1)}{match.group(2)}", candidate)
        candidate = re.sub(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", r"\1 - \2", candidate)
        candidate = re.sub(r"\s+", " ", candidate).strip()
        match = DETAIL_RE.match(candidate)
        if not match:
            return self._parse_scan_buffer(buffer)
        payload = match.groupdict()
        return {
            "fecha_inicio": payload["fecha_inicio"],
            "producto": payload["producto"],
            "vigencia": payload["vigencia"],
            "tipo_documento": payload["tipo_documento"],
            "contrato": payload["contrato"],
            "nro_documento": payload["nro_documento"],
            "doc_legal": payload["doc_legal"],
            "monto_doc": to_decimal_flexible(payload["monto_doc"]),
            "monto_comision": to_decimal_flexible(payload["monto_comision"]),
            "pct_comision": to_decimal_flexible(payload["pct"]),
            "identificacion": payload["identificacion"],
            "cliente": payload["cliente"],
            "raw_line": candidate,
        }

    def _parse_scan_buffer(self, buffer: list[str]) -> dict | None:
        candidate = re.sub(r"\s+", " ", " ".join(buffer)).strip()
        candidate = re.sub(r"EPS-\s+(?=\d)", "EPS-", candidate)
        candidate = candidate.replace("FO02-", "F002-").replace("FO002-", "F002-").replace("BO02-", "B002-")

        head_match = re.match(
            r"^(?P<fecha_inicio>\d{2}/\d{2}/\d{4})\s+(?P<producto>.+?)\s+(?P<vigencia_inicio>\d{2}/\d{2}/\d{4})\s*-\s+"
            r"(?P<tipo_documento>[A-Z]{3})\s+(?P<contrato>\d+)\s+EPS-\s+(?P<doc_legal_prefix>[BF]\d{3}-)\s+"
            r"(?P<monto_doc>-?[\d,]+\.\d{3})\s+(?P<monto_comision>-?[\d,]+\.\d{3})\s+\((?P<pct>[\d.]+)(?:\s*%\)?)?\s+"
            r"(?P<identificacion>\d{8,14})\s+(?P<rest>.+)$",
            candidate,
        )
        if not head_match:
            return None
        payload = head_match.groupdict()
        tail_match = re.search(
            r"(?P<cliente_1>.+?)\s+(?P<vigencia_fin>\d{2}/\d{2}/\d{4})\s+(?P<nro_documento>\d+)\s+(?P<doc_legal_tail>\d+)\s*(?P<cliente_2>.*)$",
            payload["rest"],
        )
        if not tail_match:
            return None
        tail_payload = tail_match.groupdict()
        cliente = re.sub(r"\s+", " ", f"{tail_payload['cliente_1']} {tail_payload['cliente_2']}").strip()
        return {
            "fecha_inicio": payload["fecha_inicio"],
            "producto": payload["producto"].strip(),
            "vigencia": f"{payload['vigencia_inicio']} - {tail_payload['vigencia_fin']}",
            "tipo_documento": payload["tipo_documento"],
            "contrato": payload["contrato"],
            "nro_documento": f"EPS-{tail_payload['nro_documento']}",
            "doc_legal": f"{payload['doc_legal_prefix']}{tail_payload['doc_legal_tail']}",
            "monto_doc": to_decimal_flexible(payload["monto_doc"]),
            "monto_comision": to_decimal_flexible(payload["monto_comision"]),
            "pct_comision": to_decimal_flexible(payload["pct"]),
            "identificacion": payload["identificacion"],
            "cliente": cliente,
            "raw_line": candidate,
        }

    def _skip_line(self, line: str) -> bool:
        upper = normalize_for_match(line)
        if upper.endswith("SANITAS") or upper == "LA PROTECTORA CORREDORES DE SEGUROS SA":
            return True
        return any(
            token in upper
            for token in (
                "LIQUIDACION NUMERO",
                "BROKER:",
                "LIQUIDACION FECHA",
                "FECHA Y HORA",
                "FECHA INICIO PRODUCTO VIGENCIA",
                "BROKER",
                "DOCUMENTO",
                "DOCUMENTO CONTRATO",
                "CONTRATO NRO",
                "DOC. LEGAL",
                "MONTO DOC",
                "COMISION",
                "NRO DE",
                "IDENTIFICACION",
                "NRO DE IDENTIFICACION",
                "CLIENTE",
            )
        )

    def _is_total_line(self, line: str) -> bool:
        upper = normalize_for_match(line)
        return upper.startswith("TOTAL SIN IMPUESTOS") or upper.startswith("TOTAL IGV") or upper.startswith("TOTAL A COBRAR") or upper.startswith("TOTALES")

    def _extract_totals(self, lines: list[str]) -> list[dict]:
        totals: list[dict] = []
        for line in lines:
            for label, metric in (
                ("TOTAL SIN IMPUESTOS", "total_sin_impuestos"),
                ("TOTAL IGV", "igv"),
                ("TOTAL A COBRAR", "total_a_cobrar"),
            ):
                match = re.match(rf"^{label}:\s*(-?[\d,]+\.\d{{3}})$", line, flags=re.IGNORECASE)
                if match:
                    totals.append({"scope": "DOCUMENTO", "metric": metric, "value": to_decimal_flexible(match.group(1))})
            match = re.match(r"^TOTALES\s+(-?[\d,]+\.\d{3})\s+(-?[\d,]+\.\d{3})$", line, flags=re.IGNORECASE)
            if match:
                totals.append({"scope": "DOCUMENTO", "metric": "total_monto_doc", "value": to_decimal_flexible(match.group(1))})
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
