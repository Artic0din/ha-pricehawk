"""Alinta Energy incentive parser — Phase 2.11.2.

Catalog v3 finding: 66 Alinta plans publish tiered FIT as
"Solar Feed-in Tariff" / "Stepped FiT" incentive:
  "This Energy Plan includes a stepped feed-in tariff, where you will
   receive a feed-in of 7c/kWh for the first 10kW exported. For any
   export after that you will obtain Alinta Energy's standard retailer
   feed-in tariff of 0.04c/kWh."

Cap is daily → cap_window: DAY in tiered_fit. The 0.04c/kWh tier-2 rate
is implicit (not parsed) — caller falls back to base FIT from
solarFeedInTariff[]. In practice Alinta sets base FIT to that 0.04c
value so the math is identical.

Phase 2.11.2 ships tiered FIT only — no other Alinta-specific patterns
in v1.5.0 scope.
"""

from __future__ import annotations

from typing import Callable

from .common import base_fit_c_per_kwh_inc_gst
from .common.tiered_fit import apply_rule, parse_from_incentives


def parse_rules(plan_data: dict) -> dict:
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
    del slot_in_window
    rules = parse_rules(plan_data)
    if not rules:
        return
    breakdown.notes.append(f"alinta parser hits: {list(rules.keys())}")
    if "tiered_fit" in rules:
        apply_rule(
            rules["tiered_fit"],
            slots,
            breakdown,
            base_fit_c_per_kwh=base_fit_c_per_kwh_inc_gst(plan_data),
            state_context=_extra.get("state_context"),
        )
