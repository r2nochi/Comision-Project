from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

from ..models import ParseContext, ParsedDocument
from ..utils import clean_lines, normalize_code_like_field, normalize_for_match, normalize_spaces, to_decimal_flexible
from .generic_liquidation import GenericLiquidationProfile
from .rotatable_liquidation_layout import (
    _score_candidate,
    expected_total_from_reported,
    extract_best_rotatable_layout_rows,
)


DATE_LINE_RE = re.compile(
    r"^(?P<fecha_inicio>\d{2}/\d{2}/\d{4})\s+(?P<prefix>.+?)\s+(?P<document_number>\S+)\s+"
    r"(?P<document_legal>\S+)\s+(?P<monto_documento>-?[\d,]+\.\d{2})\s+"
    r"\((?P<pct>[\d.]+)\s*%\)\s+(?:RUC\s*[=-]\s*)?(?P<identificacion>\d{8,14})\s+(?P<cliente>.+)$",
    flags=re.IGNORECASE,
)
RELAXED_DATE_LINE_RE = re.compile(
    r"^(?P<fecha_inicio>\d{2}/\d{2}/\d{4})\s+(?P<prefix>.+?)\s+(?P<document_number>\S+)\s+"
    r"(?P<document_legal>\S+)\s+(?P<monto_documento>\S+)\s+"
    r"\((?P<pct>[\d.]+)\s*%\)\s+(?:RUC\s*[=-]\s*)?(?P<identificacion>\d{8,14})\s+(?P<cliente>.+)$",
    flags=re.IGNORECASE,
)

DESCRIPTOR_RE = re.compile(
    r"^(?P<descripcion>.+?)\s+(?P<monto_comision>-?[\d,]+\.\d{2})(?:\s+(?P<cliente_prefijo>.+))?$",
    flags=re.IGNORECASE,
)
AMOUNT_RE = re.compile(r"-?[\d,]+\.\d{2}")


class CrecerLiquidationProfile(GenericLiquidationProfile):
    def __init__(self) -> None:
        super().__init__(
            profile_id="crecer_liquidation",
            insurer="CRECER",
            display_name="Crecer Liquidacion",
            keywords=("CRECER", "LIQUIDACION NUMERO", "TOTAL A COBRAR"),
        )

    def parse(self, text: str, context: ParseContext) -> ParsedDocument:
        lines = clean_lines(text)
        reported_totals = self._extract_totals(lines)
        expected_total = expected_total_from_reported(reported_totals)
        text_rows, text_warnings = self._extract_detail_rows(lines)
        hint_rows = self._extract_detail_hints(lines)
        layout_candidate = extract_best_rotatable_layout_rows(
            insurer=self.insurer,
            file_path=context.file_path,
            expected_total=expected_total,
        )
        merged_rows = self._merge_text_and_layout_rows(
            text_rows=text_rows,
            layout_rows=layout_candidate.rows,
            hint_rows=hint_rows,
        )
        merged_score = _score_candidate(merged_rows, [*text_warnings, *layout_candidate.warnings], expected_total) + (
            1 if merged_rows else 0
        )
        candidates = [
            (
                "text",
                text_rows,
                list(text_warnings),
                _score_candidate(text_rows, text_warnings, expected_total),
            ),
            (
                "layout",
                layout_candidate.rows,
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
                    f"Se combino OCR lineal y layout estructurado {self.insurer} para completar filas del PDF rotado."
                ],
                _score_candidate(merged_rows, list(layout_candidate.warnings), expected_total) + (1 if merged_rows else 0),
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

    def _merge_text_and_layout_rows(
        self,
        *,
        text_rows: list[dict],
        layout_rows: list[dict],
        hint_rows: list[dict],
    ) -> list[dict]:
        if not text_rows:
            if not hint_rows:
                return [self._post_process_row(dict(row)) for row in layout_rows]
        if not layout_rows:
            return [self._post_process_row(dict(row)) for row in text_rows]

        overlays_by_key: dict[str, list[dict]] = {}
        for row in [*hint_rows, *text_rows]:
            key = self._merge_key(row)
            overlays_by_key.setdefault(key, []).append(row)

        merged: list[dict] = []
        seen: set[str] = set()

        for row in layout_rows:
            key = self._merge_key(row)
            overlay = self._best_overlay(overlays_by_key.get(key, []))
            merged_row = self._merge_row_fields(row, overlay) if overlay else dict(row)
            merged.append(self._post_process_row(merged_row))
            seen.add(key)

        for row in text_rows:
            key = self._merge_key(row)
            if key not in seen:
                merged.append(self._post_process_row(dict(row)))
                seen.add(key)

        return merged

    def _merge_key(self, row: dict) -> str:
        identification = str(row.get("identificacion", ""))
        identification_digits = "".join(character for character in identification if character.isdigit())
        if identification_digits:
            return identification_digits
        return normalize_code_like_field(str(row.get("document_number", ""))) or normalize_code_like_field(
            str(row.get("raw_line", ""))
        )

    def _best_overlay(self, rows: list[dict]) -> dict | None:
        if not rows:
            return None
        return max(rows, key=self._overlay_quality)

    def _overlay_quality(self, row: dict) -> int:
        score = 0
        if row.get("descripcion"):
            score += len(str(row["descripcion"]))
        if row.get("document_number"):
            score += len(str(row["document_number"])) * 2
        if row.get("document_legal"):
            score += len(str(row["document_legal"])) * 2
        if row.get("identificacion"):
            score += len(str(row["identificacion"]))
            if str(row["identificacion"]).upper().startswith("RUC - "):
                score += 10
        if row.get("cliente"):
            score += len(str(row["cliente"]))
            if not self._starts_with_corporate_suffix(str(row["cliente"])):
                score += 15
        if row.get("monto_documento") not in {None, ""}:
            score += 5
        return score

    def _merge_row_fields(self, base_row: dict, overlay_row: dict | None) -> dict:
        if not overlay_row:
            return dict(base_row)

        merged = dict(base_row)
        for field in ("descripcion", "document_number", "document_legal", "identificacion", "cliente"):
            merged[field] = self._choose_better_field(field, merged.get(field), overlay_row.get(field))

        for field in ("monto_documento", "monto_comision", "pct_comision"):
            if merged.get(field) in {None, ""} and overlay_row.get(field) not in {None, ""}:
                merged[field] = overlay_row.get(field)

        if overlay_row.get("raw_line"):
            merged["raw_line"] = overlay_row["raw_line"]
        return merged

    def _choose_better_field(self, field: str, base_value, overlay_value):
        if overlay_value in {None, ""}:
            return base_value
        if base_value in {None, ""}:
            return overlay_value

        base = str(base_value)
        overlay = str(overlay_value)

        if field == "descripcion":
            if "SEGUROS S.A." in normalize_for_match(overlay) and "SEGUROS S.A." not in normalize_for_match(base):
                return overlay_value
            return overlay_value if len(overlay) > len(base) else base_value

        if field in {"document_number", "document_legal"}:
            return overlay_value if self._code_quality(overlay) >= self._code_quality(base) else base_value

        if field == "identificacion":
            if overlay.upper().startswith("RUC - ") and not base.upper().startswith("RUC - "):
                return overlay_value
            return overlay_value if len(overlay) > len(base) else base_value

        if field == "cliente":
            return overlay_value if self._client_quality(overlay) >= self._client_quality(base) else base_value

        return overlay_value if len(overlay) > len(base) else base_value

    def _code_quality(self, value: str) -> int:
        normalized = normalize_code_like_field(value)
        digits = sum(character.isdigit() for character in normalized)
        score = len(normalized) + digits * 2
        if "-" in normalized:
            score += 5
        if "/" in normalized:
            score += 3
        return score

    def _client_quality(self, value: str) -> int:
        normalized = normalize_spaces(str(value))
        score = len(normalized)
        if not self._starts_with_corporate_suffix(normalized):
            score += 20
        if any(token in normalize_for_match(normalized) for token in ("S.A.C", "S.A.C.S", "S.A.", "SUTRAN")):
            score += 10
        return score

    def _starts_with_corporate_suffix(self, value: str) -> bool:
        return bool(re.match(r"^(S\.?A\.?C\.?S?\.?|S\.?A\.?)\b", normalize_spaces(value), flags=re.IGNORECASE))

    def _extract_detail_rows(self, lines: list[str]) -> tuple[list[dict], list[str]]:
        rows: list[dict] = []
        warnings: list[str] = []
        pending_descriptor: str | None = None

        for line in lines:
            if self._skip_line(line):
                continue
            if self._is_total_line(line):
                pending_descriptor = None
                continue

            if re.match(r"^\d{2}/\d{2}/\d{4}\b", line):
                parsed = self._parse_crecer_row(line, pending_descriptor)
                if parsed:
                    rows.append(parsed)
                else:
                    message = f"Fila {self.insurer} no parseada: {line}"
                    if pending_descriptor:
                        message = f"{message} | descriptor={pending_descriptor}"
                    warnings.append(message)
                pending_descriptor = None
                continue

            pending_descriptor = line

        return rows, warnings

    def _extract_detail_hints(self, lines: list[str]) -> list[dict]:
        hints: list[dict] = []
        pending_descriptor: str | None = None

        for line in lines:
            if self._skip_line(line):
                continue
            if self._is_total_line(line):
                pending_descriptor = None
                continue

            if re.match(r"^\d{2}/\d{2}/\d{4}\b", line):
                hint = self._parse_crecer_hint(line, pending_descriptor)
                if hint:
                    hints.append(hint)
                pending_descriptor = None
                continue

            pending_descriptor = line

        return hints

    def _parse_crecer_row(self, line: str, descriptor_line: str | None) -> dict | None:
        candidate = normalize_spaces(line)
        match = DATE_LINE_RE.match(candidate)
        if not match:
            return None

        payload = match.groupdict()
        monto_documento = to_decimal_flexible(payload["monto_documento"])
        pct_comision = to_decimal_flexible(payload["pct"])
        monto_comision = (monto_documento * pct_comision / Decimal("100")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        descripcion = payload["prefix"]
        cliente = payload["cliente"]
        document_number = normalize_code_like_field(payload["document_number"])
        document_legal = normalize_code_like_field(payload["document_legal"])
        descriptor_payload = None

        if descriptor_line:
            descriptor_candidate = normalize_spaces(descriptor_line)
            descriptor_match = DESCRIPTOR_RE.match(descriptor_candidate)
            if descriptor_match:
                descriptor_payload = descriptor_match.groupdict()
                description_base, document_prefix, legal_prefix = self._split_descriptor_prefixes(
                    descriptor_payload["descripcion"]
                )
                descripcion = self._merge_descriptions(description_base, payload["prefix"])
                document_number = self._combine_code_prefix(document_prefix, payload["document_number"])
                document_legal = self._combine_code_prefix(legal_prefix, payload["document_legal"])
                if descriptor_payload.get("cliente_prefijo"):
                    cliente = f"{descriptor_payload['cliente_prefijo']} {cliente}".strip()
                monto_comision = to_decimal_flexible(descriptor_payload["monto_comision"])
            else:
                descripcion = self._merge_descriptions(descriptor_candidate, payload["prefix"])
        else:
            descripcion = self._merge_descriptions(descripcion, "")

        return {
            "fecha_inicio": payload["fecha_inicio"],
            "descripcion": descripcion,
            "document_number": document_number,
            "document_legal": document_legal,
            "monto_documento": monto_documento,
            "monto_comision": monto_comision,
            "pct_comision": pct_comision,
            "identificacion": f"RUC - {payload['identificacion']}",
            "cliente": cliente,
            "raw_line": " | ".join(filter(None, [descriptor_line, candidate])),
        }

    def _parse_crecer_hint(self, line: str, descriptor_line: str | None) -> dict | None:
        if not descriptor_line:
            return None

        candidate = normalize_spaces(line)
        match = RELAXED_DATE_LINE_RE.match(candidate)
        if not match:
            return None

        payload = match.groupdict()
        descriptor_candidate = normalize_spaces(descriptor_line)
        descriptor_match = DESCRIPTOR_RE.match(descriptor_candidate)
        if not descriptor_match:
            return None

        descriptor_payload = descriptor_match.groupdict()
        description_base, document_prefix, legal_prefix = self._split_descriptor_prefixes(
            descriptor_payload["descripcion"]
        )
        cliente = payload["cliente"]
        if descriptor_payload.get("cliente_prefijo"):
            cliente = f"{descriptor_payload['cliente_prefijo']} {cliente}".strip()

        monto_documento = self._safe_decimal(payload["monto_documento"])
        pct_comision = self._safe_decimal(payload["pct"])
        monto_comision = self._safe_decimal(descriptor_payload["monto_comision"])

        return {
            "fecha_inicio": payload["fecha_inicio"],
            "descripcion": self._merge_descriptions(description_base, payload["prefix"]),
            "document_number": self._combine_code_prefix(document_prefix, payload["document_number"]),
            "document_legal": self._combine_code_prefix(legal_prefix, payload["document_legal"]),
            "monto_documento": monto_documento,
            "monto_comision": monto_comision,
            "pct_comision": pct_comision,
            "identificacion": f"RUC - {payload['identificacion']}",
            "cliente": cliente,
            "raw_line": " | ".join(filter(None, [descriptor_line, candidate])),
        }

    def _split_descriptor_prefixes(self, value: str) -> tuple[str, str, str]:
        normalized = normalize_spaces(value)
        tokens = normalized.split()
        suffix_tokens: list[str] = []

        while tokens:
            token = tokens[-1]
            if not self._looks_like_code_prefix(token):
                break
            suffix_tokens.insert(0, token)
            tokens.pop()

        description = normalize_spaces(" ".join(tokens))
        if not suffix_tokens:
            return description, "", ""
        if len(suffix_tokens) == 1:
            return description, "", suffix_tokens[0]
        return description, " ".join(suffix_tokens[:-1]), suffix_tokens[-1]

    def _looks_like_code_prefix(self, token: str) -> bool:
        normalized = normalize_code_like_field(token)
        if not normalized:
            return False
        if not re.fullmatch(r"[A-Z0-9/-]+", normalized):
            return False
        return "-" in token or bool(re.search(r"\d", normalized))

    def _merge_descriptions(self, base_description: str, prefix_text: str) -> str:
        base = normalize_spaces(base_description)
        prefix = normalize_spaces(prefix_text)
        if not prefix:
            return base
        if "SEGUROS" in normalize_for_match(prefix) and normalize_for_match(prefix) not in normalize_for_match(base):
            return normalize_spaces(f"{base} {prefix}")
        return base or prefix

    def _normalize_prefix_token(self, value: str) -> str:
        normalized = normalize_code_like_field(value)
        if re.fullmatch(r"[A-Z]\d{4}", normalized) and normalized[1:4] == "000":
            normalized = f"{normalized[0]}00{normalized[-1]}"
        return normalized

    def _combine_code_prefix(self, prefix: str, value: str) -> str:
        normalized_prefix = self._normalize_prefix_token(prefix)
        normalized_value = normalize_code_like_field(value)
        if not normalized_prefix:
            return normalized_value
        if not normalized_value:
            return normalized_prefix
        if normalized_value.startswith(normalized_prefix):
            return normalized_value
        return f"{normalized_prefix}-{normalized_value}"

    def _safe_decimal(self, value: str):
        try:
            return to_decimal_flexible(value)
        except Exception:
            return None

    def _post_process_row(self, row: dict) -> dict:
        processed = dict(row)
        processed["identificacion"] = self._normalize_identificacion(processed.get("identificacion"))
        processed["cliente"] = self._normalize_cliente(processed.get("cliente"))
        processed["descripcion"] = normalize_spaces(str(processed.get("descripcion", "")))
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

    def _normalize_cliente(self, value) -> str:
        normalized = normalize_spaces(str(value or ""))
        normalized = re.sub(r"S\.A,C,S[.,]?", "S.A.C.S.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"S\.A,C[.,]?", "S.A.C.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bS\.?A\.?C\.?S\.?\b", "S.A.C.S.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bS\.?A\.?C\.?\b", "S.A.C.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bS\.?A\.?\b(?!\.C)", "S.A.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\.{2,}", ".", normalized)

        if self._starts_with_corporate_suffix(normalized):
            parts = normalized.split(maxsplit=1)
            if len(parts) == 2:
                normalized = f"{parts[1]} {parts[0]}"

        return normalize_spaces(normalized)
