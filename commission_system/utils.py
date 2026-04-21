from __future__ import annotations

import re
import unicodedata
from decimal import Decimal


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()


def normalize_for_match(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(character for character in normalized if not unicodedata.combining(character))
    return normalize_spaces(ascii_text).upper()


def clean_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = normalize_spaces(raw_line)
        if not line or line.startswith("[[PAGE "):
            continue
        lines.append(line)
    return lines


def next_non_empty_line(lines: list[str], index: int) -> str | None:
    for candidate in lines[index + 1 :]:
        if candidate.strip():
            return candidate.strip()
    return None


def find_prefixed_value(lines: list[str], prefix: str) -> str | None:
    upper_prefix = prefix.upper()
    for index, line in enumerate(lines):
        upper_line = line.upper()
        if upper_line.rstrip(":") == upper_prefix:
            return next_non_empty_line(lines, index)
        match = re.match(rf"^{re.escape(prefix)}\b\s*:?\s*(.+)$", line, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip(" :")
        if value:
            return value
    return None


def find_next_numeric_line(lines: list[str], index: int) -> str | None:
    for candidate in lines[index + 1 :]:
        if re.search(r"-?[\d,]+(?:\.\d{1,3})?", candidate):
            return candidate
    return None


def to_decimal_flexible(value: str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    normalized = str(value).strip()
    if not normalized:
        return Decimal("0")
    negative = "(" in normalized or bool(re.search(r"-\s*\d", normalized))
    normalized = normalized.upper()
    normalized = normalized.replace("S/", "").replace("US$", "").replace("$", "")
    normalized = normalized.replace("|", "").replace("!", "1").replace("O", "0")
    normalized = normalized.replace(" ", "")
    normalized = normalized.replace("(", "").replace(")", "").replace("-", "")
    normalized = normalized.strip(",.")
    if not normalized:
        return Decimal("0")

    if "," in normalized and "." in normalized:
        if normalized.rfind(",") > normalized.rfind("."):
            normalized = normalized.replace(".", "").replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
    elif "," in normalized:
        head, tail = normalized.rsplit(",", 1)
        if len(tail) in {2, 3}:
            normalized = f"{head.replace(',', '')}.{tail}"
        else:
            normalized = normalized.replace(",", "")
    else:
        normalized = normalized.replace(",", "")

    if normalized in {"-", ".", "-."}:
        return Decimal("0")
    if negative:
        normalized = f"-{normalized}"
    return Decimal(normalized)


def sanitize_output_stem(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    return sanitized or "archivo"


def build_validation(
    *,
    scope: str,
    metric: str,
    expected: Decimal,
    calculated: Decimal,
    tolerance: Decimal = Decimal("0.01"),
    message_ok: str = "Coincide con el detalle.",
    message_fail: str = "La suma calculada no coincide con el total reportado.",
) -> dict:
    difference = calculated - expected
    status = "OK" if abs(difference) <= tolerance else "REVISAR"
    return {
        "scope": scope,
        "metric": metric,
        "expected": expected,
        "calculated": calculated,
        "difference": difference,
        "status": status,
        "message": message_ok if status == "OK" else message_fail,
    }


def merge_split_token(text: str, pattern: str, replacement: str) -> str:
    return re.sub(pattern, replacement, text, flags=re.IGNORECASE)
