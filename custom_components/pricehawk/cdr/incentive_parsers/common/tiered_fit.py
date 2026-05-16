"""Tiered solar feed-in tariff rule — Phase 2.11.

Catalog v3 finding: 210 plans across 5 retailers (Origin, AGL, Alinta,
EnergyAustralia, GloBird) publish "first N kWh at rate1 c/kWh, rest at
rate2 c/kWh" tiered FIT as a free-text incentive instead of structuring
it under `solarFeedInTariff[]`. Without this parser the evaluator misses
the higher tier-1 rate entirely.

Two retailer dialects observed:

1. **Daily cap** (AGL, Alinta, GloBird ZEROHERO-VPP variants):
   "first 10 kWh exported each day at 6c/kWh, then 1.5c/kWh for the rest
   of that day". Cap resets every midnight.

2. **Billing-period cap** (Origin, EnergyAustralia Solar Max):
   "12 cents per kWh until a daily export limit of 8 kWh is reached.
   The daily export limit is averaged across your billing period". Real
   cap is `8 × num_days_in_period` kWh, pooled across the whole period.
   Users can over-export early in the month and still hit tier-1 rate
   on later days, as long as the period total stays under the pool.

Both dialects credit to `breakdown.incentive_aud_inc_gst` as the DELTA
above base FIT. Base FIT is already credited by the core evaluator via
`solarFeedInTariff[]` — this parser only adds the top-up.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

# Rate-first: "X cents/kWh ... until N kWh" (Alinta, Origin)
# Allow optional filler between the trigger word and the cap number to
# catch Origin's "until a daily export limit of 8 kWh" wording.
RATE_FIRST_RE = re.compile(
    r"(?P<rate1>[\d.]+)\s*c(?:ents)?(?:[\s/]+(?:per\s+)?kWh)?\s+"
    r"(?:until|for\s+the\s+first|for\s+a?\s*daily\s+export\s+limit\s+of)"
    r"[^.]{0,60}?(?P<cap>[\d.]+)\s*kW(?:h)?",
    re.I | re.S,
)

# Quantity-first: "first N kWh ... at X c/kWh ... then Y c/kWh" (AGL)
QUANTITY_FIRST_RE = re.compile(
    r"first\s+(?P<cap>[\d.]+)\s*kW(?:h)?\s+(?:exported\s+)?"
    r"(?:each\s+day|per\s+day|daily)?[^.]{0,80}?"
    r"(?P<rate1>[\d.]+)\s*c(?:ents)?(?:[\s/]+(?:per\s+)?kWh).{0,80}?"
    r"(?:then|after|remaining)[^.]{0,80}?"
    r"(?P<rate2>[\d.]+)\s*c(?:ents)?(?:[\s/]+(?:per\s+)?kWh)",
    re.I | re.S,
)

# Period detector — words that signal billing-period pooling vs strict daily
PERIOD_AVERAGED_RE = re.compile(
    r"averaged\s+across\s+your\s+billing\s+period|"
    r"averaged\s+by\s+dividing.+?billing\s+period",
    re.I | re.S,
)


CapWindow = Literal["DAY", "PERIOD"]


def _decimal(v) -> Decimal:
    if v is None:
        return Decimal("0")
    return Decimal(str(v))


def parse_rule(eligibility: str) -> dict | None:
    """Extract a tiered-FIT rule from one incentive's free-text.

    Returns ``None`` if the text doesn't match either dialect. Returns
    ``{"tier1_c_per_kwh": Decimal, "cap_kwh": Decimal,
       "tier2_c_per_kwh": Decimal | None, "cap_window": "DAY"|"PERIOD",
       "source": str}`` otherwise.

    Tier-2 rate is ``None`` for rate-first matches that don't specify
    an explicit second rate — caller falls back to base FIT.
    """
    if not eligibility:
        return None

    cap_window: CapWindow = "PERIOD" if PERIOD_AVERAGED_RE.search(eligibility) else "DAY"

    m = QUANTITY_FIRST_RE.search(eligibility)
    if m:
        return {
            "tier1_c_per_kwh": _decimal(m.group("rate1")),
            "cap_kwh": _decimal(m.group("cap")),
            "tier2_c_per_kwh": _decimal(m.group("rate2")),
            "cap_window": cap_window,
            "source": eligibility[:200],
        }

    m = RATE_FIRST_RE.search(eligibility)
    if m:
        return {
            "tier1_c_per_kwh": _decimal(m.group("rate1")),
            "cap_kwh": _decimal(m.group("cap")),
            "tier2_c_per_kwh": None,  # caller uses base FIT
            "cap_window": cap_window,
            "source": eligibility[:200],
        }

    return None


def apply_rule(
    rule: dict,
    slots: list[dict],
    breakdown,
    *,
    base_fit_c_per_kwh: Decimal,
) -> None:
    """Credit tier-1 export above base FIT to ``incentive_aud_inc_gst``.

    Args:
      rule: dict from ``parse_rule()``.
      slots: list of slot dicts with ``ts_local`` (ISO local) and
        either ``grid_export_kwh`` or ``solar_export_kwh``.
      breakdown: ``CostBreakdown`` instance; mutated in-place. The
        ``incentive_aud_inc_gst`` field is DECREASED (more negative =
        bigger user credit, matching the AGL/GloBird convention).
      base_fit_c_per_kwh: Base FIT already credited by the core
        evaluator from ``solarFeedInTariff[]``. Used to compute the
        delta on tier-1 exports.

    Math semantics:
      DAY window — cap resets every local midnight. Sum exports per
      day, credit (min(daily_export, cap) × (tier1 - base_fit)) plus
      the tier-2 delta on any overflow.

      PERIOD window — cap pooled across all slots passed in. Multiply
      cap by number of distinct days observed to honour the
      "8 kWh averaged across billing period" wording.

    Numerics: all math in Decimal. Convert c/kWh → AUD/kWh via /100.
    """
    tier1_aud = rule["tier1_c_per_kwh"] / Decimal("100")
    tier2_c = rule["tier2_c_per_kwh"]
    tier2_aud = tier2_c / Decimal("100") if tier2_c is not None else None
    base_aud = base_fit_c_per_kwh / Decimal("100")
    cap = rule["cap_kwh"]
    window = rule["cap_window"]

    if window == "PERIOD":
        # KNOWN LIMITATION (CR review, tracked for proper fix):
        # multiplying ``cap`` by distinct days in slots is correct ONLY
        # when the evaluation window spans whole billing periods. A
        # 7-day eval against a monthly period under-credits by a factor
        # of ~4×; a partial-into-next-period eval over-credits. Correct
        # fix needs ``electricityContract.billingPeriod`` (ISO-8601
        # duration) parsed at plan-load time, then ``effective_cap =
        # cap * ceil(slot_days / period_days) * period_days`` — out of
        # scope for the CR-fix commit, tracked in a follow-up issue.
        days = {slot["ts_local"][:10] for slot in slots}
        effective_cap = cap * Decimal(len(days)) if days else cap
    else:
        effective_cap = cap

    by_day: dict[str, list[dict]] = {}
    for slot in slots:
        by_day.setdefault(slot["ts_local"][:10], []).append(slot)

    period_credited = Decimal("0")
    period_overflow = Decimal("0")

    for _day, day_slots in sorted(by_day.items()):
        day_export = Decimal("0")
        for slot in day_slots:
            exp = _decimal(
                slot.get("grid_export_kwh", 0) or slot.get("solar_export_kwh", 0)
            )
            if exp > 0:
                day_export += exp

        if day_export <= 0:
            continue

        if window == "DAY":
            credited = min(day_export, cap)
            overflow = max(Decimal("0"), day_export - cap)
        else:
            remaining = effective_cap - period_credited
            credited = min(day_export, max(Decimal("0"), remaining))
            overflow = day_export - credited

        # Delta credit on tier-1 export: tier1 - base_fit
        if credited > 0:
            delta1 = (tier1_aud - base_aud) * credited
            breakdown.incentive_aud_inc_gst -= delta1
            period_credited += credited

        # Tier-2 delta only if explicit rate provided AND differs from base
        if overflow > 0 and tier2_aud is not None and tier2_aud != base_aud:
            delta2 = (tier2_aud - base_aud) * overflow
            breakdown.incentive_aud_inc_gst -= delta2
            period_overflow += overflow

    if period_credited > 0 or period_overflow > 0:
        breakdown.trace.append({
            "incentive": "tiered_fit",
            "cap_window": window,
            "tier1_kwh": float(period_credited),
            "tier1_c_per_kwh": float(rule["tier1_c_per_kwh"]),
            "tier2_kwh": float(period_overflow),
            "tier2_c_per_kwh": float(tier2_c) if tier2_c is not None else None,
        })


def parse_from_incentives(incentives: list[dict]) -> dict | None:
    """Walk a plan's ``incentives[]`` and return the first tiered-FIT
    rule found. Checks both ``description`` and ``eligibility`` fields
    because retailers publish the math in either slot.
    """
    for inc in incentives or []:
        for field in ("eligibility", "description"):
            rule = parse_rule((inc.get(field) or "").strip())
            if rule:
                rule["source_displayName"] = inc.get("displayName") or ""
                return rule
    return None
