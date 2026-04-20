from __future__ import annotations

from pathlib import Path

from .excel_exporter import export_results
from .models import DocumentResult
from .parser import parse_positiva_document
from .pdf_utils import detect_input_mode, extract_pdf_text, extract_scan_text


def process_directory(input_dir: str | Path, output_path: str | Path) -> tuple[list[DocumentResult], Path]:
    directory = Path(input_dir)
    pdf_files = sorted(directory.glob("*.pdf"))
    documents = [process_file(pdf_path) for pdf_path in pdf_files]
    excel_path = export_results(documents, output_path)
    return documents, excel_path


def process_file(file_path: str | Path) -> DocumentResult:
    path = Path(file_path)
    input_mode, digital_probe = detect_input_mode(path)
    warnings: list[str] = []

    if input_mode == "digital":
        extraction = extract_pdf_text(path)
    else:
        extraction = extract_scan_text(path)
        warnings.append("Se activo OCR local porque el PDF no tenia texto digital suficiente.")

    document = parse_positiva_document(
        text=extraction.text,
        source_file=path,
        input_mode=input_mode,
        char_count=extraction.char_count,
        page_count=extraction.page_count,
    )

    if input_mode == "digital" and digital_probe.char_count < 500:
        warnings.append("El PDF fue clasificado como digital con poco texto. Conviene revisar una muestra manualmente.")

    document.warnings.extend(warnings)
    return document
