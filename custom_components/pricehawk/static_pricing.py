"""Static-PRD pricing helpers — Phase 7 / PR-4.

Phase 7 PR-4 introduces a per-comparator opt-in between three modes:

- ``off``        — provider not registered.
- ``live_api``   — REST/WebSocket poll using user-supplied API key.
- ``static_prd`` — rates derived from a chosen CDR PlanDetailV2 envelope
                  for the retailer; no API hit.

This module exposes two pure helpers:

- :func:`resolve_pricing_mode` — back-compat-aware mode resolver.
- :func:`evaluate_static_rates` — current-clock-time rate lookup against
  a PRD ``tariffPeriod`` (delegates to ``cdr.evaluator`` window helpers
  so there's a single source of truth for window matching).

Both are sync — they're called from the coordinator's sync tick path.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from .const import (
    ALL_PRICING_MODES,
    PRICING_MODE_LIVE_API,
    PRICING_MODE_OFF,
)

# inc-GST conversion: PRD rates are ex-GST $/kWh. Convert to inc-GST c/kWh
# by multiplying by 1.10 (GST) * 100 (dollars → cents). Matches the
# convention used by cdr.streaming.current_import_rate_c_kwh.
_GST_MULTIPLIER = Decimal("1.10")
_CENTS_PER_DOLLAR = Decimal("100")


def resolve_pricing_mode(
    options: dict[str, Any],
    data: dict[str, Any],
    *,
    mode_key: str,
    legacy_enabled_key: str,
) -> str:
    """Resolve a comparator's current pricing mode with legacy back-compat.

    Resolution order:
        1. ``options[mode_key]`` if present AND in :data:`ALL_PRICING_MODES`.
        2. Legacy: truthy ``options[legacy_enabled_key]`` OR
           ``data[legacy_enabled_key]`` → :data:`PRICING_MODE_LIVE_API`.
        3. Default → :data:`PRICING_MODE_OFF`.

    No write-back migration — callers read the mode every time. Existing
    Phase 2.x entries with ``CONF_<P>_ENABLED=True`` continue working as
    live_api comparators until the user re-runs OptionsFlow.
    """
    explicit = options.get(mode_key)
    if explicit in ALL_PRICING_MODES:
        return explicit

    legacy = options.get(legacy_enabled_key, data.get(legacy_enabled_key))
    if legacy:
        return PRICING_MODE_LIVE_API
    return PRICING_MODE_OFF


def evaluate_static_rates(
    plan_envelope: dict[str, Any] | None,
    now_local: datetime,
) -> tuple[float, float]:
    """Derive ``(import_c_kwh, export_c_kwh)`` from a PRD PlanDetailV2 envelope.

    Returns inc-GST c/kWh rates (matching :class:`CdrPlanProvider`'s
    convention). Falls back to ``(0.0, 0.0)`` if:

    - ``plan_envelope`` is ``None`` or empty.
    - No ``electricityContract.tariffPeriod`` is present.
    - No TOU window matches ``now_local``.

    Static-PRD pricing reflects the FIRST tier of stepped-pricing plans
    (``singleRate`` rates[0].unitPrice); accurate per-tier stepping needs
    live_api mode which tracks per-tick daily kWh against the threshold.
    Lossiness is documented; users wanting precise stepped math must opt
    into live_api.
    """
    if not plan_envelope:
        return (0.0, 0.0)

    plan_data = plan_envelope.get("data", plan_envelope)
    elec = plan_data.get("electricityContract", {}) or {}
    tps = elec.get("tariffPeriod", []) or []
    if not tps:
        return (0.0, 0.0)

    import_ex = _import_rate_ex_gst(tps[0], now_local)
    export_ex = _export_rate_ex_gst(elec, now_local)

    return (
        float(import_ex * _GST_MULTIPLIER * _CENTS_PER_DOLLAR),
        float(export_ex * _GST_MULTIPLIER * _CENTS_PER_DOLLAR),
    )


def _import_rate_ex_gst(tariff_period: dict[str, Any], now_local: datetime) -> Decimal:
    """Return ex-GST $/kWh import rate for ``now_local`` from one tariffPeriod."""
    # Local import — avoid a top-level cycle (cdr/* may evolve independently).
    from .cdr.evaluator import _resolve_tou_rate  # noqa: PLC0415

    if tariff_period.get("rateBlockUType") == "singleRate":
        rates = (tariff_period.get("singleRate") or {}).get("rates", []) or []
        return Decimal(str(rates[0].get("unitPrice", 0))) if rates else Decimal("0")

    tou_rates = tariff_period.get("timeOfUseRates", []) or []
    entry = _resolve_tou_rate(now_local, tou_rates)
    if not entry:
        return Decimal("0")
    rates = entry.get("rates", []) or []
    return Decimal(str(rates[0].get("unitPrice", 0))) if rates else Decimal("0")


def _export_rate_ex_gst(electricity_contract: dict[str, Any], now_local: datetime) -> Decimal:
    """Return ex-GST $/kWh export rate for ``now_local`` from electricityContract."""
    from .cdr.evaluator import slot_in_window  # noqa: PLC0415

    fits = electricity_contract.get("solarFeedInTariff", []) or []
    for fit in fits:
        utype = fit.get("tariffUType")
        if utype == "timeVaryingTariffs":
            for tvt in fit.get("timeVaryingTariffs") or []:
                for tv in tvt.get("timeVariations") or []:
                    if slot_in_window(
                        now_local,
                        tv.get("days", []),
                        tv.get("startTime", "00:00"),
                        tv.get("endTime", "23:59"),
                    ):
                        rates = tvt.get("rates", []) or []
                        if rates:
                            return Decimal(str(rates[0].get("unitPrice", 0)))
        elif utype == "singleTariff":
            st = fit.get("singleTariff") or {}
            rates = st.get("rates", []) or []
            if rates:
                return Decimal(str(rates[0].get("unitPrice", 0)))

    return Decimal("0")
