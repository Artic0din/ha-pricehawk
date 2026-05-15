"""Per-retailer incentive parser registry.

Hardcoded dict per locked decision §I.3 — NOT decorator magic, NOT
filesystem scan. Add a retailer = edit this file.

v1.5.0 retailers (Phase 2.6):
  - globird: ZEROHERO + Super Export + 3-for-Free (full math)
  - agl: Solar Savers bonus FIT + Three for Free (presence detect only)

v1.5.1 retailers (Phase 2.11.2 — tiered FIT activation):
  - origin: Solar feed-in tariffs (period-averaged tiered FIT)
  - alinta: Solar Feed-in Tariff / Stepped FiT (daily tiered FIT)
  - energyaustralia: Solar Max + PowerResponse VPP (tiered FIT only here)

Each parser is `(plan_data, slots, breakdown, *, slot_in_window)`:
  - plan_data: unwrapped CDR PlanDetail dict (data.* contents)
  - slots: list of consumption slot dicts
  - breakdown: CostBreakdown instance — mutate `incentive_aud_inc_gst`
  - slot_in_window: dependency-injected window matcher from evaluator
    (avoids circular import + lets tests override semantics)

Parsers MUST express credits in INC-GST DOLLARS. PDF rate phrases
("$1/Day", "15 cents/kWh") are inc-GST per legacy convention.
"""
from __future__ import annotations

from typing import Callable

from .agl import apply as _apply_agl
from .alinta import apply as _apply_alinta
from .energyaustralia import apply as _apply_energyaustralia
from .engie import apply as _apply_engie
from .globird import apply as _apply_globird
from .origin import apply as _apply_origin
from .ovo import apply as _apply_ovo
from .red import apply as _apply_red

# Hardcoded registry. Keys are CDR `brand` slugs (lowercase).
RETAILER_PARSERS: dict[str, Callable] = {
    "globird": _apply_globird,
    "agl": _apply_agl,
    "origin": _apply_origin,
    "alinta": _apply_alinta,
    "energyaustralia": _apply_energyaustralia,
    "engie-au": _apply_engie,
    "ovo-energy": _apply_ovo,
    "red-energy": _apply_red,
}


def apply_retailer_incentives(
    plan_data: dict,
    slots: list[dict],
    breakdown,  # CostBreakdown — forward ref to avoid circular import
    *,
    slot_in_window: Callable,
    entry_options: dict | None = None,
) -> None:
    """Dispatch to the retailer-specific parser based on CDR `brand`.

    ``entry_options`` (Phase 2.12.1) carries user-side opt-in fields the
    parsers can't infer from plan data alone:
      - ``ovo_interest_balance_aud`` (Decimal/float, default 0)
      - ``vpp_batteries_enrolled`` (int, default 0)

    Parsers ignore unknown keys; missing keys default to "not opted in"
    (math no-ops).
    """
    brand = (plan_data.get("brand", "") or "").lower()
    parser = RETAILER_PARSERS.get(brand)
    if parser is None:
        return
    parser(
        plan_data, slots, breakdown,
        slot_in_window=slot_in_window,
        entry_options=entry_options or {},
    )
