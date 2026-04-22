from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pypdfium2 as pdfium
import pytesseract
from pytesseract import Output

from ..models import ParseContext, ParsedDocument
from ..ocr import ensure_tesseract, preprocess_image
from ..utils import clean_lines, normalize_for_match, normalize_spaces, to_decimal_flexible
from .generic_liquidation import GenericLiquidationProfile


DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
PERCENT_RE = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)")
NUMBER_RE = re.compile(r"\d[\d,]*[.,]\d{1,2}")

DATE_MAX_X = 390
DESCRIPTION_MAX_X = 551
DOCUMENT_MAX_X = 712
LEGAL_MAX_X = 840
MONTO_DOC_MAX_X = 970
COMMISSION_MAX_X = 1062
IDENTIFICATION_MAX_X = 1222
CONTINUATION_GAP = 18
LINE_GROUP_GAP = 12
RENDER_SCALE = 3.0


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
        detail_rows, warnings = self._extract_detail_rows_from_layout(context.file_path)
        if not detail_rows:
            fallback_rows, fallback_warnings = super()._extract_detail_rows(lines)
            detail_rows = fallback_rows
            warnings.extend(fallback_warnings)
            warnings.append("Se uso el parser generico SANITAS porque el OCR por coordenadas no devolvio filas.")

        reported_totals = self._extract_totals(lines)
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

    def _extract_detail_rows_from_layout(self, file_path: Path) -> tuple[list[dict], list[str]]:
        rows: list[dict] = []
        warnings: list[str] = []
        ensure_tesseract()
        pdf = pdfium.PdfDocument(str(file_path))

        try:
            for page_index in range(len(pdf)):
                page = pdf.get_page(page_index)
                bitmap = page.render(scale=RENDER_SCALE)
                image = bitmap.to_pil().copy().rotate(270, expand=True)
                processed = preprocess_image(image)

                try:
                    data = pytesseract.image_to_data(
                        processed,
                        lang="spa",
                        config="--psm 11",
                        output_type=Output.DICT,
                    )
                finally:
                    processed.close()
                    image.close()
                    bitmap.close()
                    page.close()

                page_rows, page_warnings = self._parse_page_ocr_data(data)
                rows.extend(page_rows)
                warnings.extend(page_warnings)
        except Exception as exc:  # pragma: no cover - fallback path
            warnings.append(f"No se pudo extraer SANITAS por OCR de layout: {exc}")
            return [], warnings
        finally:
            pdf.close()

        return rows, warnings

    def _parse_page_ocr_data(self, data: dict[str, list]) -> tuple[list[dict], list[str]]:
        tokens = self._collect_tokens(data)
        clusters = self._group_tokens_into_lines(tokens)
        row_clusters = self._group_lines_into_rows(clusters)

        rows: list[dict] = []
        warnings: list[str] = []
        for record in row_clusters:
            parsed = self._parse_row_record(record)
            if parsed:
                rows.append(parsed)
            else:
                raw_text = " | ".join(self._cluster_text(cluster) for cluster in record)
                warnings.append(f"Fila SANITAS no parseada: {raw_text}")
        return rows, warnings

    def _collect_tokens(self, data: dict[str, list]) -> list[tuple[int, int, str]]:
        tokens: list[tuple[int, int, str]] = []
        for index, raw_text in enumerate(data["text"]):
            text = str(raw_text).strip()
            conf = str(data["conf"][index]).strip()
            if not text or conf in {"", "-1"}:
                continue
            tokens.append((int(data["top"][index]), int(data["left"][index]), text))
        return sorted(tokens)

    def _group_tokens_into_lines(self, tokens: list[tuple[int, int, str]]) -> list[list[tuple[int, int, str]]]:
        clusters: list[list[tuple[int, int, str]]] = []
        cluster_tops: list[int] = []
        for top, left, text in tokens:
            if not clusters or abs(top - cluster_tops[-1]) > LINE_GROUP_GAP:
                clusters.append([(top, left, text)])
                cluster_tops.append(top)
            else:
                clusters[-1].append((top, left, text))
        return clusters

    def _group_lines_into_rows(self, clusters: list[list[tuple[int, int, str]]]) -> list[list[list[tuple[int, int, str]]]]:
        rows: list[list[list[tuple[int, int, str]]]] = []
        active: list[list[tuple[int, int, str]]] = []
        pending_prefix: list[list[tuple[int, int, str]]] = []

        for cluster in clusters:
            if self._is_header_or_footer(cluster) or self._is_total_cluster(cluster):
                if active:
                    rows.append(active)
                    active = []
                pending_prefix = []
                continue

            if self._starts_with_date(cluster):
                if active:
                    rows.append(active)
                active = [*pending_prefix, cluster]
                pending_prefix = []
                continue

            if active and self._cluster_gap(active[-1], cluster) <= CONTINUATION_GAP:
                active.append(cluster)
                continue

            if active:
                rows.append(active)
                active = []

            if self._looks_like_descriptor(cluster):
                pending_prefix = [cluster]
            else:
                pending_prefix = []

        if active:
            rows.append(active)

        return rows

    def _parse_row_record(self, record: list[list[tuple[int, int, str]]]) -> dict | None:
        buckets = {
            "date": [],
            "descripcion": [],
            "document_number": [],
            "document_legal": [],
            "monto_documento": [],
            "comision": [],
            "identificacion": [],
            "cliente": [],
        }

        for cluster in record:
            for _, left, text in sorted(cluster, key=lambda item: item[1]):
                if DATE_RE.match(text):
                    buckets["date"].append(text)
                elif left < DESCRIPTION_MAX_X:
                    buckets["descripcion"].append(text)
                elif left < DOCUMENT_MAX_X:
                    buckets["document_number"].append(text)
                elif left < LEGAL_MAX_X:
                    buckets["document_legal"].append(text)
                elif left < MONTO_DOC_MAX_X:
                    buckets["monto_documento"].append(text)
                elif left < COMMISSION_MAX_X:
                    buckets["comision"].append(text)
                elif left < IDENTIFICATION_MAX_X:
                    buckets["identificacion"].append(text)
                else:
                    buckets["cliente"].append(text)

        if not buckets["date"]:
            return None

        fecha_inicio = buckets["date"][0]
        descripcion = self._normalize_description(" ".join(buckets["descripcion"]))
        document_number = self._normalize_document_field(" ".join(buckets["document_number"]))
        document_legal = self._normalize_document_field(" ".join(buckets["document_legal"]))
        identificacion = self._extract_identification(" ".join(buckets["identificacion"]))
        cliente = normalize_spaces(" ".join(buckets["cliente"]).replace("—", " ").replace("»", " "))
        monto_documento = self._extract_amount(" ".join(buckets["monto_documento"]))
        monto_comision, pct_comision = self._extract_commission_fields(
            " ".join(buckets["comision"]),
            monto_documento,
        )

        if monto_documento is None:
            return None

        if monto_comision is None and pct_comision is not None:
            monto_comision = (monto_documento * pct_comision / Decimal("100")).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        if pct_comision is None and monto_comision is not None and monto_documento:
            pct_comision = (monto_comision * Decimal("100") / monto_documento).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )

        if monto_comision is None:
            return None

        if "NOTA DE CREDITO" in normalize_for_match(descripcion):
            monto_comision = -abs(monto_comision)

        raw_line = " | ".join(self._cluster_text(cluster) for cluster in record)
        return {
            "fecha_inicio": fecha_inicio,
            "descripcion": descripcion,
            "document_number": document_number,
            "document_legal": document_legal,
            "monto_documento": monto_documento,
            "monto_comision": monto_comision,
            "pct_comision": pct_comision or Decimal("0"),
            "identificacion": identificacion,
            "cliente": cliente,
            "raw_line": raw_line,
        }

    def _starts_with_date(self, cluster: list[tuple[int, int, str]]) -> bool:
        first_token = min(cluster, key=lambda item: item[1])[2]
        return bool(DATE_RE.match(first_token))

    def _looks_like_descriptor(self, cluster: list[tuple[int, int, str]]) -> bool:
        text = normalize_for_match(self._cluster_text(cluster))
        return "NOTA DE CREDITO" in text or ("PROFORMA" in text and any(NUMBER_RE.search(part[2]) for part in cluster))

    def _is_header_or_footer(self, cluster: list[tuple[int, int, str]]) -> bool:
        text = normalize_spaces(self._cluster_text(cluster)).upper()
        return any(
            token in text
            for token in (
                "SANITAS PERÚ S.A. - EPS",
                "CALLE AMADOR",
                "SAN ISIDRO",
                "LIQUIDACIÓN NÚMERO",
                "BROKER:",
                "LIQUIDACIÓN FECHA",
                "FECHA Y HORA",
                "TIPO DE",
                "FECHA INICIO",
                "DOCUMENTO LEGAL",
                "COMISIÓN",
                "BROKER",
                "CLIENTE",
                "PÁGINA",
                "PAGINA",
            )
        )

    def _is_total_cluster(self, cluster: list[tuple[int, int, str]]) -> bool:
        text = normalize_spaces(self._cluster_text(cluster)).upper()
        return text.startswith("TOTALES") or text.startswith("TOTAL SIN IMPUESTOS") or text.startswith("TOTAL IGV") or text.startswith("TOTAL A COBRAR")

    def _cluster_gap(self, first: list[tuple[int, int, str]], second: list[tuple[int, int, str]]) -> int:
        first_top = min(item[0] for item in first)
        second_top = min(item[0] for item in second)
        return second_top - first_top

    def _cluster_text(self, cluster: list[tuple[int, int, str]]) -> str:
        return normalize_spaces(" ".join(text for _, _, text in sorted(cluster, key=lambda item: item[1])))

    def _normalize_description(self, value: str) -> str:
        normalized = normalize_spaces(value.replace("»", " ").replace("—", " ").replace("=", "-"))
        normalized = re.sub(r"\bSA,\b", "S.A.", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bSAC\b", "S.A.C.", normalized, flags=re.IGNORECASE)
        return normalized

    def _normalize_document_field(self, value: str) -> str:
        normalized = normalize_spaces(value).upper()
        normalized = normalized.replace("—", "-").replace("»", "-").replace("=", "-")
        normalized = normalized.replace(" ", "")
        normalized = re.sub(r"[^A-Z0-9/\-.]+", "", normalized)
        normalized = normalized.replace("..", ".")
        return normalized.strip("-")

    def _extract_identification(self, value: str) -> str:
        match = re.search(r"(\d{8,14})", value)
        return match.group(1) if match else self._normalize_document_field(value)

    def _extract_amount(self, value: str) -> Decimal | None:
        matches = NUMBER_RE.findall(value.replace("O", "0"))
        if not matches:
            return None
        return to_decimal_flexible(matches[0])

    def _extract_commission_fields(
        self,
        value: str,
        monto_documento: Decimal | None,
    ) -> tuple[Decimal | None, Decimal | None]:
        normalized = normalize_spaces(value.replace("O", "0"))
        matches = NUMBER_RE.findall(normalized)
        monto_comision = to_decimal_flexible(matches[0]) if matches else None
        pct_comision: Decimal | None = None

        pct_match = PERCENT_RE.search(normalized)
        if len(matches) >= 2:
            pct_match = None
            pct_comision = self._normalize_percent(matches[1])
        elif pct_match:
            pct_comision = self._normalize_percent(pct_match.group(1))

        if pct_comision is None and monto_comision is not None and monto_documento:
            pct_comision = (monto_comision * Decimal("100") / monto_documento).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        return monto_comision, pct_comision

    def _normalize_percent(self, raw_value: str) -> Decimal | None:
        cleaned = raw_value.strip().replace("%", "")
        if not cleaned:
            return None
        value = to_decimal_flexible(cleaned)
        if value > 100 and value % 100 == 0:
            value = (value / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if value <= 0:
            return None
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
