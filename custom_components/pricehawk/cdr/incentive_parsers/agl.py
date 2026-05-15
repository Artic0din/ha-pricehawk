"""AGL incentive parser — Phase 2.6.

AGL publishes most of its tariff math structurally under
`electricityContract.solarFeedInTariff[]`, which the core evaluator
already credits via `_apply_fit_credit`. This parser covers the
non-structural patterns AGL ships as free-text in
`electricityContract.incentives[]`:

1. **Bonus FIT** — "Solar Savers / Solar Sunshine / Solar Maximiser":
   extra cents/kWh on top of the base FIT, capped at first N kWh
   exported in a daily time window. Pattern is regular enough to extract
   via regex.

2. **Three for Free** — 3 hours/day free electricity. Not directly a FIT
   parser — it's an import-side credit — but lives in the same
   incentives block. v1.5.0 ships a presence-detector that logs the
   plan needs follow-up (the actual time-shift math depends on the
   user's chosen 3-hour window, which AGL pushes to a separate app).

Both rules emit credits in INC-GST DOLLARS into
`breakdown.incentive_aud_inc_gst`. AGL fact sheets quote dollar amounts
in the same convention as GloBird (inc-GST already), per the AER
Schedule 1 disclosure rules.

Coverage gap acknowledged in TODOS.md TODO-6: OVO's "Free 3" is also
this pattern but the wording differs enough that a separate `ovo.py`
parser is cleaner than one rule-set covering both. AGL only here.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Callable

BONUS_FIT_RE = re.compile(
    r"(?P<cents>[\d.]+)\s*c(?:ents)?/kWh\s+(?:bonus|extra|additional|solar\s+savings)"
    r"(?:\s+feed[-\s]?in)?\s+(?:for\s+)?(?:the\s+)?first\s+(?P<kwh>[\d.]+)"
    r"\s+kWh(?:\s+(?:of\s+)?exports?)?(?:\s+per\s+day)?\s+between\s+"
    r"(?P<start>\d{1,2}(?::\d{2})?\s*(?:am|pm))-(?P<end>\d{1,2}(?::\d{2})?\s*(?:am|pm))",
    re.I,
)
THREE_FOR_FREE_RE = re.compile(
    r"three\s+for\s+free|3\s+hours?\s+(?:per\s+day\s+)?(?:of\s+)?free",
    re.I,
)


def _hh_token_to_minutes(tok: str) -> int:
    """Parse '6pm', '6:30am', '12pm' → minutes from midnight."""
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


def parse_rules(plan_data: dict) -> dict:
    """Extract structured rule dicts from AGL incentives free-text.

    Returns ``{"bonus_fit": {...}, "three_for_free": {...}}`` with
    missing keys silently dropped. Each rule is independent.
    """
    elec = plan_data.get("electricityContract", {}) or {}
    rules: dict = {}
    for inc in elec.get("incentives", []) or []:
        desc = inc.get("description") or ""

        m = BONUS_FIT_RE.search(desc)
        if m and "bonus_fit" not in rules:
            rules["bonus_fit"] = {
                "cents_per_kwh": Decimal(m.group("cents")),
                "first_kwh_per_day": Decimal(m.group("kwh")),
                "start_min": _hh_token_to_minutes(m.group("start")),
                "end_min": _hh_token_to_minutes(m.group("end")),
                "source_displayName": inc.get("displayName"),
            }

        if THREE_FOR_FREE_RE.search(desc) and "three_for_free" not in rules:
            rules["three_for_free"] = {
                "detected": True,
                "source_displayName": inc.get("displayName"),
                "source_description": desc,
            }
    return rules


def apply(
    plan_data: dict,
    slots: list[dict],
    breakdown,
    *,
    slot_in_window: Callable,
) -> None:
    """Credit bonus-FIT exports to ``breakdown.incentive_aud_inc_gst``.

    `slot_in_window` is supplied for API parity; this parser uses
    minute-based windows derived from the parsed text and does not need
    the CDR HH:MM resolver.
    """
    del slot_in_window  # reserved
    rules = parse_rules(plan_data)

    # Phase 2.11.4 — also extract free_window rules so we can route
    # AGL Three for Free even when the legacy bonus_fit/three_for_free
    # regexes (description-only) didn't match (eligibility-only plans).
    from .common import peak_import_rate_c_per_kwh_inc_gst
    from .common.free_window import (
        apply_rule as _apply_free_window,
        parse_from_incentives as _parse_free_windows,
    )
    elec = plan_data.get("electricityContract") or {}
    fw_rules = _parse_free_windows(elec.get("incentives") or [])

    if not rules and not fw_rules:
        return
    rule_names = list(rules.keys())
    if fw_rules:
        rule_names.append("free_window")
    breakdown.notes.append(f"agl parser hits: {rule_names}")

    by_day: dict[str, list[dict]] = {}
    for slot in slots:
        by_day.setdefault(slot["ts_local"][:10], []).append(slot)

    if "bonus_fit" in rules:
        rule = rules["bonus_fit"]
        rate_per_kwh = rule["cents_per_kwh"] / Decimal("100")
        for day, day_slots in by_day.items():
            day_credited_kwh = Decimal("0")
            for slot in day_slots:
                local_dt = datetime.fromisoformat(slot["ts_local"])
                minutes = local_dt.hour * 60 + local_dt.minute
                if not (rule["start_min"] <= minutes < rule["end_min"]):
                    continue
                exp = _decimal(
                    slot.get("grid_export_kwh", 0)
                    or slot.get("solar_export_kwh", 0)
                )
                if exp <= 0:
                    continue
                remaining = rule["first_kwh_per_day"] - day_credited_kwh
                if remaining <= 0:
                    break
                credit_kwh = min(exp, remaining)
                breakdown.incentive_aud_inc_gst -= credit_kwh * rate_per_kwh
                day_credited_kwh += credit_kwh
            if day_credited_kwh > 0:
                breakdown.trace.append({
                    "incentive": "agl_bonus_fit",
                    "day": day,
                    "credited_kwh": float(day_credited_kwh),
                    "rate_c_kwh_inc_gst": float(rule["cents_per_kwh"]),
                })

    if "three_for_free" in rules:
        # Phase 2.11.4 supersedes the Phase 2.6 deferred stub: the AGL
        # eligibility text DOES specify the window ("Free electricity
        # usage applies from 10am to 1pm every day"). free_window helper
        # below credits the import-side math; this note is informational.
        breakdown.notes.append("agl: 'Three for Free' detected.")

    # Phase 2.11.4 — credit free import window math
    if fw_rules:
        peak_rate = peak_import_rate_c_per_kwh_inc_gst(plan_data)
        for fw in fw_rules:
            _apply_free_window(
                fw, slots, breakdown,
                normal_import_rate_c_per_kwh_inc_gst=peak_rate,
            )
