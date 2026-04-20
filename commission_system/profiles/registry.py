from __future__ import annotations

from .avla_liquidation import AvlaLiquidationProfile
from .cesce_liquidation import CesceLiquidationProfile
from .generic_liquidation import GenericLiquidationProfile
from .pacifico_preliquidation import PacificoPreliquidationProfile
from .positiva_boleta import PositivaBoletaProfile
from .protecta_lote import ProtectaLoteProfile
from .qualitas_liquidation import QualitasLiquidationProfile
from .rimac_preliquidation import RimacPreliquidationProfile
from .sanitas_eps import SanitasEpsProfile


PROFILE_REGISTRY = [
    PositivaBoletaProfile(),
    PacificoPreliquidationProfile(),
    RimacPreliquidationProfile(),
    QualitasLiquidationProfile(),
    AvlaLiquidationProfile(),
    SanitasEpsProfile(),
    GenericLiquidationProfile(
        profile_id="crecer_liquidation",
        insurer="CRECER",
        display_name="Crecer Liquidacion",
        keywords=("CRECER", "LIQUIDACION NUMERO", "TOTAL A COBRAR"),
    ),
    GenericLiquidationProfile(
        profile_id="protecta_liquidation",
        insurer="PROTECTA",
        display_name="Protecta Liquidacion",
        keywords=("PROTECTA", "LIQUIDACION NUMERO", "TOTAL A COBRAR"),
    ),
    GenericLiquidationProfile(
        profile_id="sanitas_rotated_liquidation",
        insurer="SANITAS",
        display_name="Sanitas Liquidacion Escaneada",
        keywords=("SANITAS", "LIQUIDACION NUMERO", "TOTAL A COBRAR"),
    ),
    ProtectaLoteProfile(),
    CesceLiquidationProfile(),
]

SUPPORTED_INSURERS = sorted({profile.insurer for profile in PROFILE_REGISTRY})
