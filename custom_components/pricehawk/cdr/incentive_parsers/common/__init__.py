"""Shared incentive-rule helpers used by per-retailer parser files.

Each helper in this package is retailer-agnostic. It extracts a rule
from CDR free-text and applies math to a CostBreakdown. Per-retailer
modules (agl.py, globird.py, origin.py, etc.) wire these helpers up
based on the specific incentive patterns their retailer publishes.

See scripts/CDR_INCENTIVE_CATALOG.md for the catalog of incentive
shapes observed across all 78 AU energy retailers.
"""
from __future__ import annotations

from decimal import Decimal


GST_FACTOR = Decimal("1.10")


def base_fit_c_per_kwh_inc_gst(plan_data: dict) -> Decimal:
    """Read the first solarFeedInTariff[] rate as inc-GST cents/kWh.

    CDR `unitPrice` is ex-GST per spec; multiply by 110 (×100 for cents,
    ×1.10 for GST) to get the inc-GST cents/kWh that incentive parsers
    use for their delta calculations.

    Returns Decimal("0") if no FIT configured (parsers will then credit
    the FULL tier1 rate, not just a delta).
    """
    elec = plan_data.get("electricityContract") or {}
    for fit in (elec.get("solarFeedInTariff") or []):
        utype = fit.get("tariffUType")
        if utype == "singleTariff":
            rates = (fit.get("singleTariff") or {}).get("rates") or []
        elif utype == "timeVaryingTariffs":
            tvts = fit.get("timeVaryingTariffs") or []
            rates = (tvts[0].get("rates") or []) if tvts else []
        else:
            continue
        if rates:
            unit_price = rates[0].get("unitPrice")
            if unit_price is not None:
                return Decimal(str(unit_price)) * Decimal("100") * GST_FACTOR
    return Decimal("0")
