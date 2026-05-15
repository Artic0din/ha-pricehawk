"""OVO Energy incentive parser — Phase 2.11.4.

Catalog v3 finding: 38 OVO/MYOB plans publish "Free 3" incentive:
  "Free electricity between 11am and 2pm everyday."

OVO also ships:
- "Interest Rewards" — 3% interest on credit balances (Phase 2.11.7)
- "EV Off-Peak" — $0.045/kWh midnight-6am (Phase 2.11.6)

Phase 2.11.4 ships free_window only (Free 3). EV off-peak override and
interest-on-balance defer to dedicated parser modules.

Brand slug for both OVO Energy + MYOB powered by OVO is `ovo-energy`
(catalog confirms; MYOB is a co-brand on the same CDR base URI).
"""
from __future__ import annotations

from typing import Callable

from .common import peak_import_rate_c_per_kwh_inc_gst
from .common.free_window import (
    apply_rule as _apply_free_window,
    parse_from_incentives as _parse_free_windows,
)


def parse_rules(plan_data: dict) -> dict:
    elec = plan_data.get("electricityContract") or {}
    rules: dict = {}
    fws = _parse_free_windows(elec.get("incentives") or [])
    if fws:
        rules["free_windows"] = fws
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
    breakdown.notes.append(f"ovo parser hits: {list(rules.keys())}")
    if "free_windows" in rules:
        peak_rate = peak_import_rate_c_per_kwh_inc_gst(plan_data)
        for fw in rules["free_windows"]:
            _apply_free_window(
                fw, slots, breakdown,
                normal_import_rate_c_per_kwh_inc_gst=peak_rate,
            )
