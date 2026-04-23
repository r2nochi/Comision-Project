from __future__ import annotations

from ..models import ParseContext, ParsedDocument
from ..utils import clean_lines
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
