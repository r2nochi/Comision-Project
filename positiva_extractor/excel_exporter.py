from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl.styles import Font

from .models import DocumentResult


def export_results(documents: list[DocumentResult], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    summary_rows = [doc.to_summary_record() for doc in documents]
    detail_rows = [row.to_record() for doc in documents for row in doc.detail_rows]
    office_rows = [row.to_record() for doc in documents for row in doc.office_totals]
    validation_rows = [row.to_record() for doc in documents for row in doc.validations]

    frames = {
        "resumen_boletas": pd.DataFrame(summary_rows),
        "detalle_comisiones": pd.DataFrame(detail_rows),
        "totales_oficina": pd.DataFrame(office_rows),
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
