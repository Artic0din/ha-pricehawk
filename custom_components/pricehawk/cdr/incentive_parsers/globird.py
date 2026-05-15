"""GloBird incentive parser.

Extracts structured rules from `incentives[].description` free-text and
applies per-day credits to a `CostBreakdown.incentive_aud_inc_gst`.

Phase 0 + Phase 1 scope (v1.5.0):
  - ZEROHERO Credit: $1/Day when imports during 6-8pm avg ≤ 0.03 kWh/h
  - Super Export Credit: 15 c/kWh on first 10 kWh exports in 6-8pm window

Deferred to v1.5.1 (TODOS.md):
  - FOUR4FREE explicit parser (currently the free 11am-2pm window is
    encoded as 0c/kWh in the FLEXIBLE tariff itself, so no separate
    credit math needed for ZEROHERO-Combo-FOUR4FREE plans)
  - Critical Peak Export/Import (event schedule API not available)

Source for regex patterns: GloBird Victorian Energy Fact Sheets
(Victorian_Energy_Fact_Sheet_GLO707520MR_Electricity_CZ_6.pdf and
relatives). Hand-merged into CDR fixture per D-P0-5 because EME proxy
strips incentive descriptions to displayName-only stubs.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Callable

ZEROHERO_RE = re.compile(
    r"\$(?P<aud>[\d.]+)\s*/?\s*Day\s+when\s+imports\s+are\s+(?P<thresh>[\d.]+)"
    r"\s+kWh/hour\s+or\s+less[,]?\s+between\s+"
    r"(?P<start>\d{1,2}(?:am|pm))-(?P<end>\d{1,2}(?:am|pm))",
    re.I,
)
SUPER_EXPORT_RE = re.compile(
    r"(?P<cents>[\d.]+)\s*cents/kWh\s+applies\s+to\s+the\s+first\s+(?P<kwh>[\d.]+)"
    r"\s+kWh\s+of\s+exports\s+between\s+"
    r"(?P<start>\d{1,2}(?:am|pm))-(?P<end>\d{1,2}(?:am|pm))",
    re.I,
)


def _hh_token_to_minutes(tok: str) -> int:
    m = re.match(r"(\d{1,2})(am|pm)", tok.strip(), re.I)
    if not m:
        raise ValueError(f"can't parse time token {tok!r}")
    h = int(m.group(1)) % 12
    if m.group(2).lower() == "pm":
        h += 12
    return h * 60


def _decimal(v) -> Decimal:
    if v is None:
        return Decimal("0")
    return Decimal(str(v))


def parse_rules(plan_data: dict) -> dict:
    """Return parsed-rule dict from CDR `incentives` descriptions.

    Keys: "zerohero", "super_export" — each maps to a dict of structured
    fields the apply step uses. Missing patterns are silently skipped.
    """
    elec = plan_data.get("electricityContract", {}) or {}
    rules: dict = {}
    for inc in elec.get("incentives", []) or []:
        desc = inc.get("description") or ""
        name = (inc.get("displayName") or "").upper()

        m = ZEROHERO_RE.search(desc)
        if m and "ZEROHERO" in name:
            rules["zerohero"] = {
                "credit_aud_per_day": Decimal(m.group("aud")),
                "max_kwh_per_hour": Decimal(m.group("thresh")),
                "start_min": _hh_token_to_minutes(m.group("start")),
                "end_min": _hh_token_to_minutes(m.group("end")),
                "source_displayName": inc.get("displayName"),
            }

        m = SUPER_EXPORT_RE.search(desc)
        if m and "SUPER" in name:
            rules["super_export"] = {
                "cents_per_kwh": Decimal(m.group("cents")),
                "first_kwh_per_day": Decimal(m.group("kwh")),
                "start_min": _hh_token_to_minutes(m.group("start")),
                "end_min": _hh_token_to_minutes(m.group("end")),
                "source_displayName": inc.get("displayName"),
            }
    return rules


def apply(
    plan_data: dict,
    slots: list[dict],
    breakdown,  # CostBreakdown forward ref
    *,
    slot_in_window: Callable,  # unused now — kept for parser-API uniformity
) -> None:
    """Apply ZEROHERO + Super Export + Peak FIT credits.

    Three rules combined:
      - ZEROHERO Credit: $1/day if behavioral threshold met
      - Super Export: 15c/kWh first 15kWh exports 6-9pm
      - Peak FIT (Phase 2.11.3): 2c/kWh all exports 4-11pm — wired via
        common.bonus_fit.parse_uncapped_window from CDR `eligibility`

    `slot_in_window` is the dependency-injected window matcher from the
    evaluator. Currently unused by this parser (uses minute-based windows
    parsed from PDF "6pm-8pm" tokens, not CDR HH:MM windows) but kept
    in the signature so future GloBird parser extensions can match the
    same TOU resolver semantics.

    Phase 2.11.3 known gap: Super Export and Peak FIT overlap in 6-9pm
    window. Both credit additively, over-counting Peak FIT for first
    15 kWh of 6-9pm exports by ~$5-30/yr. Refinement deferred to 2.11.4.
    """
    del slot_in_window  # reserved, see docstring
    rules = parse_rules(plan_data)

    # Phase 2.11.3 — extract Peak FIT (uncapped windowed bonus) from
    # eligibility text, additive on top of base FIT and Super Export.
    from .common.bonus_fit import (
        apply_uncapped_window,
        parse_from_incentives as _parse_bonus_fit,
    )
    elec = plan_data.get("electricityContract") or {}
    bonus_fit_rules = _parse_bonus_fit(elec.get("incentives") or [])

    if not rules and not bonus_fit_rules["uncapped"]:
        return
    rule_names = list(rules.keys())
    if bonus_fit_rules["uncapped"]:
        rule_names.append("peak_fit")
    breakdown.notes.append(f"globird parser hits: {rule_names}")

    for peak_rule in bonus_fit_rules["uncapped"]:
        apply_uncapped_window(peak_rule, slots, breakdown)

    # Group slots by local-date once
    by_day: dict[str, list[dict]] = {}
    for slot in slots:
        by_day.setdefault(slot["ts_local"][:10], []).append(slot)

    if "zerohero" in rules:
        rule = rules["zerohero"]
        for day, day_slots in by_day.items():
            window_kwh = Decimal("0")
            window_hours = Decimal("0")
            for slot in day_slots:
                local_dt = datetime.fromisoformat(slot["ts_local"])
                minutes = local_dt.hour * 60 + local_dt.minute
                if rule["start_min"] <= minutes < rule["end_min"]:
                    window_kwh += _decimal(slot.get("grid_import_kwh", 0))
                    window_hours += Decimal("0.5")
            if window_hours == 0:
                continue
            avg_per_hour = window_kwh / window_hours
            if avg_per_hour <= rule["max_kwh_per_hour"]:
                breakdown.incentive_aud_inc_gst -= rule["credit_aud_per_day"]
                breakdown.trace.append({
                    "incentive": "zerohero",
                    "day": day,
                    "window_kwh": float(window_kwh),
                    "window_hours": float(window_hours),
                    "avg_kwh_h": float(avg_per_hour),
                    "credited_aud_inc_gst": float(rule["credit_aud_per_day"]),
                })

    if "super_export" in rules:
        rule = rules["super_export"]
        rate_per_kwh = rule["cents_per_kwh"] / Decimal("100")  # inc-GST $/kWh
        for day, day_slots in by_day.items():
            day_credited_kwh = Decimal("0")
            for slot in day_slots:
                local_dt = datetime.fromisoformat(slot["ts_local"])
                minutes = local_dt.hour * 60 + local_dt.minute
                if not (rule["start_min"] <= minutes < rule["end_min"]):
                    continue
                exp = _decimal(slot.get("grid_export_kwh", 0) or slot.get("solar_export_kwh", 0))
                if exp <= 0:
                    continue
                remaining = rule["first_kwh_per_day"] - day_credited_kwh
                if remaining <= 0:
                    break
                credit_kwh = min(exp, remaining)
                breakdown.incentive_aud_inc_gst -= credit_kwh * rate_per_kwh
                day_credited_kwh += credit_kwh
