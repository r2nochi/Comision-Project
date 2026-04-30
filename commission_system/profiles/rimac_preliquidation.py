from __future__ import annotations

import re
from decimal import Decimal

from ..models import ParseContext, ParsedDocument
from ..utils import build_validation, clean_lines, normalize_code_like_field, normalize_for_match, normalize_spaces, to_decimal_flexible
from .base import BaseProfile


POLIZA_RE = r"[A-Z0-9]{8,12}"
START_LINE_RE = re.compile(
    rf"^(?P<producto>EPS|S\.C\.T\.R\.\s*-\s*[A-Z]+)\s+(?P<poliza>{POLIZA_RE})\s+(?P<cliente>.+?)\s+"
    r"(?P<documento>(?:LQ|LA)-\d+)\s+(?P<doc_sunat_prefix>(?:FA|NA|BV)-[A-Z0-9]+-)\s+(?P<tipo>COMI|EXTR)\s+(?P<rest>.+)$",
    flags=re.IGNORECASE,
)
COMI_RE = re.compile(
    r"(?P<fecha>\d{2}/\d{2}/\d{4})\s+(?P<prima>-?[\d,]+\.\d{1,2})\s+(?P<pct>-?\d{1,2}\.\d{2})\s+(?P<comision>-?[\d,]+\.\d{2})",
    flags=re.IGNORECASE,
)
EXTR_RE = re.compile(r"(?P<comision>-?[\d,]+\.\d{2})", flags=re.IGNORECASE)
TOTAL_VALUE_RE = re.compile(r"(-?[\d,]+\.\d{2})")


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
        reported_totals = self._extract_totals(text)
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
        normalized = self._normalize_line(normalized)
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
            if self._is_footer_line(line):
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = []
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
        skipped_chunks = 0
        for chunk in chunks:
            if any("TOTAL" == normalize_for_match(line) or "I.G.V." in normalize_for_match(line) for line in chunk):
                continue
            parsed = self._parse_chunk(chunk)
            if parsed is None:
                skipped_chunks += 1
                continue
            rows.append(parsed)
        if not rows:
            warnings.append("No se pudieron reconstruir filas RIMAC con OCR suficiente.")
        elif skipped_chunks:
            warnings.append(f"Se omitieron {skipped_chunks} bloques RIMAC que no pudieron parsearse con OCR.")
        return rows, warnings

    def _parse_chunk(self, chunk_lines: list[str]) -> dict | None:
        normalized_lines = [self._normalize_line(line) for line in chunk_lines if self._normalize_line(line)]
        if not normalized_lines:
            return None

        start_line = normalized_lines[0]
        head_match = START_LINE_RE.match(start_line)
        if not head_match:
            return None

        payload = head_match.groupdict()
        continuation_lines = normalized_lines[1:]
        doc_tail = self._extract_doc_tail(continuation_lines)
        doc_sunat = f"{payload['doc_sunat_prefix']}{doc_tail}" if doc_tail else payload["doc_sunat_prefix"]
        trailing_decimal_digit = self._extract_trailing_decimal_digit(continuation_lines, doc_tail)

        pct = Decimal("0")
        prima = Decimal("0")
        comision = Decimal("0")
        fecha_pago = None
        client_continuation = self._build_continuation_text(continuation_lines, doc_tail)
        if normalize_for_match(payload["tipo"]) == "EXTR":
            extr_match = EXTR_RE.search(payload["rest"])
            if not extr_match:
                return None
            comision = to_decimal_flexible(extr_match.group("comision"))
        else:
            amount_match = COMI_RE.search(payload["rest"])
            if not amount_match:
                return None
            fecha_pago = amount_match.group("fecha")
            prima = to_decimal_flexible(self._complete_split_decimal(amount_match.group("prima"), trailing_decimal_digit))
            pct = to_decimal_flexible(amount_match.group("pct"))
            comision = to_decimal_flexible(amount_match.group("comision"))

        producto = self._normalize_producto(payload["producto"])
        cliente = self._normalize_cliente(" ".join(part for part in [payload["cliente"], client_continuation] if part))
        return {
            "producto": producto,
            "poliza": self._normalize_policy(payload["poliza"], producto),
            "cliente": cliente,
            "documento": normalize_code_like_field(payload["documento"], allowed="A-Z0-9-"),
            "doc_sunat": self._normalize_doc_sunat(doc_sunat),
            "tipo": normalize_for_match(payload["tipo"]),
            "fecha_pago": fecha_pago,
            "prima": prima,
            "pct_comision": pct,
            "comision": comision,
            "raw_chunk": normalize_spaces(" | ".join(normalized_lines)),
        }

    def _skip_line(self, line: str) -> bool:
        upper = normalize_for_match(line)
        if upper == "EPS":
            return True
        return any(
            token in upper
            for token in (
                "PAGINA:",
                "PRELIQUIDACION DE ASESORIAS",
                "PRELIGUIDACION DE ASESORIAS",
                "HORA:",
                "USUARIO:",
                "MONEDA:",
                "NRO-PRELIQUIDACION",
                "NRO-PRELIGUIDACION",
                "INTERMEDIARIO:",
                "PAGUESE A LA ORDEN",
                "PRODUCTO PLIZA CLIENTE DOCUMENTO",
                "PRODUCTO POLIZA CLIENTE DOCUMENTO",
                "DOC.SUNAT",
                "PORCENT.",
                "COMISIONPRIMA",
                "TOTAL",
            )
        )

    def _is_footer_line(self, line: str) -> bool:
        upper = normalize_for_match(self._normalize_line(line))
        return (
            bool(re.match(r"^(?:[IL1]\.?G\.?V\.?)\b", upper))
            or bool(re.match(r"^TOTA[LI]\s+-?[\d,]+\.\d{2}$", upper))
            or bool(re.match(r"^LIMA,\s*\d{1,2}\s+DE\b", upper))
            or "EL CORREDOR DE SEGUROS, A QUIEN SE LE HACE ENTREGA DE LA PRESENTE PRE-LIQUIDACION" in upper
            or "POR PARTE DE LAS SUMAS INDICADAS EN DICHA PRE-LIQUIDACION" in upper
            or "TASAS O PORCENTAJES APLICADOS PARA EL CALCULO DE LOS SENALADOS IMPORTES" in upper
            or "LA OBLIGACION DE PAGO DE LAS SUMAS DESCRITAS EN LA PRESENTE PRE-LIQUIDACION" in upper
            or upper == 'MODALIDAD".'
            or upper == "MODALIDAD."
        )

    def _extract_totals(self, text: str) -> list[dict]:
        totals: list[dict] = []
        lines = clean_lines(text)
        total_values: list[Decimal] = []
        igv_value: Decimal | None = None

        for raw_line in lines:
            line = self._normalize_line(raw_line)
            upper = normalize_for_match(line)
            value_match = TOTAL_VALUE_RE.search(line)
            if not value_match:
                continue
            value = to_decimal_flexible(value_match.group(1))
            if re.search(r"(?:^|[\s])TOTA[LI](?:$|[\s])", upper):
                total_values.append(value)
            elif re.search(r"[IL1]\.?G\.?V\.?", upper):
                igv_value = value

        if total_values:
            totals.append({"scope": "DOCUMENTO", "metric": "total_comision", "value": total_values[0]})
        if igv_value is not None:
            totals.append({"scope": "DOCUMENTO", "metric": "igv", "value": igv_value})
        if len(total_values) >= 2:
            totals.append({"scope": "DOCUMENTO", "metric": "total_general", "value": total_values[-1]})
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
        if "total_general" in reported_lookup and "igv" in reported_lookup:
            validations.append(
                build_validation(
                    scope="DOCUMENTO",
                    metric="total_general",
                    expected=reported_lookup["total_general"],
                    calculated=calculated + reported_lookup["igv"],
                )
            )
        return validations

    def _normalize_line(self, value: str) -> str:
        normalized = normalize_spaces(value)
        normalized = normalized.replace("_", " ").replace("£", "")
        normalized = normalized.replace("—", " ").replace("–", " ").replace("—", " ")
        normalized = normalized.replace("LOQ-", "LQ-").replace("LQO-", "LQ-").replace("LO-", "LQ-")
        normalized = normalized.replace("Nro-Preliguidacion", "Nro-Preliquidacion")
        normalized = re.sub(r"\bTOTAI\b", "TOTAL", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"(?<=[A-Z])LQ-", r" LQ-", normalized)
        normalized = re.sub(r"(?<=-)LQ-", r" LQ-", normalized)
        normalized = re.sub(r"(?<=[A-Z])LA-", r" LA-", normalized)
        normalized = re.sub(r"(?<=-)LA-", r" LA-", normalized)
        normalized = re.sub(r"(?<=[A-Z])FA-", r" FA-", normalized)
        normalized = re.sub(r"(?<=[A-Z])NA-", r" NA-", normalized)
        normalized = re.sub(r"(?<=[A-Z])BV-", r" BV-", normalized)
        normalized = normalize_spaces(normalized)
        return normalized

    def _extract_doc_tail(self, continuation_lines: list[str]) -> str:
        for line in continuation_lines:
            match = re.search(r"\b(\d{7,})\b", line)
            if match:
                return match.group(1)
        return ""

    def _build_continuation_text(self, continuation_lines: list[str], doc_tail: str) -> str:
        cleaned_parts: list[str] = []
        for line in continuation_lines:
            if self._is_footer_line(line):
                break
            cleaned = line
            if doc_tail:
                cleaned = cleaned.replace(doc_tail, " ")
            cleaned = re.sub(r"\b\d\b", " ", cleaned)
            cleaned = re.sub(r"\b\d{7,}\b", " ", cleaned)
            cleaned = normalize_spaces(cleaned)
            if cleaned:
                cleaned_parts.append(cleaned)
        return normalize_spaces(" ".join(cleaned_parts))

    def _extract_trailing_decimal_digit(self, continuation_lines: list[str], doc_tail: str) -> str | None:
        for line in continuation_lines:
            probe = normalize_spaces(line)
            if doc_tail and doc_tail in probe:
                tail = normalize_spaces(probe.replace(doc_tail, " "))
                match = re.search(r"\b(\d)\b$", tail)
                if match:
                    return match.group(1)
            else:
                match = re.fullmatch(r"\d", probe)
                if match:
                    return match.group(0)
        return None

    def _complete_split_decimal(self, raw_value: str, trailing_digit: str | None) -> str:
        normalized = normalize_spaces(raw_value)
        match = re.search(r"(?P<int>-?[\d,]+)\.(?P<dec>\d)$", normalized)
        if not match:
            return normalized
        if trailing_digit:
            return f"{match.group('int')}.{match.group('dec')}{trailing_digit}"
        return f"{match.group('int')}.{match.group('dec')}0"

    def _normalize_producto(self, value: str) -> str:
        normalized = normalize_spaces(value).upper()
        normalized = normalized.replace("S.C.T.R.-", "S.C.T.R. - ").replace("S.C.T.R.- ", "S.C.T.R. - ")
        normalized = normalized.replace("S.C.T.R. - SALU", "S.C.T.R. - SALU")
        return normalized

    def _normalize_cliente(self, value: str) -> str:
        normalized = normalize_spaces(value)
        normalized = re.sub(r"\b([A-Z])Y([A-Z])(?=[A-Z]{3,})", r"\1 Y \2 ", normalized)
        normalized = re.sub(r"(?<=[A-Z])(?=(SCRL|SAC|SRL|EIRL)\b)", " ", normalized)
        normalized = re.sub(r"(?<=[A-Z])(?=(S\.A\.C\.|S\.A\.|S\.R\.L\.)\b)", " ", normalized)
        return normalize_spaces(normalized)

    def _normalize_policy(self, value: str, producto: str) -> str:
        normalized = re.sub(r"[^A-Z0-9]", "", normalize_spaces(str(value)).upper())
        normalized = normalized.replace("O", "0").replace("I", "1").replace("L", "1")
        expected_prefix = self._expected_policy_prefix(producto)
        if not expected_prefix:
            return normalized

        digits = "".join(character for character in normalized if character.isdigit())
        if len(digits) >= 7:
            tail = digits[-7:]
            return f"{expected_prefix}{tail}"
        if normalized.startswith(expected_prefix):
            return normalized
        return f"{expected_prefix}{digits.rjust(7, '0')}"

    def _expected_policy_prefix(self, producto: str) -> str | None:
        normalized = normalize_for_match(producto)
        if normalized == "EPS":
            return "00E"
        if "PENS" in normalized:
            return "00P"
        if "SALU" in normalized:
            return "00S"
        return None

    def _normalize_doc_sunat(self, value: str) -> str:
        normalized = re.sub(r"[^A-Z0-9-]", "", normalize_code_like_field(value, allowed="A-Z0-9-"))
        return normalized.replace("--", "-")
