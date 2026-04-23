from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pypdfium2 as pdfium
import pytesseract
from pytesseract import Output

from ..ocr import ensure_tesseract, preprocess_image
from ..utils import normalize_for_match, normalize_spaces, to_decimal_flexible


DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
PERCENT_RE = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)")
NUMBER_RE = re.compile(r"\d[\d,]*[.,]\d{1,2}")

DESCRIPTION_MAX_X = 551
DOCUMENT_MAX_X = 712
LEGAL_MAX_X = 840
MONTO_DOC_MAX_X = 970
COMMISSION_MAX_X = 1062
IDENTIFICATION_MAX_X = 1222
WIDE_DESCRIPTION_MAX_X = 620
WIDE_DOCUMENT_MAX_X = 860
WIDE_LEGAL_MAX_X = 1000
WIDE_MONTO_DOC_MAX_X = 1220
WIDE_COMMISSION_MAX_X = 1420
WIDE_IDENTIFICATION_MAX_X = 1610
CONTINUATION_GAP = 18
LINE_GROUP_GAP = 12
RENDER_SCALE = 3.0


@dataclass(slots=True)
class LayoutExtractionCandidate:
    rows: list[dict]
    warnings: list[str]
    rotation: int
    score: int
    difference: Decimal | None


@dataclass(frozen=True, slots=True)
class LayoutBoundaries:
    description_max_x: int
    document_max_x: int
    legal_max_x: int
    monto_doc_max_x: int
    commission_max_x: int
    identification_max_x: int


DEFAULT_BOUNDARIES = LayoutBoundaries(
    description_max_x=DESCRIPTION_MAX_X,
    document_max_x=DOCUMENT_MAX_X,
    legal_max_x=LEGAL_MAX_X,
    monto_doc_max_x=MONTO_DOC_MAX_X,
    commission_max_x=COMMISSION_MAX_X,
    identification_max_x=IDENTIFICATION_MAX_X,
)

WIDE_BOUNDARIES = LayoutBoundaries(
    description_max_x=WIDE_DESCRIPTION_MAX_X,
    document_max_x=WIDE_DOCUMENT_MAX_X,
    legal_max_x=WIDE_LEGAL_MAX_X,
    monto_doc_max_x=WIDE_MONTO_DOC_MAX_X,
    commission_max_x=WIDE_COMMISSION_MAX_X,
    identification_max_x=WIDE_IDENTIFICATION_MAX_X,
)


def expected_total_from_reported(reported_totals: list[dict]) -> Decimal | None:
    lookup = {row["metric"]: row["value"] for row in reported_totals}
    return lookup.get("total_sin_impuestos") or lookup.get("total_sin_impuestos_detalle")


def choose_best_detail_candidate(
    *,
    insurer: str,
    file_path: Path,
    expected_total: Decimal | None,
    fallback_rows: list[dict],
    fallback_warnings: list[str],
) -> tuple[list[dict], list[str], str]:
    fallback_candidate = LayoutExtractionCandidate(
        rows=fallback_rows,
        warnings=fallback_warnings,
        rotation=-1,
        score=_score_candidate(fallback_rows, fallback_warnings, expected_total),
        difference=_difference(fallback_rows, expected_total),
    )
    layout_candidate = extract_best_rotatable_layout_rows(
        insurer=insurer,
        file_path=file_path,
        expected_total=expected_total,
    )
    best = max([fallback_candidate, layout_candidate], key=lambda candidate: candidate.score)
    source = "layout" if best is layout_candidate else "text"
    warnings = list(best.warnings)
    if source == "layout" and best.rows:
        warnings.append(
            f"Se uso OCR estructurado del layout {insurer} con rotacion {best.rotation} para reconstruir mejor el detalle."
        )
    return best.rows, warnings, source


def extract_best_rotatable_layout_rows(
    *,
    insurer: str,
    file_path: Path,
    expected_total: Decimal | None,
    rotation_candidates: tuple[int, ...] = (0, 270),
) -> LayoutExtractionCandidate:
    candidates: list[LayoutExtractionCandidate] = []
    for rotation in rotation_candidates:
        rows, warnings = _extract_rows_for_rotation(file_path=file_path, rotation=rotation, insurer=insurer)
        candidates.append(
            LayoutExtractionCandidate(
                rows=rows,
                warnings=warnings,
                rotation=rotation,
                score=_score_candidate(rows, warnings, expected_total),
                difference=_difference(rows, expected_total),
            )
        )
    return max(candidates, key=lambda candidate: candidate.score)


def _extract_rows_for_rotation(file_path: Path, rotation: int, insurer: str) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    warnings: list[str] = []
    ensure_tesseract()
    pdf = pdfium.PdfDocument(str(file_path))
    boundaries = _boundaries_for_insurer(insurer)

    try:
        for page_index in range(len(pdf)):
            page = pdf.get_page(page_index)
            bitmap = page.render(scale=RENDER_SCALE)
            image = bitmap.to_pil().copy()
            if rotation:
                rotated = image.rotate(rotation, expand=True)
                image.close()
                image = rotated
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

            page_rows, page_warnings = _parse_page_ocr_data(data, boundaries, insurer)
            rows.extend(page_rows)
            warnings.extend(page_warnings)
    except Exception as exc:  # pragma: no cover - OCR/layout failure path
        warnings.append(f"No se pudo extraer el layout rotatable: {exc}")
        return [], warnings
    finally:
        pdf.close()

    return rows, warnings


def _parse_page_ocr_data(
    data: dict[str, list],
    boundaries: LayoutBoundaries,
    insurer: str,
) -> tuple[list[dict], list[str]]:
    tokens = _collect_tokens(data)
    clusters = _group_tokens_into_lines(tokens)
    row_clusters = _group_lines_into_rows(clusters)

    rows: list[dict] = []
    warnings: list[str] = []
    for record in row_clusters:
        parsed = _parse_row_record(record, boundaries, insurer)
        if parsed:
            rows.append(parsed)
        else:
            raw_text = " | ".join(_cluster_text(cluster) for cluster in record)
            warnings.append(f"Fila layout no parseada: {raw_text}")
    return rows, warnings


def _collect_tokens(data: dict[str, list]) -> list[tuple[int, int, str]]:
    tokens: list[tuple[int, int, str]] = []
    for index, raw_text in enumerate(data["text"]):
        text = str(raw_text).strip()
        conf = str(data["conf"][index]).strip()
        if not text or conf in {"", "-1"}:
            continue
        tokens.append((int(data["top"][index]), int(data["left"][index]), text))
    return sorted(tokens)


def _group_tokens_into_lines(tokens: list[tuple[int, int, str]]) -> list[list[tuple[int, int, str]]]:
    clusters: list[list[tuple[int, int, str]]] = []
    cluster_tops: list[int] = []
    for top, left, text in tokens:
        if not clusters or abs(top - cluster_tops[-1]) > LINE_GROUP_GAP:
            clusters.append([(top, left, text)])
            cluster_tops.append(top)
        else:
            clusters[-1].append((top, left, text))
    return clusters


def _group_lines_into_rows(clusters: list[list[tuple[int, int, str]]]) -> list[list[list[tuple[int, int, str]]]]:
    rows: list[list[list[tuple[int, int, str]]]] = []
    active: list[list[tuple[int, int, str]]] = []
    pending_prefix: list[list[tuple[int, int, str]]] = []

    for cluster in clusters:
        if _is_total_cluster(cluster) or _is_header_or_footer(cluster):
            if active:
                rows.append(active)
                active = []
            pending_prefix = []
            continue

        if _starts_with_date(cluster):
            if active:
                rows.append(active)
            active = [*pending_prefix, cluster]
            pending_prefix = []
            continue

        if active and _cluster_gap(active[-1], cluster) <= CONTINUATION_GAP:
            active.append(cluster)
            continue

        if active:
            rows.append(active)
            active = []

        if _looks_like_descriptor(cluster):
            pending_prefix = [cluster]
        else:
            pending_prefix = []

    if active:
        rows.append(active)

    return rows


def _parse_row_record(record: list[list[tuple[int, int, str]]]) -> dict | None:
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
    descripcion = _normalize_description(" ".join(buckets["descripcion"]))
    document_number = _normalize_document_field(" ".join(buckets["document_number"]))
    document_legal = _normalize_document_field(" ".join(buckets["document_legal"]))
    identificacion = _extract_identification(" ".join(buckets["identificacion"]))
    cliente = normalize_spaces(" ".join(buckets["cliente"]).replace("â€”", " ").replace("Â»", " "))
    monto_documento = _extract_amount(" ".join(buckets["monto_documento"]))
    monto_comision, pct_comision = _extract_commission_fields(" ".join(buckets["comision"]), monto_documento)

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

    raw_line = " | ".join(_cluster_text(cluster) for cluster in record)
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


def _parse_row_record(
    record: list[list[tuple[int, int, str]]],
    boundaries: LayoutBoundaries,
    insurer: str,
) -> dict | None:
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
    prefix_description: list[str] = []
    prefix_commission: list[str] = []
    prefix_cliente: list[str] = []

    date_cluster_index = next((index for index, cluster in enumerate(record) if _starts_with_date(cluster)), None)
    if date_cluster_index is None:
        return None

    prefix_clusters = record[:date_cluster_index]
    main_clusters = record[date_cluster_index:]

    for cluster in prefix_clusters:
        for _, left, text in sorted(cluster, key=lambda item: item[1]):
            if left >= boundaries.identification_max_x:
                prefix_cliente.append(text)
            elif left >= boundaries.monto_doc_max_x and NUMBER_RE.search(text):
                prefix_commission.append(text)
            else:
                prefix_description.append(text)

    for cluster in main_clusters:
        for _, left, text in sorted(cluster, key=lambda item: item[1]):
            if DATE_RE.match(text):
                buckets["date"].append(text)
            elif left < boundaries.description_max_x:
                if not prefix_description:
                    buckets["descripcion"].append(text)
            elif left < boundaries.document_max_x:
                buckets["document_number"].append(text)
            elif left < boundaries.legal_max_x:
                buckets["document_legal"].append(text)
            elif left < boundaries.monto_doc_max_x:
                buckets["monto_documento"].append(text)
            elif left < boundaries.commission_max_x:
                buckets["comision"].append(text)
            elif left < boundaries.identification_max_x:
                buckets["identificacion"].append(text)
            else:
                buckets["cliente"].append(text)

    if not buckets["date"]:
        return None

    fecha_inicio = buckets["date"][0]
    descripcion = _normalize_description(" ".join(prefix_description or buckets["descripcion"]))
    document_number = _normalize_document_field(" ".join(buckets["document_number"]))
    document_legal = _normalize_document_field(" ".join(buckets["document_legal"]))
    identificacion = _extract_identification(" ".join(buckets["identificacion"]))
    cliente = normalize_spaces(
        " ".join([*prefix_cliente, *buckets["cliente"]]).replace("Ã¢â‚¬â€", " ").replace("Ã‚Â»", " ")
    )
    monto_documento = _extract_amount(" ".join(buckets["monto_documento"]))
    descriptor_monto_comision = _extract_amount(" ".join(prefix_commission))
    bucket_monto_comision, pct_comision = _extract_commission_fields(" ".join(buckets["comision"]), monto_documento)
    raw_line = " | ".join(_cluster_text(cluster) for cluster in record)
    explicit_pct = _extract_explicit_percent(raw_line)
    monto_comision = descriptor_monto_comision or bucket_monto_comision

    if monto_documento is None:
        return None

    if explicit_pct is not None:
        pct_comision = explicit_pct

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


def _starts_with_date(cluster: list[tuple[int, int, str]]) -> bool:
    first_token = min(cluster, key=lambda item: item[1])[2]
    return bool(DATE_RE.match(first_token))


def _looks_like_descriptor(cluster: list[tuple[int, int, str]]) -> bool:
    text = normalize_for_match(_cluster_text(cluster))
    return any(token in text for token in ("NOTA DE CREDITO", "PROFORMA", "AVISO DE COBRANZA")) and any(
        NUMBER_RE.search(part[2]) for part in cluster
    )


def _is_header_or_footer(cluster: list[tuple[int, int, str]]) -> bool:
    text = normalize_for_match(_cluster_text(cluster))
    return any(
        token in text
        for token in (
            "LIQUIDACION NUMERO",
            "BROKER:",
            "LIQUIDACION FECHA",
            "FECHA Y HORA",
            "FECHA INICIO",
            "TIPO DE DOCUMENTO",
            "NRO. DOCUMENTO",
            "NRO DOCUMENTO",
            "DOCUMENTO LEGAL",
            "MONTO DOC",
            "MONTO COMISION BROKER",
            "NRO DE IDENTIFICACION",
            "CLIENTE",
            "PAGINA",
            "PAGINA 1 DE",
            "PAGINA 2 DE",
            "PAGINA 3 DE",
            "PAGINA 4 DE",
            "PAGINA 5 DE",
            "PAGINA 6 DE",
        )
    )


def _is_total_cluster(cluster: list[tuple[int, int, str]]) -> bool:
    text = normalize_for_match(_cluster_text(cluster))
    return text.startswith("TOTALES") or text.startswith("TOTAL SIN IMPUESTOS") or text.startswith("TOTAL IGV") or text.startswith("TOTAL A COBRAR")


def _cluster_gap(first: list[tuple[int, int, str]], second: list[tuple[int, int, str]]) -> int:
    first_top = min(item[0] for item in first)
    second_top = min(item[0] for item in second)
    return second_top - first_top


def _cluster_text(cluster: list[tuple[int, int, str]]) -> str:
    return normalize_spaces(" ".join(text for _, _, text in sorted(cluster, key=lambda item: item[1])))


def _normalize_description(value: str) -> str:
    normalized = normalize_spaces(value.replace("Â»", " ").replace("â€”", " ").replace("=", "-"))
    normalized = re.sub(r"\bSA,\b", "S.A.", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bSAC\b", "S.A.C.", normalized, flags=re.IGNORECASE)
    return normalized


def _normalize_document_field(value: str) -> str:
    normalized = normalize_spaces(value).upper()
    normalized = normalized.replace("â€”", "-").replace("Â»", "-").replace("=", "-")
    normalized = normalized.replace(" ", "")
    normalized = re.sub(r"[^A-Z0-9/\-.]+", "", normalized)
    normalized = normalized.replace("..", ".")
    return normalized.strip("-")


def _extract_identification(value: str) -> str:
    match = re.search(r"(\d{8,14})", value)
    return match.group(1) if match else _normalize_document_field(value)


def _extract_amount(value: str) -> Decimal | None:
    matches = NUMBER_RE.findall(value.replace("O", "0"))
    if not matches:
        return None
    return to_decimal_flexible(matches[0])


def _extract_commission_fields(
    value: str,
    monto_documento: Decimal | None,
) -> tuple[Decimal | None, Decimal | None]:
    normalized = normalize_spaces(value.replace("O", "0"))
    matches = NUMBER_RE.findall(normalized)
    monto_comision = to_decimal_flexible(matches[0]) if matches else None
    pct_comision: Decimal | None = None
    explicit_pct = _extract_explicit_percent(normalized)

    if len(matches) >= 2:
        pct_comision = _normalize_percent(matches[1])
    else:
        pct_comision = explicit_pct
        if pct_comision is not None:
            monto_comision = None

    if pct_comision is None and monto_comision is not None and monto_documento:
        pct_comision = (monto_comision * Decimal("100") / monto_documento).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    return monto_comision, pct_comision


def _extract_explicit_percent(value: str) -> Decimal | None:
    match = re.search(r"\((\d{1,2}(?:[.,]\d{1,2})?)\s*%", value)
    if not match:
        match = re.search(r"\b(\d{1,2}(?:[.,]\d{1,2})?)\s*%", value)
    if not match:
        return None
    return _normalize_percent(match.group(1))


def _normalize_percent(raw_value: str) -> Decimal | None:
    cleaned = raw_value.strip().replace("%", "")
    if not cleaned:
        return None
    value = to_decimal_flexible(cleaned)
    if value > 100 and value % 100 == 0:
        value = (value / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if value <= 0:
        return None
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _boundaries_for_insurer(insurer: str) -> LayoutBoundaries:
    normalized = normalize_for_match(insurer)
    if normalized in {"CRECER", "PROTECTA"}:
        return WIDE_BOUNDARIES
    return DEFAULT_BOUNDARIES


def _difference(rows: list[dict], expected_total: Decimal | None) -> Decimal | None:
    if expected_total is None:
        return None
    calculated = sum((row["monto_comision"] for row in rows), start=Decimal("0"))
    return abs(calculated - expected_total)


def _score_candidate(rows: list[dict], warnings: list[str], expected_total: Decimal | None) -> int:
    score = len(rows) * 1000 - len(warnings) * 10
    difference = _difference(rows, expected_total)
    if difference is None:
        return score
    if difference <= Decimal("0.01"):
        score += 5000
    else:
        score -= int(difference * 20)
    return score
