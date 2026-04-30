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
    "fecha",
    "document_tipo",
    "descripcion",
    "tipo_documento",
    "document_number",
    "tipo",
    "document_legal",
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

POSITIVA_DETAIL_ORDER = [
    "ramo",
    "poliza",
    "document",
    "issue_date",
    "description",
    "prima_neta",
    "pct_comision",
    "comision",
    "descuento",
    "raw_line",
]

SANITAS_EPS_DETAIL_ORDER = [
    "fecha_inicio",
    "producto",
    "vigencia",
    "tipo_documento",
    "contrato",
    "nro_documento",
    "doc_legal",
    "monto_doc",
    "monto_comision",
    "pct_comision",
    "identificacion",
    "cliente",
]

SANITAS_LIQ_DETAIL_ORDER = [
    "fecha_inicio",
    "tipo_documento",
    "document_number",
    "document_legal",
    "monto_documento",
    "monto_comision",
    "pct_comision",
    "identificacion",
    "cliente",
]

CRECER_DETAIL_ORDER = [
    "fecha_inicio",
    "document_tipo",
    "document_number",
    "document_legal",
    "monto_documento",
    "monto_comision",
    "pct_comision",
    "identificacion",
    "cliente",
]

PROTECTA_DETAIL_ORDER = [
    "fecha_inicio",
    "document_tipo",
    "document_number",
    "document_legal",
    "monto_documento",
    "monto_comision",
    "pct_comision",
    "identificacion",
    "cliente",
]

CESCE_DETAIL_ORDER = [
    "cliente",
    "poliza",
    "tipo_doc",
    "nro_doc",
    "fecha_pago",
    "pct_comision",
    "moneda",
    "prima_neta",
    "comision_total",
    "comision_pagar",
    "raw_line",
]

AVLA_DETAIL_ORDER = [
    "tomador",
    "poliza",
    "fecha",
    "moneda",
    "base",
    "pct_comision",
    "monto_comision",
    "raw_line",
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
    "total_general",
    "total_documento",
    "total_monto_doc",
    "total_sin_impuestos_detalle",
    "total_sin_impuestos",
    "igv",
    "total_a_cobrar",
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

PROFILE_TOTAL_METRIC_ORDER = {
    "AVLA Liquidacion": [
        "total_comision",
        "igv",
        "total_a_pagar",
    ],
    "Rimac Preliquidacion": [
        "total_general",
        "igv",
        "total_comision",
    ],
    "Cesce Liquidacion": [
        "valor_venta",
        "igv",
        "valor_total",
    ],
}


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
    if _has_columns(ordered, POSITIVA_DETAIL_ORDER):
        ordered = _reorder_detail_block(ordered, POSITIVA_DETAIL_ORDER)
    if _has_columns(ordered, SANITAS_EPS_DETAIL_ORDER):
        ordered = _reorder_detail_block(ordered, SANITAS_EPS_DETAIL_ORDER)
    if _has_columns(ordered, SANITAS_LIQ_DETAIL_ORDER):
        ordered = _reorder_detail_block(ordered, SANITAS_LIQ_DETAIL_ORDER)
    if _has_columns(ordered, CRECER_DETAIL_ORDER):
        ordered = _reorder_detail_block(ordered, CRECER_DETAIL_ORDER)
    if _has_columns(ordered, PROTECTA_DETAIL_ORDER):
        ordered = _reorder_detail_block(ordered, PROTECTA_DETAIL_ORDER)
    if _has_columns(ordered, CESCE_DETAIL_ORDER):
        ordered = _reorder_detail_block(ordered, CESCE_DETAIL_ORDER)
    if _has_columns(ordered, AVLA_DETAIL_ORDER):
        ordered = _reorder_detail_block(ordered, AVLA_DETAIL_ORDER)
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
        global_metric_rank = {metric: index for index, metric in enumerate(TOTAL_METRIC_ORDER)}
        profile_metric_rank = {
            profile: {metric: index for index, metric in enumerate(metrics)}
            for profile, metrics in PROFILE_TOTAL_METRIC_ORDER.items()
        }
        rank_column = ordered.apply(
            lambda row: _metric_rank_for_row(
                metric=row.get("metric"),
                profile=row.get("detected_profile"),
                global_metric_rank=global_metric_rank,
                profile_metric_rank=profile_metric_rank,
            ),
            axis=1,
        )
        scope_rank = ordered["scope_order"] if "scope_order" in ordered.columns else pd.Series([10_000] * len(ordered), index=ordered.index)
        metric_order = ordered["metric_order"] if "metric_order" in ordered.columns else pd.Series([10_000] * len(ordered), index=ordered.index)
        ordered = (
            ordered.assign(_scope_rank=scope_rank, _metric_order=metric_order, _metric_rank=rank_column)
            .sort_values(
                by=[
                    column
                    for column in ("source_file", "_scope_rank", "_metric_order", "scope", "_metric_rank")
                    if column in ordered.columns or column in {"_scope_rank", "_metric_order", "_metric_rank"}
                ],
                kind="stable",
            )
            .drop(columns=[column for column in ("_scope_rank", "_metric_order", "_metric_rank", "scope_order", "metric_order") if column in ordered.columns])
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


def _metric_rank_for_row(*, metric, profile, global_metric_rank: dict[str, int], profile_metric_rank: dict[str, dict[str, int]]) -> int:
    profile_order = profile_metric_rank.get(str(profile or ""))
    if profile_order and metric in profile_order:
        return profile_order[metric]
    return global_metric_rank.get(metric, len(global_metric_rank) + 100)
