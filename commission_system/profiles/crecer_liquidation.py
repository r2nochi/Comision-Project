from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

from ..models import ParseContext, ParsedDocument
from ..utils import clean_lines, normalize_spaces, to_decimal_flexible
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

DESCRIPTOR_RE = re.compile(
    r"^(?P<descripcion>.+?)\s+(?P<monto_comision>-?[\d,]+\.\d{2})(?:\s+(?P<cliente_prefijo>.+))?$",
    flags=re.IGNORECASE,
)


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
        layout_candidate = extract_best_rotatable_layout_rows(
            insurer=self.insurer,
            file_path=context.file_path,
            expected_total=expected_total,
        )
        merged_rows = self._merge_text_and_layout_rows(
            text_rows=text_rows,
            layout_rows=layout_candidate.rows,
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
                    *text_warnings,
                    *layout_candidate.warnings,
                    f"Se combino OCR lineal y layout estructurado {self.insurer} para completar filas del PDF rotado."
                ],
                _score_candidate(merged_rows, [*text_warnings, *layout_candidate.warnings], expected_total),
            ),
        ]
        _, detail_rows, warnings, _ = max(candidates, key=lambda item: item[3])
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

    def _merge_text_and_layout_rows(self, *, text_rows: list[dict], layout_rows: list[dict]) -> list[dict]:
        if not text_rows:
            return list(layout_rows)
        if not layout_rows:
            return list(text_rows)

        text_by_key = {self._merge_key(row): row for row in text_rows}
        merged: list[dict] = []
        seen: set[tuple[str, str, str]] = set()

        for row in layout_rows:
            key = self._merge_key(row)
            merged.append(text_by_key.get(key, row))
            seen.add(key)

        for row in text_rows:
            key = self._merge_key(row)
            if key not in seen:
                merged.append(row)
                seen.add(key)

        return merged

    def _merge_key(self, row: dict) -> tuple[str, str, str]:
        return (
            str(row.get("fecha_inicio", "")),
            str(row.get("identificacion", "")),
            str(row.get("monto_documento", "")),
        )

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
        descriptor_payload = None

        if descriptor_line:
            descriptor_candidate = normalize_spaces(descriptor_line)
            descriptor_match = DESCRIPTOR_RE.match(descriptor_candidate)
            if descriptor_match:
                descriptor_payload = descriptor_match.groupdict()
                descripcion = descriptor_payload["descripcion"]
                if descriptor_payload.get("cliente_prefijo"):
                    cliente = f"{descriptor_payload['cliente_prefijo']} {cliente}".strip()
                monto_comision = to_decimal_flexible(descriptor_payload["monto_comision"])

        return {
            "fecha_inicio": payload["fecha_inicio"],
            "descripcion": descripcion,
            "document_number": payload["document_number"],
            "document_legal": payload["document_legal"],
            "monto_documento": monto_documento,
            "monto_comision": monto_comision,
            "pct_comision": pct_comision,
            "identificacion": payload["identificacion"],
            "cliente": cliente,
            "raw_line": " | ".join(filter(None, [descriptor_line, candidate])),
        }
