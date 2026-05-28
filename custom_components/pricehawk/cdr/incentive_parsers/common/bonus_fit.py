"""Bonus solar feed-in tariff rules — Phase 2.11.3.

Catalog v3 finding: 90 GloBird ZEROHERO plans publish two stacked
bonus FIT rules in addition to the structural solarFeedInTariff[]:

1. **Uncapped windowed bonus** (Peak solar feed-in, 70 plans):
   "X cents/kWh applies to exports between Yam-Zpm (Local Time)
    everyday." Additive credit on all exports in the window — no
    daily kWh cap. Stacks with base FIT.

2. **Capped windowed bonus** (Super Export Credit, 20 plans):
   "X cents/kWh applies to the first N kWh of exports between
    Yam-Zpm everyday, and is inclusive of any other Feed-in tariff
    as applicable in Energy Plan." Capped at N kWh per day. The
    "inclusive of any other FIT" wording means this REPLACES base
    FIT in the window (not adds), but for Phase 2.11.3 v1 we credit
    additively (DELTA above base) so the math composes with the
    uncapped bonus already credited.

Known gap (TODO Phase 2.11.4 polish): when both bonuses overlap in
time, the user is over-credited by `peak_fit_rate × min(export, cap)`.
For ZEROHERO at 2c Peak FIT × 15 kWh cap × 365 days = $109.50/yr
maximum over-credit. Real-world: most users export <15kWh in 6-9pm
window so the actual error is smaller (~$5-30/yr).

Math for ZEROHERO with base FIT ≈0c:
- 4-6pm: Peak FIT 2c → 2c total ✓
- 6-9pm first 15kWh: Peak FIT 2c + Super Export 13c (=15-2) = 15c ✓
- 6-9pm beyond 15kWh: Peak FIT 2c only = 2c ✓
- 9-11pm: Peak FIT 2c → 2c total ✓

Phase 2.11.3 ships Peak FIT additive only. Super Export overlap
adjustment deferred to 2.11.4.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal


# "X cents/kWh applies to exports between Yam-Zpm" (no kWh cap)
UNCAPPED_WINDOW_RE = re.compile(
    r"(?P<cents>[\d.]+)\s*c(?:ents)?/kWh\s+applies?\s+to\s+(?:any\s+)?"
    r"exports?\s+between\s+(?P<start>\d{1,2}(?::\d{2})?\s*(?:am|pm))[-\s]+"
    r"(?P<end>\d{1,2}(?::\d{2})?\s*(?:am|pm))",
    re.I,
)

# "X cents/kWh applies to the first N kWh of exports between Yam-Zpm"
CAPPED_WINDOW_RE = re.compile(
    r"(?P<cents>[\d.]+)\s*c(?:ents)?/kWh\s+applies?\s+to\s+the\s+first\s+"
    r"(?P<kwh>[\d.]+)\s+kWh\s+of\s+exports?\s+between\s+"
    r"(?P<start>\d{1,2}(?::\d{2})?\s*(?:am|pm))[-\s]+"
    r"(?P<end>\d{1,2}(?::\d{2})?\s*(?:am|pm))",
    re.I,
)


def _hh_token_to_minutes(tok: str) -> int:
    """'6pm', '6:30am', '12pm' → minutes from midnight. Public for tests."""
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", tok.strip(), re.I)
    if not m:
        raise ValueError(f"can't parse time token {tok!r}")
    h = int(m.group(1)) % 12
    if m.group(3).lower() == "pm":
        h += 12
    minute = int(m.group(2)) if m.group(2) else 0
    return h * 60 + minute


def _decimal(v) -> Decimal:
    if v is None:
        return Decimal("0")
    return Decimal(str(v))


def parse_uncapped_window(eligibility: str) -> dict | None:
    """Extract uncapped windowed bonus FIT (Peak solar feed-in pattern).

    Returns None if no match. CAPPED variant takes precedence — caller
    should check `parse_capped_window` first to avoid false positives
    on eligibility texts that match both patterns.
    """
    if not eligibility or CAPPED_WINDOW_RE.search(eligibility):
        return None
    m = UNCAPPED_WINDOW_RE.search(eligibility)
    if not m:
        return None
    return {
        "bonus_c_per_kwh": _decimal(m.group("cents")),
        "start_min": _hh_token_to_minutes(m.group("start")),
        "end_min": _hh_token_to_minutes(m.group("end")),
        "source": eligibility[:200],
    }


def parse_capped_window(eligibility: str) -> dict | None:
    """Extract capped windowed bonus FIT (Super Export Credit pattern)."""
    if not eligibility:
        return None
    m = CAPPED_WINDOW_RE.search(eligibility)
    if not m:
        return None
    return {
        "bonus_c_per_kwh": _decimal(m.group("cents")),
        "cap_kwh_per_day": _decimal(m.group("kwh")),
        "start_min": _hh_token_to_minutes(m.group("start")),
        "end_min": _hh_token_to_minutes(m.group("end")),
        "source": eligibility[:200],
    }


def _slot_minutes(ts_local: str) -> int:
    local_dt = datetime.fromisoformat(ts_local)
    return local_dt.hour * 60 + local_dt.minute


def apply_uncapped_window(rule: dict, slots: list[dict], breakdown) -> None:
    """Credit `bonus_c_per_kwh` on all exports in the time window.

    Additive — does NOT subtract base FIT. Treat as a stacking incentive
    on top of whatever the evaluator already credited from
    solarFeedInTariff[].
    """
    rate_aud = rule["bonus_c_per_kwh"] / Decimal("100")
    total_kwh = Decimal("0")
    for slot in slots:
        if not (rule["start_min"] <= _slot_minutes(slot["ts_local"]) < rule["end_min"]):
            continue
        exp = _decimal(slot.get("grid_export_kwh", 0) or slot.get("solar_export_kwh", 0))
        if exp <= 0:
            continue
        breakdown.incentive_aud_inc_gst -= exp * rate_aud
        total_kwh += exp
    if total_kwh > 0:
        breakdown.trace.append(
            {
                "incentive": "bonus_fit_uncapped_window",
                "rate_c_per_kwh": float(rule["bonus_c_per_kwh"]),
                "credited_kwh": float(total_kwh),
                "window": f"{rule['start_min'] // 60:02d}:00-{rule['end_min'] // 60:02d}:00",
            }
        )


def apply_capped_window(
    rule: dict,
    slots: list[dict],
    breakdown,
    *,
    overlap_uncapped_rate_c_per_kwh: Decimal = Decimal("0"),
) -> None:
    """Credit `bonus_c_per_kwh` on first `cap_kwh_per_day` exports in window.

    Cap resets at local midnight.

    Phase 2.11.10 overlap fix: when an uncapped bonus FIT also credits
    slots inside this window (e.g., GloBird ZEROHERO Peak FIT 4-11pm 2c
    overlapping Super Export 6-9pm 15c), the catalog "inclusive of any
    other Feed-in tariff" wording means the capped rate REPLACES the
    uncapped rate inside the cap. Caller passes the overlapping
    uncapped rate; we subtract it from the per-kWh capped rate so net
    credit on first-N-kWh = capped_rate, not capped_rate +
    uncapped_rate.

    Math:
      net_capped_rate = capped_rate - overlap_uncapped_rate
      → after uncapped already credited overlap_uncapped_rate on the
        same kWh, total = uncapped + (capped - uncapped) = capped ✓

    If overlap_uncapped_rate_c_per_kwh is 0 (no overlap), behaviour is
    unchanged from Phase 2.11.3.
    """
    effective_rate_c = rule["bonus_c_per_kwh"] - overlap_uncapped_rate_c_per_kwh
    if effective_rate_c <= 0:
        # Uncapped already covers what capped would pay — no incremental
        # credit. Skip the trace entry too.
        return
    rate_aud = effective_rate_c / Decimal("100")
    cap = rule["cap_kwh_per_day"]

    by_day: dict[str, list[dict]] = {}
    for slot in slots:
        by_day.setdefault(slot["ts_local"][:10], []).append(slot)

    total_credited_kwh = Decimal("0")
    for _day, day_slots in sorted(by_day.items()):
        day_credited = Decimal("0")
        for slot in day_slots:
            if not (rule["start_min"] <= _slot_minutes(slot["ts_local"]) < rule["end_min"]):
                continue
            exp = _decimal(slot.get("grid_export_kwh", 0) or slot.get("solar_export_kwh", 0))
            if exp <= 0:
                continue
            remaining = cap - day_credited
            if remaining <= 0:
                break
            credit_kwh = min(exp, remaining)
            breakdown.incentive_aud_inc_gst -= credit_kwh * rate_aud
            day_credited += credit_kwh
        total_credited_kwh += day_credited

    if total_credited_kwh > 0:
        breakdown.trace.append(
            {
                "incentive": "bonus_fit_capped_window",
                "rate_c_per_kwh": float(rule["bonus_c_per_kwh"]),
                "effective_rate_c_per_kwh": float(effective_rate_c),
                "cap_kwh_per_day": float(cap),
                "credited_kwh": float(total_credited_kwh),
                "window": f"{rule['start_min'] // 60:02d}:00-{rule['end_min'] // 60:02d}:00",
            }
        )


def parse_from_incentives(incentives: list[dict]) -> dict:
    """Walk a plan's ``incentives[]`` and extract any bonus FIT rules.

    Returns ``{"uncapped": [...], "capped": [...]}`` with each list
    holding parsed rule dicts. Both fields are always present so
    callers can iterate without key checks. Multiple rules per type
    supported (a plan could ship two different windowed bonuses).
    """
    out: dict = {"uncapped": [], "capped": []}
    for inc in incentives or []:
        for field in ("eligibility", "description"):
            text = (inc.get(field) or "").strip()
            if not text:
                continue
            capped = parse_capped_window(text)
            if capped:
                capped["source_displayName"] = inc.get("displayName") or ""
                out["capped"].append(capped)
                break  # one rule per incentive
            uncapped = parse_uncapped_window(text)
            if uncapped:
                uncapped["source_displayName"] = inc.get("displayName") or ""
                out["uncapped"].append(uncapped)
                break
    return out
