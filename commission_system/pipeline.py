from __future__ import annotations

from pathlib import Path

from .excel_exporter import export_results
from .models import ParseContext, ParsedDocument
from .pdf_utils import detect_input_mode, extract_pdf_text, extract_scan_text, extract_scan_text_fixed
from .profiles.registry import PROFILE_REGISTRY


def detect_profile(text: str) -> tuple[object, int, list[str]]:
    best_profile = PROFILE_REGISTRY[0]
    best_score = -1
    best_markers: list[str] = []
    for profile in PROFILE_REGISTRY:
        score, markers = profile.match_score(text)
        if score > best_score:
            best_profile = profile
            best_score = score
            best_markers = markers
    return best_profile, best_score, best_markers


def process_directory(
    input_dir: str | Path,
    output_path: str | Path,
    *,
    include_scans: bool = False,
    expected_insurer: str | None = None,
) -> tuple[list[ParsedDocument], Path]:
    directory = Path(input_dir)
    pdf_files = sorted(
        path for path in directory.glob("*.pdf") if include_scans or not path.stem.lower().endswith("_scan")
    )
    documents = [process_file(pdf_path, expected_insurer=expected_insurer) for pdf_path in pdf_files]
    excel_path = export_results(documents, output_path)
    return documents, excel_path


def process_file(file_path: str | Path, *, expected_insurer: str | None = None) -> ParsedDocument:
    path = Path(file_path)
    input_mode, digital_probe = detect_input_mode(path)
    warnings: list[str] = []

    if input_mode == "digital":
        parse_extraction = extract_pdf_text(path)
        detection_text = parse_extraction.text
    else:
        parse_extraction = extract_scan_text(path)
        detection_text = parse_extraction.text
        warnings.append("Se activo OCR completo porque el PDF no tenia texto digital suficiente.")

    profile, score, markers = detect_profile(detection_text)

    if input_mode == "digital" and score < 60:
        logo_probe = extract_scan_text(path, max_pages=1, render_scale=3.0)
        augmented_text = f"{detection_text}\n{logo_probe.text}"
        augmented_profile, augmented_score, augmented_markers = detect_profile(augmented_text)
        if augmented_score > score:
            profile, score, markers = augmented_profile, augmented_score, augmented_markers
            warnings.append("La deteccion de aseguradora se reforzo con OCR del logo/nombre en la primera pagina.")

    if input_mode == "digital" and getattr(profile, "prefer_ocr_even_for_digital", False):
        parse_extraction = extract_scan_text(path)
        warnings.append("Se uso OCR completo para parsear mejor el layout detectado.")

    context = ParseContext(
        file_path=path,
        input_mode=input_mode,
        extracted_char_count=parse_extraction.char_count,
        page_count=parse_extraction.page_count,
    )
    document = profile.parse(parse_extraction.text, context)
    document.detection_score = score
    document.detection_markers = markers
    document.warnings.extend(warnings)

    if input_mode == "scan" and getattr(profile, "profile_id", "") == "sanitas_eps":
        retry_extraction = extract_scan_text_fixed(path, psm=6, render_scale=3.0)
        retry_context = ParseContext(
            file_path=path,
            input_mode=input_mode,
            extracted_char_count=retry_extraction.char_count,
            page_count=retry_extraction.page_count,
        )
        retry_document = profile.parse(retry_extraction.text, retry_context)
        current_score = (len(document.detail_rows), -len(document.warnings))
        retry_score = (len(retry_document.detail_rows), -len(retry_document.warnings))
        if retry_score > current_score:
            retry_document.detection_score = score
            retry_document.detection_markers = markers
            retry_document.warnings.extend(warnings)
            retry_document.warnings.append("Se uso una segunda pasada OCR con psm 6 para reconstruir mejor las filas SANITAS.")
            document = retry_document

    if input_mode == "digital" and digital_probe.char_count < 300:
        document.warnings.append("El PDF fue detectado como digital, pero con poco texto util. Conviene revisar manualmente.")

    if expected_insurer and expected_insurer.upper() not in {"", "AUTO"}:
        if document.detected_insurer.upper() != expected_insurer.upper():
            document.warnings.append(
                f"Se detecto contenido de {document.detected_insurer} aunque en el formulario se esperaba {expected_insurer}."
            )
    return document
