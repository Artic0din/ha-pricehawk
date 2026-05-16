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

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.pricehawk.cdr.cdr_client import (
    CdrAPIError,
    CdrPlanNotFound,
    CdrUnavailable,
)
from custom_components.pricehawk.cdr.ranking import (
    DEFAULT_TOP_K,
    cheap_rank,
    cheap_rank_score,
    deep_rank,
    fetch_plans_for_retailer,
    filter_eligible_plans,
    matches_geography,
    rank_alternatives,
    summarize_for_sensor,
)
from custom_components.pricehawk.cdr.registry import RetailerEndpoint


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

    def test_negative_unit_price_scores_negatively(self):
        """Negative unitPrice (rare but observed during AEMO export-only
        feed-in plans) must produce a negative score, not be rejected.
        Documents the rank-toward-cheaper behaviour: a negative peak
        rate legitimately makes a plan rank cheaper than a positive
        one. AEGIS rule: tariff calculation changes require negative
        rate edge case tests."""
        plan = _make_plan(peak="-0.05", supply="1.00")
        # peak -5 * 0.7 = -3.5
        # supply 100 * 0.3 = 30
        # total = 26.5
        score = cheap_rank_score(plan)
        assert score == Decimal("26.500")
        # Sanity: negative-peak plan ranks cheaper than positive-peak.
        positive = _make_plan(peak="0.30", supply="1.00")
        assert cheap_rank_score(plan) < cheap_rank_score(positive)


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


# ---------------------------------------------------------------------------
# Orchestrator: fetch + rank
# ---------------------------------------------------------------------------


def _retailer(name: str = "GloBird", brand_id: str = "1") -> RetailerEndpoint:
    return RetailerEndpoint(
        brand_id=brand_id,
        brand_name=name,
        base_uri="https://example/" + name.lower(),
        cdr_brand=name.lower(),
    )


def _wrap_detail(plan: dict) -> dict:
    """Wrap a plan body in a CDR detail envelope shape (``{data: plan}``)."""
    return {"data": plan, "links": {}, "meta": {}}


class TestFetchPlansForRetailer:
    def test_happy_path_returns_unwrapped_details(self):
        retailer = _retailer()
        summaries = [{"planId": "P1"}, {"planId": "P2"}]
        details = [
            _wrap_detail(_make_plan(peak="0.30", supply="1.00")),
            _wrap_detail(_make_plan(peak="0.40", supply="1.10")),
        ]
        with (
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_list",
                AsyncMock(return_value=summaries),
            ),
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_detail",
                AsyncMock(side_effect=details),
            ),
        ):
            result = asyncio.run(
                fetch_plans_for_retailer(
                    session=None, retailer=retailer, detail_delay_sec=0
                )
            )
        assert len(result) == 2
        # Bodies are unwrapped (no envelope ``data`` key on result rows).
        assert "geography" in result[0]

    def test_uses_cache_to_skip_known_plans(self):
        retailer = _retailer()
        summaries = [{"planId": "P1"}, {"planId": "P2"}]
        cached_body = _make_plan(peak="0.25", supply="0.90")
        cache = {"P1": cached_body}
        fresh_detail = _wrap_detail(_make_plan(peak="0.40", supply="1.10"))
        detail_mock = AsyncMock(return_value=fresh_detail)
        with (
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_list",
                AsyncMock(return_value=summaries),
            ),
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_detail",
                detail_mock,
            ),
        ):
            result = asyncio.run(
                fetch_plans_for_retailer(
                    session=None, retailer=retailer, cache=cache,
                    detail_delay_sec=0,
                )
            )
        # Detail called only once (for the un-cached P2).
        assert detail_mock.call_count == 1
        assert len(result) == 2
        # Cache now has both plans.
        assert "P1" in cache and "P2" in cache

    def test_skips_summaries_without_planid(self):
        retailer = _retailer()
        summaries = [{"planId": "P1"}, {"customerType": "RESIDENTIAL"}]
        with (
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_list",
                AsyncMock(return_value=summaries),
            ),
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_detail",
                AsyncMock(return_value=_wrap_detail(_make_plan())),
            ),
        ):
            result = asyncio.run(
                fetch_plans_for_retailer(
                    session=None, retailer=retailer, detail_delay_sec=0
                )
            )
        assert len(result) == 1

    def test_list_unavailable_returns_empty(self):
        retailer = _retailer()
        with patch(
            "custom_components.pricehawk.cdr.ranking.fetch_plan_list",
            AsyncMock(side_effect=CdrUnavailable("HTTP 503")),
        ):
            result = asyncio.run(
                fetch_plans_for_retailer(
                    session=None, retailer=retailer, detail_delay_sec=0
                )
            )
        assert result == []

    def test_list_api_error_returns_empty(self):
        retailer = _retailer()
        with patch(
            "custom_components.pricehawk.cdr.ranking.fetch_plan_list",
            AsyncMock(side_effect=CdrAPIError("HTTP 400")),
        ):
            result = asyncio.run(
                fetch_plans_for_retailer(
                    session=None, retailer=retailer, detail_delay_sec=0
                )
            )
        assert result == []

    def test_detail_failures_skip_plan_not_whole_retailer(self):
        """One bad planId on a retailer mustn't sink the whole batch."""
        retailer = _retailer()
        summaries = [{"planId": "P1"}, {"planId": "P_STALE"}, {"planId": "P3"}]
        details = [
            _wrap_detail(_make_plan(peak="0.30")),
            CdrPlanNotFound("404"),
            _wrap_detail(_make_plan(peak="0.35")),
        ]
        with (
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_list",
                AsyncMock(return_value=summaries),
            ),
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_detail",
                AsyncMock(side_effect=details),
            ),
        ):
            result = asyncio.run(
                fetch_plans_for_retailer(
                    session=None, retailer=retailer, detail_delay_sec=0
                )
            )
        assert len(result) == 2  # stale plan dropped, two survivors

    def test_passes_brand_to_underlying_calls(self):
        """``cdr_brand`` discriminator must flow through to both list +
        detail calls so shared-base-URI retailers (Energy Locals brands etc)
        get disambiguated."""
        retailer = RetailerEndpoint(
            brand_id="1",
            brand_name="ARCLINE",
            base_uri="https://cdr.energymadeeasy.gov.au/energy-locals",
            cdr_brand="arcline",
        )
        list_mock = AsyncMock(return_value=[{"planId": "P1"}])
        detail_mock = AsyncMock(return_value=_wrap_detail(_make_plan()))
        with (
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_list",
                list_mock,
            ),
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_detail",
                detail_mock,
            ),
        ):
            asyncio.run(
                fetch_plans_for_retailer(
                    session=None, retailer=retailer, detail_delay_sec=0
                )
            )
        assert list_mock.call_args.kwargs["brand"] == "arcline"
        assert detail_mock.call_args.kwargs["brand"] == "arcline"


class TestRankAlternatives:
    def test_end_to_end_ranks_across_retailers(self):
        r1 = _retailer("R1", brand_id="1")
        r2 = _retailer("R2", brand_id="2")
        # r1 has 1 plan (cheap), r2 has 2 plans (mid + expensive).
        summaries_r1 = [{"planId": "R1-P1"}]
        summaries_r2 = [{"planId": "R2-P1"}, {"planId": "R2-P2"}]
        details_r1 = [
            _wrap_detail(_make_plan(peak="0.20", supply="0.90", postcodes=["3000"]))
        ]
        details_r2 = [
            _wrap_detail(_make_plan(peak="0.30", supply="1.00", postcodes=["3000"])),
            _wrap_detail(_make_plan(peak="0.45", supply="1.20", postcodes=["3000"])),
        ]
        all_details = details_r1 + details_r2

        def _list_for(_session, base_url, **_kwargs):
            if "r1" in base_url.lower():
                return summaries_r1
            return summaries_r2

        with (
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_list",
                AsyncMock(side_effect=_list_for),
            ),
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_detail",
                AsyncMock(side_effect=all_details),
            ),
        ):
            result = asyncio.run(rank_alternatives(
                session=None,
                retailers=[r1, r2],
                postcode="3000",
                top_k=10,
                detail_delay_sec=0,
            ))
        # 3 plans total, all eligible, sorted ascending by score
        assert len(result) == 3
        peaks = [
            p["electricityContract"]["tariffPeriod"][0]["timeOfUseRates"][0][
                "rates"
            ][0]["unitPrice"]
            for p in result
        ]
        assert peaks == ["0.20", "0.30", "0.45"]

    def test_geography_filter_drops_non_matching(self):
        r = _retailer()
        # Postcode 3000 plan + 2000 plan; only 3000 should survive.
        details = [
            _wrap_detail(_make_plan(peak="0.30", postcodes=["3000"])),
            _wrap_detail(_make_plan(peak="0.20", postcodes=["2000"])),
        ]
        with (
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_list",
                AsyncMock(return_value=[{"planId": "P1"}, {"planId": "P2"}]),
            ),
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_detail",
                AsyncMock(side_effect=details),
            ),
        ):
            result = asyncio.run(rank_alternatives(
                session=None, retailers=[r], postcode="3000",
                top_k=10, detail_delay_sec=0,
            ))
        # 2 plans fetched, 1 survives geography filter.
        assert len(result) == 1

    def test_top_k_truncates_across_retailers(self):
        r = _retailer()
        details = [
            _wrap_detail(_make_plan(peak=f"0.{20 + i * 2}", postcodes=["3000"]))
            for i in range(10)
        ]
        with (
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_list",
                AsyncMock(return_value=[{"planId": f"P{i}"} for i in range(10)]),
            ),
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_detail",
                AsyncMock(side_effect=details),
            ),
        ):
            result = asyncio.run(rank_alternatives(
                session=None, retailers=[r], postcode="3000",
                top_k=3, detail_delay_sec=0,
            ))
        assert len(result) == 3

    def test_empty_retailers_returns_empty(self):
        result = asyncio.run(rank_alternatives(
            session=None, retailers=[], postcode="3000",
        ))
        assert result == []

    def test_failed_retailer_doesnt_block_others(self):
        r_bad = _retailer("Bad", brand_id="1")
        r_good = _retailer("Good", brand_id="2")

        def _list_for(_session, base_url, **_kwargs):
            if "bad" in base_url.lower():
                raise CdrUnavailable("HTTP 503")
            return [{"planId": "G1"}]

        with (
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_list",
                AsyncMock(side_effect=_list_for),
            ),
            patch(
                "custom_components.pricehawk.cdr.ranking.fetch_plan_detail",
                AsyncMock(return_value=_wrap_detail(_make_plan(postcodes=["3000"]))),
            ),
        ):
            result = asyncio.run(rank_alternatives(
                session=None,
                retailers=[r_bad, r_good],
                postcode="3000",
                detail_delay_sec=0,
            ))
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Deep-rank (evaluator integration)
# ---------------------------------------------------------------------------


def _consumption_slots(
    n_days: int = 1,
    *,
    kwh_per_slot: float = 0.5,
) -> list[dict]:
    """Build a minimal slot list spanning n_days at 30-min granularity.

    48 slots per day. ``grid_import_kwh`` is set per slot; export defaults
    to zero. ``ts_local`` is the half-hour-aligned ISO timestamp the
    evaluator expects."""
    from datetime import datetime, timedelta
    slots: list[dict] = []
    start = datetime(2026, 5, 1, 0, 0)
    for d in range(n_days):
        for s in range(48):
            ts = start + timedelta(days=d, minutes=30 * s)
            slots.append({
                "ts_local": ts.isoformat(),
                "grid_import_kwh": kwh_per_slot,
                "grid_export_kwh": 0.0,
                "solar_export_kwh": 0.0,
            })
    return slots


def _make_full_plan(
    *,
    plan_id: str = "P1",
    peak: str = "0.30",
    supply: str = "1.00",
    postcodes: list[str] | None = None,
) -> dict:
    """Build a CDR PlanDetailV2 body the evaluator can actually consume."""
    geo: dict = {}
    if postcodes is not None:
        geo["includedPostcodes"] = postcodes
    return {
        "planId": plan_id,
        "customerType": "RESIDENTIAL",
        "fuelType": "ELECTRICITY",
        "geography": geo,
        "electricityContract": {
            "pricingModel": "SINGLE_RATE",
            "tariffPeriod": [
                {
                    "displayName": "Always",
                    "dailySupplyCharge": supply,
                    "rateBlockUType": "singleRate",
                    "timeOfUseRates": [
                        {
                            "displayName": "Anytime",
                            "rates": [{"unitPrice": peak}],
                            "timeOfUse": [],
                            "type": "SINGLE_RATE",
                        }
                    ],
                }
            ],
        },
    }


class TestDeepRank:
    def test_orders_by_total_projected_cost(self):
        """Lower per-kWh + lower supply must rank cheaper for the same load."""
        cheap = _make_full_plan(plan_id="cheap", peak="0.20", supply="0.80")
        mid = _make_full_plan(plan_id="mid", peak="0.30", supply="1.00")
        expensive = _make_full_plan(plan_id="exp", peak="0.45", supply="1.20")
        slots = _consumption_slots(n_days=2, kwh_per_slot=0.5)
        ranked = deep_rank([expensive, cheap, mid], slots)
        ids = [p.get("planId") for p, _ in ranked]
        assert ids == ["cheap", "mid", "exp"]

    def test_returns_plan_with_breakdown(self):
        plan = _make_full_plan()
        slots = _consumption_slots()
        ranked = deep_rank([plan], slots)
        assert len(ranked) == 1
        returned_plan, bd = ranked[0]
        assert returned_plan is plan
        assert bd.total_aud_inc_gst > 0
        assert bd.slot_count == 48

    def test_empty_slots_returns_empty(self):
        plan = _make_full_plan()
        assert deep_rank([plan], []) == []

    def test_zero_slot_breakdown_dropped(self):
        """A plan with no tariffPeriod can't be evaluated honestly — drop it
        rather than zero-score it (would falsely rank as 'free')."""
        broken = {
            "planId": "broken",
            "electricityContract": {"tariffPeriod": []},
        }
        good = _make_full_plan(plan_id="good")
        slots = _consumption_slots()
        ranked = deep_rank([broken, good], slots)
        ids = [p.get("planId") for p, _ in ranked]
        assert ids == ["good"]

    def test_evaluator_exception_doesnt_sink_batch_explicit_mock(self):
        """Mock the evaluator to raise on the first plan only. Tests the
        exception-isolation contract directly without relying on the
        real evaluator's failure modes (which could become more
        defensive over time and silently no-op this test)."""
        bad = _make_full_plan(plan_id="bad")
        good = _make_full_plan(plan_id="good")
        slots = _consumption_slots()
        good_bd = MagicMock(slot_count=48, total_aud_inc_gst=Decimal("12.50"))

        def _selective(plan, *_args, **_kwargs):
            if plan.get("planId") == "bad":
                raise RuntimeError("simulated evaluator crash")
            return good_bd

        with patch(
            "custom_components.pricehawk.cdr.ranking.evaluate",
            side_effect=_selective,
        ):
            ranked = deep_rank([bad, good], slots)
        ids = [p.get("planId") for p, _ in ranked]
        assert ids == ["good"]

    def test_passes_entry_options_through(self):
        """`entry_options` (opt-in fields like OVO interest balance) must
        flow through to evaluator so per-plan credit math fires."""
        plan = _make_full_plan()
        slots = _consumption_slots()
        fake_bd = MagicMock()
        fake_bd.slot_count = 48
        fake_bd.total_aud_inc_gst = Decimal("10.00")
        opts = {"ovo_interest_balance_aud": 250.0}
        with patch(
            "custom_components.pricehawk.cdr.ranking.evaluate",
            return_value=fake_bd,
        ) as eval_mock:
            deep_rank([plan], slots, entry_options=opts)
        # entry_options was forwarded
        assert eval_mock.call_args.kwargs.get("entry_options") == opts


# ---------------------------------------------------------------------------
# Sensor summary
# ---------------------------------------------------------------------------


class TestSummarizeForSensor:
    def test_extracts_headline_fields(self):
        plan = {
            "planId": "P1",
            "displayName": "Super Saver",
            "brand": "GloBird",
            "customerType": "RESIDENTIAL",
            "electricityContract": {
                "tariffPeriod": [
                    {
                        "dailySupplyCharge": "1.05",
                        "timeOfUseRates": [{"rates": [{"unitPrice": "0.36"}]}],
                    }
                ]
            },
        }
        s = summarize_for_sensor(plan)
        assert s["plan_id"] == "P1"
        assert s["display_name"] == "Super Saver"
        assert s["brand"] == "GloBird"
        assert s["customer_type"] == "RESIDENTIAL"
        assert s["peak_c_per_kwh"] == 36.0
        assert s["supply_c_per_day"] == 105.0
        # score = 36*0.7 + 105*0.3 = 25.2 + 31.5 = 56.7
        assert s["score"] == 56.7

    def test_unscored_plan_returns_none_score(self):
        plan = {"planId": "BROKEN", "electricityContract": {"tariffPeriod": []}}
        s = summarize_for_sensor(plan)
        assert s["plan_id"] == "BROKEN"
        assert s["peak_c_per_kwh"] is None
        assert s["supply_c_per_day"] is None
        assert s["score"] is None

    def test_missing_top_level_fields_return_none(self):
        s = summarize_for_sensor({})
        assert s["plan_id"] is None
        assert s["display_name"] is None
        assert s["brand"] is None

    def test_output_is_json_serialisable(self):
        """HA recorder requires entity attributes to be JSON-encodable.
        ``Decimal`` is not — verify summary returns plain floats."""
        import json
        plan = _make_plan(peak="0.30", supply="1.00")
        s = summarize_for_sensor(plan)
        # Round-trip through json without raising.
        json.loads(json.dumps(s))
