"""EV off-peak rate override — Phase 2.11.6.

Catalog v3 finding: 165 plans across OVO and ENGIE override the normal
import rate during overnight (typically midnight-6am) with a flat low
rate to incentivise EV charging:

| Wording (catalog-confirmed)                                                  | Rate    |
|------------------------------------------------------------------------------|---------|
| "$0.045/kWh usage charge between midnight and 6am"                           | 4.5c    |
| "$0.08/kWh between midnight and 7am, applied to all import"                  | 8.0c    |
| "Flat $0.10/kWh from 12am to 6am, excludes controlled load"                  | 10c     |

Math: identical to ``free_window`` (in-window imports billed at the EV
rate instead of normal TOU peak/shoulder; credit the delta), but the
catalog phrasing diverges in two ways that need their own regex:

1. **"usage charge" / "applied to" / "from"** wording (free_window keys
   off "free electricity" or "$0 for consumption").
2. **"midnight" / "noon" tokens** in the time window (free_window only
   handles ``Xam`` / ``Xpm`` numeric tokens).

Once parsed, the result has the same ``{"rate_c_per_kwh", "windows",
"source"}`` shape as free_window, so we reuse ``free_window.apply_rule``
directly.

Known limitation: "Does not apply to controlled loads" disclaimer is
ignored — we credit the rate on ALL in-window imports, slightly
over-crediting users who have a separate controlled-load circuit (hot
water / pool pump). Acceptable for v1.5.x; refining requires PriceHawk
to distinguish controlled-load kWh from regular load, which is a
larger HA-energy-config-aware change.
"""
from __future__ import annotations

import re
from decimal import Decimal

from .free_window import apply_rule as _apply_window_rule

# "$0.045/kWh" optionally followed by "incl. GST" or "usage charge".
RATE_RE = re.compile(
    r"\$(?P<rate>[\d.]+)\s*(?:/\s*kWh)?(?:\s+(?:incl?\.?\s*GST))?",
    re.I,
)

# Triggers that distinguish ev_offpeak from generic mid-day free_window:
TRIGGER_RE = re.compile(
    r"\busage\s+charge\b|\bapplied?\s+to\s+(?:all\s+)?import\b|"
    r"\bovernight\b|\bEV\s+charging\b|\bvehicle\s+charging\b",
    re.I,
)

# Window: "between X and Y" / "from X to Y", where X/Y can be midnight,
# noon, or HH(:MM)?am/pm tokens.
_TIME_TOKEN = r"(?:midnight|noon|\d{1,2}(?::\d{2})?\s*(?:am|pm))"
WINDOW_RE = re.compile(
    rf"(?:between|from)\s+(?P<start>{_TIME_TOKEN})\s*"
    r"(?:-|–|—|to|and)\s*"
    rf"(?P<end>{_TIME_TOKEN})",
    re.I,
)


def _token_to_minutes(tok: str) -> int:
    """'midnight'→0, 'noon'→720, '6am'→360, '11:30pm'→1410."""
    t = tok.strip().lower()
    if t == "midnight":
        return 0
    if t == "noon":
        return 12 * 60
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", t)
    if not m:
        raise ValueError(f"can't parse time token {tok!r}")
    h = int(m.group(1)) % 12
    if m.group(3) == "pm":
        h += 12
    minute = int(m.group(2)) if m.group(2) else 0
    return h * 60 + minute


def parse_rule(text: str) -> dict | None:
    """Extract EV-offpeak rule from eligibility/description text.

    Returns ``None`` if no match. On match returns the same shape as
    ``free_window.parse_rule``:
      ``{"rate_c_per_kwh": Decimal, "windows": [(start_min, end_min)],
         "source": str}``
    """
    if not text or not TRIGGER_RE.search(text):
        return None

    rate_match = RATE_RE.search(text)
    window_match = WINDOW_RE.search(text)
    if not (rate_match and window_match):
        return None

    rate_aud = Decimal(rate_match.group("rate"))
    rate_c = rate_aud * Decimal("100")  # $0.045 → 4.5c

    start = _token_to_minutes(window_match.group("start"))
    end = _token_to_minutes(window_match.group("end"))

    return {
        "rate_c_per_kwh": rate_c,
        "windows": [(start, end)],
        "source": text[:200],
    }


def parse_from_incentives(incentives: list[dict]) -> list[dict]:
    """Walk a plan's ``incentives[]`` and extract EV-offpeak rules.

    Same return shape as ``free_window.parse_from_incentives`` so the
    caller can reuse ``free_window.apply_rule`` for the math.
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
                break
    return out


def apply_rule(rule: dict, slots: list[dict], breakdown, **kwargs) -> None:
    """Delegate to free_window's apply_rule — math is identical."""
    _apply_window_rule(rule, slots, breakdown, **kwargs)
