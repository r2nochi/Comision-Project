from __future__ import annotations

import re

from ..models import ParseContext, ParsedDocument
from ..utils import clean_lines, normalize_code_like_field, normalize_for_match, normalize_spaces
from .generic_liquidation import GenericLiquidationProfile
from .rotatable_liquidation_layout import choose_best_detail_candidate, expected_total_from_reported


class SanitasLiquidationProfile(GenericLiquidationProfile):
    def __init__(self) -> None:
        super().__init__(
            profile_id="sanitas_liquidation",
            insurer="SANITAS",
            display_name="Sanitas Liquidacion",
            keywords=("SANITAS", "LIQUIDACION NUMERO", "TOTAL A COBRAR"),
        )

    def parse(self, text: str, context: ParseContext) -> ParsedDocument:
        lines = clean_lines(text)
        reported_totals = self._extract_totals(lines)
        text_rows, text_warnings = super()._extract_detail_rows(lines)
        detail_rows, warnings, _ = choose_best_detail_candidate(
            insurer=self.insurer,
            file_path=context.file_path,
            expected_total=expected_total_from_reported(reported_totals),
            fallback_rows=text_rows,
            fallback_warnings=text_warnings,
        )
        if not detail_rows:
            warnings.append("Se uso el parser generico SANITAS porque el OCR estructurado no devolvio filas.")
        detail_rows = [self._normalize_output_row(row) for row in detail_rows]

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

    def _normalize_output_row(self, row: dict) -> dict:
        output = dict(row)
        raw_line = str(output.get("raw_line", ""))
        output["tipo_documento"] = self._normalize_tipo_documento(output.pop("descripcion", ""), raw_line)
        output["document_number"] = self._normalize_document_number(output.get("document_number"), raw_line)
        output["document_legal"] = self._normalize_document_legal(output.get("document_legal"), raw_line)
        output["identificacion"] = self._normalize_identificacion(output.get("identificacion"))
        output["cliente"] = self._normalize_cliente(output.get("cliente"), raw_line)

        if normalize_for_match(str(output["tipo_documento"])).startswith("NOTA DE CREDITO") and output.get("monto_comision") is not None:
            output["monto_comision"] = abs(output["monto_comision"])

        return output

    def _normalize_tipo_documento(self, value, raw_line: str) -> str:
        normalized = normalize_spaces(str(value or ""))
        normalized = normalized.replace("Sanitas Perú SA,", "Sanitas Perú S.A.")
        normalized = normalized.replace("Sanitas Perú S.A,", "Sanitas Perú S.A.")
        normalized = normalized.replace("Sanitas Perú SA.", "Sanitas Perú S.A.")

        direct_match = re.match(r"^(Cuota|Proforma|Factura)\s*-\s*Sanitas(?:\s+Perú)?(?:\s+S\.?A[.,]?)?$", normalized, flags=re.IGNORECASE)
        if direct_match:
            return f"{direct_match.group(1).title()} - Sanitas Perú S.A."

        upper_raw = normalize_for_match(raw_line)
        if normalize_for_match(normalized).startswith("NOTA DE CREDITO CPE"):
            source_match = re.search(r"\|\s*\d{2}/\d{2}/\d{4}\s+((?:FACTURA|CUOTA|PROFORMA)\s*-\s*SANITAS)\b", upper_raw)
            if source_match:
                base = source_match.group(1).title().replace("Sanitas", "Sanitas")
                return f"Nota de crédito CPE {base} Perú S.A."
            return "Nota de crédito CPE Factura - Sanitas Perú S.A."

        return normalized

    def _normalize_document_number(self, value, raw_line: str) -> str:
        normalized = normalize_code_like_field(str(value or ""))
        normalized = normalized.replace("--", "-")
        if not normalized:
            raw_match = re.search(r"\b([A-Z]{2,}-[0-9/]+)\b", normalize_spaces(raw_line), flags=re.IGNORECASE)
            if raw_match:
                normalized = normalize_code_like_field(raw_match.group(1))
        return normalized

    def _normalize_document_legal(self, value, raw_line: str) -> str:
        normalized = normalize_code_like_field(str(value or "")).replace("--", "-")
        if re.fullmatch(r"\d{8}", normalized):
            raw_upper = normalize_for_match(raw_line)
            prefix = "B002" if "B002" in raw_upper or "BO02" in raw_upper else "F002"
            normalized = f"{prefix}-{normalized}"
        return normalized

    def _normalize_identificacion(self, value) -> str:
        digits = "".join(character for character in str(value or "") if character.isdigit())
        return f"RUC - {digits}" if digits else str(value or "")

    def _normalize_cliente(self, value, raw_line: str) -> str:
        normalized = normalize_spaces(str(value or ""))
        raw_candidate = self._extract_client_from_raw_line(raw_line)
        if raw_candidate and self._client_quality(raw_candidate) > self._client_quality(normalized):
            normalized = raw_candidate

        normalized = re.sub(r"\b([A-Z])\s+(?:81|8I|BI)\s+([A-Z])\b", r"\1 & \2", normalized)
        normalized = re.sub(r"S\.A,C,S[.,]?", "S.A.C.S.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"S\.A,C[.,]?", "S.A.C.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bS\.?A\.?C\.?S\.?\b", "S.A.C.S.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bS\.?A\.?C\.?\b", "S.A.C.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bS\.?A\.?\b(?!\.C)", "S.A.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bPENITENCIARIA\s+INPE\b\s*-?", "PENITENCIARIA - INPE", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\.{2,}", ".", normalized)
        return normalize_spaces(normalized.strip(" -"))

    def _extract_client_from_raw_line(self, raw_line: str) -> str:
        match = re.search(r"RUC\s*[-=]?\s*\d{8,14}\s+(?P<client>.+?)\s+\|", normalize_spaces(raw_line), flags=re.IGNORECASE)
        return normalize_spaces(match.group("client")) if match else ""

    def _client_quality(self, value: str) -> int:
        normalized = normalize_spaces(value)
        score = len(normalized)
        if not re.match(r"^(S\.?A\.?C\.?S?\.?|S\.?A\.?)\b", normalized, flags=re.IGNORECASE):
            score += 20
        if re.search(r"(S\.?A\.?C\.?S?\.?|S\.?A\.)$", normalized, flags=re.IGNORECASE):
            score += 10
        if " - INPE" in normalize_for_match(normalized):
            score += 5
        return score
