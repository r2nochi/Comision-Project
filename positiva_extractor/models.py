from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(slots=True)
class TextExtractionResult:
    text: str
    char_count: int
    page_numbers: list[int]
    page_count: int
    has_meaningful_text: bool


@dataclass(slots=True)
class DetailRecord:
    source_file: str
    entity: str
    boleta_number: str
    office: str
    ramo: str
    poliza: str
    document: str
    issue_date: str
    description: str
    prima_neta: Decimal
    pct_comision: Decimal
    comision: Decimal
    descuento: Decimal
    raw_line: str

    def to_record(self) -> dict:
        return {
            "source_file": self.source_file,
            "entity": self.entity,
            "boleta_number": self.boleta_number,
            "office": self.office,
            "ramo": self.ramo,
            "poliza": self.poliza,
            "document": self.document,
            "issue_date": self.issue_date,
            "description": self.description,
            "prima_neta": float(self.prima_neta),
            "pct_comision": float(self.pct_comision),
            "comision": float(self.comision),
            "descuento": float(self.descuento),
            "raw_line": self.raw_line,
        }


@dataclass(slots=True)
class OfficeTotalRecord:
    source_file: str
    entity: str
    boleta_number: str
    office: str
    total_comision: Decimal
    total_descuento: Decimal
    detail_rows: int

    def to_record(self) -> dict:
        return {
            "source_file": self.source_file,
            "entity": self.entity,
            "boleta_number": self.boleta_number,
            "office": self.office,
            "total_comision": float(self.total_comision),
            "total_descuento": float(self.total_descuento),
            "detail_rows": self.detail_rows,
        }


@dataclass(slots=True)
class ValidationRecord:
    source_file: str
    entity: str
    boleta_number: str
    scope: str
    metric: str
    expected: Decimal
    calculated: Decimal
    difference: Decimal
    status: str
    message: str

    def to_record(self) -> dict:
        return {
            "source_file": self.source_file,
            "entity": self.entity,
            "boleta_number": self.boleta_number,
            "scope": self.scope,
            "metric": self.metric,
            "expected": float(self.expected),
            "calculated": float(self.calculated),
            "difference": float(self.difference),
            "status": self.status,
            "message": self.message,
        }


@dataclass(slots=True)
class DocumentResult:
    source_file: str
    entity: str
    boleta_number: str
    generated_at: str | None
    broker: str | None
    igv_mode: str | None
    currency: str | None
    ruc: str | None
    address: str | None
    offices_reported: list[str]
    input_mode: str
    extracted_char_count: int
    page_count: int
    total_comision: Decimal | None
    total_descuento: Decimal | None
    total_neto: Decimal | None
    igv_amount: Decimal | None
    total_general: Decimal | None
    detail_rows: list[DetailRecord] = field(default_factory=list)
    office_totals: list[OfficeTotalRecord] = field(default_factory=list)
    validations: list[ValidationRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_summary_record(self) -> dict:
        return {
            "source_file": self.source_file,
            "entity": self.entity,
            "boleta_number": self.boleta_number,
            "generated_at": self.generated_at,
            "broker": self.broker,
            "igv_mode": self.igv_mode,
            "currency": self.currency,
            "ruc": self.ruc,
            "address": self.address,
            "offices_reported": " | ".join(self.offices_reported),
            "input_mode": self.input_mode,
            "extracted_char_count": self.extracted_char_count,
            "page_count": self.page_count,
            "detail_row_count": len(self.detail_rows),
            "office_total_count": len(self.office_totals),
            "total_comision": float(self.total_comision or Decimal("0")),
            "total_descuento": float(self.total_descuento or Decimal("0")),
            "total_neto": float(self.total_neto or Decimal("0")),
            "igv_amount": float(self.igv_amount or Decimal("0")),
            "total_general": float(self.total_general or Decimal("0")),
            "warnings": " | ".join(self.warnings),
        }
