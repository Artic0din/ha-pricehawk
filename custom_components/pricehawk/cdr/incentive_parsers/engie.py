"""ENGIE Australia incentive parser — Phase 2.11.5 + 2.11.6.

Catalog v3 findings for ENGIE:
- 165 of 165 ENGIE plans ship "EV Plan" overnight rate override:
  "$0.08/kWh between midnight and 7am. Does not apply to controlled
  loads."  → ev_offpeak.py handles this.
- 687 of 687 ENGIE PowerResponse VPP plans ship a $15/month battery-VPP
  rebate. Opt-in only (user must enroll their Powerwall/battery via the
  ENGIE PowerResponse onboarding). Phase 2.11.5 defers VPP — needs
  config-flow toggle to flip user-side opt-in state.

This file ships Phase 2.11.6 only. VPP rebate adds to this same file
once Phase 2.11.5 lands its config-flow toggle pattern.

Brand slug: `engie-au` (catalog-confirmed via CDR brand registry).
"""
from __future__ import annotations

from typing import Callable

from .common import peak_import_rate_c_per_kwh_inc_gst
from .common.ev_offpeak import (
    apply_rule as _apply_ev_offpeak,
    parse_from_incentives as _parse_ev_offpeak,
)
from .common.vpp_rebate import (
    apply_rule as _apply_vpp,
    parse_from_incentives as _parse_vpp,
)


def parse_rules(plan_data: dict, entry_options: dict | None = None) -> dict:
    elec = plan_data.get("electricityContract") or {}
    opts = entry_options or {}
    rules: dict = {}
    evs = _parse_ev_offpeak(elec.get("incentives") or [])
    if evs:
        rules["ev_offpeak"] = evs
    # Phase 2.12.1: opt-in batteries_enrolled flows through entry_options.
    from . import safe_int
    batteries = safe_int(opts.get("vpp_batteries_enrolled"))
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
    breakdown.notes.append(f"engie parser hits: {list(rules.keys())}")
    peak_rate = peak_import_rate_c_per_kwh_inc_gst(plan_data)
    if "ev_offpeak" in rules:
        for ev in rules["ev_offpeak"]:
            _apply_ev_offpeak(
                ev, slots, breakdown,
                normal_import_rate_c_per_kwh_inc_gst=peak_rate,
            )
    if "vpp" in rules:
        # Default batteries_enrolled=0 → no-op until user opts in.
        for vpp_rule in rules["vpp"]:
            _apply_vpp(vpp_rule, slots, breakdown)
