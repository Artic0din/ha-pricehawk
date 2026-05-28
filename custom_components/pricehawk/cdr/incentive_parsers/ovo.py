"""OVO Energy incentive parser — Phase 2.11.4 + 2.11.6.

Catalog v3 finding: 38 OVO/MYOB plans publish "Free 3" incentive:
  "Free electricity between 11am and 2pm everyday."

Plus 165 OVO + ENGIE plans publish "EV Off-Peak":
  "$0.045/kWh usage charge between midnight and 6am."

OVO also ships:
- "Interest Rewards" — 3% interest on credit balances (Phase 2.11.7)

Phase 2.11.4 shipped free_window only. Phase 2.11.6 adds EV off-peak.
Interest-on-balance defers to ovo_interest.py (Phase 2.11.7).

Brand slug for both OVO Energy + MYOB powered by OVO is `ovo-energy`
(catalog confirms; MYOB is a co-brand on the same CDR base URI).
"""

from __future__ import annotations

from typing import Callable

from .common import peak_import_rate_c_per_kwh_inc_gst
from .common.ev_offpeak import (
    apply_rule as _apply_ev_offpeak,
    parse_from_incentives as _parse_ev_offpeak,
)
from .common.free_window import (
    apply_rule as _apply_free_window,
    parse_from_incentives as _parse_free_windows,
)
from .common.ovo_interest import (
    apply_rule as _apply_ovo_interest,
    parse_from_incentives as _parse_ovo_interest,
)


def parse_rules(plan_data: dict, entry_options: dict | None = None) -> dict:
    elec = plan_data.get("electricityContract") or {}
    opts = entry_options or {}
    rules: dict = {}
    fws = _parse_free_windows(elec.get("incentives") or [])
    if fws:
        rules["free_windows"] = fws
    evs = _parse_ev_offpeak(elec.get("incentives") or [])
    if evs:
        rules["ev_offpeak"] = evs
    # Phase 2.12.1 + 3.0g (CodeRabbit): defensive Decimal cast for
    # user-supplied opt-in field. Garbage / None / "" → 0 (no credit).
    from . import safe_decimal

    balance = safe_decimal(opts.get("ovo_interest_balance_aud"))
    interest = _parse_ovo_interest(elec.get("incentives") or [], balance_aud=balance)
    if interest:
        rules["interest"] = interest
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
    breakdown.notes.append(f"ovo parser hits: {list(rules.keys())}")
    peak_rate = peak_import_rate_c_per_kwh_inc_gst(plan_data)
    if "free_windows" in rules:
        for fw in rules["free_windows"]:
            _apply_free_window(
                fw,
                slots,
                breakdown,
                normal_import_rate_c_per_kwh_inc_gst=peak_rate,
            )
    if "ev_offpeak" in rules:
        for ev in rules["ev_offpeak"]:
            _apply_ev_offpeak(
                ev,
                slots,
                breakdown,
                normal_import_rate_c_per_kwh_inc_gst=peak_rate,
            )
    if "interest" in rules:
        # Default balance_aud=0 in parser → apply_rule no-ops. Future
        # options-flow patch will populate balance_aud per-user.
        for interest_rule in rules["interest"]:
            _apply_ovo_interest(interest_rule, slots, breakdown)
