from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

from ..utils import normalize_spaces, to_decimal_flexible
from .generic_liquidation import GenericLiquidationProfile


DATE_LINE_RE = re.compile(
    r"^(?P<fecha_inicio>\d{2}/\d{2}/\d{4})\s+(?P<prefix>.+?)\s+(?P<monto_documento>-?[\d,.]+)\s+"
    r"\(?(?P<pct>[\d.,]+)(?:\s*[2Z])?\s*%?\)?\s+(?:RUC[^0-9]{0,6})(?P<identificacion>\d{8,14})\s+"
    r"(?P<cliente>.+)$",
    flags=re.IGNORECASE,
)

ALT_DATE_LINE_RE = re.compile(
    r"^(?P<fecha_inicio>\d{2}/\d{2}/\d{4})\s+(?P<descripcion>.+?)\s+"
    r"(?P<document_number>[A-Z0-9=/\->]+(?:\s+[A-Z0-9=/\->]+)?)\s+"
    r"(?P<monto_documento>-?[\d,.]+)\s+(?P<ignored>-?[\d,\-]+)\s+(?:RUC[^0-9]{0,6})(?P<identificacion>\d{8,14})\s+"
    r"(?P<cliente>.+?)\s+PROTECTA\s+S\.A[.,]?\s+(?P<document_legal>[A-Z0-9]+)\s*[:;]?\s*"
    r"\((?P<pct>[\d.,]+)\s*%\).*$",
    flags=re.IGNORECASE,
)

DESCRIPTOR_RE = re.compile(
    r"^(?P<descripcion>.+?)\s+(?P<monto_comision>-?[\d,.]+)(?:\s+(?P<cliente_extra>.+))?$",
    flags=re.IGNORECASE,
)


class ProtectaLiquidationProfile(GenericLiquidationProfile):
    def __init__(self) -> None:
        super().__init__(
            profile_id="protecta_liquidation",
            insurer="PROTECTA",
            display_name="Protecta Liquidacion",
            keywords=("PROTECTA", "LIQUIDACION NUMERO", "TOTAL A COBRAR"),
        )

    def _extract_detail_rows(self, lines: list[str]) -> tuple[list[dict], list[str]]:
        rows: list[dict] = []
        warnings: list[str] = []
        pending: list[str] = []
        active: list[str] = []

        for line in lines:
            if self._skip_line(line):
                continue
            if self._is_total_line(line):
                rows, warnings = self._flush_active(rows, warnings, active)
                active = []
                pending = []
                continue
            if self._looks_like_row_start(line):
                rows, warnings = self._flush_active(rows, warnings, active)
                active = [*pending, line]
                pending = []
                continue
            if active:
                parsed = self._parse_row(active)
                if parsed:
                    rows.append(parsed)
                    active = []
                    pending = [line] if self._looks_like_descriptor(line) else []
                else:
                    active.append(line)
            else:
                if self._looks_like_descriptor(line):
                    pending.append(line)
                pending = pending[-2:]

        rows, warnings = self._flush_active(rows, warnings, active)
        return rows, warnings

    def _looks_like_row_start(self, line: str) -> bool:
        return bool(re.match(r"^\d{2}/\d{2}/\d{4}\b", line))

    def _looks_like_descriptor(self, line: str) -> bool:
        upper = normalize_spaces(line).upper()
        return any(token in upper for token in ("AVISO DE COBRANZA", "CC-AC-SCTR", "FO07", "F007"))

    def _flush_active(self, rows: list[dict], warnings: list[str], active: list[str]) -> tuple[list[dict], list[str]]:
        if not active:
            return rows, warnings
        parsed = self._parse_row(active)
        if parsed:
            rows.append(parsed)
        else:
            warnings.append(f"Fila {self.insurer} no parseada: {' '.join(active)}")
        return rows, warnings

    def _parse_row(self, parts: list[str]) -> dict | None:
        raw_candidate = normalize_spaces(" ".join(parts))
        if not raw_candidate:
            return None

        date_match = re.search(r"\d{2}/\d{2}/\d{4}", raw_candidate)
        if not date_match:
            return None

        descriptor_text = normalize_spaces(raw_candidate[: date_match.start()])
        main_text = normalize_spaces(raw_candidate[date_match.start() :])
        main_text = (
            main_text.replace("—", " ")
            .replace("–", " ")
            .replace("?", " ")
            .replace("  ", " ")
        )

        match = DATE_LINE_RE.match(main_text)
        if not match:
            alt_match = ALT_DATE_LINE_RE.match(main_text)
            if not alt_match:
                return None
            return self._build_alt_row(raw_candidate, descriptor_text, alt_match.groupdict())

        payload = match.groupdict()
        monto_documento = to_decimal_flexible(payload["monto_documento"])
        pct_comision = to_decimal_flexible(payload["pct"])
        descripcion, document_number, document_legal = self._split_prefix(payload["prefix"])
        descriptor_description, _, cliente_extra = self._parse_descriptor(descriptor_text)
        monto_comision = self._calculate_commission(monto_documento, pct_comision)

        cliente = payload["cliente"].strip(" -")
        if cliente_extra:
            cliente = f"{cliente} {cliente_extra}".strip()

        if descriptor_description:
            descripcion = descriptor_description

        return {
            "fecha_inicio": payload["fecha_inicio"],
            "descripcion": descripcion,
            "document_number": document_number,
            "document_legal": document_legal,
            "monto_documento": monto_documento,
            "monto_comision": monto_comision,
            "pct_comision": pct_comision,
            "identificacion": payload["identificacion"],
            "cliente": cliente,
            "raw_line": raw_candidate,
        }

    def _build_alt_row(self, raw_candidate: str, descriptor_text: str, payload: dict[str, str]) -> dict:
        monto_documento = to_decimal_flexible(payload["monto_documento"])
        pct_comision = to_decimal_flexible(payload["pct"])
        monto_comision = self._calculate_commission(monto_documento, pct_comision)
        descriptor_description, _, cliente_extra = self._parse_descriptor(descriptor_text)

        descripcion = descriptor_description or normalize_spaces(payload["descripcion"])
        cliente = payload["cliente"].strip(" -")
        if cliente_extra:
            cliente = f"{cliente} {cliente_extra}".strip()

        return {
            "fecha_inicio": payload["fecha_inicio"],
            "descripcion": descripcion,
            "document_number": self._normalize_doc_token(payload["document_number"]),
            "document_legal": self._normalize_doc_token(payload["document_legal"]),
            "monto_documento": monto_documento,
            "monto_comision": monto_comision,
            "pct_comision": pct_comision,
            "identificacion": payload["identificacion"],
            "cliente": cliente,
            "raw_line": raw_candidate,
        }

    def _skip_line(self, line: str) -> bool:
        upper = normalize_spaces(line).upper()
        if any(
            token in upper
            for token in (
                "PROTECTA SECURITY",
                "PROTECTA SCUOTIRITY",
                "PROTECTA SA. COMPA",
                "REASEGUROS",
                "DOMINGO ORUE",
                "LIMA - LIMA - SURQUILLO",
                "FECHA INICIO",
                "MONTO",
                "TIPO DE DOC.",
                "MONTO DOC.",
                "NRO DE",
                "DOCUMENTO LEGAL",
                "COMISION",
                "IDENTIFICACION",
                "PAGINA",
            )
        ):
            return True
        return super()._skip_line(line)

    def _split_prefix(self, prefix: str) -> tuple[str, str, str]:
        normalized = normalize_spaces(prefix.replace(":", " "))
        tokens = normalized.split()
        if not tokens:
            return prefix, "", ""

        document_legal = ""
        document_number = ""

        if tokens and re.search(r"\d", tokens[-1]):
            document_legal = self._normalize_doc_token(tokens.pop())

        if tokens and re.search(r"[\d/=]", tokens[-1]):
            document_number = self._normalize_doc_token(tokens.pop())

        descripcion = " ".join(tokens).strip() or prefix
        return descripcion, document_number, document_legal

    def _parse_descriptor(self, descriptor_text: str) -> tuple[str | None, Decimal | None, str | None]:
        if not descriptor_text:
            return None, None, None

        normalized = normalize_spaces(descriptor_text.replace(">", " ").replace("_", " "))
        match = DESCRIPTOR_RE.match(normalized)
        if not match:
            return normalized, None, None

        payload = match.groupdict()
        return (
            payload["descripcion"].strip(),
            to_decimal_flexible(payload["monto_comision"]),
            normalize_spaces(payload["cliente_extra"]) if payload.get("cliente_extra") else None,
        )

    def _calculate_commission(self, monto_documento: Decimal, pct_comision: Decimal) -> Decimal:
        return (monto_documento * pct_comision / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _normalize_doc_token(self, value: str) -> str:
        normalized = value.upper().replace("O", "0").replace("I", "1").replace("L", "1")
        normalized = re.sub(r"[^A-Z0-9/=\-]+", "", normalized)
        return normalized.strip("-")
