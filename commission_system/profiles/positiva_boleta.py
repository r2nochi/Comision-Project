from __future__ import annotations

from decimal import Decimal

from positiva_extractor.parser import parse_positiva_document

from ..models import ParseContext, ParsedDocument
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
        for office_total in legacy.office_totals:
            reported_totals.append(
                {
                    "scope": office_total.office,
                    "metric": "total_comision_oficina",
                    "value": office_total.total_comision,
                }
            )
            reported_totals.append(
                {
                    "scope": office_total.office,
                    "metric": "total_descuento_oficina",
                    "value": office_total.total_descuento,
                }
            )

        document_totals = {
            "total_comision": legacy.total_comision,
            "total_descuento": legacy.total_descuento,
            "total_neto": legacy.total_neto,
            "igv_amount": legacy.igv_amount,
            "total_general": legacy.total_general,
        }
        for metric, value in document_totals.items():
            if value is None:
                continue
            reported_totals.append({"scope": "DOCUMENTO", "metric": metric, "value": value})

        detail_rows = [
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
