from __future__ import annotations

import re
from decimal import Decimal

import pypdfium2 as pdfium
import pytesseract
from pytesseract import Output

from ..models import ParseContext, ParsedDocument
from ..ocr import ensure_tesseract, preprocess_image
from ..utils import build_validation, clean_lines, normalize_code_like_field, normalize_spaces, replace_ocr_o_with_zero_in_numeric_segments, to_decimal_flexible
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
        if any(self._policy_needs_recovery(str(row.get("poliza", ""))) for row in detail_rows):
            recovered_policies = self._recover_policies_from_layout(context.file_path)
            recovered_count = self._merge_recovered_policies(detail_rows, recovered_policies)
            if recovered_count:
                warnings.append(f"Se recuperaron {recovered_count} polizas CESCE con OCR por coordenadas.")
        reported_totals = self._extract_totals(lines)
        validations = self._build_validations(detail_rows, reported_totals)
        document_match = re.search(r"LIQUIDACIONES\s+DE\s+COMISIONES\s+NRO:\s*([0-9O]+)", text, flags=re.IGNORECASE)

        return ParsedDocument(
            source_file=context.file_path.name,
            source_stem=context.file_path.stem,
            detected_insurer=self.insurer,
            detected_profile=self.display_name,
            document_number=normalize_code_like_field(document_match.group(1), allowed="A-Z0-9") if document_match else context.file_path.stem,
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
            normalized = replace_ocr_o_with_zero_in_numeric_segments(normalized)
            normalized = re.sub(r"\b(?:e|ile)\b\s+(NCREDITO|FACTURA)\b", r"\1", normalized, flags=re.IGNORECASE)
            match = DETAIL_RE.match(normalized)
            if not match:
                continue
            payload = match.groupdict()
            cliente, poliza = self._split_prefix(payload["prefix"])
            rows.append(
                {
                    "cliente": cliente,
                    "poliza": normalize_code_like_field(poliza, allowed="A-Z0-9-"),
                    "tipo_doc": payload["tipo_doc"],
                    "nro_doc": normalize_code_like_field(payload["nro_doc"], allowed="A-Z0-9-"),
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

    def _recover_policies_from_layout(self, file_path) -> list[str]:
        ensure_tesseract()
        policy_candidates: list[list[str]] = []

        for psm in (11, 12):
            recovered: list[str] = []
            pdf = pdfium.PdfDocument(str(file_path))
            try:
                for page_index in range(len(pdf)):
                    page = pdf.get_page(page_index)
                    bitmap = page.render(scale=4.0)
                    image = bitmap.to_pil().copy()
                    processed = preprocess_image(image)
                    try:
                        data = pytesseract.image_to_data(
                            processed,
                            lang="spa",
                            config=f"--psm {psm}",
                            output_type=Output.DICT,
                        )
                        recovered.extend(self._recover_policies_from_page(processed, data))
                    finally:
                        processed.close()
                        image.close()
                        bitmap.close()
                        page.close()
            finally:
                pdf.close()
            policy_candidates.append(recovered)

        return max(policy_candidates, key=lambda rows: (sum(1 for value in rows if self._looks_like_policy(value)), len(rows)), default=[])

    def _recover_policies_from_page(self, processed_image, data: dict[str, list]) -> list[str]:
        tokens: list[dict[str, int | str]] = []
        for index, raw_text in enumerate(data["text"]):
            text = str(raw_text).strip()
            conf = str(data["conf"][index]).strip()
            if not text or conf in {"", "-1"}:
                continue
            tokens.append(
                {
                    "text": text,
                    "left": int(data["left"][index]),
                    "top": int(data["top"][index]),
                }
            )

        if not tokens:
            return []

        policy_header = next((token for token in tokens if str(token["text"]).upper() == "POLIZA"), None)
        tipo_doc_header = next(
            (
                token
                for token in tokens
                if str(token["text"]).upper().replace(".", "") == "TIPODOC"
            ),
            None,
        )
        if not policy_header or not tipo_doc_header:
            return []

        left_bound = max(int(policy_header["left"]) - max(120, int(tipo_doc_header["left"]) - int(policy_header["left"])), 0)
        right_bound = int(tipo_doc_header["left"]) - 25
        type_doc_tokens = sorted(
            (
                token
                for token in tokens
                if str(token["text"]).upper() in {"FACTURA", "NCREDITO"} and int(token["left"]) >= left_bound
            ),
            key=lambda item: (int(item["top"]), int(item["left"])),
        )

        recovered: list[str] = []
        for index, type_doc in enumerate(type_doc_tokens):
            row_top = int(type_doc["top"])
            next_row_top = int(type_doc_tokens[index + 1]["top"]) if index + 1 < len(type_doc_tokens) else row_top + 72
            crop_top = max(row_top - 24, 0)
            crop_bottom = min(next_row_top - 6, processed_image.height)
            if crop_bottom <= crop_top:
                crop_bottom = min(row_top + 72, processed_image.height)
            crop_box = (left_bound, crop_top, right_bound, crop_bottom)
            crop = processed_image.crop(crop_box)
            try:
                raw_candidate = pytesseract.image_to_string(crop, lang="spa", config="--psm 6")
            finally:
                crop.close()
            candidate = self._normalize_policy_crop_text(raw_candidate)
            if self._looks_like_policy(candidate):
                recovered.append(candidate)
        return recovered

    def _normalize_policy_crop_text(self, raw_value: str) -> str:
        tokens = [normalize_code_like_field(token, allowed="A-Z0-9-") for token in re.split(r"\s+", raw_value) if token.strip()]
        merged = ""
        for token in tokens:
            if not token:
                continue
            if not merged:
                merged = token
                continue
            if token.startswith("-") or merged.endswith("-"):
                merged = f"{merged}{token}"
                continue
            if sum(character.isdigit() for character in token) >= 2:
                separator = "-" if not token.startswith("-") else ""
                merged = f"{merged}{separator}{token}"
        return merged.strip("-")

    def _merge_recovered_policies(self, detail_rows: list[dict], recovered_policies: list[str]) -> int:
        if not detail_rows or not recovered_policies:
            return 0

        applied = 0
        if len(recovered_policies) == len(detail_rows):
            for index, row in enumerate(detail_rows):
                candidate = recovered_policies[index]
                if self._policy_quality(candidate) > self._policy_quality(str(row.get("poliza", ""))):
                    row["poliza"] = candidate
                    applied += 1
            return applied

        recovered_iter = iter(recovered_policies)
        for row in detail_rows:
            if not self._policy_needs_recovery(str(row.get("poliza", ""))):
                continue
            for candidate in recovered_iter:
                if self._looks_like_policy(candidate):
                    row["poliza"] = candidate
                    applied += 1
                    break
        return applied

    def _policy_needs_recovery(self, value: str) -> bool:
        return self._policy_quality(value) < 20

    def _policy_quality(self, value: str) -> int:
        normalized = normalize_code_like_field(value, allowed="A-Z0-9-")
        digits = sum(character.isdigit() for character in normalized)
        score = digits + normalized.count("-") * 3
        if self._looks_like_policy(normalized):
            score += 50
        return score

    def _looks_like_policy(self, value: str) -> bool:
        normalized = normalize_code_like_field(value, allowed="A-Z0-9-")
        digits = sum(character.isdigit() for character in normalized)
        return digits >= 8 and normalized.count("-") >= 2

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
