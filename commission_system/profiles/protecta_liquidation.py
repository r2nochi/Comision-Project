from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

import pypdfium2 as pdfium
import pytesseract
from pytesseract import Output

from ..models import ParseContext, ParsedDocument
from ..ocr import ensure_tesseract, preprocess_image
from ..utils import clean_lines, normalize_code_like_field, normalize_for_match, normalize_spaces, to_decimal_flexible
from .generic_liquidation import GenericLiquidationProfile
from .rotatable_liquidation_layout import (
    _collect_tokens,
    _group_lines_into_rows,
    _group_tokens_into_lines,
    _score_candidate,
    expected_total_from_reported,
    extract_best_rotatable_layout_rows,
)


DATE_LINE_RE = re.compile(
    r"^(?P<fecha_inicio>\d{2}/\d{2}/\d{4})\s+(?P<prefix>.+?)\s+(?P<monto_documento>-?[\d,.]+)\s+"
    r"\(?(?P<pct>[\d.,]+)(?:\s*[2Z])?\s*%?\)?\s+(?:RUC[^0-9]{0,6})(?P<identificacion>\d{8,14})\s+"
    r"(?P<cliente>.+)$",
    flags=re.IGNORECASE,
)

ALT_DATE_LINE_RE = re.compile(
    r"^(?P<fecha_inicio>\d{2}/\d{2}/\d{4})\s+(?P<descripcion>.+?)\s+"
    r"(?P<document_number>[A-Z0-9=/\->]+(?:\s+[A-Z0-9=/\->]+)?)\s+"
    r"(?P<monto_documento>-?[\d,.]+)\s+(?P<ignored>-?[\d,\-]+)\s+(?:RUC[^0-9]{0,6})(?P<identificacion>\d{8,14})\s+"
    r"(?P<cliente>.+?)\s+PROTECTA\s+S\.A[.,]?\s+(?P<document_legal>[A-Z0-9]+)\s*[:;]?\s*"
    r"\((?P<pct>[\d.,]+)\s*%\).*$",
    flags=re.IGNORECASE,
)

DESCRIPTOR_RE = re.compile(
    r"^(?P<descripcion>.+?)\s+(?P<monto_comision>-?[\d,.]+)(?:\s+(?P<cliente_extra>.+))?$",
    flags=re.IGNORECASE,
)
BAND_PREFIX_RE = re.compile(
    r"^(?P<prefix>(?:CC-AC-SCTR|AC-SCTR)-?)\s+(?P<legal_prefix>F0*07-?)\s+(?P<monto_comision>-?[\d,.]+)$",
    flags=re.IGNORECASE,
)
BAND_MAIN_RE = re.compile(
    r"^(?P<fecha_inicio>\d{2}/\d{2}/\d{4})\s+(?P<descripcion>.+?)\s+(?P<document_number>[A-Z0-9=/.-]+)\s+"
    r"(?P<document_legal>[A-Z0-9.-]+)\s+(?P<monto_documento>-?[\d,.]+)\s+\((?P<pct>[\d.,]+).+?"
    r"RUC\s*-\s*(?P<identificacion>\d{8,14})\s+(?P<cliente>.+)$",
    flags=re.IGNORECASE,
)
CORPORATE_SUFFIX_RE = re.compile(r"\b(S\.?A\.?C\.?S?\.?|S\.?A\.?|S\.?R\.?L\.?|E\.?I\.?R\.?L\.?)\b", flags=re.IGNORECASE)
ROW_RENDER_SCALE = 3.0
ROW_CROP_LEFT = 180
ROW_CROP_RIGHT = 2200
ROW_CROP_TOP_PADDING = 12
ROW_CROP_BOTTOM_PADDING = 42


class ProtectaLiquidationProfile(GenericLiquidationProfile):
    def __init__(self) -> None:
        super().__init__(
            profile_id="protecta_liquidation",
            insurer="PROTECTA",
            display_name="Protecta Liquidacion",
            keywords=("PROTECTA", "LIQUIDACION NUMERO", "TOTAL A COBRAR"),
        )

    def parse(self, text: str, context: ParseContext) -> ParsedDocument:
        lines = clean_lines(text)
        reported_totals = self._extract_totals(lines)
        expected_total = expected_total_from_reported(reported_totals)
        text_rows, text_warnings = self._extract_detail_rows(lines)
        layout_candidate = extract_best_rotatable_layout_rows(
            insurer=self.insurer,
            file_path=context.file_path,
            expected_total=expected_total,
        )
        band_rows, band_warnings = self._extract_band_overlay_rows(context.file_path, layout_candidate.rotation)
        merged_rows = self._merge_rows(
            text_rows=text_rows,
            layout_rows=layout_candidate.rows,
            band_rows=band_rows,
        )
        candidates = [
            (
                "text",
                [self._post_process_row(dict(row)) for row in text_rows],
                list(text_warnings),
                _score_candidate(text_rows, text_warnings, expected_total),
            ),
            (
                "layout",
                [self._post_process_row(dict(row)) for row in layout_candidate.rows],
                [
                    *layout_candidate.warnings,
                    f"Se uso OCR estructurado del layout {self.insurer} con rotacion {layout_candidate.rotation} para reconstruir mejor el detalle.",
                ]
                if layout_candidate.rows
                else list(layout_candidate.warnings),
                _score_candidate(layout_candidate.rows, layout_candidate.warnings, expected_total),
            ),
            (
                "merged",
                merged_rows,
                [
                    *layout_candidate.warnings,
                    *band_warnings,
                    f"Se combino OCR estructurado, OCR por banda y OCR lineal de {self.insurer} para reconstruir mejor las filas con saltos de linea.",
                ],
                _score_candidate(merged_rows, [*layout_candidate.warnings, *band_warnings], expected_total) + (1 if merged_rows else 0),
            ),
        ]
        _, detail_rows, warnings, _ = max(candidates, key=lambda item: item[3])
        validations = self._build_validations(detail_rows, reported_totals)
        output_detail_rows = [self._to_output_row(row) for row in detail_rows]
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
            detail_rows=output_detail_rows,
            reported_totals=reported_totals,
            validations=validations,
            warnings=warnings,
        )

    def _merge_rows(self, *, text_rows: list[dict], layout_rows: list[dict], band_rows: list[dict]) -> list[dict]:
        base_rows = layout_rows or band_rows or text_rows
        if not base_rows:
            return []

        text_by_key: dict[str, list[dict]] = {}
        for row in text_rows:
            text_by_key.setdefault(self._merge_key(row), []).append(row)

        band_by_key: dict[str, list[dict]] = {}
        for row in band_rows:
            band_by_key.setdefault(self._merge_key(row), []).append(row)

        merged: list[dict] = []
        seen: set[str] = set()

        for row in base_rows:
            key = self._merge_key(row)
            merged_row = dict(row)
            band_overlay = self._best_overlay(band_by_key.get(key, []))
            if band_overlay:
                merged_row = self._merge_row_fields(merged_row, band_overlay)
            text_overlay = self._best_overlay(text_by_key.get(key, []))
            if text_overlay:
                merged_row = self._merge_row_fields(merged_row, text_overlay)
            merged.append(self._post_process_row(merged_row))
            seen.add(key)

        for row in [*band_rows, *text_rows]:
            key = self._merge_key(row)
            if key in seen:
                continue
            merged.append(self._post_process_row(dict(row)))
            seen.add(key)

        return merged

    def _merge_key(self, row: dict) -> str:
        fecha = normalize_spaces(str(row.get("fecha_inicio", "")))
        identificacion = "".join(character for character in str(row.get("identificacion", "")) if character.isdigit())
        monto_documento = row.get("monto_documento")
        monto_token = normalize_spaces(str(monto_documento))
        return "|".join((fecha, identificacion, monto_token))

    def _best_overlay(self, rows: list[dict]) -> dict | None:
        if not rows:
            return None
        return max(rows, key=self._overlay_quality)

    def _overlay_quality(self, row: dict) -> int:
        score = 0
        score += self._description_quality(str(row.get("descripcion", "")))
        score += self._document_number_quality(str(row.get("document_number", ""))) * 2
        score += self._document_legal_quality(str(row.get("document_legal", ""))) * 2
        score += self._client_quality(str(row.get("cliente", "")))
        if str(row.get("identificacion", "")).upper().startswith("RUC - "):
            score += 25
        score += len("".join(character for character in str(row.get("identificacion", "")) if character.isdigit()))
        if row.get("monto_comision") not in {None, ""}:
            score += 10
        return score

    def _merge_row_fields(self, base_row: dict, overlay_row: dict) -> dict:
        merged = dict(base_row)
        for field in ("descripcion", "document_number", "document_legal", "identificacion", "cliente", "raw_line"):
            merged[field] = self._choose_better_field(field, merged.get(field), overlay_row.get(field))

        for field in ("monto_documento", "monto_comision", "pct_comision"):
            if merged.get(field) in {None, ""} and overlay_row.get(field) not in {None, ""}:
                merged[field] = overlay_row.get(field)
        return merged

    def _choose_better_field(self, field: str, base_value, overlay_value):
        if overlay_value in {None, ""}:
            return base_value
        if base_value in {None, ""}:
            return overlay_value

        base = str(base_value)
        overlay = str(overlay_value)

        if field == "descripcion":
            return overlay_value if self._description_quality(overlay) >= self._description_quality(base) else base_value
        if field == "document_number":
            return (
                overlay_value
                if self._document_number_quality(overlay) >= self._document_number_quality(base)
                else base_value
            )
        if field == "document_legal":
            return (
                overlay_value
                if self._document_legal_quality(overlay) >= self._document_legal_quality(base)
                else base_value
            )
        if field == "identificacion":
            base_digits = len("".join(character for character in base if character.isdigit()))
            overlay_digits = len("".join(character for character in overlay if character.isdigit()))
            if overlay.upper().startswith("RUC - ") and not base.upper().startswith("RUC - "):
                return overlay_value
            return overlay_value if overlay_digits >= base_digits else base_value
        if field == "cliente":
            return overlay_value if self._client_quality(overlay) >= self._client_quality(base) else base_value
        if field == "raw_line":
            return overlay_value if len(overlay) > len(base) else base_value
        return overlay_value if len(overlay) > len(base) else base_value

    def _description_quality(self, value: str) -> int:
        normalized = normalize_spaces(value)
        upper = normalize_for_match(normalized)
        score = len(normalized)
        if upper == "CUOTA - PROTECTA S.A.":
            score += 80
        if upper == "AVISO DE COBRANZA - PROTECTA S.A.":
            score += 90
        elif upper == "AVISO DE COBRANZA":
            score += 80
        if "FO07" in upper or "F007" in upper or "CC-AC-SCTR" in upper:
            score -= 40
        if upper.endswith(("LIN", "ON", "OO", "DOS", "GO", "CAJA")):
            score -= 25
        if normalized.endswith(","):
            score -= 5
        return score

    def _document_number_quality(self, value: str) -> int:
        normalized = normalize_spaces(value)
        score = len(normalized)
        if normalized.startswith("CC-AC-SCTR-"):
            score += 40
        if normalized.startswith("AC-SCTR-"):
            score += 35
        digits = sum(character.isdigit() for character in normalized)
        score += digits * 3
        if "/" in normalized:
            score += 10
        return score

    def _document_legal_quality(self, value: str) -> int:
        normalized = normalize_spaces(value)
        score = len(normalized)
        if normalized.startswith("F007-"):
            score += 40
        digits = sum(character.isdigit() for character in normalized)
        score += digits * 3
        return score

    def _client_quality(self, value: str) -> int:
        normalized = normalize_spaces(value)
        upper = normalize_for_match(normalized)
        score = len(normalized)
        if not self._starts_with_corporate_suffix(normalized):
            score += 10
        if any(token in upper for token in ("S.A.C", "S.A.C.S", "S.A.", "S.R.L.", "E.I.R.L.")):
            score += 15
        if "PROTECTA S.A" in upper:
            score -= 40
        if upper.startswith("EESPONSABILIDAD"):
            score -= 25
        return score

    def _extract_band_overlay_rows(self, file_path, rotation: int) -> tuple[list[dict], list[str]]:
        rows: list[dict] = []
        warnings: list[str] = []
        ensure_tesseract()
        pdf = pdfium.PdfDocument(str(file_path))

        try:
            for page_index in range(len(pdf)):
                page = pdf.get_page(page_index)
                bitmap = page.render(scale=ROW_RENDER_SCALE)
                image = bitmap.to_pil().copy()
                if rotation:
                    rotated = image.rotate(rotation, expand=True)
                    image.close()
                    image = rotated

                processed_page = preprocess_image(image)
                try:
                    data = pytesseract.image_to_data(
                        processed_page,
                        lang="spa",
                        config="--psm 11",
                        output_type=Output.DICT,
                    )
                finally:
                    processed_page.close()

                tokens = _collect_tokens(data)
                clusters = _group_tokens_into_lines(tokens)
                row_clusters = _group_lines_into_rows(clusters)

                for record in row_clusters:
                    overlay = self._parse_band_overlay(image, record)
                    if overlay:
                        rows.append(overlay)

                image.close()
                bitmap.close()
                page.close()
        except Exception as exc:  # pragma: no cover - OCR fallback path
            warnings.append(f"No se pudo reconstruir bandas OCR de {self.insurer}: {exc}")
            return [], warnings
        finally:
            pdf.close()

        return rows, warnings

    def _parse_band_overlay(self, image, record: list[list[tuple[int, int, str]]]) -> dict | None:
        tops = [top for cluster in record for top, _, _ in cluster]
        if not tops:
            return None

        top = max(min(tops) - ROW_CROP_TOP_PADDING, 0)
        bottom = min(max(tops) + ROW_CROP_BOTTOM_PADDING, image.height)
        crop = image.crop((ROW_CROP_LEFT, top, min(ROW_CROP_RIGHT, image.width), bottom))
        processed_crop = preprocess_image(crop)
        try:
            text = pytesseract.image_to_string(processed_crop, lang="spa", config="--psm 6")
        finally:
            processed_crop.close()
            crop.close()

        lines = [normalize_spaces(line) for line in text.splitlines() if normalize_spaces(line)]
        if not lines:
            return None

        prefix = ""
        legal_prefix = ""
        monto_comision = None
        main_line = ""
        for line in lines:
            prefix_match = BAND_PREFIX_RE.match(line)
            if prefix_match:
                prefix = normalize_spaces(prefix_match.group("prefix"))
                legal_prefix = normalize_spaces(prefix_match.group("legal_prefix"))
                monto_comision = to_decimal_flexible(prefix_match.group("monto_comision"))
                continue
            if re.match(r"^\d{2}/\d{2}/\d{4}\b", line):
                main_line = line
                break

        if not main_line:
            return None

        main_match = BAND_MAIN_RE.match(main_line)
        if not main_match:
            return None

        payload = main_match.groupdict()
        return {
            "fecha_inicio": payload["fecha_inicio"],
            "descripcion": payload["descripcion"],
            "document_number": f"{prefix} {payload['document_number']}".strip() if prefix else payload["document_number"],
            "document_legal": f"{legal_prefix} {payload['document_legal']}".strip() if legal_prefix else payload["document_legal"],
            "monto_documento": to_decimal_flexible(payload["monto_documento"]),
            "monto_comision": monto_comision or self._calculate_commission(
                to_decimal_flexible(payload["monto_documento"]),
                to_decimal_flexible(payload["pct"]),
            ),
            "pct_comision": to_decimal_flexible(payload["pct"]),
            "identificacion": f"RUC - {payload['identificacion']}",
            "cliente": payload["cliente"],
            "raw_line": normalize_spaces(" | ".join(lines)),
        }

    def _post_process_row(self, row: dict) -> dict:
        processed = dict(row)
        raw_line = str(processed.get("raw_line", ""))
        processed["descripcion"] = self._normalize_descripcion(processed.get("descripcion"), raw_line)
        processed["document_number"] = self._normalize_document_number(
            processed.get("document_number"),
            raw_line=raw_line,
            descripcion=str(processed.get("descripcion", "")),
        )
        processed["document_legal"] = self._normalize_document_legal(
            processed.get("document_legal"),
            raw_line=raw_line,
        )
        processed["pct_comision"] = self._normalize_pct_comision(processed.get("pct_comision"), raw_line=raw_line)
        processed["identificacion"] = self._normalize_identificacion(processed.get("identificacion"))
        processed["cliente"] = self._normalize_cliente(processed.get("cliente"), raw_line=raw_line)
        return processed

    def _to_output_row(self, row: dict) -> dict:
        output = dict(row)
        output["document_tipo"] = output.pop("descripcion", "")
        return output

    def _normalize_identificacion(self, value) -> str:
        digits = "".join(character for character in str(value or "") if character.isdigit())
        if not digits:
            return str(value or "")
        return f"RUC - {digits}"

    def _normalize_pct_comision(self, value, *, raw_line: str):
        current = value if isinstance(value, Decimal) else to_decimal_flexible(str(value or "0"))
        percent_match = re.search(r"\((\d{1,2})[.,](\d{2})\s*%\)", raw_line)
        if percent_match:
            whole = percent_match.group(1)
            decimals = percent_match.group(2)
            return Decimal(f"{whole}.{decimals}")
        return current

    def _normalize_descripcion(self, value, raw_line: str) -> str:
        normalized = normalize_spaces(str(value or ""))
        upper = normalize_for_match(f"{normalized} {raw_line}")
        if "AVISO DE COBRANZA" in upper:
            return "Aviso de cobranza - Protecta S.A."
        if "CUOTA" in upper and "PROTECTA" in upper:
            return "Cuota - Protecta S.A."
        normalized = re.sub(r"^[—–+_=:-]+\s*", "", normalized)
        normalized = normalized.replace("Protecta S.A,", "Protecta S.A.").replace("Protecta S.A ,", "Protecta S.A.")
        normalized = re.sub(r"\bFOO?07-?\b", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bCC-AC-SCTR-?\b", "", normalized, flags=re.IGNORECASE)
        normalized = normalize_spaces(normalized.strip(" -"))
        return normalized

    def _normalize_document_number(self, value, *, raw_line: str, descripcion: str) -> str:
        normalized_value = normalize_spaces(str(value or ""))
        upper_source = normalize_for_match(f"{descripcion} {raw_line}")
        expected_prefix = "AC-SCTR-" if "AVISO DE COBRANZA" in upper_source else "CC-AC-SCTR-"

        candidates: list[str] = [normalized_value]
        candidates.extend(
            match.group(1)
            for match in re.finditer(
                r"(?:CC-AC-SCTR|AC-SCTR|SCRR)\s*[-=]?\s*([A-Z0-9=/.-]{5,})",
                f"{normalized_value} {raw_line}",
                flags=re.IGNORECASE,
            )
        )

        candidate = ""
        best_score = -1
        for raw_candidate in candidates:
            probe = normalize_code_like_field(raw_candidate, allowed="A-Z0-9/.-")
            probe = re.sub(r"^(CC-AC-SCTR-|AC-SCTR-)", "", probe)
            probe = re.sub(r"^SCRR-?", "", probe)
            probe = probe.replace("F007", "").replace("FO07", "").strip("-")
            score = sum(character.isdigit() for character in probe) * 3 + (10 if "/" in probe else 0) + len(probe)
            if score > best_score:
                candidate = probe
                best_score = score

        if candidate and "/" not in candidate and any(character.isdigit() for character in candidate):
            if expected_prefix == "CC-AC-SCTR-":
                candidate = f"{candidate}/1"

        if not candidate:
            return expected_prefix.rstrip("-")
        return f"{expected_prefix}{candidate}".replace("--", "-")

    def _normalize_document_legal(self, value, *, raw_line: str) -> str:
        source = f"{value or ''} {raw_line}"
        candidates = [str(value or "")]
        candidates.extend(match.group(1) for match in re.finditer(r"F0*07-?\s*([A-Z0-9]{3,12})", source, flags=re.IGNORECASE))
        candidates.extend(match.group(1) for match in re.finditer(r"\b([0-9OIL]{6,10})\b", source, flags=re.IGNORECASE))
        candidate = max(candidates, key=self._legal_candidate_quality)
        digits = "".join(character for character in normalize_code_like_field(candidate, allowed="A-Z0-9") if character.isdigit())
        digits = self._normalize_legal_digits(digits)
        return f"F007-{digits}" if digits else "F007"

    def _legal_candidate_quality(self, value: str) -> int:
        digits = "".join(character for character in normalize_code_like_field(value, allowed="A-Z0-9") if character.isdigit())
        score = len(digits) * 3
        if digits.startswith("0008"):
            score += 80
        if digits.startswith(("0009", "009", "090", "0000")):
            score += 40
        return score

    def _normalize_legal_digits(self, digits: str) -> str:
        if not digits:
            return ""
        if "0008" in digits:
            start = digits.find("0008")
            return digits[start : start + 9]
        if len(digits) >= 9 and digits.startswith("0000"):
            return f"0008{digits[-5:]}"
        if len(digits) >= 9 and digits.startswith(("0009", "009", "090")):
            return f"0008{digits[-5:]}"
        if len(digits) > 9:
            return digits[-9:]
        if len(digits) < 9:
            return f"0008{digits[-5:]}" if len(digits) >= 5 else digits.zfill(9)
        return digits

    def _normalize_cliente(self, value, *, raw_line: str) -> str:
        normalized = normalize_spaces(str(value or ""))
        normalized = re.sub(r"^[—–-]+\s*", "", normalized)
        normalized = re.sub(r"\bS[,.]?A[,.]?C[,.]?S[,.]?\b", "S.A.C.S.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bS[,.]?A[,.]?C[,.]?\b", "S.A.C.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bS[,.]?A[,.]?\b", "S.A.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bS[,.]?R[,.]?L[,.]?\b", "S.R.L.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bE[,.]?I[,.]?R[,.]?L[,.]?\b", "E.I.R.L.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bSAC\b", "S.A.C.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bSA\b", "S.A.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bSRL\b", "S.R.L.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bEIRL\b", "E.I.R.L.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s+\.$", ".", normalized)

        wilcom_match = re.match(
            r"^(?P<suffix>.+?S\.A\.C\.?)\s+(?P<body>WILCOM ENERGY SOCIEDAD ANONIMA CERRADA - WILCOM)$",
            normalized,
            flags=re.IGNORECASE,
        )
        if wilcom_match:
            normalized = f"{wilcom_match.group('body')} {wilcom_match.group('suffix')}"

        if "ESPONSABILIDAD LIMITADA" in normalize_for_match(normalized) and "SOCIEDAD COMERCIAL DE" in normalize_for_match(normalized):
            normalized = re.sub(
                r"^[A-Z]?ESPONSABILIDAD LIMITADA\s+(.+?)\s+SOCIEDAD COMERCIAL DE$",
                r"\1 SOCIEDAD COMERCIAL DE RESPONSABILIDAD LIMITADA",
                normalized,
                flags=re.IGNORECASE,
            )

        trailing_word_match = re.match(r"^(?P<body>.+?\b(?:S\.A\.C\.|S\.A\.C\.S\.|S\.A\.|S\.R\.L\.|E\.I\.R\.L\.))\s+(?P<head>[A-Z0-9&.-]+)$", normalized)
        if trailing_word_match and trailing_word_match.group("head").upper() not in {"S.A.C.", "S.A.", "S.R.L.", "E.I.R.L."}:
            normalized = f"{trailing_word_match.group('head')} {trailing_word_match.group('body')}"

        if self._starts_with_corporate_suffix(normalized):
            parts = normalized.split(maxsplit=1)
            if len(parts) == 2:
                normalized = f"{parts[1]} {parts[0]}"

        if "WILCOM ENERGY SOCIEDAD ANONIMA CERRADA - WILCOM" in normalize_for_match(normalized):
            normalized = re.sub(
                r"^(WILCOM ENERGY SOCIEDAD ANONIMA CERRADA - WILCOM)(?:\s+ENERGY\s+S\.A\.C\.|\s+ENERGYS?\.A\.C\.?)*$",
                r"\1 ENERGY S.A.C.",
                normalized,
                flags=re.IGNORECASE,
            )

        if "AYSATEL" in normalize_for_match(raw_line):
            normalized = "AYSATEL E.I.R.L."

        normalized = re.sub(r"\.{2,}", ".", normalized)
        normalized = normalized.rstrip(",")
        normalized = re.sub(
            r"(?i)\b((?:.+?)\b(?:S\.A\.C\.S\.|S\.A\.C\.|S\.A\.|S\.R\.L\.|E\.I\.R\.L\.))\s+[a-z]$",
            r"\1",
            normalized,
        )
        return normalize_spaces(normalized)

    def _starts_with_corporate_suffix(self, value: str) -> bool:
        return bool(re.match(r"^(S\.?A\.?C\.?S?\.?|S\.?A\.?|S\.?R\.?L\.?|E\.?I\.?R\.?L\.?)\b", normalize_spaces(value), flags=re.IGNORECASE))

    def _extract_detail_rows(self, lines: list[str]) -> tuple[list[dict], list[str]]:
        rows: list[dict] = []
        warnings: list[str] = []
        pending: list[str] = []
        active: list[str] = []

        for line in lines:
            if self._skip_line(line):
                continue
            if self._is_total_line(line):
                rows, warnings = self._flush_active(rows, warnings, active)
                active = []
                pending = []
                continue
            if self._looks_like_row_start(line):
                rows, warnings = self._flush_active(rows, warnings, active)
                active = [*pending, line]
                pending = []
                continue
            if active:
                parsed = self._parse_row(active)
                if parsed:
                    rows.append(parsed)
                    active = []
                    pending = [line] if self._looks_like_descriptor(line) else []
                else:
                    active.append(line)
            else:
                if self._looks_like_descriptor(line):
                    pending.append(line)
                pending = pending[-2:]

        rows, warnings = self._flush_active(rows, warnings, active)
        return rows, warnings

    def _looks_like_row_start(self, line: str) -> bool:
        return bool(re.match(r"^\d{2}/\d{2}/\d{4}\b", line))

    def _looks_like_descriptor(self, line: str) -> bool:
        upper = normalize_spaces(line).upper()
        return any(token in upper for token in ("AVISO DE COBRANZA", "CC-AC-SCTR", "FO07", "F007"))

    def _flush_active(self, rows: list[dict], warnings: list[str], active: list[str]) -> tuple[list[dict], list[str]]:
        if not active:
            return rows, warnings
        parsed = self._parse_row(active)
        if parsed:
            rows.append(parsed)
        else:
            warnings.append(f"Fila {self.insurer} no parseada: {' '.join(active)}")
        return rows, warnings

    def _parse_row(self, parts: list[str]) -> dict | None:
        raw_candidate = normalize_spaces(" ".join(parts))
        if not raw_candidate:
            return None

        date_match = re.search(r"\d{2}/\d{2}/\d{4}", raw_candidate)
        if not date_match:
            return None

        descriptor_text = normalize_spaces(raw_candidate[: date_match.start()])
        main_text = normalize_spaces(raw_candidate[date_match.start() :])
        main_text = (
            main_text.replace("—", " ")
            .replace("–", " ")
            .replace("?", " ")
            .replace("  ", " ")
        )

        match = DATE_LINE_RE.match(main_text)
        if not match:
            alt_match = ALT_DATE_LINE_RE.match(main_text)
            if not alt_match:
                return None
            return self._build_alt_row(raw_candidate, descriptor_text, alt_match.groupdict())

        payload = match.groupdict()
        monto_documento = to_decimal_flexible(payload["monto_documento"])
        pct_comision = to_decimal_flexible(payload["pct"])
        descripcion, document_number, document_legal = self._split_prefix(payload["prefix"])
        descriptor_description, _, cliente_extra = self._parse_descriptor(descriptor_text)
        monto_comision = self._calculate_commission(monto_documento, pct_comision)

        cliente = payload["cliente"].strip(" -")
        if cliente_extra:
            cliente = f"{cliente} {cliente_extra}".strip()

        if descriptor_description:
            descripcion = descriptor_description

        return {
            "fecha_inicio": payload["fecha_inicio"],
            "descripcion": descripcion,
            "document_number": document_number,
            "document_legal": document_legal,
            "monto_documento": monto_documento,
            "monto_comision": monto_comision,
            "pct_comision": pct_comision,
            "identificacion": payload["identificacion"],
            "cliente": cliente,
            "raw_line": raw_candidate,
        }

    def _build_alt_row(self, raw_candidate: str, descriptor_text: str, payload: dict[str, str]) -> dict:
        monto_documento = to_decimal_flexible(payload["monto_documento"])
        pct_comision = to_decimal_flexible(payload["pct"])
        monto_comision = self._calculate_commission(monto_documento, pct_comision)
        descriptor_description, _, cliente_extra = self._parse_descriptor(descriptor_text)

        descripcion = descriptor_description or normalize_spaces(payload["descripcion"])
        cliente = payload["cliente"].strip(" -")
        if cliente_extra:
            cliente = f"{cliente} {cliente_extra}".strip()

        return {
            "fecha_inicio": payload["fecha_inicio"],
            "descripcion": descripcion,
            "document_number": self._normalize_doc_token(payload["document_number"]),
            "document_legal": self._normalize_doc_token(payload["document_legal"]),
            "monto_documento": monto_documento,
            "monto_comision": monto_comision,
            "pct_comision": pct_comision,
            "identificacion": payload["identificacion"],
            "cliente": cliente,
            "raw_line": raw_candidate,
        }

    def _skip_line(self, line: str) -> bool:
        upper = normalize_spaces(line).upper()
        if any(
            token in upper
            for token in (
                "PROTECTA SECURITY",
                "PROTECTA SCUOTIRITY",
                "PROTECTA SA. COMPA",
                "REASEGUROS",
                "DOMINGO ORUE",
                "LIMA - LIMA - SURQUILLO",
                "FECHA INICIO",
                "MONTO",
                "TIPO DE DOC.",
                "MONTO DOC.",
                "NRO DE",
                "DOCUMENTO LEGAL",
                "COMISION",
                "IDENTIFICACION",
                "PAGINA",
            )
        ):
            return True
        return super()._skip_line(line)

    def _split_prefix(self, prefix: str) -> tuple[str, str, str]:
        normalized = normalize_spaces(prefix.replace(":", " "))
        tokens = normalized.split()
        if not tokens:
            return prefix, "", ""

        document_legal = ""
        document_number = ""

        if tokens and re.search(r"\d", tokens[-1]):
            document_legal = self._normalize_doc_token(tokens.pop())

        if tokens and re.search(r"[\d/=]", tokens[-1]):
            document_number = self._normalize_doc_token(tokens.pop())

        descripcion = " ".join(tokens).strip() or prefix
        return descripcion, document_number, document_legal

    def _parse_descriptor(self, descriptor_text: str) -> tuple[str | None, Decimal | None, str | None]:
        if not descriptor_text:
            return None, None, None

        normalized = normalize_spaces(descriptor_text.replace(">", " ").replace("_", " "))
        match = DESCRIPTOR_RE.match(normalized)
        if not match:
            return normalized, None, None

        payload = match.groupdict()
        return (
            payload["descripcion"].strip(),
            to_decimal_flexible(payload["monto_comision"]),
            normalize_spaces(payload["cliente_extra"]) if payload.get("cliente_extra") else None,
        )

    def _calculate_commission(self, monto_documento: Decimal, pct_comision: Decimal) -> Decimal:
        return (monto_documento * pct_comision / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _normalize_doc_token(self, value: str) -> str:
        normalized = value.upper().replace("I", "1").replace("L", "1")
        normalized = normalize_code_like_field(normalized, allowed="A-Z0-9/=-")
        normalized = re.sub(r"[^A-Z0-9/=\-]+", "", normalized)
        return normalized.strip("-")
