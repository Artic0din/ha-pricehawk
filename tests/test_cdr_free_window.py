"""Tests for cdr.incentive_parsers.common.free_window — Phase 2.11.4.

Pin behaviour against the 5 catalog-confirmed wordings observed across
214 plans (GloBird, AGL, OVO, Red).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from custom_components.pricehawk.cdr.incentive_parsers.common.free_window import (
    apply_rule,
    parse_from_incentives,
    parse_rule,
)


@dataclass
class _StubBreakdown:
    incentive_aud_inc_gst: Decimal = Decimal("0")
    notes: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# parse_rule — all 5 catalog wordings
# ---------------------------------------------------------------------------


class TestParseFreeWordings:
    def test_ovo_free_3(self):
        # OVO/MYOB "Free 3" — 38 plans
        text = ("Free electricity between 11am and 2pm everyday. "
                "For more information head to https://pages.ovoenergy.com.au/the-free-3-plan")
        rule = parse_rule(text)
        assert rule is not None
        assert rule["rate_c_per_kwh"] == Decimal("0")
        assert rule["windows"] == [(11 * 60, 14 * 60)]

    def test_agl_three_for_free_usage(self):
        # AGL "Three for Free Usage"
        text = ("Free electricity usage applies from 10am to 1pm every day. "
                "Daily supply charges still apply. This rate can change with "
                "notice to you.")
        rule = parse_rule(text)
        assert rule is not None
        assert rule["rate_c_per_kwh"] == Decimal("0")
        assert rule["windows"] == [(10 * 60, 13 * 60)]

    def test_globird_four_hour_free(self):
        # GloBird "Four-hour free usage every day"
        text = ("$0.00 for consumption between 10am-2pm (Local Time), "
                "excluding controlled load.")
        rule = parse_rule(text)
        assert rule is not None
        assert rule["rate_c_per_kwh"] == Decimal("0")
        assert rule["windows"] == [(10 * 60, 14 * 60)]

    def test_globird_perfect_if_you_love_free_stuff(self):
        # ZEROHERO 3-for-Free
        text = ("$0.00 for consumption between 11am-2pm (Local Time), "
                "excluding controlled load.")
        rule = parse_rule(text)
        assert rule is not None
        assert rule["rate_c_per_kwh"] == Decimal("0")
        assert rule["windows"] == [(11 * 60, 14 * 60)]


class TestParseDiscountedTwoWindow:
    def test_globird_nine_hour_low_ev_rate(self):
        # GloBird "Nine-hour low EV rate" — TWO windows joined by &.
        text = ("$0.06/kWh incl. GST for consumption between 11am-2pm & "
                "12am-6am (Local Time), excluding controlled load.")
        rule = parse_rule(text)
        assert rule is not None
        assert rule["rate_c_per_kwh"] == Decimal("6.00")
        assert len(rule["windows"]) == 2
        assert rule["windows"][0] == (11 * 60, 14 * 60)
        assert rule["windows"][1] == (0, 6 * 60)


class TestParseEdgeCases:
    def test_empty_returns_none(self):
        assert parse_rule("") is None
        assert parse_rule(None) is None  # type: ignore[arg-type]

    def test_unrelated_text_returns_none(self):
        assert parse_rule("Receive 3 Velocity Points per $1") is None

    def test_no_window_returns_none(self):
        # "Free electricity" without a time window is just marketing.
        assert parse_rule("Free electricity for everyone!") is None


# ---------------------------------------------------------------------------
# apply_rule — math semantics
# ---------------------------------------------------------------------------


class TestApplyFreeWindow:
    def test_zero_rate_credits_full_normal_rate(self):
        # Free 3: 5 kWh imported at noon, normal rate 30c/kWh inc-GST.
        # Credit = (30 - 0) / 100 × 5 = 1.50 AUD
        rule = parse_rule(
            "Free electricity between 11am and 2pm everyday."
        )
        assert rule is not None
        slots = [{"ts_local": "2026-05-15T12:00:00", "grid_import_kwh": 5.0}]
        b = _StubBreakdown()
        apply_rule(rule, slots, b,
                   normal_import_rate_c_per_kwh_inc_gst=Decimal("30"))
        assert b.incentive_aud_inc_gst == Decimal("-1.50")

    def test_discounted_rate_credits_delta(self):
        # 9-hour EV rate: 5 kWh at noon, normal 30c, discount to 6c.
        # Credit = (30 - 6) / 100 × 5 = 1.20 AUD
        rule = parse_rule(
            "$0.06/kWh incl. GST for consumption between 11am-2pm & 12am-6am"
        )
        assert rule is not None
        slots = [{"ts_local": "2026-05-15T13:00:00", "grid_import_kwh": 5.0}]
        b = _StubBreakdown()
        apply_rule(rule, slots, b,
                   normal_import_rate_c_per_kwh_inc_gst=Decimal("30"))
        assert b.incentive_aud_inc_gst == Decimal("-1.20")

    def test_two_windows_both_credit(self):
        # 9-hour EV rate: imports in BOTH windows credited.
        rule = parse_rule(
            "$0.06/kWh incl. GST for consumption between 11am-2pm & 12am-6am"
        )
        assert rule is not None
        slots = [
            {"ts_local": "2026-05-15T03:00:00", "grid_import_kwh": 4.0},  # 3am — window 2
            {"ts_local": "2026-05-15T08:00:00", "grid_import_kwh": 2.0},  # 8am — outside
            {"ts_local": "2026-05-15T13:00:00", "grid_import_kwh": 5.0},  # 1pm — window 1
        ]
        b = _StubBreakdown()
        apply_rule(rule, slots, b,
                   normal_import_rate_c_per_kwh_inc_gst=Decimal("30"))
        # (30 - 6)/100 × (4 + 5) = 0.24 × 9 = 2.16
        assert b.incentive_aud_inc_gst == Decimal("-2.16")

    def test_outside_window_no_credit(self):
        rule = parse_rule(
            "Free electricity between 11am and 2pm everyday."
        )
        assert rule is not None
        slots = [{"ts_local": "2026-05-15T15:00:00", "grid_import_kwh": 5.0}]
        b = _StubBreakdown()
        apply_rule(rule, slots, b,
                   normal_import_rate_c_per_kwh_inc_gst=Decimal("30"))
        assert b.incentive_aud_inc_gst == Decimal("0")
        assert b.trace == []

    def test_zero_normal_rate_no_credit(self):
        # If tariff already encodes 0c during window (GloBird Flex
        # 11am-2pm), normal_rate=0 → no credit, no double-counting.
        rule = parse_rule(
            "$0.00 for consumption between 11am-2pm (Local Time)"
        )
        assert rule is not None
        slots = [{"ts_local": "2026-05-15T12:00:00", "grid_import_kwh": 5.0}]
        b = _StubBreakdown()
        apply_rule(rule, slots, b,
                   normal_import_rate_c_per_kwh_inc_gst=Decimal("0"))
        assert b.incentive_aud_inc_gst == Decimal("0")
        assert b.trace == []

    def test_normal_below_free_no_credit(self):
        # Edge case: normal_rate < free_rate. delta is negative; we don't
        # CHARGE the user extra — we just no-op.
        rule = parse_rule(
            "$0.06/kWh incl. GST for consumption between 11am-2pm & 12am-6am"
        )
        assert rule is not None
        slots = [{"ts_local": "2026-05-15T12:00:00", "grid_import_kwh": 5.0}]
        b = _StubBreakdown()
        apply_rule(rule, slots, b,
                   normal_import_rate_c_per_kwh_inc_gst=Decimal("3"))
        assert b.incentive_aud_inc_gst == Decimal("0")

    def test_zero_import_no_credit(self):
        rule = parse_rule(
            "Free electricity between 11am and 2pm everyday."
        )
        assert rule is not None
        slots = [{"ts_local": "2026-05-15T12:00:00", "grid_import_kwh": 0.0}]
        b = _StubBreakdown()
        apply_rule(rule, slots, b,
                   normal_import_rate_c_per_kwh_inc_gst=Decimal("30"))
        assert b.incentive_aud_inc_gst == Decimal("0")

    def test_trace_records_window_strings(self):
        rule = parse_rule(
            "$0.06/kWh incl. GST for consumption between 11am-2pm & 12am-6am"
        )
        assert rule is not None
        slots = [{"ts_local": "2026-05-15T12:00:00", "grid_import_kwh": 1.0}]
        b = _StubBreakdown()
        apply_rule(rule, slots, b,
                   normal_import_rate_c_per_kwh_inc_gst=Decimal("30"))
        assert len(b.trace) == 1
        t = b.trace[0]
        assert t["incentive"] == "free_window"
        assert t["free_rate_c_per_kwh"] == 6.0
        assert t["normal_rate_c_per_kwh"] == 30.0
        assert t["windows"] == "11:00-14:00 & 00:00-06:00"


# ---------------------------------------------------------------------------
# parse_from_incentives — full plan walk
# ---------------------------------------------------------------------------


class TestParseFromIncentives:
    def test_single_free_window_rule_extracted(self):
        incentives = [{
            "displayName": "Free 3",
            "eligibility": "Free electricity between 11am and 2pm everyday.",
        }]
        rules = parse_from_incentives(incentives)
        assert len(rules) == 1
        assert rules[0]["rate_c_per_kwh"] == Decimal("0")
        assert rules[0]["source_displayName"] == "Free 3"

    def test_multiple_rules_per_plan(self):
        # ZEROHERO ships both "Perfect if you love free stuff" (free)
        # AND "Nine-hour low EV rate" (discounted) on some variants.
        incentives = [
            {"displayName": "Perfect if you love free stuff",
             "eligibility": "$0.00 for consumption between 11am-2pm"},
            {"displayName": "Nine-hour low EV rate",
             "eligibility": ("$0.06/kWh incl. GST for consumption between "
                             "11am-2pm & 12am-6am (Local Time)")},
        ]
        rules = parse_from_incentives(incentives)
        assert len(rules) == 2
        assert rules[0]["rate_c_per_kwh"] == Decimal("0")
        assert rules[1]["rate_c_per_kwh"] == Decimal("6.00")

    def test_no_match_returns_empty(self):
        incentives = [{"displayName": "Welcome", "eligibility": "$50 sign-up"}]
        assert parse_from_incentives(incentives) == []

    def test_empty_input(self):
        assert parse_from_incentives([]) == []
        assert parse_from_incentives(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# End-to-end dispatch — OVO + Red + AGL + GloBird wiring
# ---------------------------------------------------------------------------


class TestDispatchE2E:
    """Verify free_window credit lands via apply_retailer_incentives
    for every Phase 2.11.4 retailer."""

    def _import_dispatch(self):
        from custom_components.pricehawk.cdr.incentive_parsers import (
            apply_retailer_incentives,
        )
        return apply_retailer_incentives

    def _flat_tou_plan(self, brand: str, eligibility: str,
                       display_name: str = "Free 3") -> dict:
        # Plan with a SINGLE flat 30c/kWh rate (ex-GST) → peak rate
        # helper returns 30 × 110 = 33 c/kWh inc-GST.
        return {
            "brand": brand,
            "electricityContract": {
                "tariffPeriod": [{
                    "rateBlockUType": "singleRate",
                    "singleRate": {"rates": [{"unitPrice": "0.30"}]},
                }],
                "incentives": [{
                    "displayName": display_name,
                    "eligibility": eligibility,
                }],
            },
        }

    def test_ovo_free_3_credits_via_dispatch(self):
        # 5 kWh imported at noon. Peak rate 33c inc-GST. Free rate 0c.
        # Credit = (33 - 0) / 100 × 5 = 1.65 AUD
        dispatch = self._import_dispatch()
        plan = self._flat_tou_plan(
            "ovo-energy",
            "Free electricity between 11am and 2pm everyday."
        )
        slots = [{"ts_local": "2026-05-15T12:00:00", "grid_import_kwh": 5.0}]
        b = _StubBreakdown()
        dispatch(plan, slots, b, slot_in_window=lambda *a, **kw: False)
        assert b.incentive_aud_inc_gst == Decimal("-1.65")
        assert any("ovo parser hits" in n for n in b.notes)

    def test_red_free_window_credits_via_dispatch(self):
        dispatch = self._import_dispatch()
        plan = self._flat_tou_plan(
            "red-energy",
            ("Between 12pm and 2pm Saturday and Sunday, your electricity "
             "usage charges will be waived"),  # parser captures hours; weekend
            display_name="Free Electricity Use Period",
        )
        slots = [{"ts_local": "2026-05-15T13:00:00", "grid_import_kwh": 4.0}]
        b = _StubBreakdown()
        dispatch(plan, slots, b, slot_in_window=lambda *a, **kw: False)
        # (33 - 0) / 100 × 4 = 1.32
        assert b.incentive_aud_inc_gst == Decimal("-1.32")

    def test_agl_three_for_free_credits_via_dispatch(self):
        dispatch = self._import_dispatch()
        plan = self._flat_tou_plan(
            "agl",
            ("Free electricity usage applies from 10am to 1pm every day. "
             "Daily supply charges still apply."),
            display_name="Three for Free Usage",
        )
        slots = [{"ts_local": "2026-05-15T11:00:00", "grid_import_kwh": 3.0}]
        b = _StubBreakdown()
        dispatch(plan, slots, b, slot_in_window=lambda *a, **kw: False)
        assert b.incentive_aud_inc_gst == Decimal("-0.99")  # 33 × 3 / 100

    def test_globird_flex_no_double_credit(self):
        # GloBird ZEROHERO Flex tariff already encodes 11am-2pm as
        # ~0c off-peak. Helper detects this (min rate ≤ 1c threshold)
        # and returns 0 → free_window applies no credit.
        dispatch = self._import_dispatch()
        plan = {
            "brand": "globird",
            "electricityContract": {
                "tariffPeriod": [{
                    "rateBlockUType": "timeOfUseRates",
                    "timeOfUseRates": [
                        {"type": "PEAK", "rates": [{"unitPrice": "0.36"}]},
                        {"type": "OFF_PEAK", "rates": [{"unitPrice": "0.000001"}]},
                        {"type": "SHOULDER", "rates": [{"unitPrice": "0.25"}]},
                    ],
                }],
                "incentives": [{
                    "displayName": "Perfect if you love free stuff",
                    "eligibility": ("$0.00 for consumption between 11am-2pm "
                                    "(Local Time), excluding controlled load."),
                }],
            },
        }
        slots = [{"ts_local": "2026-05-15T12:00:00", "grid_import_kwh": 5.0}]
        b = _StubBreakdown()
        dispatch(plan, slots, b, slot_in_window=lambda *a, **kw: False)
        # No credit — tariff already encodes the free window.
        assert b.incentive_aud_inc_gst == Decimal("0")
