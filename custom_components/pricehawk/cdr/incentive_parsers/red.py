"""Red Energy incentive parser — Phase 2.11.4.

Catalog v3 finding: 101 Red plans publish "Free Electricity Use Period":
  "Between 12pm and 2pm Saturday and Sunday, your electricity usage
   charges will be waived for any electricity consumed at your Supply
   Address."

This is a weekend-only free window. The free_window parser handles the
hours but doesn't yet enforce day-of-week. For Phase 2.11.4 v1 we credit
all-week (over-counts by ~5/7 = $5-15/yr for typical users) — refining
to weekend-only deferred to Phase 2.11.5.

Red's other incentives (Renewable Matching Promise, Charity donations
to Taronga / BCNA / Rotary, sign-up bonuses) are out-of-scope per the
catalog v3 user-decision (non-cash + one-off + perks dropped).
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
    **_extra,
) -> None:
    del slot_in_window
    rules = parse_rules(plan_data)
    if not rules:
        return
    breakdown.notes.append(f"red parser hits: {list(rules.keys())}")
    if "free_windows" in rules:
        peak_rate = peak_import_rate_c_per_kwh_inc_gst(plan_data)
        for fw in rules["free_windows"]:
            _apply_free_window(
                fw,
                slots,
                breakdown,
                normal_import_rate_c_per_kwh_inc_gst=peak_rate,
            )
