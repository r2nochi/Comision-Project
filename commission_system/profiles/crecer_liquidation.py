from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

from ..utils import normalize_spaces, to_decimal_flexible
from .generic_liquidation import GenericLiquidationProfile


DATE_LINE_RE = re.compile(
    r"^(?P<fecha_inicio>\d{2}/\d{2}/\d{4})\s+(?P<prefix>.+?)\s+(?P<document_number>\S+)\s+"
    r"(?P<document_legal>\S+)\s+(?P<monto_documento>-?[\d,]+\.\d{2})\s+"
    r"\((?P<pct>[\d.]+)\s*%\)\s+(?:RUC\s*[=-]\s*)?(?P<identificacion>\d{8,14})\s+(?P<cliente>.+)$",
    flags=re.IGNORECASE,
)

DESCRIPTOR_RE = re.compile(
    r"^(?P<descripcion>.+?)\s+(?P<monto_comision>-?[\d,]+\.\d{2})(?:\s+(?P<cliente_prefijo>.+))?$",
    flags=re.IGNORECASE,
)


class CrecerLiquidationProfile(GenericLiquidationProfile):
    def __init__(self) -> None:
        super().__init__(
            profile_id="crecer_liquidation",
            insurer="CRECER",
            display_name="Crecer Liquidacion",
            keywords=("CRECER", "LIQUIDACION NUMERO", "TOTAL A COBRAR"),
        )

    def _extract_detail_rows(self, lines: list[str]) -> tuple[list[dict], list[str]]:
        rows: list[dict] = []
        warnings: list[str] = []
        pending_descriptor: str | None = None

        for line in lines:
            if self._skip_line(line):
                continue
            if self._is_total_line(line):
                pending_descriptor = None
                continue

            if re.match(r"^\d{2}/\d{2}/\d{4}\b", line):
                parsed = self._parse_crecer_row(line, pending_descriptor)
                if parsed:
                    rows.append(parsed)
                else:
                    message = f"Fila {self.insurer} no parseada: {line}"
                    if pending_descriptor:
                        message = f"{message} | descriptor={pending_descriptor}"
                    warnings.append(message)
                pending_descriptor = None
                continue

            pending_descriptor = line

        return rows, warnings

    def _parse_crecer_row(self, line: str, descriptor_line: str | None) -> dict | None:
        candidate = normalize_spaces(line)
        match = DATE_LINE_RE.match(candidate)
        if not match:
            return None

        payload = match.groupdict()
        monto_documento = to_decimal_flexible(payload["monto_documento"])
        pct_comision = to_decimal_flexible(payload["pct"])
        monto_comision = (monto_documento * pct_comision / Decimal("100")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        descripcion = payload["prefix"]
        cliente = payload["cliente"]
        descriptor_payload = None

        if descriptor_line:
            descriptor_candidate = normalize_spaces(descriptor_line)
            descriptor_match = DESCRIPTOR_RE.match(descriptor_candidate)
            if descriptor_match:
                descriptor_payload = descriptor_match.groupdict()
                descripcion = descriptor_payload["descripcion"]
                if descriptor_payload.get("cliente_prefijo"):
                    cliente = f"{descriptor_payload['cliente_prefijo']} {cliente}".strip()
                monto_comision = to_decimal_flexible(descriptor_payload["monto_comision"])

        return {
            "fecha_inicio": payload["fecha_inicio"],
            "descripcion": descripcion,
            "document_number": payload["document_number"],
            "document_legal": payload["document_legal"],
            "monto_documento": monto_documento,
            "monto_comision": monto_comision,
            "pct_comision": pct_comision,
            "identificacion": payload["identificacion"],
            "cliente": cliente,
            "raw_line": " | ".join(filter(None, [descriptor_line, candidate])),
        }
