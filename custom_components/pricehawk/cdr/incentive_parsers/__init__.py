"""Per-retailer incentive parser registry.

Hardcoded dict per locked decision §I.3 — NOT decorator magic, NOT
filesystem scan. Add a retailer = edit this file. v1.5.0 ships
GloBird only (load-bearing); OVO, Flow Power, AGL Three for Free
deferred to v1.5.1 per TODOS.md.

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
from .globird import apply as _apply_globird

# Hardcoded registry. Keys are CDR `brand` slugs (lowercase).
RETAILER_PARSERS: dict[str, Callable] = {
    "globird": _apply_globird,
    "agl": _apply_agl,
}


def apply_retailer_incentives(
    plan_data: dict,
    slots: list[dict],
    breakdown,  # CostBreakdown — forward ref to avoid circular import
    *,
    slot_in_window: Callable,
) -> None:
    """Dispatch to the retailer-specific parser based on CDR `brand`."""
    brand = (plan_data.get("brand", "") or "").lower()
    parser = RETAILER_PARSERS.get(brand)
    if parser is None:
        return
    parser(plan_data, slots, breakdown, slot_in_window=slot_in_window)
