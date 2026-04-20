from __future__ import annotations

import argparse
from pathlib import Path

from positiva_extractor import process_directory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extrae boletas de comisiones POSITIVA a Excel.")
    parser.add_argument("--input-dir", default="files", help="Carpeta con PDFs POSITIVA.")
    parser.add_argument("--output", default="output/positiva_comisiones.xlsx", help="Ruta del Excel de salida.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    documents, excel_path = process_directory(args.input_dir, args.output)

    print(f"PDFs procesados: {len(documents)}")
    print(f"Excel generado: {excel_path.resolve()}")
    for document in documents:
        print(
            f"- {document.source_file}: modo={document.input_mode}, "
            f"detalle={len(document.detail_rows)}, oficinas={len(document.office_totals)}, "
            f"boleta={document.boleta_number}"
        )
        if document.warnings:
            for warning in document.warnings:
                print(f"  aviso: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
