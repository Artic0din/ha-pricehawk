"""EnergyAustralia incentive parser — Phase 2.11.2 + 2.11.5.

Catalog v3 finding: 20 EA "Solar Max" plans + ~600 with VPP rebates.

Phase 2.11.2 shipped TIERED FIT (Solar Max). Phase 2.11.5 adds
PowerResponse VPP rebate detection (opt-in via batteries_enrolled
options-flow field, default 0 = no credit).
"""
from __future__ import annotations

from typing import Callable

from .common import base_fit_c_per_kwh_inc_gst
from .common.tiered_fit import apply_rule as _apply_tiered_fit
from .common.tiered_fit import parse_from_incentives as _parse_tiered_fit
from .common.vpp_rebate import (
    apply_rule as _apply_vpp,
    parse_from_incentives as _parse_vpp,
)


def parse_rules(plan_data: dict, entry_options: dict | None = None) -> dict:
    elec = plan_data.get("electricityContract") or {}
    opts = entry_options or {}
    rules: dict = {}
    rule = _parse_tiered_fit(elec.get("incentives") or [])
    if rule:
        rules["tiered_fit"] = rule
    batteries = int(opts.get("vpp_batteries_enrolled", 0) or 0)
    vpp = _parse_vpp(elec.get("incentives") or [], batteries_enrolled=batteries)
    if vpp:
        rules["vpp"] = vpp
    return rules


def apply(
    plan_data: dict,
    slots: list[dict],
    breakdown,
    *,
    slot_in_window: Callable,
    entry_options: dict | None = None,
) -> None:
    del slot_in_window
    rules = parse_rules(plan_data, entry_options=entry_options)
    if not rules:
        return
    breakdown.notes.append(f"energyaustralia parser hits: {list(rules.keys())}")
    if "tiered_fit" in rules:
        _apply_tiered_fit(
            rules["tiered_fit"], slots, breakdown,
            base_fit_c_per_kwh=base_fit_c_per_kwh_inc_gst(plan_data),
        )
    if "vpp" in rules:
        for vpp_rule in rules["vpp"]:
            _apply_vpp(vpp_rule, slots, breakdown)
