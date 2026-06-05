"""Origin Energy incentive parser — Phase 2.11.2.

Catalog v3 finding: 84 Origin plans publish tiered FIT as
"Solar feed-in tariffs" incentive with eligibility text:
  "Origin offers 12 cents per kWh until a daily export limit of
   8 kWh is reached. The daily export limit is averaged across your
   billing period (calculated by multiplying the number of days in
   your billing period by your daily export limit of 8)"

Cap is monthly-averaged → cap_window: PERIOD in tiered_fit. Real cap
across a billing period = `8 × num_days_in_period` kWh.

No other Origin-specific patterns extracted in v1.5.0 — the rest of
Origin's incentives are loyalty / sign-up / GreenPower (out-of-scope
per user direction). Phase 2.11.2 ships tiered FIT only.
"""

from __future__ import annotations

from typing import Callable

from .common import base_fit_c_per_kwh_inc_gst
from .common.tiered_fit import apply_rule, parse_from_incentives


def parse_rules(plan_data: dict) -> dict:
    """Extract structured rule dicts from Origin incentives free-text."""
    elec = plan_data.get("electricityContract") or {}
    rules: dict = {}
    rule = parse_from_incentives(elec.get("incentives") or [])
    if rule:
        rules["tiered_fit"] = rule
    return rules


def apply(
    plan_data: dict,
    slots: list[dict],
    breakdown,
    *,
    slot_in_window: Callable,
    **_extra,
) -> None:
    """Credit Origin tiered FIT delta to ``breakdown.incentive_aud_inc_gst``."""
    del slot_in_window  # not used by tiered_fit
    rules = parse_rules(plan_data)
    if not rules:
        return
    breakdown.notes.append(f"origin parser hits: {list(rules.keys())}")
    if "tiered_fit" in rules:
        apply_rule(
            rules["tiered_fit"],
            slots,
            breakdown,
            base_fit_c_per_kwh=base_fit_c_per_kwh_inc_gst(plan_data),
            state_context=_extra.get("state_context"),
        )
