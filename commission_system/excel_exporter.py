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
    "document_number",
    "document_type",
    "input_mode",
    "fecha_inicio",
    "fecha",
    "document_tipo",
    "descripcion",
    "tipo",
    "document_legal",
    "tipo_documento",
    "nro_documento",
    "nro_doc",
    "doc_legal",
    "contrato",
    "ramo",
    "poliza",
    "contratante",
    "fecha_emision",
    "estado",
    "nro_factura",
    "orden_pago",
    "asegurado_concepto",
    "prima",
    "pct_comision",
    "comision",
    "igv",
    "cargo",
    "pago_comision",
    "total",
    "endoso",
    "recibo",
    "remesa",
    "orden",
    "producto",
    "item",
    "documento",
    "doc_sunat",
    "monto_documento",
    "prima_neta",
    "base",
    "monto_comision",
    "comision_total",
    "comision_pagar",
    "moneda",
    "identificacion",
    "cliente",
    "tomador",
    "raw_line",
]

TOTAL_PREFERRED_ORDER = [
    "source_file",
    "source_stem",
    "detected_insurer",
    "detected_profile",
    "document_number",
    "document_type",
    "input_mode",
    "scope",
    "label",
    "month",
    "metric",
    "prima",
    "pct_comision",
    "comision",
    "igv",
    "saldo_actual_total",
    "monto_neto",
    "saldo_anterior",
    "comision_total_periodo",
    "comision_total_periodo_resumen",
    "otros_cargos",
    "otros_abonos",
    "pago_comisiones_periodo_anterior",
    "pago_detracciones_periodo_anterior",
    "saldo_actual_neto",
    "saldo_actual_neto_resumen",
    "total_a_pagar",
    "monto_total",
    "valor_venta",
    "valor_total",
    "value",
    "raw_line",
]

QUALITAS_DETAIL_ORDER = [
    "tipo",
    "poliza",
    "endoso",
    "recibo",
    "orden_pago",
    "fecha_pago",
    "remesa",
    "asegurado_concepto",
    "prima_neta",
    "pct_comision",
    "comision",
    "igv",
    "cargo",
    "pago_comision",
]

RIMAC_DETAIL_ORDER = [
    "producto",
    "poliza",
    "cliente",
    "documento",
    "doc_sunat",
    "tipo",
    "fecha_pago",
    "prima",
    "pct_comision",
    "comision",
]

TOTAL_METRIC_ORDER = [
    "total_comision",
    "igv",
    "total_general",
    "saldo_anterior",
    "comision_total_periodo",
    "comision_total_periodo_resumen",
    "otros_cargos",
    "otros_abonos",
    "pago_comisiones_periodo_anterior",
    "pago_detracciones_periodo_anterior",
    "saldo_actual_neto",
    "saldo_actual_neto_resumen",
    "saldo_actual_total",
    "total_a_pagar",
    "monto_neto",
    "monto_total",
    "valor_venta",
    "valor_total",
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
        "totales_reportados": _prepare_total_frame(pd.DataFrame(total_rows)),
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
    ordered = frame.loc[:, [*preferred, *remaining]]

    if _has_columns(ordered, QUALITAS_DETAIL_ORDER):
        ordered = _reorder_detail_block(ordered, QUALITAS_DETAIL_ORDER)
    if _has_columns(ordered, RIMAC_DETAIL_ORDER):
        ordered = _reorder_detail_block(ordered, RIMAC_DETAIL_ORDER)

    return ordered


def _prepare_total_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame

    preferred: list[str] = []
    seen: set[str] = set()
    for column in TOTAL_PREFERRED_ORDER:
        if column in frame.columns and column not in seen:
            preferred.append(column)
            seen.add(column)

    remaining = [column for column in frame.columns if column not in seen]
    ordered = frame.loc[:, [*preferred, *remaining]]

    if "metric" in ordered.columns:
        metric_rank = {metric: index for index, metric in enumerate(TOTAL_METRIC_ORDER)}
        rank_column = ordered["metric"].map(lambda value: metric_rank.get(value, len(metric_rank) + 100))
        ordered = (
            ordered.assign(_metric_rank=rank_column)
            .sort_values(
                by=[column for column in ("source_file", "scope", "_metric_rank") if column in ordered.columns or column == "_metric_rank"],
                kind="stable",
            )
            .drop(columns="_metric_rank")
            .reset_index(drop=True)
        )

    return ordered


def _has_columns(frame: pd.DataFrame, columns: list[str]) -> bool:
    return all(column in frame.columns for column in columns)


def _reorder_detail_block(frame: pd.DataFrame, desired_block: list[str]) -> pd.DataFrame:
    existing_block = [column for column in desired_block if column in frame.columns]
    if not existing_block:
        return frame

    prefix_columns = []
    seen_block = set(existing_block)
    for column in frame.columns:
        if column in seen_block:
            break
        prefix_columns.append(column)

    suffix_columns = [column for column in frame.columns if column not in set(prefix_columns) | seen_block]
    return frame.loc[:, [*prefix_columns, *existing_block, *suffix_columns]]
