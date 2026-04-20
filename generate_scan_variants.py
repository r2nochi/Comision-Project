from __future__ import annotations

import argparse
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from commission_system.pdf_utils import detect_input_mode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Genera versiones escaneadas image-only de PDFs digitales."
    )
    parser.add_argument("--input-dir", default="files", help="Carpeta con los PDFs origen.")
    parser.add_argument(
        "--pattern",
        default="POSITIVA*.pdf",
        help="Patron de archivos a convertir.",
    )
    parser.add_argument(
        "--suffix",
        default="_scan",
        help="Sufijo del archivo generado antes de la extension.",
    )
    parser.add_argument(
        "--render-scale",
        type=float,
        default=3.0,
        help="Escala de render para rasterizar cada pagina.",
    )
    parser.add_argument(
        "--digital-only",
        action="store_true",
        help="Solo genera variantes para PDFs detectados como digitales.",
    )
    return parser


def render_pdf_to_images(file_path: Path, render_scale: float) -> list[Image.Image]:
    pdf = pdfium.PdfDocument(str(file_path))
    images: list[Image.Image] = []
    try:
        for page_index in range(len(pdf)):
            page = pdf[page_index]
            bitmap = page.render(scale=render_scale)
            try:
                image = bitmap.to_pil().convert("RGB")
                images.append(image.copy())
            finally:
                image.close()
                bitmap.close()
                page.close()
    finally:
        pdf.close()
    return images


def save_images_as_pdf(images: list[Image.Image], output_path: Path) -> None:
    if not images:
        raise ValueError(f"No se pudieron renderizar paginas para {output_path.name}")
    first, *rest = images
    output_path.parent.mkdir(parents=True, exist_ok=True)
    first.save(output_path, "PDF", resolution=200.0, save_all=True, append_images=rest)
    for image in images:
        image.close()


def generate_scan_variant(file_path: Path, suffix: str, render_scale: float) -> Path:
    output_path = file_path.with_name(f"{file_path.stem}{suffix}{file_path.suffix}")
    images = render_pdf_to_images(file_path, render_scale)
    save_images_as_pdf(images, output_path)
    return output_path


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    generated: list[Path] = []
    for file_path in sorted(input_dir.glob(args.pattern)):
        if file_path.stem.endswith(args.suffix) or file_path.name.startswith("_scan_"):
            continue
        if args.digital_only:
            input_mode, _ = detect_input_mode(file_path)
            if input_mode != "digital":
                continue
        generated.append(generate_scan_variant(file_path, args.suffix, args.render_scale))

    print(f"Archivos generados: {len(generated)}")
    for path in generated:
        print(path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
