"""Tests for cdr.incentive_parsers.common.tiered_fit — Phase 2.11.

Catalog v3 finding: 210 plans across Origin, AGL, Alinta, EnergyAustralia,
GloBird ship tiered FIT as free-text incentives. These tests pin the
math against the exact eligibility text observed in the live sweep
(scripts/CDR_INCENTIVE_CATALOG.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from custom_components.pricehawk.cdr.incentive_parsers.common.tiered_fit import (
    apply_rule,
    parse_from_incentives,
    parse_rule,
)


@dataclass
class _StubBreakdown:
    """Minimal CostBreakdown stand-in — only the fields tiered_fit touches."""
    incentive_aud_inc_gst: Decimal = Decimal("0")
    notes: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# parse_rule — regex coverage
# ---------------------------------------------------------------------------


class TestParseRateFirstDialect:
    """Catalog: Alinta + Origin + EA Solar Max."""

    def test_alinta_stepped_fit_exact_text(self):
        # 66 plans use this exact wording.
        text = ("This Energy Plan includes a stepped feed-in tariff, where "
                "you will receive a feed-in of 7c/kWh for the first 10kW "
                "exported. For any export after that you will obtain Alinta "
                "Energy's standard retailer feed-in tariff of 0.04c/kWh.")
        rule = parse_rule(text)
        assert rule is not None
        assert rule["tier1_c_per_kwh"] == Decimal("7")
        assert rule["cap_kwh"] == Decimal("10")
        assert rule["cap_window"] == "DAY"

    def test_origin_period_averaged(self):
        # 84 Origin plans — text triggers PERIOD cap_window.
        text = ("Origin offers 12 cents per kWh until a daily export limit "
                "of 8 kWh is reached. The daily export limit is averaged "
                "across your billing period (calculated by multiplying the "
                "number of days in your billing period by your daily export "
                "limit of 8)")
        rule = parse_rule(text)
        assert rule is not None
        assert rule["tier1_c_per_kwh"] == Decimal("12")
        assert rule["cap_kwh"] == Decimal("8")
        assert rule["cap_window"] == "PERIOD"

    def test_energyaustralia_solar_max(self):
        # 20 EnergyAustralia "Solar Max" plans.
        text = ("Solar Max is for electricity only and is available to "
                "eligible residential solar customers not receiving any "
                "Government feed-in-tariff. The daily export is averaged "
                "by dividing the total solar export by the number of days "
                "in each billing period")
        rule = parse_rule(text)
        # Solar Max text doesn't include rate1 in its eligibility — it's
        # named-only. Parser correctly returns None and lets the caller
        # skip / log. Pin that behaviour.
        assert rule is None


class TestParseQuantityFirstDialect:
    """Catalog: AGL Solar Feed-in Tarriff [sic]."""

    def test_agl_tiered_fit_exact_text(self):
        # 40 AGL plans use this exact wording (note typo "Tarriff").
        text = ("This plan features a tiered feed-in tariff. For the first "
                "10kWh exported each day, we'll pay you a higher feed-in "
                "tariff of 6c/kWh. Then, we'll pay 1.5c/kWh for the rest "
                "of that day")
        rule = parse_rule(text)
        assert rule is not None
        assert rule["tier1_c_per_kwh"] == Decimal("6")
        assert rule["cap_kwh"] == Decimal("10")
        assert rule["tier2_c_per_kwh"] == Decimal("1.5")
        assert rule["cap_window"] == "DAY"


class TestParseEdgeCases:
    def test_empty_string_returns_none(self):
        assert parse_rule("") is None
        assert parse_rule(None) is None  # type: ignore[arg-type]

    def test_marketing_copy_returns_none(self):
        # Common pattern: incentive name only, no math.
        assert parse_rule("Generous solar feed-in") is None

    def test_unrelated_disclaimer_returns_none(self):
        text = ("The Terms and Conditions for Feed-in Tariffs - Victoria "
                "applies to both additional and standard retailer feed-in "
                "tariff. When the benefit period ends you'll receive our "
                "standard feed-in tariff available at the time as published")
        assert parse_rule(text) is None


# ---------------------------------------------------------------------------
# apply_rule — math semantics
# ---------------------------------------------------------------------------


def _slots(day_exports: dict[str, list[float]]) -> list[dict]:
    """Build slot fixtures: {date_str: [export_kwh, ...]}.

    Each export becomes one slot at hh:00 starting 09:00.
    """
    out: list[dict] = []
    for date, exports in day_exports.items():
        for i, exp in enumerate(exports):
            hour = 9 + i
            out.append({
                "ts_local": f"{date}T{hour:02d}:00:00",
                "grid_export_kwh": exp,
            })
    return out


class TestApplyDayCap:
    """DAY cap_window — strict daily reset."""

    def test_alinta_single_day_below_cap(self):
        # 5 kWh exported, cap 10 kWh, tier1 7c/kWh, base FIT 0.04c.
        # Delta = (7 - 0.04) / 100 × 5 = 0.348 AUD credit.
        rule = {"tier1_c_per_kwh": Decimal("7"), "cap_kwh": Decimal("10"),
                "tier2_c_per_kwh": None, "cap_window": "DAY",
                "source": "test"}
        slots = _slots({"2026-05-15": [5.0]})
        b = _StubBreakdown()
        apply_rule(rule, slots, b, base_fit_c_per_kwh=Decimal("0.04"))
        # incentive_aud_inc_gst is DECREASED (negative = credit to user)
        assert b.incentive_aud_inc_gst == Decimal("-0.348")

    def test_alinta_single_day_above_cap_no_tier2(self):
        # 15 kWh exported, cap 10 kWh, tier1 7c, no tier2 (rate-first)
        # Tier1 credit: (7 - 0.04) / 100 × 10 = 0.696
        # Tier2 implicit: nothing (rule says fall back to base FIT,
        # which is what evaluator already credited)
        rule = {"tier1_c_per_kwh": Decimal("7"), "cap_kwh": Decimal("10"),
                "tier2_c_per_kwh": None, "cap_window": "DAY",
                "source": "test"}
        slots = _slots({"2026-05-15": [15.0]})
        b = _StubBreakdown()
        apply_rule(rule, slots, b, base_fit_c_per_kwh=Decimal("0.04"))
        assert b.incentive_aud_inc_gst == Decimal("-0.696")

    def test_agl_single_day_above_cap_with_tier2(self):
        # 25 kWh exported, cap 10 kWh, tier1 6c, tier2 1.5c, base 5c.
        # Tier1 delta: (6 - 5) / 100 × 10 = 0.10
        # Tier2 delta: (1.5 - 5) / 100 × 15 = -0.525 (tier2 BELOW base
        #   means user gets LESS than evaluator already credited)
        # Net: 0.10 + (-0.525) = -0.425; sign flips to user's pocket
        # So incentive_aud_inc_gst -= -0.425 → +0.425 (extra cost)
        rule = {"tier1_c_per_kwh": Decimal("6"), "cap_kwh": Decimal("10"),
                "tier2_c_per_kwh": Decimal("1.5"), "cap_window": "DAY",
                "source": "test"}
        slots = _slots({"2026-05-15": [25.0]})
        b = _StubBreakdown()
        apply_rule(rule, slots, b, base_fit_c_per_kwh=Decimal("5"))
        # Net mutation: incentive_aud_inc_gst -= 0.10  (tier 1 wins)
        #              then incentive_aud_inc_gst -= -0.525 (tier 2 loses)
        # Final: -0.10 + 0.525 = +0.425
        assert b.incentive_aud_inc_gst == Decimal("0.425")

    def test_day_cap_resets_each_day(self):
        # Two days, 8 kWh each. Cap 10 kWh per day. Tier1 7c, base 0.04c.
        # Each day below cap → 2 × (7-0.04)/100 × 8 = 1.1136
        rule = {"tier1_c_per_kwh": Decimal("7"), "cap_kwh": Decimal("10"),
                "tier2_c_per_kwh": None, "cap_window": "DAY",
                "source": "test"}
        slots = _slots({"2026-05-15": [8.0], "2026-05-16": [8.0]})
        b = _StubBreakdown()
        apply_rule(rule, slots, b, base_fit_c_per_kwh=Decimal("0.04"))
        assert b.incentive_aud_inc_gst == Decimal("-1.1136")

    def test_zero_export_no_credit(self):
        rule = {"tier1_c_per_kwh": Decimal("7"), "cap_kwh": Decimal("10"),
                "tier2_c_per_kwh": None, "cap_window": "DAY",
                "source": "test"}
        slots = _slots({"2026-05-15": [0.0, 0.0]})
        b = _StubBreakdown()
        apply_rule(rule, slots, b, base_fit_c_per_kwh=Decimal("0.04"))
        assert b.incentive_aud_inc_gst == Decimal("0")
        assert b.trace == []


class TestApplyPeriodCap:
    """PERIOD cap_window — Origin/EA monthly-averaged pool."""

    def test_origin_30day_period_within_pool(self):
        # 30 days × 8 kWh/day cap = 240 kWh effective pool.
        # 30 days × 7 kWh exported = 210 kWh, all under pool.
        # Tier1 12c, base 0.04c → (12 - 0.04)/100 × 210 = 25.116
        rule = {"tier1_c_per_kwh": Decimal("12"), "cap_kwh": Decimal("8"),
                "tier2_c_per_kwh": None, "cap_window": "PERIOD",
                "source": "test"}
        slots = _slots({f"2026-05-{day:02d}": [7.0] for day in range(1, 31)})
        b = _StubBreakdown()
        apply_rule(rule, slots, b, base_fit_c_per_kwh=Decimal("0.04"))
        assert b.incentive_aud_inc_gst == Decimal("-25.116")

    def test_origin_period_pool_exhausted_early(self):
        # 30 days × 8 cap = 240 kWh pool.
        # User over-exports first 10 days at 30 kWh/day = 300 kWh total
        # for first 10 days, then 0 thereafter. Pool exhausted on day 8.
        # Day 1-8: 30 kWh × 8 = 240 kWh credited at tier1.
        # Day 8 partial + day 9-10: 60 kWh overflow (no tier2 → no credit).
        # Tier1 delta: (12 - 0.04)/100 × 240 = 28.704
        rule = {"tier1_c_per_kwh": Decimal("12"), "cap_kwh": Decimal("8"),
                "tier2_c_per_kwh": None, "cap_window": "PERIOD",
                "source": "test"}
        slots = _slots({f"2026-05-{day:02d}": [30.0]
                        for day in range(1, 11)})
        # Pad to full 30-day period so effective_cap = 8 × 30 = 240
        for day in range(11, 31):
            slots.append({"ts_local": f"2026-05-{day:02d}T09:00:00",
                          "grid_export_kwh": 0.0})
        b = _StubBreakdown()
        apply_rule(rule, slots, b, base_fit_c_per_kwh=Decimal("0.04"))
        assert b.incentive_aud_inc_gst == Decimal("-28.704")

    def test_period_trace_records_window_type(self):
        rule = {"tier1_c_per_kwh": Decimal("12"), "cap_kwh": Decimal("8"),
                "tier2_c_per_kwh": None, "cap_window": "PERIOD",
                "source": "test"}
        slots = _slots({"2026-05-15": [5.0]})
        b = _StubBreakdown()
        apply_rule(rule, slots, b, base_fit_c_per_kwh=Decimal("0.04"))
        assert len(b.trace) == 1
        assert b.trace[0]["incentive"] == "tiered_fit"
        assert b.trace[0]["cap_window"] == "PERIOD"
        assert b.trace[0]["tier1_kwh"] == 5.0
        assert b.trace[0]["tier1_c_per_kwh"] == 12.0


# ---------------------------------------------------------------------------
# parse_from_incentives — full-incentive-list helper
# ---------------------------------------------------------------------------


class TestParseFromIncentives:
    def test_walks_eligibility_field_alinta(self):
        incentives = [{
            "displayName": "Solar Feed-in Tariff",
            "description": "Stepped FiT",
            "eligibility": ("This Energy Plan includes a stepped feed-in "
                            "tariff, where you will receive a feed-in of "
                            "7c/kWh for the first 10kW exported. For any "
                            "export after that you will obtain Alinta "
                            "Energy's standard retailer feed-in tariff "
                            "of 0.04c/kWh."),
        }]
        rule = parse_from_incentives(incentives)
        assert rule is not None
        assert rule["tier1_c_per_kwh"] == Decimal("7")
        assert rule["source_displayName"] == "Solar Feed-in Tariff"

    def test_walks_description_field_when_eligibility_empty(self):
        # AGL pattern: math sometimes lives in description, not eligibility.
        incentives = [{
            "displayName": "Solar Feed-in Tarriff",
            "description": ("This plan features a tiered feed-in tariff. "
                            "For the first 10kWh exported each day, we'll "
                            "pay you a higher feed-in tariff of 6c/kWh. "
                            "Then, we'll pay 1.5c/kWh for the rest of "
                            "that day"),
            "eligibility": "",
        }]
        rule = parse_from_incentives(incentives)
        assert rule is not None
        assert rule["tier2_c_per_kwh"] == Decimal("1.5")

    def test_returns_first_match_when_multiple_present(self):
        incentives = [
            {"displayName": "Loyalty", "eligibility": "Earn Qantas Points"},
            {"displayName": "Tiered FiT",
             "eligibility": "7c/kWh for the first 10 kWh"},
        ]
        rule = parse_from_incentives(incentives)
        assert rule is not None
        assert rule["tier1_c_per_kwh"] == Decimal("7")

    def test_no_match_returns_none(self):
        incentives = [
            {"displayName": "Welcome", "eligibility": "$50 sign-up credit"},
            {"displayName": "Greenpower", "eligibility": "100% matched"},
        ]
        assert parse_from_incentives(incentives) is None

    def test_empty_list_returns_none(self):
        assert parse_from_incentives([]) is None
        assert parse_from_incentives(None) is None  # type: ignore[arg-type]
