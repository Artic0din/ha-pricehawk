"""Tests for cdr.ranking — Phase 3.1 multi-plan ranking engine.

Covers:
- ``matches_geography`` — postcode/distributor/state filter combinations.
- ``cheap_rank_score`` — heuristic on TOU + flat plans, malformed payloads.
- ``filter_eligible_plans`` — bulk geography pass.
- ``cheap_rank`` — sort + top-K + malformed-plan drop.

All test data is hand-built CDR-shaped dicts; no real plan fixtures
are pulled here so the test stays fast and the heuristic is exercised
under controlled conditions.
"""
from __future__ import annotations

from decimal import Decimal

from custom_components.pricehawk.cdr.ranking import (
    DEFAULT_TOP_K,
    cheap_rank,
    cheap_rank_score,
    filter_eligible_plans,
    matches_geography,
)


def _make_plan(
    *,
    peak: str = "0.30",
    supply: str = "1.00",
    postcodes: list[str] | None = None,
    excluded: list[str] | None = None,
    distributors: list[str] | None = None,
    state: str | None = None,
) -> dict:
    """Build a CDR-shaped plan body. Single tariff period with one TOU
    rate set so cheap_rank can score it."""
    geo: dict = {}
    if postcodes is not None:
        geo["includedPostcodes"] = postcodes
    if excluded is not None:
        geo["excludedPostcodes"] = excluded
    if distributors is not None:
        geo["distributors"] = distributors
    if state is not None:
        geo["state"] = state

    return {
        "geography": geo,
        "electricityContract": {
            "tariffPeriod": [
                {
                    "dailySupplyCharge": supply,
                    "timeOfUseRates": [
                        {"rates": [{"unitPrice": peak}]},
                    ],
                }
            ]
        },
    }


# ---------------------------------------------------------------------------
# Geography matching
# ---------------------------------------------------------------------------


class TestMatchesGeography:
    def test_no_filter_accepts_any_plan(self):
        plan = _make_plan(postcodes=["3000"])
        assert matches_geography(plan) is True

    def test_postcode_in_included_passes(self):
        plan = _make_plan(postcodes=["3000", "3001"])
        assert matches_geography(plan, postcode="3000") is True

    def test_postcode_outside_included_fails(self):
        plan = _make_plan(postcodes=["3000", "3001"])
        assert matches_geography(plan, postcode="2000") is False

    def test_postcode_in_excluded_fails(self):
        plan = _make_plan(postcodes=["3000", "3001"], excluded=["3000"])
        assert matches_geography(plan, postcode="3000") is False

    def test_distributor_match_case_insensitive(self):
        plan = _make_plan(distributors=["United Energy"])
        assert matches_geography(plan, distributor="UNITED ENERGY") is True
        assert matches_geography(plan, distributor="united energy") is True

    def test_distributor_miss_fails(self):
        plan = _make_plan(distributors=["United Energy"])
        assert matches_geography(plan, distributor="Powercor") is False

    def test_state_match_case_insensitive(self):
        plan = _make_plan(state="VIC")
        assert matches_geography(plan, state="vic") is True

    def test_state_miss_fails(self):
        plan = _make_plan(state="NSW")
        assert matches_geography(plan, state="VIC") is False

    def test_combined_filters_all_must_match(self):
        plan = _make_plan(
            postcodes=["3000"],
            distributors=["United Energy"],
            state="VIC",
        )
        assert matches_geography(
            plan, postcode="3000", distributor="United Energy", state="VIC"
        ) is True
        assert matches_geography(
            plan, postcode="3000", distributor="Powercor", state="VIC"
        ) is False

    def test_missing_geography_block_treated_as_national(self):
        plan = {"electricityContract": {"tariffPeriod": []}}
        assert matches_geography(plan, postcode="3000") is True

    def test_no_included_postcodes_passes_when_filter_set(self):
        """If a plan omits includedPostcodes entirely (national plan),
        any postcode is acceptable."""
        plan = _make_plan(distributors=["United Energy"])
        assert matches_geography(plan, postcode="3000") is True

    def test_unknown_distributor_field_in_plan_is_wildcard(self):
        """If a plan omits distributors entirely, treat as nationally
        available — don't accidentally drop matchless plans."""
        plan = _make_plan(postcodes=["3000"])
        assert matches_geography(plan, distributor="United Energy") is True


# ---------------------------------------------------------------------------
# Cheap-rank heuristic
# ---------------------------------------------------------------------------


class TestCheapRankScore:
    def test_basic_tou_plan_scored(self):
        plan = _make_plan(peak="0.30", supply="1.00")
        # peak 30 c/kWh * 0.7 = 21
        # supply 100 c/day * 0.3 = 30
        # total = 51
        assert cheap_rank_score(plan) == Decimal("51.00")

    def test_higher_peak_scores_higher(self):
        cheap = _make_plan(peak="0.20", supply="1.00")
        expensive = _make_plan(peak="0.45", supply="1.00")
        assert cheap_rank_score(cheap) < cheap_rank_score(expensive)

    def test_higher_supply_scores_higher(self):
        cheap = _make_plan(peak="0.30", supply="0.80")
        expensive = _make_plan(peak="0.30", supply="1.50")
        assert cheap_rank_score(cheap) < cheap_rank_score(expensive)

    def test_tou_picks_highest_rate_as_peak(self):
        """Multiple TOU windows: peak is whichever has the largest unitPrice."""
        plan = {
            "electricityContract": {
                "tariffPeriod": [
                    {
                        "dailySupplyCharge": "1.00",
                        "timeOfUseRates": [
                            {"rates": [{"unitPrice": "0.15"}]},  # off-peak
                            {"rates": [{"unitPrice": "0.40"}]},  # peak
                            {"rates": [{"unitPrice": "0.25"}]},  # shoulder
                        ],
                    }
                ]
            }
        }
        # peak 40 * 0.7 + supply 100 * 0.3 = 28 + 30 = 58
        assert cheap_rank_score(plan) == Decimal("58.00")

    def test_no_tariff_period_returns_none(self):
        plan = {"electricityContract": {"tariffPeriod": []}}
        assert cheap_rank_score(plan) is None

    def test_no_tou_rates_returns_none(self):
        plan = {
            "electricityContract": {
                "tariffPeriod": [
                    {"dailySupplyCharge": "1.00", "timeOfUseRates": []}
                ]
            }
        }
        assert cheap_rank_score(plan) is None

    def test_malformed_unit_price_skipped_not_raised(self):
        """Garbage in a single rate doesn't bork the whole plan score —
        the parseable rates still rank the plan."""
        plan = {
            "electricityContract": {
                "tariffPeriod": [
                    {
                        "dailySupplyCharge": "1.00",
                        "timeOfUseRates": [
                            {"rates": [{"unitPrice": "not-a-number"}]},
                            {"rates": [{"unitPrice": "0.30"}]},
                        ],
                    }
                ]
            }
        }
        # Falls back to the parseable rate (30c).
        assert cheap_rank_score(plan) == Decimal("51.00")

    def test_all_malformed_returns_none(self):
        plan = {
            "electricityContract": {
                "tariffPeriod": [
                    {
                        "dailySupplyCharge": "1.00",
                        "timeOfUseRates": [
                            {"rates": [{"unitPrice": "garbage"}]},
                        ],
                    }
                ]
            }
        }
        assert cheap_rank_score(plan) is None

    def test_malformed_supply_returns_none(self):
        plan = _make_plan(peak="0.30", supply="garbage")
        assert cheap_rank_score(plan) is None

    def test_empty_plan_returns_none(self):
        assert cheap_rank_score({}) is None


# ---------------------------------------------------------------------------
# Bulk filter
# ---------------------------------------------------------------------------


class TestFilterEligiblePlans:
    def test_drops_non_matching_postcodes(self):
        plans = [
            _make_plan(postcodes=["3000"]),
            _make_plan(postcodes=["2000"]),
            _make_plan(postcodes=["3000", "3100"]),
        ]
        result = filter_eligible_plans(plans, postcode="3000")
        assert len(result) == 2

    def test_empty_list_returns_empty(self):
        assert filter_eligible_plans([], postcode="3000") == []


# ---------------------------------------------------------------------------
# Top-K sort
# ---------------------------------------------------------------------------


class TestCheapRank:
    def test_sorted_ascending_by_score(self):
        plans = [
            _make_plan(peak="0.40", supply="1.20"),  # high
            _make_plan(peak="0.20", supply="0.80"),  # low
            _make_plan(peak="0.30", supply="1.00"),  # mid
        ]
        ranked = cheap_rank(plans, top_k=3)
        peaks = [
            p["electricityContract"]["tariffPeriod"][0]["timeOfUseRates"][0][
                "rates"
            ][0]["unitPrice"]
            for p in ranked
        ]
        assert peaks == ["0.20", "0.30", "0.40"]

    def test_top_k_truncates(self):
        plans = [
            _make_plan(peak=f"0.{20 + i * 2}", supply="1.00") for i in range(10)
        ]
        ranked = cheap_rank(plans, top_k=3)
        assert len(ranked) == 3

    def test_malformed_plans_dropped_not_zero_scored(self):
        """A malformed plan returning None must NOT rank as cheapest
        (would falsely surface as best). It's dropped."""
        plans = [
            _make_plan(peak="0.30", supply="1.00"),
            {"electricityContract": {"tariffPeriod": []}},  # malformed
            _make_plan(peak="0.20", supply="1.00"),
        ]
        ranked = cheap_rank(plans, top_k=10)
        assert len(ranked) == 2  # malformed dropped

    def test_default_top_k_constant(self):
        assert DEFAULT_TOP_K == 20

    def test_empty_input_returns_empty(self):
        assert cheap_rank([], top_k=10) == []
