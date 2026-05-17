"""Tests for Phase 3.1 ranking-job orchestration.

The pure logic lives in ``cdr.ranking_job`` so it's unit-testable
without HA's app context (``PriceHawkCoordinator`` itself is
unreachable in test because its ``DataUpdateCoordinator[T]`` base
gets mocked away by ``tests/conftest.py``).

Coordinator-side wrappers (``schedule_daily_ranking``,
``cancel_ranking``, ``async_run_ranking_job``) are 1-line delegates
verified by integration. The ``_RANKING_RUN_HOUR / MINUTE`` constants
are smoke-tested via direct module import.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from custom_components.pricehawk.cdr.ranking_job import (
    DEFAULT_COMPETITOR_BRAND_FRAGMENTS,
    get_competitor_retailers,
    get_user_geography,
    run_ranking_job,
)
from custom_components.pricehawk.cdr.registry import RetailerEndpoint


def _retailer(name: str, brand_id: str = "1") -> RetailerEndpoint:
    return RetailerEndpoint(
        brand_id=brand_id,
        brand_name=name,
        base_uri=f"https://example/{name.lower()}",
        cdr_brand=name.lower(),
    )


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_competitor_fragments_includes_big_4(self):
        assert "agl" in DEFAULT_COMPETITOR_BRAND_FRAGMENTS
        assert "origin" in DEFAULT_COMPETITOR_BRAND_FRAGMENTS
        assert "energyaustralia" in DEFAULT_COMPETITOR_BRAND_FRAGMENTS
        assert "red energy" in DEFAULT_COMPETITOR_BRAND_FRAGMENTS

    def test_ranking_run_time_constants_after_midnight_rollover(self):
        """00:30 local lets midnight daily-cost rollover complete first."""
        from custom_components.pricehawk.coordinator import (
            _RANKING_RUN_HOUR,
            _RANKING_RUN_MINUTE,
        )
        assert _RANKING_RUN_HOUR == 0
        assert _RANKING_RUN_MINUTE == 30


# ---------------------------------------------------------------------------
# Geography extraction
# ---------------------------------------------------------------------------


class TestGetUserGeography:
    def test_extracts_postcode_and_distributor_from_cdr_plan(self):
        opts = {
            "cdr_postcode": "3104",
            "cdr_plan": {
                "data": {"geography": {"distributors": ["United Energy"]}}
            },
        }
        state, postcode, distributor = get_user_geography(opts)
        assert state is None  # derived later, not stored
        assert postcode == "3104"
        assert distributor == "United Energy"

    def test_missing_postcode_returns_none(self):
        opts = {"cdr_plan": {"data": {"geography": {}}}}
        _, postcode, _ = get_user_geography(opts)
        assert postcode is None

    def test_missing_cdr_plan_returns_none_distributor(self):
        opts = {"cdr_postcode": "3000"}
        _, postcode, distributor = get_user_geography(opts)
        assert postcode == "3000"
        assert distributor is None

    def test_first_distributor_picked_when_plan_has_multiple(self):
        opts = {"cdr_plan": {"data": {"geography": {"distributors": ["A", "B"]}}}}
        _, _, distributor = get_user_geography(opts)
        assert distributor == "A"

    def test_empty_distributors_list_returns_none(self):
        opts = {"cdr_plan": {"data": {"geography": {"distributors": []}}}}
        _, _, distributor = get_user_geography(opts)
        assert distributor is None

    def test_empty_options_returns_all_none(self):
        state, postcode, distributor = get_user_geography({})
        assert (state, postcode, distributor) == (None, None, None)

    def test_non_list_distributors_safely_skipped(self):
        """CR-fix: malformed payload may ship distributors as string/dict.
        ``"United Energy"[0]`` would silently become ``"U"`` and skew
        the ranking filter. Type guard returns None instead."""
        for bad in ["United Energy", {"name": "United"}, 42]:
            opts = {"cdr_plan": {"data": {"geography": {"distributors": bad}}}}
            _, _, distributor = get_user_geography(opts)
            assert distributor is None

    def test_non_dict_cdr_plan_safely_skipped(self):
        """CR-fix: ``cdr_plan`` shipped as a string/list/int doesn't
        raise — return None distributor instead of AttributeError."""
        for bad in ["not-a-dict", ["wrong", "shape"], 42, None]:
            opts = {"cdr_plan": bad}
            _, _, distributor = get_user_geography(opts)
            assert distributor is None

    def test_non_dict_data_safely_skipped(self):
        """``cdr_plan["data"]`` shipped as non-dict (string / list /
        None) returns None distributor — no AttributeError on
        ``.get("geography")``."""
        for bad in ["broken", [1, 2], 0, None]:
            opts = {"cdr_plan": {"data": bad}}
            _, _, distributor = get_user_geography(opts)
            assert distributor is None

    def test_non_dict_geography_safely_skipped(self):
        """``data["geography"]`` shipped as non-dict returns None
        distributor — no AttributeError on ``.get("distributors")``."""
        for bad in ["str-geo", [1], 42, None]:
            opts = {"cdr_plan": {"data": {"geography": bad}}}
            _, _, distributor = get_user_geography(opts)
            assert distributor is None

    def test_first_distributor_must_be_string(self):
        """Distributor list with non-str first element returns None —
        prevents accidentally passing a dict / int as the distributor
        filter to rank_alternatives."""
        opts = {"cdr_plan": {"data": {"geography": {
            "distributors": [{"name": "U"}, "AGL"],
        }}}}
        _, _, distributor = get_user_geography(opts)
        assert distributor is None


# ---------------------------------------------------------------------------
# Competitor retailer composition
# ---------------------------------------------------------------------------


class TestGetCompetitorRetailers:
    def test_includes_user_current_retailer_first(self):
        opts = {"cdr_plan": {"data": {"brand": "GloBird"}}}
        globird = _retailer("GloBird", brand_id="100")
        agl = _retailer("AGL Energy", brand_id="200")
        origin = _retailer("Origin", brand_id="300")
        ea = _retailer("EnergyAustralia", brand_id="400")
        red = _retailer("Red Energy", brand_id="500")
        endpoints = [globird, agl, origin, ea, red]

        with patch(
            "custom_components.pricehawk.cdr.ranking_job.get_registry",
            AsyncMock(return_value=(endpoints, "live")),
        ):
            result = asyncio.run(get_competitor_retailers(None, opts))
        assert result[0].brand_name == "GloBird"
        names = [r.brand_name for r in result]
        assert "AGL Energy" in names
        assert "Origin" in names
        assert "EnergyAustralia" in names
        assert "Red Energy" in names

    def test_dedup_when_current_retailer_is_a_competitor(self):
        """User on AGL: AGL appears once, not twice."""
        opts = {"cdr_plan": {"data": {"brand": "AGL"}}}
        agl = _retailer("AGL Energy", brand_id="200")
        origin = _retailer("Origin", brand_id="300")
        endpoints = [agl, origin]

        with patch(
            "custom_components.pricehawk.cdr.ranking_job.get_registry",
            AsyncMock(return_value=(endpoints, "live")),
        ):
            result = asyncio.run(get_competitor_retailers(None, opts))
        brand_ids = [r.brand_id for r in result]
        assert brand_ids.count("200") == 1

    def test_missing_brand_returns_only_big4(self):
        """No `brand` in current cdr_plan: still returns the big-4."""
        opts = {"cdr_plan": {"data": {}}}
        agl = _retailer("AGL Energy", brand_id="200")
        with patch(
            "custom_components.pricehawk.cdr.ranking_job.get_registry",
            AsyncMock(return_value=([agl], "live")),
        ):
            result = asyncio.run(get_competitor_retailers(None, opts))
        assert len(result) == 1
        assert result[0].brand_name == "AGL Energy"

    def test_unmatchable_fragment_silently_skipped(self):
        """If a competitor fragment finds no match in the registry,
        it's omitted from the result — doesn't raise."""
        opts = {"cdr_plan": {"data": {"brand": "AGL"}}}
        with patch(
            "custom_components.pricehawk.cdr.ranking_job.get_registry",
            AsyncMock(return_value=([], "baked-in")),
        ):
            result = asyncio.run(get_competitor_retailers(None, opts))
        assert result == []

    def test_custom_competitor_fragments_override_default(self):
        opts = {"cdr_plan": {"data": {}}}
        custom = _retailer("Sumo", brand_id="999")
        with patch(
            "custom_components.pricehawk.cdr.ranking_job.get_registry",
            AsyncMock(return_value=([custom], "live")),
        ):
            result = asyncio.run(get_competitor_retailers(
                None, opts, competitor_fragments=("sumo",)
            ))
        assert len(result) == 1
        assert result[0].brand_name == "Sumo"


# ---------------------------------------------------------------------------
# Top-level run_ranking_job
# ---------------------------------------------------------------------------


class TestRunRankingJob:
    def test_empty_retailers_returns_empty(self):
        opts = {"cdr_plan": {"data": {}}}
        with patch(
            "custom_components.pricehawk.cdr.ranking_job.get_registry",
            AsyncMock(return_value=([], "baked-in")),
        ):
            result = asyncio.run(run_ranking_job(None, opts))
        assert result == []

    def test_happy_path_forwards_geography_to_rank_alternatives(self):
        opts = {
            "cdr_postcode": "3104",
            "cdr_plan": {
                "data": {
                    "brand": "GloBird",
                    "geography": {"distributors": ["United Energy"]},
                }
            },
        }
        globird = _retailer("GloBird", brand_id="100")
        ranked = [{"planId": "BEST"}]

        with (
            patch(
                "custom_components.pricehawk.cdr.ranking_job.get_registry",
                AsyncMock(return_value=([globird], "live")),
            ),
            patch(
                "custom_components.pricehawk.cdr.ranking_job.rank_alternatives",
                AsyncMock(return_value=ranked),
            ) as rank_mock,
        ):
            result = asyncio.run(run_ranking_job(None, opts))
        assert result == ranked
        assert rank_mock.call_args.kwargs["postcode"] == "3104"
        assert rank_mock.call_args.kwargs["distributor"] == "United Energy"

    def test_passes_plan_cache_through(self):
        opts = {"cdr_plan": {"data": {"brand": "AGL"}}}
        agl = _retailer("AGL Energy", brand_id="200")
        my_cache: dict = {"P1": {"cached": True}}

        with (
            patch(
                "custom_components.pricehawk.cdr.ranking_job.get_registry",
                AsyncMock(return_value=([agl], "live")),
            ),
            patch(
                "custom_components.pricehawk.cdr.ranking_job.rank_alternatives",
                AsyncMock(return_value=[]),
            ) as rank_mock,
        ):
            asyncio.run(run_ranking_job(None, opts, plan_cache=my_cache))
        assert rank_mock.call_args.kwargs["cache"] is my_cache

    def test_top_k_forwarded(self):
        opts = {"cdr_plan": {"data": {"brand": "AGL"}}}
        agl = _retailer("AGL Energy", brand_id="200")
        with (
            patch(
                "custom_components.pricehawk.cdr.ranking_job.get_registry",
                AsyncMock(return_value=([agl], "live")),
            ),
            patch(
                "custom_components.pricehawk.cdr.ranking_job.rank_alternatives",
                AsyncMock(return_value=[]),
            ) as rank_mock,
        ):
            asyncio.run(run_ranking_job(None, opts, top_k=5))
        assert rank_mock.call_args.kwargs["top_k"] == 5
