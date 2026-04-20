from __future__ import annotations

import logging
import re
from pathlib import Path

import pypdfium2 as pdfium
from pypdf import PdfReader

from .models import TextExtractionResult
from .ocr import image_to_text


logging.getLogger("pypdf").setLevel(logging.ERROR)


SCAN_DETAIL_LINE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2} .* -?[\d,]+\.\d{2} -?[\d,]+\.\d{2} -?[\d,]+\.\d{2} -?[\d,]+\.\d{2}"
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

            candidates = [
                image_to_text(image, psm=4),
                image_to_text(image, psm=6),
                image_to_text(image, psm=3),
                image_to_text(image, psm=11),
                image_to_text(image, psm=4, threshold=180),
                image_to_text(image, psm=6, threshold=180),
            ]
            image.close()

            best_text = max(candidates, key=_score_ocr_candidate).strip()
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


def _score_ocr_candidate(text: str) -> tuple[int, int, int]:
    if not text:
        return (0, 0, 0)
    structured_lines = len(SCAN_DETAIL_LINE_RE.findall(text))
    date_count = len(re.findall(r"\d{4}-\d{2}-\d{2}", text))
    total_count = len(re.findall(r"\bTotal\b", text, flags=re.IGNORECASE))
    # Prefer text with whole rows preserved; length only breaks ties.
    return (structured_lines * 100 + date_count * 10 + total_count * 5, structured_lines, len(text))


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
