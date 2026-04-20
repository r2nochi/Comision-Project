from __future__ import annotations

import re
from decimal import Decimal

from ..models import ParseContext, ParsedDocument
from ..utils import build_validation, clean_lines, normalize_for_match, normalize_spaces, to_decimal_flexible
from .base import BaseProfile


POLIZA_RE = r"(?:00[ESP]\d{6,}|00[0-9]\d{6,}|[SP]\d{7,}|0{1,2}[A-Z]\d{6,})"

PATTERN_A = re.compile(
    rf"^(?P<descriptor>.+?)\s+(?P<poliza>{POLIZA_RE})\s+(?P<documento>(?:LQ|LA)-\d+)\s+"
    r"(?P<doc_sunat>(?:FA|NA)-[A-Z0-9]+-\d+)\s+(?P<tipo>COMI|EXTR)\s+"
    r"(?:(?P<pct>-?\d{1,2}\.\d{2})\s+(?P<prima>-?[\d,]+\.\d{2})\s+(?P<comision>-?[\d,]+\.\d{2})\s+(?P<fecha>\d{2}/\d{2}/\d{4})|(?P<extra>-?[\d,]+\.\d{2}))",
    flags=re.IGNORECASE,
)
PATTERN_B = re.compile(
    rf"^(?P<producto>(?:EPS|S\.C\.T\.R\.\s*-\s*[A-Z]+))\s+(?P<poliza>{POLIZA_RE})\s+(?P<descriptor>.+?)\s+"
    r"(?P<documento>(?:LQ|LA)-\d+)\s+(?P<doc_sunat>(?:FA|NA)-[A-Z0-9]+-\d+)\s+(?P<tipo>COMI|EXTR)\s+"
    r"(?:(?P<fecha>\d{2}/\d{2}/\d{4})\s+(?P<prima>-?[\d,]+\.\d{2})\s+(?P<pct>-?\d{1,2}\.\d{2})\s+(?P<comision>-?[\d,]+\.\d{2})|(?P<extra>-?[\d,]+\.\d{2}))",
    flags=re.IGNORECASE,
)


class RimacPreliquidationProfile(BaseProfile):
    profile_id = "rimac_preliquidation"
    insurer = "RIMAC"
    display_name = "Rimac Preliquidacion"
    keywords = ("RIMAC", "PRELIQUIDACION DE ASESORIAS", "NRO-PRELIQUIDACION", "I.G.V.")
    priority = 90
    prefer_ocr_even_for_digital = True

    def parse(self, text: str, context: ParseContext) -> ParsedDocument:
        normalized_text = self._normalize_text(text)
        detail_rows, warnings = self._extract_detail_rows(text)
        reported_totals = self._extract_totals(normalized_text)
        validations = self._build_validations(detail_rows, reported_totals)
        document_match = re.search(r"NRO-?PRELIQUIDACION:\s*([0-9]+)", normalized_text, flags=re.IGNORECASE)
        fecha_match = re.search(r"FECHA:\s*(\d{2}/\d{2}/\d{4})", normalized_text, flags=re.IGNORECASE)
        broker_match = re.search(r"INTERMEDIARIO:\s*(.+?)\s+PAGUESE A LA ORDEN", normalized_text, flags=re.IGNORECASE)

        return ParsedDocument(
            source_file=context.file_path.name,
            source_stem=context.file_path.stem,
            detected_insurer=self.insurer,
            detected_profile=self.display_name,
            document_number=document_match.group(1) if document_match else context.file_path.stem,
            document_type="Preliquidacion de Asesorias",
            broker=normalize_spaces(broker_match.group(1)) if broker_match else None,
            currency="SOL",
            generated_at=fecha_match.group(1) if fecha_match else None,
            input_mode=context.input_mode,
            extracted_char_count=context.extracted_char_count,
            page_count=context.page_count,
            detail_rows=detail_rows,
            reported_totals=reported_totals,
            validations=validations,
            warnings=warnings,
        )

    def _normalize_text(self, text: str) -> str:
        lines = clean_lines(text)
        kept = []
        for line in lines:
            upper = line.upper()
            if any(
                token in upper
                for token in (
                    "PAGINA:",
                    "USUARIO:",
                    "PRODUCTO PLIZA CLIENTE DOCUMENTO",
                    "PAGUESE A LA ORDEN",
                )
            ):
                continue
            kept.append(line)
        normalized = " ".join(kept)
        normalized = normalized.replace("LO-", "LQ-").replace("LOQ-", "LQ-").replace("RIM AC", "RIMAC")
        normalized = re.sub(r"(FA|NA)-([A-Z0-9]+)-\s+(\d+)", r"\1-\2-\3", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"(\d{1,3}(?:,\d{3})*\.\d)\s+(\d)\b", r"\1\2", normalized)
        normalized = re.sub(r"(\d{1,2}\.\d{2})(?=\d)", r"\1 ", normalized)
        normalized = normalize_spaces(normalized)
        return normalized

    def _extract_detail_rows(self, text: str) -> tuple[list[dict], list[str]]:
        warnings: list[str] = []
        lines = clean_lines(text)
        chunks: list[list[str]] = []
        current_chunk: list[str] = []

        for line in lines:
            if self._skip_line(line):
                continue
            if re.match(r"^(EPS|S\.C\.T\.R\.)", line):
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = [line]
                continue
            if not current_chunk:
                continue
            current_chunk.append(line)
        if current_chunk:
            chunks.append(current_chunk)

        rows: list[dict] = []
        for chunk in chunks:
            if any("TOTAL" == normalize_for_match(line) or "I.G.V." in normalize_for_match(line) for line in chunk):
                continue
            parsed = self._parse_chunk(chunk)
            if parsed is None:
                continue
            rows.append(parsed)
        if not rows:
            warnings.append("No se pudieron reconstruir filas RIMAC con OCR suficiente.")
        return rows, warnings

    def _parse_chunk(self, chunk_lines: list[str]) -> dict | None:
        chunk_text = normalize_spaces(" ".join(chunk_lines))
        chunk_text = chunk_text.replace("LO-", "LQ-").replace("LOQ-", "LQ-").replace("LQO-", "LQ-")

        head_match = re.match(
            rf"^(?P<producto>(?:EPS|S\.C\.T\.R\.\s*-\s*[A-Z]+))\s+(?P<poliza>{POLIZA_RE})\s+"
            r"(?P<cliente>.+?)\s+(?P<documento>(?:LQ|LA)-\d+)\s+"
            r"(?P<doc_sunat_prefix>(?:FA|NA)-[A-Z0-9]+-)\s+(?P<tipo>COMI|EXTR)\s+(?P<rest>.+)$",
            chunk_text,
            flags=re.IGNORECASE,
        )
        if not head_match:
            return None

        payload = head_match.groupdict()
        tail_match = re.search(r"\b(\d{7,})\b", payload["rest"])
        doc_tail = tail_match.group(1) if tail_match else ""
        doc_sunat = f"{payload['doc_sunat_prefix']}{doc_tail}" if doc_tail else payload["doc_sunat_prefix"]

        pct = Decimal("0")
        prima = Decimal("0")
        comision = Decimal("0")
        fecha_pago = None
        client_continuation = payload["rest"]
        if normalize_for_match(payload["tipo"]) == "EXTR":
            extr_match = re.match(r"(?P<comision>-?[\d,]+\.\d{2})(?P<extra>.*)$", payload["rest"])
            if not extr_match:
                return None
            comision = to_decimal_flexible(extr_match.group("comision"))
            client_continuation = extr_match.group("extra")
        else:
            amount_match = re.match(
                r"(?P<fecha>\d{2}/\d{2}/\d{4})\s+(?P<a>-?[\d,]+\.\d{1,2})\s+(?P<b>-?[\d,]+\.\d{2})\s+(?P<c>-?[\d,]+\.\d{2})(?P<extra>.*)$",
                payload["rest"],
            )
            if not amount_match:
                return None
            fecha_pago = amount_match.group("fecha")
            values = [
                to_decimal_flexible(amount_match.group("a")),
                to_decimal_flexible(amount_match.group("b")),
                to_decimal_flexible(amount_match.group("c")),
            ]
            pct_candidates = [value for value in values if abs(value) <= Decimal("30")]
            if pct_candidates:
                pct = pct_candidates[0]
                values.remove(pct)
            if values:
                prima = max(values, key=abs)
                comision = min(values, key=abs) if len(values) >= 2 else values[0]
            client_continuation = amount_match.group("extra")

        client_continuation = re.sub(r"\b\d{7,}\b", "", client_continuation).strip(" ,")
        descriptor = normalize_spaces(
            " ".join(part for part in [payload["producto"], payload["cliente"], client_continuation] if part)
        )
        return {
            "descriptor": descriptor,
            "poliza": payload["poliza"],
            "documento": payload["documento"],
            "doc_sunat": doc_sunat,
            "tipo": normalize_for_match(payload["tipo"]),
            "fecha_pago": fecha_pago,
            "prima": prima,
            "pct_comision": pct,
            "comision": comision,
            "raw_chunk": chunk_text,
        }

    def _skip_line(self, line: str) -> bool:
        upper = normalize_for_match(line)
        return any(
            token in upper
            for token in (
                "PAGINA:",
                "PRELIQUIDACION DE ASESORIAS",
                "HORA:",
                "USUARIO:",
                "MONEDA:",
                "NRO-PRELIQUIDACION",
                "INTERMEDIARIO:",
                "PAGUESE A LA ORDEN",
                "PRODUCTO PLIZA CLIENTE DOCUMENTO",
                "DOC.SUNAT",
                "PORCENT.",
                "COMISIONPRIMA",
                "TOTAL",
            )
        )

    def _extract_totals(self, normalized_text: str) -> list[dict]:
        totals: list[dict] = []
        match = re.search(
            r"TOTAL\s+I\.G\.V\.\s+TOTAL\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})",
            normalized_text,
            flags=re.IGNORECASE,
        )
        if match:
            totals.append({"scope": "DOCUMENTO", "metric": "total_comision", "value": to_decimal_flexible(match.group(1))})
            totals.append({"scope": "DOCUMENTO", "metric": "igv", "value": to_decimal_flexible(match.group(2))})
            totals.append({"scope": "DOCUMENTO", "metric": "total_general", "value": to_decimal_flexible(match.group(3))})
        return totals

    def _build_validations(self, detail_rows: list[dict], reported_totals: list[dict]) -> list[dict]:
        validations: list[dict] = []
        reported_lookup = {row["metric"]: row["value"] for row in reported_totals}
        calculated = sum((row["comision"] for row in detail_rows), start=Decimal("0"))
        if "total_comision" in reported_lookup:
            validations.append(
                build_validation(
                    scope="DOCUMENTO",
                    metric="total_comision",
                    expected=reported_lookup["total_comision"],
                    calculated=calculated,
                )
            )
        return validations
