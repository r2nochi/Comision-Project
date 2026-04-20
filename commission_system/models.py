from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path


@dataclass(slots=True)
class TextExtractionResult:
    text: str
    char_count: int
    page_numbers: list[int]
    page_count: int
    has_meaningful_text: bool


@dataclass(slots=True)
class ParseContext:
    file_path: Path
    input_mode: str
    extracted_char_count: int
    page_count: int


def _serialize_value(value):
    if isinstance(value, Decimal):
        return float(value)
    return value


def _normalize_column_name(value: str) -> str:
    return "".join(character.lower() if character.isalnum() else "_" for character in value).strip("_")


@dataclass(slots=True)
class ParsedDocument:
    source_file: str
    source_stem: str
    detected_insurer: str
    detected_profile: str
    document_number: str | None
    document_type: str | None
    broker: str | None
    currency: str | None
    generated_at: str | None
    input_mode: str
    extracted_char_count: int
    page_count: int
    detection_score: int = 0
    detection_markers: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    detail_rows: list[dict] = field(default_factory=list)
    reported_totals: list[dict] = field(default_factory=list)
    validations: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary_record(self) -> dict:
        record = {
            "source_file": self.source_file,
            "source_stem": self.source_stem,
            "detected_insurer": self.detected_insurer,
            "detected_profile": self.detected_profile,
            "document_number": self.document_number,
            "document_type": self.document_type,
            "broker": self.broker,
            "currency": self.currency,
            "generated_at": self.generated_at,
            "input_mode": self.input_mode,
            "extracted_char_count": self.extracted_char_count,
            "page_count": self.page_count,
            "detail_row_count": len(self.detail_rows),
            "reported_total_count": len(self.reported_totals),
            "validation_count": len(self.validations),
            "detection_score": self.detection_score,
            "detection_markers": " | ".join(self.detection_markers),
            "warnings": " | ".join(self.warnings),
        }
        for key, value in self.metadata.items():
            record[f"meta_{_normalize_column_name(key)}"] = value
        return record

    def detail_records(self) -> list[dict]:
        base = {
            "source_file": self.source_file,
            "source_stem": self.source_stem,
            "detected_insurer": self.detected_insurer,
            "detected_profile": self.detected_profile,
            "document_number": self.document_number,
            "document_type": self.document_type,
            "input_mode": self.input_mode,
        }
        rows: list[dict] = []
        for detail in self.detail_rows:
            row = dict(base)
            row.update({key: _serialize_value(value) for key, value in detail.items()})
            rows.append(row)
        return rows

    def reported_total_records(self) -> list[dict]:
        base = {
            "source_file": self.source_file,
            "source_stem": self.source_stem,
            "detected_insurer": self.detected_insurer,
            "detected_profile": self.detected_profile,
            "document_number": self.document_number,
            "document_type": self.document_type,
            "input_mode": self.input_mode,
        }
        rows: list[dict] = []
        for total in self.reported_totals:
            row = dict(base)
            row.update({key: _serialize_value(value) for key, value in total.items()})
            rows.append(row)
        return rows

    def validation_records(self) -> list[dict]:
        base = {
            "source_file": self.source_file,
            "source_stem": self.source_stem,
            "detected_insurer": self.detected_insurer,
            "detected_profile": self.detected_profile,
            "document_number": self.document_number,
            "document_type": self.document_type,
            "input_mode": self.input_mode,
        }
        rows: list[dict] = []
        for validation in self.validations:
            row = dict(base)
            row.update({key: _serialize_value(value) for key, value in validation.items()})
            rows.append(row)
        return rows
