from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .models import DetailRecord, DocumentResult, OfficeTotalRecord, ValidationRecord


DETAIL_RE = re.compile(
    r"^(?P<ramo>.+?)\s+(?P<poliza>\S+)\s+(?P<document>\S+)\s+(?P<issue_date>\d{4}-\d{2}-\d{2})\s+"
    r"(?P<description>.+?)\s+(?P<prima_neta>-?[\d,]+(?:\.\d{1,2})?)\s+"
    r"(?P<pct_comision>-?[\d,]+(?:\.\d{1,2})?)\s+(?P<comision>-?[\d,]+(?:\.\d{1,2})?)\s+"
    r"(?P<descuento>-?[\d,]+(?:\.\d{1,2})?)$"
)

TOTAL_LINE_RE = re.compile(
    r"^(?P<label>Total Oficina|Total|Total Neto|IGV|Total General)\s*:\s*"
    r"(?P<value_1>-?[\d,]+(?:\.\d{1,2})?)(?:\s+(?P<value_2>-?[\d,]+(?:\.\d{1,2})?))?$"
)

def parse_positiva_document(
    *,
    text: str,
    source_file: str | Path,
    input_mode: str,
    char_count: int,
    page_count: int,
) -> DocumentResult:
    source_path = Path(source_file)
    lines = _clean_lines(text)
    entity = _detect_entity(lines, source_path.name)
    boleta_number = _extract_boleta_number(lines, source_path.stem)
    offices_reported = _extract_reported_offices(lines)

    result = DocumentResult(
        source_file=source_path.name,
        entity=entity,
        boleta_number=boleta_number,
        generated_at=_extract_datetime_line(lines),
        broker=_extract_prefixed_value(lines, "BROKER"),
        igv_mode=_extract_prefixed_value(lines, "IGV"),
        currency=_extract_prefixed_value(lines, "MONEDA"),
        ruc=_extract_prefixed_value(lines, "RUC"),
        address=_extract_prefixed_value(lines, "DIRECCION") or _extract_prefixed_value(lines, "DIRECCIÓN"),
        offices_reported=offices_reported,
        input_mode=input_mode,
        extracted_char_count=char_count,
        page_count=page_count,
        total_comision=None,
        total_descuento=None,
        total_neto=None,
        igv_amount=None,
        total_general=None,
    )

    _parse_body(lines=lines, result=result)
    result.validations.extend(_build_validations(result))
    if not result.detail_rows:
        result.warnings.append("No se detectaron filas de detalle.")
    if result.total_comision is None:
        result.warnings.append("No se detecto el total general del documento.")
    return result


def _clean_lines(text: str) -> list[str]:
    cleaned: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.replace("\u00a0", " ").strip()
        line = re.sub(r"\s+", " ", line)
        if not line or line.startswith("[[PAGE "):
            continue
        cleaned.append(line)
    return cleaned


def _detect_entity(lines: list[str], fallback_name: str) -> str:
    if lines:
        header = lines[0].upper()
        if "EPS" in header:
            return "POSITIVA EPS"
        if "VIDA" in header:
            return "POSITIVA VIDA"
        if "SEGUROS" in header:
            return "POSITIVA SEGUROS"
    upper_name = fallback_name.upper()
    if "EPS" in upper_name:
        return "POSITIVA EPS"
    if "VIDA" in upper_name:
        return "POSITIVA VIDA"
    return "POSITIVA SEGUROS"


def _extract_datetime_line(lines: list[str]) -> str | None:
    for line in lines:
        if re.match(r"^\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2}$", line):
            return line
    return None


def _extract_prefixed_value(lines: list[str], prefix: str) -> str | None:
    normalized_prefix = prefix.upper()
    for index, line in enumerate(lines):
        upper_line = line.upper()
        if upper_line.rstrip(":") == normalized_prefix:
            next_value = _next_meaningful_line(lines, index)
            if next_value:
                return next_value
            continue
        match = re.match(rf"^{re.escape(prefix)}\b\s*:?\s*(.+)$", line, re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip(" :")
        if value:
            return value
    return None


def _extract_boleta_number(lines: list[str], fallback: str) -> str:
    for index, line in enumerate(lines):
        match = re.search(r"\bBOLETA\s+(\d{6,})\b", line, re.IGNORECASE)
        if match:
            return match.group(1)
        if line.upper().rstrip(":") == "BOLETA":
            next_value = _next_meaningful_line(lines, index)
            if next_value and re.fullmatch(r"\d{6,}", next_value):
                return next_value
    fallback_match = re.search(r"(\d{6,})", fallback)
    return fallback_match.group(1) if fallback_match else fallback


def _extract_reported_offices(lines: list[str]) -> list[str]:
    office_line = _extract_prefixed_value(lines, "OFICINA")
    if office_line and "/" in office_line:
        return [part.strip() for part in office_line.split("/") if part.strip()]

    for line in lines:
        if "/" not in line:
            continue
        upper_line = line.upper()
        if any(label in upper_line for label in ("HTTP", "BROKER", "DIRECCION", "DIRECCIÓN")):
            continue
        offices = [part.strip() for part in line.split("/") if part.strip()]
        if len(offices) >= 2:
            return offices
    return []


def _parse_body(*, lines: list[str], result: DocumentResult) -> None:
    offices_lookup = set(result.offices_reported)
    current_office: str | None = None
    detail_buffer = ""

    for line in lines:
        if line in offices_lookup:
            if detail_buffer:
                result.warnings.append(f"Fila incompleta descartada antes de oficina {line}: {detail_buffer}")
                detail_buffer = ""
            current_office = line
            continue

        if line.startswith("Ramo "):
            continue

        total_payload = _parse_total_line(line)
        if total_payload:
            label, value_1, value_2 = total_payload

            if label == "Total Oficina" and current_office:
                detail_count = sum(1 for row in result.detail_rows if row.office == current_office)
                result.office_totals.append(
                    OfficeTotalRecord(
                        source_file=result.source_file,
                        entity=result.entity,
                        boleta_number=result.boleta_number,
                        office=current_office,
                        total_comision=value_1,
                        total_descuento=value_2 or Decimal("0"),
                        detail_rows=detail_count,
                    )
                )
                current_office = None
            elif label == "Total":
                result.total_comision = value_1
                result.total_descuento = value_2 or Decimal("0")
            elif label == "Total Neto":
                result.total_neto = value_1
            elif label == "IGV":
                result.igv_amount = value_1
            elif label == "Total General":
                result.total_general = value_1
            continue

        if not current_office:
            continue

        candidate = f"{detail_buffer} {line}".strip() if detail_buffer else line
        parsed = _parse_detail_line(candidate)
        if parsed is None:
            detail_buffer = candidate
            continue

        detail_buffer = ""
        result.detail_rows.append(
            DetailRecord(
                source_file=result.source_file,
                entity=result.entity,
                boleta_number=result.boleta_number,
                office=current_office,
                ramo=parsed["ramo"],
                poliza=parsed["poliza"],
                document=parsed["document"],
                issue_date=parsed["issue_date"],
                description=parsed["description"],
                prima_neta=parsed["prima_neta"],
                pct_comision=parsed["pct_comision"],
                comision=parsed["comision"],
                descuento=parsed["descuento"],
                raw_line=candidate,
            )
        )

    if detail_buffer:
        result.warnings.append(f"Fila incompleta descartada al final del documento: {detail_buffer}")


def _next_meaningful_line(lines: list[str], index: int) -> str | None:
    for candidate in lines[index + 1 :]:
        if candidate.strip():
            return candidate.strip()
    return None


def _parse_total_line(line: str) -> tuple[str, Decimal, Decimal | None] | None:
    normalized = line.strip()
    upper_line = normalized.upper()
    label: str | None = None

    if upper_line.startswith("TOTAL OFICINA"):
        label = "Total Oficina"
    elif upper_line.startswith("TOTAL GENERAL"):
        label = "Total General"
    elif upper_line.startswith("TOTAL NETO"):
        label = "Total Neto"
    elif upper_line.startswith("IGV") or upper_line.startswith("IV:") or upper_line.startswith("IV "):
        label = "IGV"
    elif upper_line.startswith("TOTAL"):
        label = "Total"

    if label is None:
        return None

    numbers = re.findall(r"-?[\d,]+(?:\.\d{1,2})?", normalized)
    if not numbers:
        return None
    value_1 = _to_decimal(numbers[0])
    value_2 = _to_decimal(numbers[1]) if len(numbers) > 1 else None
    return label, value_1, value_2


def _parse_detail_line(line: str) -> dict | None:
    match = DETAIL_RE.match(line)
    if not match:
        return None
    payload = match.groupdict()
    return {
        "ramo": payload["ramo"].strip(),
        "poliza": payload["poliza"].strip(),
        "document": payload["document"].strip(),
        "issue_date": payload["issue_date"].strip(),
        "description": payload["description"].strip(),
        "prima_neta": _to_decimal(payload["prima_neta"]),
        "pct_comision": _to_decimal(payload["pct_comision"]),
        "comision": _to_decimal(payload["comision"]),
        "descuento": _to_decimal(payload["descuento"]),
    }


def _to_decimal(value: str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    normalized = value.replace(",", "").strip()
    if normalized == "":
        return Decimal("0")
    return Decimal(normalized)


def _build_validations(result: DocumentResult) -> list[ValidationRecord]:
    validations: list[ValidationRecord] = []
    detail_by_office: dict[str, list[DetailRecord]] = defaultdict(list)
    for row in result.detail_rows:
        detail_by_office[row.office].append(row)

    total_detail_comision = sum((row.comision for row in result.detail_rows), start=Decimal("0"))
    total_detail_descuento = sum((row.descuento for row in result.detail_rows), start=Decimal("0"))
    total_neto_calculated = total_detail_comision + total_detail_descuento

    for office_total in result.office_totals:
        office_rows = detail_by_office.get(office_total.office, [])
        calculated_comision = sum((row.comision for row in office_rows), start=Decimal("0"))
        if _looks_like_leading_digit_ocr_error(office_total.total_comision, calculated_comision):
            result.warnings.append(
                f"Se corrigio el total de comision OCR para la oficina {office_total.office} "
                f"de {office_total.total_comision} a {calculated_comision}."
            )
            office_total.total_comision = calculated_comision
        calculated_descuento = sum((row.descuento for row in office_rows), start=Decimal("0"))
        if office_total.total_descuento != Decimal("0") and _looks_like_leading_digit_ocr_error(
            office_total.total_descuento, calculated_descuento
        ):
            result.warnings.append(
                f"Se corrigio el descuento OCR para la oficina {office_total.office} "
                f"de {office_total.total_descuento} a {calculated_descuento}."
            )
            office_total.total_descuento = calculated_descuento
        if office_total.total_descuento == Decimal("0") and calculated_descuento < Decimal("0"):
            result.warnings.append(
                f"La oficina {office_total.office} reporta descuento total en cero, "
                f"pero el detalle suma {calculated_descuento}."
            )

    if result.total_comision is not None and _looks_like_leading_digit_ocr_error(result.total_comision, total_detail_comision):
        result.warnings.append(
            f"Se corrigio el total de comision OCR de {result.total_comision} a {total_detail_comision}."
        )
        result.total_comision = total_detail_comision
    if result.total_neto is not None and _looks_like_leading_digit_ocr_error(result.total_neto, total_neto_calculated):
        result.warnings.append(
            f"Se corrigio el total neto OCR de {result.total_neto} a {total_neto_calculated}."
        )
        result.total_neto = total_neto_calculated
    if result.total_general is not None and result.igv_amount is not None:
        total_general_calculated = total_neto_calculated + result.igv_amount
        if _looks_like_leading_digit_ocr_error(result.total_general, total_general_calculated):
            result.warnings.append(
                f"Se corrigio el total general OCR de {result.total_general} a {total_general_calculated}."
            )
            result.total_general = total_general_calculated

    for office_total in result.office_totals:
        office_rows = detail_by_office.get(office_total.office, [])
        calculated_comision = sum((row.comision for row in office_rows), start=Decimal("0"))
        calculated_descuento = sum((row.descuento for row in office_rows), start=Decimal("0"))
        validations.append(
            _make_validation(
                result=result,
                scope=office_total.office,
                metric="total_comision_oficina",
                expected=office_total.total_comision,
                calculated=calculated_comision,
            )
        )
        validations.append(
            _make_validation(
                result=result,
                scope=office_total.office,
                metric="total_descuento_oficina",
                expected=office_total.total_descuento,
                calculated=calculated_descuento,
            )
        )

    if result.total_comision is not None:
        validations.append(
            _make_validation(
                result=result,
                scope="DOCUMENTO",
                metric="total_comision",
                expected=result.total_comision,
                calculated=total_detail_comision,
            )
        )
    if result.total_descuento is not None:
        validations.append(
            _make_validation(
                result=result,
                scope="DOCUMENTO",
                metric="total_descuento",
                expected=result.total_descuento,
                calculated=total_detail_descuento,
            )
        )
    if result.total_neto is not None:
        validations.append(
            _make_validation(
                result=result,
                scope="DOCUMENTO",
                metric="total_neto",
                expected=result.total_neto,
                calculated=total_neto_calculated,
            )
        )
    if result.total_general is not None and result.total_neto is not None and result.igv_amount is not None:
        validations.append(
            _make_validation(
                result=result,
                scope="DOCUMENTO",
                metric="total_general",
                expected=result.total_general,
                calculated=result.total_neto + result.igv_amount,
            )
        )
    return validations


def _looks_like_leading_digit_ocr_error(expected: Decimal, calculated: Decimal) -> bool:
    if expected == calculated:
        return False
    expected_text = f"{expected:.2f}".replace(",", "")
    calculated_text = f"{calculated:.2f}".replace(",", "")
    if len(expected_text) != len(calculated_text):
        return False
    if expected_text[1:] != calculated_text[1:]:
        return False
    return expected_text[0] in {"4", "7"} and calculated_text[0] in {"0", "1", "2", "3"}


def _make_validation(
    *,
    result: DocumentResult,
    scope: str,
    metric: str,
    expected: Decimal,
    calculated: Decimal,
) -> ValidationRecord:
    difference = calculated - expected
    if metric == "total_descuento_oficina" and expected == Decimal("0") and calculated < Decimal("0"):
        status = "OBSERVAR"
        message = "La fuente parece no consolidar descuentos negativos en el total por oficina."
    else:
        status = "OK" if abs(difference) <= Decimal("0.01") else "REVISAR"
        message = "Coincide con el detalle." if status == "OK" else "La suma del detalle no coincide con el total reportado."
    return ValidationRecord(
        source_file=result.source_file,
        entity=result.entity,
        boleta_number=result.boleta_number,
        scope=scope,
        metric=metric,
        expected=expected,
        calculated=calculated,
        difference=difference,
        status=status,
        message=message,
    )
