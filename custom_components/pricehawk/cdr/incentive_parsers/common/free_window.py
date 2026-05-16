"""Free / discounted import window rules — Phase 2.11.4 + .10 polish.

Catalog v3 finding: 214 plans across 4 retailers (GloBird, AGL, OVO,
Red) zero-rate or heavily discount imports inside specific time windows.

Phase 2.11.10 polish: optional ``days`` filter to handle weekend-only
windows (Red BCNA Saver / Wildlife Saver). When the eligibility text
contains a day-of-week constraint, only credit slots on matching days.

| Wording (catalog-confirmed)                                       | Rate    |
|-------------------------------------------------------------------|---------|
| "Free electricity between 11am and 2pm everyday"                  | $0/kWh  |
| "$0.00 for consumption between 10am-2pm"                          | $0/kWh  |
| "Free electricity usage applies from 10am to 1pm every day"       | $0/kWh  |
| "$0.06/kWh incl. GST for consumption between 11am-2pm & 12am-6am" | $0.06   |

Math: in-window imports billed at `free_rate` instead of the plan's
normal TOU rate. Caller passes the representative normal import rate
(typically peak rate, since these incentives target high-usage hours)
so the parser can credit the difference.

Known limitation (TODO Phase 2.11.5 polish): we use a single "normal
rate" rather than per-slot TOU lookup. For most affected plans this is
accurate because:
- GloBird ZEROHERO Flex already encodes 11-2pm as 0c in the tariff,
  so normal_rate=0 in window → credit=0, no double-credit.
- OVO Free 3 / AGL Three for Free typically target the SHOULDER or
  PEAK rate, so passing peak gives slightly conservative under-credit
  for shoulder slots (tolerable, ~$5-15/yr error).
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal


# Match either "$X.XX[/kWh]" or bare "Free electricity" before "between/from"
# Captures rate (0 if absent) and one OR two windows (joined by &).
RATE_RE = re.compile(
    r"(?:\$(?P<rate>[\d.]+)(?:/kWh)?(?:\s+(?:incl?\.?\s*GST))?|"
    r"(?P<freeword>free\s+(?:electricity|usage|consumption)|"
    r"(?:usage\s+)?charges?\s+(?:will\s+be\s+)?waived))",
    re.I,
)
WINDOW_RE = re.compile(
    r"(?:between|from)\s+"
    r"(?P<start1>\d{1,2}(?::\d{2})?\s*(?:am|pm))"
    r"\s*(?:-|–|—|to|and)\s*"
    r"(?P<end1>\d{1,2}(?::\d{2})?\s*(?:am|pm))"
    r"(?:\s*&\s*"
    r"(?P<start2>\d{1,2}(?::\d{2})?\s*(?:am|pm))"
    r"\s*(?:-|–|—|to|and)\s*"
    r"(?P<end2>\d{1,2}(?::\d{2})?\s*(?:am|pm)))?",
    re.I,
)

# Phase 2.11.10 polish — day-of-week filter. Matches weekend-only and
# weekday-only constraints in Red BCNA Saver / Wildlife Saver wordings.
WEEKEND_RE = re.compile(
    r"\b(?:weekends?\s+only|saturday\s+and\s+sunday|sat\s*&\s*sun|"
    r"on\s+weekends?)\b",
    re.I,
)
WEEKDAY_RE = re.compile(
    r"\b(?:weekdays?\s+only|monday\s+to\s+friday|mon\s*[-–]\s*fri|"
    r"on\s+weekdays?)\b",
    re.I,
)
# Python datetime.weekday(): Mon=0..Sun=6
WEEKEND_DAYS = (5, 6)
WEEKDAY_DAYS = (0, 1, 2, 3, 4)


def _hh_token_to_minutes(tok: str) -> int:
    """'11am', '11:30am', '12pm' → minutes from midnight."""
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


def parse_rule(eligibility: str) -> dict | None:
    """Extract free/discounted import window rule from eligibility text.

    Returns None if no match. Returns
      ``{"rate_c_per_kwh": Decimal,
         "windows": [(start_min, end_min), ...],
         "days": list[int] | None,
         "source": str}``
    on match. ``rate_c_per_kwh`` is in inc-GST cents (0 for free).
    ``days`` is None when rule applies every day, or a tuple of
    datetime.weekday() integers (Mon=0..Sun=6) for restricted days.
    """
    if not eligibility:
        return None

    rate_match = RATE_RE.search(eligibility)
    window_match = WINDOW_RE.search(eligibility)
    if not (rate_match and window_match):
        return None

    if rate_match.group("freeword"):
        rate = Decimal("0")
    else:
        # "$0.06/kWh" → 0.06 AUD = 6 cents. "$0.00" → 0.
        rate_aud = _decimal(rate_match.group("rate"))
        rate = rate_aud * Decimal("100")

    windows = [(
        _hh_token_to_minutes(window_match.group("start1")),
        _hh_token_to_minutes(window_match.group("end1")),
    )]
    if window_match.group("start2"):
        windows.append((
            _hh_token_to_minutes(window_match.group("start2")),
            _hh_token_to_minutes(window_match.group("end2")),
        ))

    # Phase 2.11.10 polish — extract weekend/weekday day filter.
    days: tuple[int, ...] | None = None
    if WEEKEND_RE.search(eligibility):
        days = WEEKEND_DAYS
    elif WEEKDAY_RE.search(eligibility):
        days = WEEKDAY_DAYS

    return {
        "rate_c_per_kwh": rate,
        "windows": windows,
        "days": days,
        "source": eligibility[:200],
    }


def _slot_minutes(ts_local: str) -> int:
    local_dt = datetime.fromisoformat(ts_local)
    return local_dt.hour * 60 + local_dt.minute


def _slot_in_any_window(ts_local: str, windows: list[tuple[int, int]]) -> bool:
    """True if slot's local clock falls in ANY of the rule's windows.

    End-exclusive (matches evaluator's `slot_in_window`). Wrap-around
    windows (end < start, e.g. 22:00-02:00) handled by splitting the
    check to either end-of-day or start-of-day inclusion.
    """
    minutes = _slot_minutes(ts_local)
    for start, end in windows:
        if end < start:
            if minutes >= start or minutes < end:
                return True
        else:
            if start <= minutes < end:
                return True
    return False


def _slot_matches_days(ts_local: str, days: tuple[int, ...] | None) -> bool:
    """True if slot's weekday is in the allowed-days tuple. None = any day."""
    if days is None:
        return True
    dt = datetime.fromisoformat(ts_local)
    return dt.weekday() in days


def apply_rule(
    rule: dict,
    slots: list[dict],
    breakdown,
    *,
    normal_import_rate_c_per_kwh_inc_gst: Decimal,
) -> None:
    """Credit `(normal - free_rate) × in-window imports` to incentive total.

    Args:
      rule: dict from `parse_rule()`.
      slots: list of slot dicts with ``ts_local`` and ``grid_import_kwh``.
      breakdown: ``CostBreakdown`` instance.
      normal_import_rate_c_per_kwh_inc_gst: representative normal rate
        the user would pay outside the free window (typically peak).
        If equal to or less than the free rate, no credit is applied.

    No-op when normal rate ≤ free rate (avoids negative credits when
    the tariff already encodes the discount).
    """
    free_aud = rule["rate_c_per_kwh"] / Decimal("100")
    normal_aud = normal_import_rate_c_per_kwh_inc_gst / Decimal("100")
    delta_aud = normal_aud - free_aud
    if delta_aud <= 0:
        return  # tariff already discounted; nothing to credit

    days = rule.get("days")
    total_kwh = Decimal("0")
    for slot in slots:
        if not _slot_in_any_window(slot["ts_local"], rule["windows"]):
            continue
        if not _slot_matches_days(slot["ts_local"], days):
            continue
        imp = _decimal(slot.get("grid_import_kwh", 0))
        if imp <= 0:
            continue
        breakdown.incentive_aud_inc_gst -= imp * delta_aud
        total_kwh += imp

    if total_kwh > 0:
        windows_str = " & ".join(
            f"{s//60:02d}:{s%60:02d}-{e//60:02d}:{e%60:02d}"
            for s, e in rule["windows"]
        )
        breakdown.trace.append({
            "incentive": "free_window",
            "free_rate_c_per_kwh": float(rule["rate_c_per_kwh"]),
            "normal_rate_c_per_kwh": float(normal_import_rate_c_per_kwh_inc_gst),
            "credited_kwh": float(total_kwh),
            "windows": windows_str,
        })


def parse_from_incentives(incentives: list[dict]) -> list[dict]:
    """Walk a plan's ``incentives[]`` and extract any free-window rules.

    Returns a list (a plan may ship multiple windowed-discount rules,
    e.g. GloBird Nine-hour low EV rate has two non-contiguous windows
    in a single rule, OR a plan could combine 'Free 3' + 'Free 6'
    incentives — both surface here).
    """
    out: list[dict] = []
    for inc in incentives or []:
        for field in ("eligibility", "description"):
            text = (inc.get(field) or "").strip()
            if not text:
                continue
            rule = parse_rule(text)
            if rule:
                rule["source_displayName"] = inc.get("displayName") or ""
                out.append(rule)
                break  # one rule per incentive
    return out
