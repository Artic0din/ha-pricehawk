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
    _state_from_dwt_region,
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

    def test_state_derived_from_dwt_region_when_cdr_postcode_unset(self):
        """Live UAT 2026-05-24: a DWT-only user (no CDR wizard) had no
        state filter on the ranking pipeline → top-K included AGL/Origin
        plans flagged for other states the user can't purchase from.
        Fallback derives the AU state from the configured DWT AEMO region."""
        cases = [
            ("VIC1", "VIC"),
            ("NSW1", "NSW"),
            ("QLD1", "QLD"),
            ("SA1", "SA"),
            ("TAS1", "TAS"),
        ]
        for region, expected_state in cases:
            opts = {"dwt_region": region}
            state, postcode, distributor = get_user_geography(opts)
            assert state == expected_state, (
                f"DWT region {region!r} should derive state {expected_state!r}, "
                f"got {state!r}"
            )
            assert postcode is None
            assert distributor is None

    def test_state_falls_back_to_dwt_region_when_cdr_plan_present(self):
        """Even with a CDR plan present (so postcode + distributor are
        known), the explicit state filter from DWT region adds an extra
        guard. Useful when retailers ship a plan with broad postcode
        coverage but the user only wants their state."""
        opts = {
            "dwt_region": "VIC1",
            "cdr_postcode": "3104",
            "cdr_plan": {
                "data": {"geography": {"distributors": ["United Energy"]}}
            },
        }
        state, postcode, distributor = get_user_geography(opts)
        assert state == "VIC"
        assert postcode == "3104"
        assert distributor == "United Energy"

    def test_unknown_dwt_region_returns_none_state(self):
        """Robustness: a malformed or unexpected ``dwt_region`` value
        should drop back to ``state=None`` (wildcard) rather than
        propagate garbage into the state filter."""
        for bad in ["WA1", "ZZ", "", 12345, None]:
            opts = {"dwt_region": bad}
            state, _, _ = get_user_geography(opts)
            assert state is None, (
                f"dwt_region {bad!r} must produce state=None; got {state!r}"
            )

    def test_dwt_region_case_insensitive(self):
        opts = {"dwt_region": "vic1"}
        state, _, _ = get_user_geography(opts)
        assert state == "VIC"

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
# Constitution P17 retroactive coverage — _state_from_dwt_region helper
# ---------------------------------------------------------------------------
#
# The ``_state_from_dwt_region`` helper + ``_AEMO_REGION_TO_STATE`` map +
# the ``dwt_region`` fallback path inside ``get_user_geography`` shipped
# without direct unit tests (the parent class above exercises some of
# these paths indirectly, but the helper itself was untested). Engineering
# Constitution P17 ("Tests Are Part of the Fix") requires retroactive
# coverage of meaningful logic. These tests pin:
#   - success path: NSW1 / VIC1 → state strings
#   - failure path: unknown region → None
#   - integration path: ``get_user_geography`` consumes the helper when
#     no explicit state is otherwise derivable
#   - precedence path: an explicit ``state`` option on the config-entry
#     options dict does NOT override the dwt_region-derived state today
#     (current contract — see docstring on the precedence test).


class TestStateFromDwtRegion:
    """Direct unit tests for the private ``_state_from_dwt_region`` helper.

    Constitution P17 retroactive coverage. Tests use the underscore-
    prefixed name deliberately — the helper IS the public-of-its-module
    surface (it has a docstring, is referenced from ``get_user_geography``,
    and shapes ranking-pipeline output).
    """

    def test_state_from_dwt_region_NSW1_returns_NSW(self):
        """``NSW1`` AEMO region → ``"NSW"`` state code."""
        assert _state_from_dwt_region({"dwt_region": "NSW1"}) == "NSW"

    def test_state_from_dwt_region_VIC1_returns_VIC(self):
        """``VIC1`` AEMO region → ``"VIC"`` state code."""
        assert _state_from_dwt_region({"dwt_region": "VIC1"}) == "VIC"

    def test_state_from_dwt_region_unknown_returns_None(self):
        """Unknown / malformed regions must produce ``None`` rather than
        propagating garbage into the downstream ``state`` filter.

        Covers: unrecognised AEMO codes (``WA1`` — not in the NEM),
        empty strings, non-string types, and entirely missing options.
        """
        assert _state_from_dwt_region({"dwt_region": "WA1"}) is None
        assert _state_from_dwt_region({"dwt_region": "ZZ"}) is None
        assert _state_from_dwt_region({"dwt_region": ""}) is None
        assert _state_from_dwt_region({"dwt_region": 12345}) is None
        assert _state_from_dwt_region({"dwt_region": None}) is None
        assert _state_from_dwt_region({}) is None

    def test_get_user_geography_uses_dwt_region_when_state_missing(self):
        """Integration: when the config-entry options dict has no other
        state source (no ``cdr_plan`` geography state field exists in
        the current schema), ``get_user_geography`` MUST surface the
        dwt-region-derived state so the ranking pipeline filters to the
        user's actual NEM region.

        Regression guard against the live UAT 2026-05-24 bug where a
        VIC DWT-only user's top-K included plans from other states.
        """
        opts = {"dwt_region": "NSW1"}
        state, postcode, distributor = get_user_geography(opts)
        assert state == "NSW"
        assert postcode is None
        assert distributor is None

    def test_get_user_geography_explicit_state_overrides_dwt_region(self):
        """Documents the current precedence contract.

        Today there is no explicit ``state`` option key consumed by
        ``get_user_geography`` — state is ONLY derived from
        ``dwt_region``. This test pins that contract: passing an extra
        ``state`` field on the options dict is silently ignored, and
        the dwt-region-derived value still wins. If a future change
        introduces an explicit ``state`` option (e.g. via a manual
        override in the options flow), this test will fail and force
        the precedence to be re-considered explicitly rather than
        slipping in implicitly.
        """
        opts = {"state": "QLD", "dwt_region": "VIC1"}
        state, _, _ = get_user_geography(opts)
        # Current contract: dwt_region drives state; explicit ``state``
        # key is not consumed. If this assertion ever flips, the
        # precedence rule MUST be added to ``get_user_geography``'s
        # docstring and a CHANGELOG entry MUST call it out.
        assert state == "VIC"


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
