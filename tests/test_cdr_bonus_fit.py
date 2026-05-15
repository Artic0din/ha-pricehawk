"""Tests for cdr.incentive_parsers.common.bonus_fit — Phase 2.11.3.

Pin behaviour against the exact ZEROHERO eligibility text observed
in catalog v3 sweep + GLO731031MR@VEC live fetch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from custom_components.pricehawk.cdr.incentive_parsers.common.bonus_fit import (
    apply_capped_window,
    apply_uncapped_window,
    parse_capped_window,
    parse_from_incentives,
    parse_uncapped_window,
)


@dataclass
class _StubBreakdown:
    incentive_aud_inc_gst: Decimal = Decimal("0")
    notes: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Regex coverage — ZEROHERO live samples
# ---------------------------------------------------------------------------


class TestParseUncappedWindow:
    def test_zerohero_peak_solar_feed_in_5c(self):
        # Catalog: "5 cents/kWh applies to exports between 4pm-11pm
        # (Local Time) everyday." (ZEROHERO VPP variant)
        text = ("5 cents/kWh applies to exports between 4pm-11pm "
                "(Local Time) everyday.")
        rule = parse_uncapped_window(text)
        assert rule is not None
        assert rule["bonus_c_per_kwh"] == Decimal("5")
        assert rule["start_min"] == 16 * 60
        assert rule["end_min"] == 23 * 60

    def test_zerohero_peak_solar_feed_in_2c_live(self):
        # Live fetch GLO731031MR@VEC: "2 cents/kWh applies to exports
        # between 4pm-11pm (Local Time) everyday."
        text = ("2 cents/kWh applies to exports between 4pm-11pm "
                "(Local Time) everyday.")
        rule = parse_uncapped_window(text)
        assert rule is not None
        assert rule["bonus_c_per_kwh"] == Decimal("2")

    def test_capped_text_does_not_match_uncapped(self):
        # Super Export text mentions "first N kWh" — uncapped parser must
        # NOT false-positive on the 15-cent rate.
        text = ("15 cents/kWh applies to the first 15 kWh of exports "
                "between 6pm-9pm (Local Time) everyday")
        assert parse_uncapped_window(text) is None

    def test_empty_returns_none(self):
        assert parse_uncapped_window("") is None
        assert parse_uncapped_window(None) is None  # type: ignore[arg-type]

    def test_unrelated_text_returns_none(self):
        assert parse_uncapped_window("$50 sign-up credit") is None


class TestParseCappedWindow:
    def test_zerohero_super_export_15c_live(self):
        # Live fetch GLO731031MR@VEC: full Super Export Credit text.
        text = ("15 cents/kWh applies to the first 15 kWh of exports "
                "between 6pm-9pm (Local Time) everyday, and is "
                "inclusive of any other Feed-in tariff as applicable "
                "in Energy Plan.")
        rule = parse_capped_window(text)
        assert rule is not None
        assert rule["bonus_c_per_kwh"] == Decimal("15")
        assert rule["cap_kwh_per_day"] == Decimal("15")
        assert rule["start_min"] == 18 * 60
        assert rule["end_min"] == 21 * 60

    def test_uncapped_text_does_not_match_capped(self):
        text = ("2 cents/kWh applies to exports between 4pm-11pm "
                "(Local Time) everyday.")
        assert parse_capped_window(text) is None


# ---------------------------------------------------------------------------
# Math — apply_uncapped_window
# ---------------------------------------------------------------------------


class TestApplyUncappedWindow:
    def test_peak_fit_credits_only_in_window(self):
        # 2c × 5 kWh in window + 0 outside.
        # Credit = -0.10 AUD (negative = user gets money)
        rule = parse_uncapped_window(
            "2 cents/kWh applies to exports between 4pm-11pm everyday."
        )
        assert rule is not None
        slots = [
            {"ts_local": "2026-05-15T15:00:00", "grid_export_kwh": 3.0},  # 3pm — outside
            {"ts_local": "2026-05-15T17:00:00", "grid_export_kwh": 5.0},  # 5pm — inside
            {"ts_local": "2026-05-15T23:00:00", "grid_export_kwh": 2.0},  # 11pm — outside (end exclusive)
        ]
        b = _StubBreakdown()
        apply_uncapped_window(rule, slots, b)
        assert b.incentive_aud_inc_gst == Decimal("-0.10")
        assert len(b.trace) == 1
        assert b.trace[0]["credited_kwh"] == 5.0

    def test_zero_export_in_window_no_credit(self):
        rule = parse_uncapped_window(
            "5 cents/kWh applies to exports between 4pm-11pm everyday."
        )
        assert rule is not None
        slots = [{"ts_local": "2026-05-15T17:00:00", "grid_export_kwh": 0.0}]
        b = _StubBreakdown()
        apply_uncapped_window(rule, slots, b)
        assert b.incentive_aud_inc_gst == Decimal("0")
        assert b.trace == []


# ---------------------------------------------------------------------------
# Math — apply_capped_window
# ---------------------------------------------------------------------------


class TestApplyCappedWindow:
    def test_super_export_first_15kwh_credited(self):
        # 20 kWh exported in 6-9pm window. Cap 15 kWh/day.
        # Credit = 15c × 15 kWh / 100 = 2.25 AUD
        rule = parse_capped_window(
            "15 cents/kWh applies to the first 15 kWh of exports "
            "between 6pm-9pm everyday"
        )
        assert rule is not None
        slots = [{"ts_local": "2026-05-15T18:00:00", "grid_export_kwh": 20.0}]
        b = _StubBreakdown()
        apply_capped_window(rule, slots, b)
        assert b.incentive_aud_inc_gst == Decimal("-2.25")

    def test_super_export_below_cap(self):
        rule = parse_capped_window(
            "15 cents/kWh applies to the first 15 kWh of exports "
            "between 6pm-9pm everyday"
        )
        assert rule is not None
        slots = [{"ts_local": "2026-05-15T18:00:00", "grid_export_kwh": 8.0}]
        b = _StubBreakdown()
        apply_capped_window(rule, slots, b)
        # 15c × 8 / 100 = 1.20
        assert b.incentive_aud_inc_gst == Decimal("-1.20")

    def test_cap_resets_each_day(self):
        rule = parse_capped_window(
            "15 cents/kWh applies to the first 15 kWh of exports "
            "between 6pm-9pm everyday"
        )
        assert rule is not None
        slots = [
            {"ts_local": "2026-05-15T18:00:00", "grid_export_kwh": 20.0},
            {"ts_local": "2026-05-16T18:00:00", "grid_export_kwh": 20.0},
        ]
        b = _StubBreakdown()
        apply_capped_window(rule, slots, b)
        # Each day caps at 15 kWh × 15c = 2.25, total 4.50
        assert b.incentive_aud_inc_gst == Decimal("-4.50")

    def test_export_outside_window_ignored(self):
        rule = parse_capped_window(
            "15 cents/kWh applies to the first 15 kWh of exports "
            "between 6pm-9pm everyday"
        )
        assert rule is not None
        slots = [{"ts_local": "2026-05-15T15:00:00", "grid_export_kwh": 10.0}]
        b = _StubBreakdown()
        apply_capped_window(rule, slots, b)
        assert b.incentive_aud_inc_gst == Decimal("0")


# ---------------------------------------------------------------------------
# parse_from_incentives — full plan walk
# ---------------------------------------------------------------------------


class TestParseFromIncentives:
    def test_zerohero_full_incentives_block(self):
        # Real ZEROHERO incentives block — should extract Peak FIT (uncapped)
        # AND Super Export (capped).
        incentives = [
            {"displayName": "Perfect if you love free stuff",
             "eligibility": ("$0.00 for consumption between 11am-2pm "
                             "(Local Time), excluding controlled load.")},
            {"displayName": "ZEROHERO Credit",
             "eligibility": ("$1/Day when imports are 0.03 kWh/hour or "
                             "less, between 6pm-9pm (Local Time).")},
            {"displayName": "Super Export Credit",
             "eligibility": ("15 cents/kWh applies to the first 15 kWh "
                             "of exports between 6pm-9pm (Local Time) "
                             "everyday, and is inclusive of any other "
                             "Feed-in tariff as applicable in Energy Plan.")},
            {"displayName": "Peak solar feed-in",
             "eligibility": ("2 cents/kWh applies to exports between "
                             "4pm-11pm (Local Time) everyday.")},
        ]
        out = parse_from_incentives(incentives)
        assert len(out["capped"]) == 1
        assert out["capped"][0]["bonus_c_per_kwh"] == Decimal("15")
        assert out["capped"][0]["source_displayName"] == "Super Export Credit"
        assert len(out["uncapped"]) == 1
        assert out["uncapped"][0]["bonus_c_per_kwh"] == Decimal("2")
        assert out["uncapped"][0]["source_displayName"] == "Peak solar feed-in"

    def test_no_match_returns_empty_lists(self):
        out = parse_from_incentives([
            {"displayName": "Welcome", "eligibility": "$50 sign-up"},
        ])
        assert out["capped"] == []
        assert out["uncapped"] == []

    def test_empty_input(self):
        out = parse_from_incentives([])
        assert out == {"capped": [], "uncapped": []}
        out = parse_from_incentives(None)  # type: ignore[arg-type]
        assert out == {"capped": [], "uncapped": []}


# ---------------------------------------------------------------------------
# End-to-end through globird.py dispatch — Phase 2.11.3 wiring
# ---------------------------------------------------------------------------


class TestGlobirdDispatchE2E:
    """Verify globird.py wires the new Peak FIT (uncapped) bonus through
    the apply_retailer_incentives dispatch chain.
    """

    def test_zerohero_peak_fit_credited_via_dispatch(self):
        from custom_components.pricehawk.cdr.incentive_parsers import (
            apply_retailer_incentives,
        )

        # Minimal ZEROHERO plan with Peak FIT eligibility.
        # 5 kWh exported at 5pm (in 4-11pm window) → 2c × 5 = 0.10 credit.
        plan = {
            "brand": "globird",
            "electricityContract": {
                "incentives": [{
                    "displayName": "Peak solar feed-in",
                    "eligibility": ("2 cents/kWh applies to exports between "
                                    "4pm-11pm (Local Time) everyday."),
                }],
            },
        }
        slots = [{"ts_local": "2026-05-15T17:00:00", "grid_export_kwh": 5.0}]
        b = _StubBreakdown()
        apply_retailer_incentives(plan, slots, b, slot_in_window=lambda *a, **kw: False)
        assert b.incentive_aud_inc_gst == Decimal("-0.10")
        assert any("peak_fit" in n for n in b.notes), b.notes
