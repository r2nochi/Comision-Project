from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl.styles import Font

from .models import ParsedDocument


DETAIL_PREFERRED_ORDER = [
    "source_file",
    "source_stem",
    "detected_insurer",
    "detected_profile",
    "document_type",
    "input_mode",
    "fecha_inicio",
    "fecha_pago",
    "fecha",
    "document_tipo",
    "descripcion",
    "document_number",
    "document_legal",
    "tipo_documento",
    "nro_documento",
    "nro_doc",
    "doc_legal",
    "contrato",
    "poliza",
    "endoso",
    "recibo",
    "remesa",
    "orden",
    "producto",
    "ramo",
    "item",
    "documento",
    "doc_sunat",
    "monto_documento",
    "prima",
    "prima_neta",
    "base",
    "pct_comision",
    "monto_comision",
    "comision_total",
    "comision_pagar",
    "igv",
    "total",
    "moneda",
    "identificacion",
    "cliente",
    "tomador",
    "raw_line",
]


def export_results(documents: list[ParsedDocument], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    summary_rows = [document.summary_record() for document in documents]
    detail_rows = [row for document in documents for row in document.detail_records()]
    total_rows = [row for document in documents for row in document.reported_total_records()]
    validation_rows = [row for document in documents for row in document.validation_records()]

    frames = {
        "resumen_documentos": pd.DataFrame(summary_rows),
        "detalle_comisiones": _prepare_detail_frame(pd.DataFrame(detail_rows)),
        "totales_reportados": pd.DataFrame(total_rows),
        "validaciones": pd.DataFrame(validation_rows),
    }

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, frame in frames.items():
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
            worksheet = writer.book[sheet_name]
            worksheet.freeze_panes = "A2"
            for cell in worksheet[1]:
                cell.font = Font(bold=True)
            _autosize_columns(worksheet)

    return output


def _autosize_columns(worksheet) -> None:
    for column_cells in worksheet.columns:
        values = [str(cell.value) if cell.value is not None else "" for cell in column_cells]
        max_length = max((len(value) for value in values), default=0)
        worksheet.column_dimensions[column_cells[0].column_letter].width = min(max_length + 2, 60)


def _prepare_detail_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame

    preferred: list[str] = []
    seen: set[str] = set()
    for column in DETAIL_PREFERRED_ORDER:
        if column in frame.columns and column not in seen:
            preferred.append(column)
            seen.add(column)

    remaining = [column for column in frame.columns if column not in seen]
    return frame.loc[:, [*preferred, *remaining]]
