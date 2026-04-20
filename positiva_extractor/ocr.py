from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pytesseract
from PIL import Image, ImageFilter, ImageOps


DEFAULT_TESSERACT_PATHS = (
    r"C:\Users\dnochi\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
)


def _resolve_tesseract_cmd() -> str:
    for candidate in DEFAULT_TESSERACT_PATHS:
        if Path(candidate).exists():
            return candidate
    return "tesseract"


@lru_cache(maxsize=1)
def ensure_tesseract() -> str:
    cmd = _resolve_tesseract_cmd()
    pytesseract.pytesseract.tesseract_cmd = cmd
    return cmd


def preprocess_image(image: Image.Image, *, threshold: int | None = None) -> Image.Image:
    processed = image.convert("L")
    processed = ImageOps.autocontrast(processed)
    if threshold is not None:
        processed = processed.point(lambda pixel: 255 if pixel >= threshold else 0)
    processed = processed.filter(ImageFilter.SHARPEN)
    return processed


def image_to_text(
    image: Image.Image,
    *,
    lang: str = "spa",
    psm: int = 6,
    threshold: int | None = None,
) -> str:
    ensure_tesseract()
    processed = preprocess_image(image, threshold=threshold)
    return pytesseract.image_to_string(
        processed,
        lang=lang,
        config=f"--psm {psm}",
    ).strip()
