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
    for fit in elec.get("solarFeedInTariff") or []:
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


def _all_import_rates_aud_per_kwh_ex_gst(plan_data: dict) -> list[Decimal]:
    """Collect every TOU/single import rate's unitPrice across all blocks."""
    elec = plan_data.get("electricityContract") or {}
    out: list[Decimal] = []
    for tp in elec.get("tariffPeriod") or []:
        if not isinstance(tp, dict):
            continue
        rbut = tp.get("rateBlockUType")
        if not rbut:
            continue
        block = tp.get(rbut)
        if isinstance(block, dict):
            blocks = [block]
        elif isinstance(block, list):
            blocks = block
        else:
            continue
        for b in blocks:
            if not isinstance(b, dict):
                continue
            for rate_entry in b.get("rates") or []:
                up = rate_entry.get("unitPrice") if isinstance(rate_entry, dict) else None
                if up is None:
                    continue
                try:
                    out.append(Decimal(str(up)))
                except Exception:
                    continue
    return out


# When the tariff's lowest TOU rate is at or below this threshold,
# the plan is assumed to already encode the free/discount window
# inside its tariffPeriod (e.g., GloBird ZEROHERO Flex sets OFF_PEAK
# to 0.000001 c/kWh for 11am-2pm). free_window incentives are then
# redundant — applying them would double-credit. Threshold is in
# inc-GST cents per kWh.
TARIFF_ENCODES_FREE_WINDOW_THRESHOLD_C_INC_GST = Decimal("1.0")


def peak_import_rate_c_per_kwh_inc_gst(plan_data: dict) -> Decimal:
    """Representative normal import rate for free_window credit math.

    Returns inc-GST cents/kWh. Returns Decimal("0") when:
    - No rates extractable (parser then no-ops), OR
    - The plan's TOU tariff already encodes a near-free window (min rate
      ≤ TARIFF_ENCODES_FREE_WINDOW_THRESHOLD_C_INC_GST). Returning 0
      makes the free_window parser no-op (since normal ≤ free → no
      credit), avoiding double-credit on plans like GloBird ZEROHERO
      Flex where the 11am-2pm window is in tariffPeriod already.

    For plans without an encoded free window (OVO Free 3 on flat TOU,
    AGL Three for Free on TOU peak/shoulder), returns the MAX rate
    across all TOU blocks — conservative (slightly over-credits for
    shoulder users, but the affected hours are short and the per-yr
    error is bounded at ~$15).
    """
    rates_ex_gst = _all_import_rates_aud_per_kwh_ex_gst(plan_data)
    if not rates_ex_gst:
        return Decimal("0")
    min_rate_inc_gst = min(rates_ex_gst) * Decimal("100") * GST_FACTOR
    if min_rate_inc_gst <= TARIFF_ENCODES_FREE_WINDOW_THRESHOLD_C_INC_GST:
        return Decimal("0")
    return max(rates_ex_gst) * Decimal("100") * GST_FACTOR
