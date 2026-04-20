from __future__ import annotations

import argparse
from pathlib import Path

from commission_system.pipeline import process_directory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Procesa PDFs de comisiones por deteccion de layout/aseguradora.")
    parser.add_argument("--input-dir", default="files", help="Carpeta con PDFs a procesar.")
    parser.add_argument("--output", default="output/comisiones_multiaseguradora.xlsx", help="Ruta del Excel de salida.")
    parser.add_argument("--include-scans", action="store_true", help="Incluye archivos *_scan.pdf en el lote.")
    parser.add_argument(
        "--expected-insurer",
        default="AUTO",
        help="Aseguradora esperada para validacion visual. El backend sigue detectando por contenido.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    documents, excel_path = process_directory(
        input_dir=args.input_dir,
        output_path=args.output,
        include_scans=args.include_scans,
        expected_insurer=args.expected_insurer,
    )
    print(f"Documentos procesados: {len(documents)}")
    for document in documents:
        print(
            f"{document.source_file} -> {document.detected_insurer} / {document.detected_profile} / "
            f"{document.input_mode} / filas={len(document.detail_rows)}"
        )
    print(Path(excel_path).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
