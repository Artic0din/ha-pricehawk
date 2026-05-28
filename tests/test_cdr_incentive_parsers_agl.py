"""Tests for cdr.incentive_parsers.agl — Phase 2.6 AGL FIT parser.

Covers:
- Bonus FIT regex extraction across the common AGL wording variants.
- Time-token parsing including HH:MM minutes.
- Three for Free detector (no math; just notes).
- apply() correctly credits export windows and stops at the per-day cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from custom_components.pricehawk.cdr.incentive_parsers.agl import (
    _hh_token_to_minutes,
    apply,
    parse_rules,
)


@dataclass
class _StubBreakdown:
    """Minimal stand-in for CostBreakdown so we can test parser side effects
    without importing the full evaluator. Mirrors the three mutated fields."""

    incentive_aud_inc_gst: Decimal = Decimal("0")
    notes: list = field(default_factory=list)
    trace: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Time token parsing
# ---------------------------------------------------------------------------


class TestHhTokenToMinutes:
    def test_am_simple(self):
        assert _hh_token_to_minutes("6am") == 360

    def test_pm_simple(self):
        assert _hh_token_to_minutes("6pm") == 1080

    def test_noon(self):
        assert _hh_token_to_minutes("12pm") == 720

    def test_midnight(self):
        assert _hh_token_to_minutes("12am") == 0

    def test_with_minutes(self):
        assert _hh_token_to_minutes("6:30pm") == 18 * 60 + 30

    def test_with_space_before_meridiem(self):
        assert _hh_token_to_minutes("6 pm") == 1080


# ---------------------------------------------------------------------------
# parse_rules — regex coverage
# ---------------------------------------------------------------------------


def _plan_with_incentives(*incentives: dict) -> dict:
    return {"electricityContract": {"incentives": list(incentives)}}


class TestParseRulesBonusFit:
    def test_basic_solar_savers_pattern(self):
        plan = _plan_with_incentives(
            {
                "displayName": "Solar Savers",
                "description": "10c/kWh bonus feed-in for the first 10 kWh of exports per day between 11am-2pm",
            }
        )
        rules = parse_rules(plan)
        assert "bonus_fit" in rules
        r = rules["bonus_fit"]
        assert r["cents_per_kwh"] == Decimal("10")
        assert r["first_kwh_per_day"] == Decimal("10")
        assert r["start_min"] == 11 * 60
        assert r["end_min"] == 14 * 60

    def test_alternate_wording_extra(self):
        plan = _plan_with_incentives(
            {
                "displayName": "Solar Sunshine",
                "description": "5c/kWh extra for the first 5 kWh between 10am-2pm",
            }
        )
        rules = parse_rules(plan)
        assert "bonus_fit" in rules

    def test_alternate_wording_additional(self):
        plan = _plan_with_incentives(
            {
                "displayName": "Solar Maximiser",
                "description": "3.5c/kWh additional feed-in for first 8 kWh exports per day between 9am-3pm",
            }
        )
        rules = parse_rules(plan)
        assert "bonus_fit" in rules
        assert rules["bonus_fit"]["cents_per_kwh"] == Decimal("3.5")

    def test_no_match_leaves_rules_empty(self):
        plan = _plan_with_incentives(
            {
                "displayName": "Random promo",
                "description": "Sign up and receive $50 credit on your next bill",
            }
        )
        rules = parse_rules(plan)
        assert "bonus_fit" not in rules

    def test_no_incentives_returns_empty(self):
        assert parse_rules({"electricityContract": {}}) == {}

    def test_handles_no_electricity_contract(self):
        assert parse_rules({}) == {}


class TestParseRulesThreeForFree:
    def test_explicit_three_for_free(self):
        plan = _plan_with_incentives(
            {
                "displayName": "AGL Three for Free",
                "description": "Three for Free: pick 3 hours per day of free electricity",
            }
        )
        rules = parse_rules(plan)
        assert "three_for_free" in rules

    def test_3_hours_phrasing(self):
        plan = _plan_with_incentives(
            {
                "displayName": "AGL ThreeFree Plan",
                "description": "Customers get 3 hours of free electricity each day",
            }
        )
        rules = parse_rules(plan)
        assert "three_for_free" in rules


# ---------------------------------------------------------------------------
# apply() — credit accumulation
# ---------------------------------------------------------------------------


def _slots_export_in_window() -> list[dict]:
    """5 half-hour slots between 11:00 and 13:00 exporting 2 kWh each."""
    return [
        {"ts_local": "2026-05-10T11:00:00", "grid_export_kwh": 2.0},
        {"ts_local": "2026-05-10T11:30:00", "grid_export_kwh": 2.0},
        {"ts_local": "2026-05-10T12:00:00", "grid_export_kwh": 2.0},
        {"ts_local": "2026-05-10T12:30:00", "grid_export_kwh": 2.0},
        {"ts_local": "2026-05-10T13:00:00", "grid_export_kwh": 2.0},
    ]


def _noop_slot_in_window(*_args, **_kwargs):
    return False


class TestApply:
    def test_no_rules_no_credit(self):
        plan = _plan_with_incentives({"displayName": "x", "description": "x"})
        bd = _StubBreakdown()
        apply(plan, _slots_export_in_window(), bd, slot_in_window=_noop_slot_in_window)
        assert bd.incentive_aud_inc_gst == Decimal("0")
        assert bd.notes == []

    def test_bonus_fit_credits_capped_kwh(self):
        plan = _plan_with_incentives(
            {
                "displayName": "Solar Savers",
                "description": "10c/kWh bonus feed-in for the first 5 kWh of exports per day between 11am-2pm",
            }
        )
        bd = _StubBreakdown()
        apply(plan, _slots_export_in_window(), bd, slot_in_window=_noop_slot_in_window)
        # 5 kWh × 10c = 50c = $0.50 — incentive is a CREDIT so subtracted.
        # incentive_aud_inc_gst represents credits as negative additions
        # to the imports total, so the field itself becomes negative.
        assert bd.incentive_aud_inc_gst == Decimal("-0.50")
        assert len(bd.trace) == 1
        assert bd.trace[0]["incentive"] == "agl_bonus_fit"
        assert bd.trace[0]["credited_kwh"] == 5.0

    def test_three_for_free_only_logs_no_math(self):
        plan = _plan_with_incentives(
            {
                "displayName": "Three for Free",
                "description": "Three for Free: 3 hours per day of free electricity, choose your window in the AGL app",
            }
        )
        bd = _StubBreakdown()
        apply(plan, _slots_export_in_window(), bd, slot_in_window=_noop_slot_in_window)
        # Detect-only stub: no math change.
        assert bd.incentive_aud_inc_gst == Decimal("0")
        # ... but notes record the gap so log readers see it.
        joined = "\n".join(bd.notes)
        assert "Three for Free" in joined

    def test_window_outside_slots_no_credit(self):
        plan = _plan_with_incentives(
            {
                "displayName": "Solar Savers",
                "description": "10c/kWh bonus feed-in for the first 10 kWh of exports per day between 6pm-9pm",
            }
        )
        bd = _StubBreakdown()
        apply(plan, _slots_export_in_window(), bd, slot_in_window=_noop_slot_in_window)
        assert bd.incentive_aud_inc_gst == Decimal("0")


# ---------------------------------------------------------------------------
# Registry wiring — 2.6 registers AGL alongside GloBird
# ---------------------------------------------------------------------------


class TestRegistryWiring:
    def test_agl_in_retailer_parsers(self):
        from custom_components.pricehawk.cdr.incentive_parsers import RETAILER_PARSERS

        assert "agl" in RETAILER_PARSERS
        assert callable(RETAILER_PARSERS["agl"])

    def test_globird_still_present(self):
        from custom_components.pricehawk.cdr.incentive_parsers import RETAILER_PARSERS

        assert "globird" in RETAILER_PARSERS
