"""EnergyAustralia incentive parser — Phase 2.11.2.

Catalog v3 finding: 20 EA "Solar Max" plans + ~600 with VPP rebates.

This parser ships TIERED FIT only in Phase 2.11.2. EA's "Solar Max"
incentive eligibility text doesn't include the rate-and-cap math
verbatim (the rate lives in the structured solarFeedInTariff[] block,
the incentive only describes the averaging window). Parser will
gracefully no-op if the eligibility text doesn't match either dialect.

PowerResponse VPP rebates ship as Phase 2.11.5 (vpp_rebate.py) —
event-driven, opt-in, separate math model.
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
) -> None:
    del slot_in_window
    rules = parse_rules(plan_data)
    if not rules:
        return
    breakdown.notes.append(f"energyaustralia parser hits: {list(rules.keys())}")
    if "tiered_fit" in rules:
        apply_rule(
            rules["tiered_fit"], slots, breakdown,
            base_fit_c_per_kwh=base_fit_c_per_kwh_inc_gst(plan_data),
        )
