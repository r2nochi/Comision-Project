from __future__ import annotations

import logging
import re
from pathlib import Path

import pypdfium2 as pdfium
from pypdf import PdfReader

from .models import TextExtractionResult
from .ocr import image_to_text
from .utils import normalize_for_match


logging.getLogger("pypdf").setLevel(logging.ERROR)

INSURER_MARKERS = (
    "POSITIVA",
    "PACIFICO",
    "RIMAC",
    "QUALITAS",
    "AVLA",
    "SANITAS",
    "PROTECTA",
    "CRECER",
    "CESCE",
)

COMMON_WORDS = (
    "LIQUID",
    "COMISION",
    "CORREDOR",
    "BROKER",
    "FACTURA",
    "TOTAL",
    "MONEDA",
    "RUC",
    "PRELIQUID",
    "POLIZA",
)


def _resolve_page_numbers(total_pages: int, max_pages: int) -> list[int]:
    return list(range(1, min(total_pages, max_pages) + 1))


def extract_pdf_text(
    file_path: str | Path,
    *,
    max_pages: int = 20,
    max_chars: int = 120_000,
) -> TextExtractionResult:
    reader = PdfReader(str(file_path))
    selected_pages = _resolve_page_numbers(len(reader.pages), max_pages)
    raw_page_texts: list[str] = []
    rendered_pages: list[str] = []
    meaningful_pages: list[int] = []

    for page_number in selected_pages:
        text = _extract_best_page_text(reader=reader, file_path=file_path, page_number=page_number).strip()
        raw_page_texts.append(text)
        if text:
            meaningful_pages.append(page_number)
        rendered_pages.append(f"[[PAGE {page_number}]]\n{text}")

    joined = "\n\n".join(rendered_pages)[:max_chars]
    effective_char_count = sum(len(page_text) for page_text in raw_page_texts)
    return TextExtractionResult(
        text=joined,
        char_count=effective_char_count,
        page_numbers=selected_pages,
        page_count=len(selected_pages),
        has_meaningful_text=bool(meaningful_pages),
    )


def detect_input_mode(file_path: str | Path, *, min_text_chars: int = 120) -> tuple[str, TextExtractionResult]:
    text_result = extract_pdf_text(file_path, max_pages=20, max_chars=40_000)
    input_mode = "digital" if text_result.has_meaningful_text and text_result.char_count >= min_text_chars else "scan"
    return input_mode, text_result


def extract_scan_text(
    file_path: str | Path,
    *,
    max_pages: int = 20,
    render_scale: float = 4.0,
) -> TextExtractionResult:
    pdf = pdfium.PdfDocument(str(file_path))
    selected_pages = _resolve_page_numbers(len(pdf), max_pages)
    page_texts: list[str] = []
    meaningful_pages: list[int] = []

    try:
        for page_number in selected_pages:
            page = pdf[page_number - 1]
            bitmap = page.render(scale=render_scale)
            try:
                image = bitmap.to_pil().copy()
            finally:
                bitmap.close()
                page.close()

            best_text = _extract_best_ocr_text(image).strip()
            image.close()
            if best_text:
                meaningful_pages.append(page_number)
            page_texts.append(f"[[PAGE {page_number}]]\n{best_text}")
    finally:
        pdf.close()

    joined = "\n\n".join(page_texts)
    char_count = sum(len(section) for section in page_texts)
    return TextExtractionResult(
        text=joined,
        char_count=char_count,
        page_numbers=selected_pages,
        page_count=len(selected_pages),
        has_meaningful_text=bool(meaningful_pages),
    )


def extract_scan_text_fixed(
    file_path: str | Path,
    *,
    max_pages: int = 20,
    render_scale: float = 4.0,
    psm: int = 6,
    threshold: int | None = None,
) -> TextExtractionResult:
    pdf = pdfium.PdfDocument(str(file_path))
    selected_pages = _resolve_page_numbers(len(pdf), max_pages)
    page_texts: list[str] = []
    meaningful_pages: list[int] = []

    try:
        for page_number in selected_pages:
            page = pdf[page_number - 1]
            bitmap = page.render(scale=render_scale)
            try:
                image = bitmap.to_pil().copy()
            finally:
                bitmap.close()
                page.close()

            best_angle, _ = _rank_rotations(image)[0]
            rotated = image.rotate(best_angle, expand=True)
            try:
                best_text = image_to_text(rotated, psm=psm, threshold=threshold).strip()
            finally:
                rotated.close()
                image.close()

            if best_text:
                meaningful_pages.append(page_number)
            page_texts.append(f"[[PAGE {page_number}]]\n{best_text}")
    finally:
        pdf.close()

    joined = "\n\n".join(page_texts)
    char_count = sum(len(section) for section in page_texts)
    return TextExtractionResult(
        text=joined,
        char_count=char_count,
        page_numbers=selected_pages,
        page_count=len(selected_pages),
        has_meaningful_text=bool(meaningful_pages),
    )


def _extract_best_ocr_text(image) -> str:
    rotations = _rank_rotations(image)
    candidate_texts: list[str] = []
    selected_rotations = rotations[:1]
    if rotations and _score_rotation_probe(rotations[0][1])[0] < 120:
        selected_rotations = rotations[:2]
    for angle, rotation_text in selected_rotations:
        rotated = image.rotate(angle, expand=True)
        try:
            candidate_texts.extend(
                [
                    rotation_text,
                    image_to_text(rotated, psm=4),
                    image_to_text(rotated, psm=3),
                    image_to_text(rotated, psm=6, threshold=180),
                    image_to_text(rotated, psm=4, threshold=180),
                ]
            )
        finally:
            rotated.close()
    return max(candidate_texts, key=_score_ocr_candidate, default="")


def _rank_rotations(image) -> list[tuple[int, str]]:
    scored: list[tuple[tuple[int, int], int, str]] = []
    for angle in (0, 90, 180, 270):
        rotated = image.rotate(angle, expand=True)
        try:
            text = image_to_text(rotated, psm=6)
        finally:
            rotated.close()
        scored.append((_score_rotation_probe(text), angle, text))
    scored.sort(reverse=True)
    return [(angle, text) for _, angle, text in scored]


def _score_rotation_probe(text: str) -> tuple[int, int]:
    upper_text = normalize_for_match(text)
    insurer_hits = sum(1 for keyword in INSURER_MARKERS if keyword in upper_text)
    keyword_hits = sum(1 for keyword in COMMON_WORDS if keyword in upper_text)
    date_hits = len(re.findall(r"\d{2}/\d{2}/\d{2,4}", text)) + len(re.findall(r"\d{4}-\d{2}-\d{2}", text))
    row_hits = _count_structured_rows(text)
    return (insurer_hits * 100 + keyword_hits * 20 + date_hits * 5 + row_hits * 15, len(text))


def _score_ocr_candidate(text: str) -> tuple[int, int]:
    if not text:
        return (0, 0)
    upper_text = normalize_for_match(text)
    insurer_hits = sum(1 for keyword in INSURER_MARKERS if keyword in upper_text)
    keyword_hits = sum(1 for keyword in COMMON_WORDS if keyword in upper_text)
    date_hits = len(re.findall(r"\d{2}/\d{2}/\d{2,4}", text)) + len(re.findall(r"\d{4}-\d{2}-\d{2}", text))
    row_hits = _count_structured_rows(text)
    inline_financial_rows = len(
        re.findall(r"^.*\d{2}/\d{2}/\d{2,4}.*(?:%|RUC|S/|FA|BOL|FAC).*$", text, flags=re.MULTILINE | re.IGNORECASE)
    )
    long_row_hits = len(
        re.findall(
            r"^.{45,}\d{2}/\d{2}/\d{2,4}.*(?:\(\d{1,2}\.\d+\s*%\)|RUC|EPS-|B002-|F002-|FO02-|FA-F).*$",
            text,
            flags=re.MULTILINE | re.IGNORECASE,
        )
    )
    return (
        insurer_hits * 100
        + keyword_hits * 20
        + date_hits * 5
        + row_hits * 12
        + inline_financial_rows * 20
        + long_row_hits * 35,
        len(text),
    )


def _count_structured_rows(text: str) -> int:
    count = 0
    for line in text.splitlines():
        number_count = len(re.findall(r"-?[\d,]+\.\d{2,3}", line))
        has_date = bool(re.search(r"\d{2}/\d{2}/\d{2,4}|\d{4}-\d{2}-\d{2}", line))
        has_business_marker = bool(re.search(r"%|RUC|POLIZA|FACTURA|BOL|FAC|FA\d|FO\d|CC-", line, flags=re.IGNORECASE))
        if has_date and (number_count >= 2 or has_business_marker):
            count += 1
    return count


def _extract_best_page_text(reader: PdfReader, file_path: str | Path, page_number: int) -> str:
    pypdf_text = (reader.pages[page_number - 1].extract_text() or "").strip()
    if len(pypdf_text) >= 40:
        return pypdf_text

    pdfium_text = _extract_page_text_with_pdfium(file_path=file_path, page_number=page_number)
    if len(pdfium_text) > len(pypdf_text):
        return pdfium_text
    return pypdf_text


def _extract_page_text_with_pdfium(file_path: str | Path, page_number: int) -> str:
    pdf = pdfium.PdfDocument(str(file_path))
    try:
        if page_number < 1 or page_number > len(pdf):
            return ""
        page = pdf[page_number - 1]
        try:
            text_page = page.get_textpage()
            try:
                return (text_page.get_text_range() or "").strip()
            finally:
                text_page.close()
        finally:
            page.close()
    finally:
        pdf.close()
