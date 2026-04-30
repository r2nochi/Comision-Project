from __future__ import annotations

from decimal import Decimal
import re

from positiva_extractor.parser import parse_positiva_document

from ..models import ParseContext, ParsedDocument
from ..utils import normalize_for_match, normalize_spaces
from .base import BaseProfile


class PositivaBoletaProfile(BaseProfile):
    profile_id = "positiva_boleta"
    insurer = "POSITIVA"
    display_name = "Positiva Boleta"
    keywords = ("LA POSITIVA", "BOLETA DE LIQUID", "RAMO PLIZA DOC", "TOTAL OFICINA")
    priority = 50

    def parse(self, text: str, context: ParseContext) -> ParsedDocument:
        legacy = parse_positiva_document(
            text=text,
            source_file=context.file_path,
            input_mode=context.input_mode,
            char_count=context.extracted_char_count,
            page_count=context.page_count,
        )

        reported_totals: list[dict] = []
        for office_index, office_total in enumerate(legacy.office_totals):
            reported_totals.append(
                {
                    "scope": office_total.office,
                    "metric": "total_comision_oficina",
                    "value": office_total.total_comision,
                    "scope_order": office_index,
                    "metric_order": 0,
                }
            )
            reported_totals.append(
                {
                    "scope": office_total.office,
                    "metric": "total_descuento_oficina",
                    "value": office_total.total_descuento,
                    "scope_order": office_index,
                    "metric_order": 1,
                }
            )

        document_scope_order = len(legacy.office_totals)
        document_totals = [
            ("total_comision", legacy.total_comision, 0),
            ("total_descuento", legacy.total_descuento, 1),
            ("total_neto", legacy.total_neto, 2),
            ("igv_amount", legacy.igv_amount, 3),
            ("total_general", legacy.total_general, 4),
        ]
        for metric, value, metric_order in document_totals:
            if value is None:
                continue
            reported_totals.append(
                {
                    "scope": "DOCUMENTO",
                    "metric": metric,
                    "value": value,
                    "scope_order": document_scope_order,
                    "metric_order": metric_order,
                }
            )

        detail_rows = [
            self._normalize_detail_row(
                {
                "office": row.office,
                "subentity": row.entity,
                "ramo": row.ramo,
                "poliza": row.poliza,
                "document": row.document,
                "issue_date": row.issue_date,
                "description": row.description,
                "prima_neta": row.prima_neta,
                "pct_comision": row.pct_comision,
                "comision": row.comision,
                "descuento": row.descuento,
                "raw_line": row.raw_line,
            }
            )
            for row in legacy.detail_rows
        ]

        validations = [
            {
                "scope": validation.scope,
                "metric": validation.metric,
                "expected": validation.expected,
                "calculated": validation.calculated,
                "difference": validation.difference,
                "status": validation.status,
                "message": validation.message,
            }
            for validation in legacy.validations
        ]

        return ParsedDocument(
            source_file=context.file_path.name,
            source_stem=context.file_path.stem,
            detected_insurer=self.insurer,
            detected_profile=self.display_name,
            document_number=legacy.boleta_number,
            document_type="Boleta de Liquidacion",
            broker=legacy.broker,
            currency=legacy.currency,
            generated_at=legacy.generated_at,
            input_mode=context.input_mode,
            extracted_char_count=context.extracted_char_count,
            page_count=context.page_count,
            metadata={
                "subentidad": legacy.entity,
                "igv_mode": legacy.igv_mode or "",
                "ruc": legacy.ruc or "",
                "direccion": legacy.address or "",
                "oficinas_reportadas": " | ".join(legacy.offices_reported),
            },
            detail_rows=detail_rows,
            reported_totals=reported_totals,
            validations=validations,
            warnings=list(legacy.warnings),
        )

    def _normalize_detail_row(self, row: dict) -> dict:
        normalized = dict(row)
        normalized["ramo"] = self._normalize_ramo(str(normalized.get("ramo", "")))
        normalized["description"] = self._normalize_description(str(normalized.get("description", "")))
        return normalized

    def _normalize_ramo(self, value: str) -> str:
        normalized = normalize_spaces(value)
        upper = normalize_for_match(normalized)
        if "BOLETA DE LIQUID" not in upper and len(normalized) < 80:
            return normalized

        known_ramos = [
            "VIDA LEY D.L. 688",
            "ACCIDENTES PERSONALES",
            "VEHICULOS",
            "INCENDIO",
            "SCTR",
            "SOAT",
        ]
        for ramo in known_ramos:
            if normalize_for_match(ramo) in upper:
                return ramo
        return normalized

    def _normalize_description(self, value: str) -> str:
        normalized = normalize_spaces(value)
        normalized = re.sub(r"^[—–-]+\s*", "", normalized)
        normalized = re.sub(r"\bRC\s+8\s+HA\b", "RC & HA", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bJ\.M\.F\.\s+8\s+S\b", "J.M.F. & S", normalized, flags=re.IGNORECASE)
        normalized = re.sub(
            r"\bC\s*E\s*P\s+REVERENDO\s+HNO\s+GASTON\s+MARIA\s+S\.?\b",
            "C E P REVERENDO HNO GASTON MARIA S",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = normalized.replace("C E P REVERENDO HNO GASTON MARIA S.", "C E P REVERENDO HNO GASTON MARIA S")
        return normalize_spaces(normalized)
