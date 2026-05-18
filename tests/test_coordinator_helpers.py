"""Phase 3.0g — tests for coordinator-level pure helpers.

CodeRabbit + Sourcery flagged the inline peak-rate derivation in
`_build_data_dict` as brittle. Extracted to module-level
`_extract_peak_rate_c_inc_gst(cdr_plan)` and pinned with edge cases.
"""
from __future__ import annotations

from custom_components.pricehawk.coordinator import (
    _extract_peak_rate_c_inc_gst,
    build_backfill_plan_set,
    build_named_comparator_provider,
)


def _plan(unit_price: str | float = "0.36") -> dict:
    """Minimal ZEROHERO-shaped CDR plan envelope with PEAK rate."""
    return {
        "data": {
            "electricityContract": {
                "tariffPeriod": [{
                    "rateBlockUType": "timeOfUseRates",
                    "timeOfUseRates": [
                        {
                            "type": "PEAK",
                            "rates": [{"unitPrice": str(unit_price)}],
                        },
                    ],
                }],
            },
        },
    }


# --- Happy path -------------------------------------------------------


def test_extracts_peak_rate_inc_gst():
    """0.36 ex-GST $/kWh × 100 × 1.10 = 39.6 c/kWh inc-GST."""
    rate = _extract_peak_rate_c_inc_gst(_plan("0.36"))
    assert rate is not None
    assert abs(rate - 39.6) < 0.001


def test_extracts_peak_when_block_is_list():
    """Some retailers nest periods directly under rateBlockUType key
    as a list (older CDR plans). Helper accepts both shapes."""
    plan = {
        "data": {
            "electricityContract": {
                "tariffPeriod": [{
                    "rateBlockUType": "timeOfUseRates",
                    "timeOfUseRates": [
                        {"type": "PEAK", "rates": [{"unitPrice": "0.42"}]},
                    ],
                }],
            },
        },
    }
    rate = _extract_peak_rate_c_inc_gst(plan)
    assert abs(rate - 46.2) < 0.001


def test_handles_lowercase_peak_type():
    """Period type might be 'peak', 'Peak', 'PEAK' — all valid."""
    plan = _plan()
    plan["data"]["electricityContract"]["tariffPeriod"][0]["timeOfUseRates"][0]["type"] = "peak"
    rate = _extract_peak_rate_c_inc_gst(plan)
    assert abs(rate - 39.6) < 0.001


# --- Edge cases ------------------------------------------------------


def test_empty_plan_returns_none():
    assert _extract_peak_rate_c_inc_gst({}) is None
    assert _extract_peak_rate_c_inc_gst(None) is None


def test_missing_tariff_period_returns_none():
    plan = {"data": {"electricityContract": {"tariffPeriod": []}}}
    assert _extract_peak_rate_c_inc_gst(plan) is None


def test_missing_electricity_contract_returns_none():
    assert _extract_peak_rate_c_inc_gst({"data": {}}) is None


def test_no_peak_period_returns_none():
    """Plan with only OFF_PEAK + SHOULDER (no PEAK) returns None."""
    plan = _plan()
    plan["data"]["electricityContract"]["tariffPeriod"][0]["timeOfUseRates"] = [
        {"type": "OFF_PEAK", "rates": [{"unitPrice": "0.10"}]},
        {"type": "SHOULDER", "rates": [{"unitPrice": "0.25"}]},
    ]
    assert _extract_peak_rate_c_inc_gst(plan) is None


def test_non_numeric_unitprice_returns_none():
    """Bad data from CDR (non-numeric unitPrice) handled gracefully."""
    plan = _plan("not-a-number")
    assert _extract_peak_rate_c_inc_gst(plan) is None


def test_empty_rates_list_returns_none():
    plan = _plan()
    plan["data"]["electricityContract"]["tariffPeriod"][0]["timeOfUseRates"][0]["rates"] = []
    assert _extract_peak_rate_c_inc_gst(plan) is None


def test_malformed_block_returns_none():
    """rateBlockUType points to a non-existent key."""
    plan = {
        "data": {
            "electricityContract": {
                "tariffPeriod": [{"rateBlockUType": "bogusKey"}],
            },
        },
    }
    assert _extract_peak_rate_c_inc_gst(plan) is None


def test_malformed_period_in_list_skipped():
    """One bad period (string instead of dict) doesn't crash; finds the
    valid PEAK after it."""
    plan = _plan()
    plan["data"]["electricityContract"]["tariffPeriod"][0]["timeOfUseRates"] = [
        "garbage",  # malformed
        {"type": "PEAK", "rates": [{"unitPrice": "0.36"}]},
    ]
    rate = _extract_peak_rate_c_inc_gst(plan)
    assert abs(rate - 39.6) < 0.001


# ---------------------------------------------------------------------------
# Phase 3.2 — build_backfill_plan_set (module-level pure helper)
# ---------------------------------------------------------------------------


class TestBuildBackfillPlanSet:
    def _cdr_plan(self, plan_id: str = "GLO123") -> dict:
        return {
            "data": {
                "planId": plan_id,
                "electricityContract": {"pricingModel": "SINGLE_RATE"},
            }
        }

    def test_includes_current_plan_keyed_by_provider_id(self):
        plans = build_backfill_plan_set(
            options={"cdr_plan": self._cdr_plan()},
            current_plan_id="current_glo123",
            ranked_alternatives=[],
            plan_cache={},
        )
        assert "current_glo123" in plans
        assert plans["current_glo123"]["planId"] == "GLO123"

    def test_keys_alternatives_with_alt_prefix(self):
        """Top-K alts surface as ``alt_<planId>`` keys — rollup sensors
        (Phase 3.3) filter on this prefix to find alternatives."""
        plans = build_backfill_plan_set(
            options={"cdr_plan": None},
            current_plan_id="current_x",
            ranked_alternatives=[
                {"planId": "AGL900"},
                {"planId": "ORG456"},
            ],
            plan_cache={
                "AGL900": {"planId": "AGL900",
                           "electricityContract": {"pricingModel": "SINGLE_RATE"}},
                "ORG456": {"planId": "ORG456",
                           "electricityContract": {"pricingModel": "SINGLE_RATE"}},
            },
        )
        assert "alt_AGL900" in plans
        assert "alt_ORG456" in plans

    def test_skips_alts_without_plan_id(self):
        """Alts missing a planId / non-dict / empty planId are dropped."""
        plans = build_backfill_plan_set(
            options={"cdr_plan": None},
            current_plan_id="current_x",
            ranked_alternatives=[
                {"brand": "AGL"},          # no planId
                {"planId": ""},            # empty planId
                "not-a-dict",              # non-dict
                {"planId": "GOOD"},
            ],
            plan_cache={
                "GOOD": {"planId": "GOOD",
                         "electricityContract": {"pricingModel": "SINGLE_RATE"}},
            },
        )
        assert list(plans.keys()) == ["alt_GOOD"]

    def test_skips_alts_missing_from_plan_cache(self):
        """Alt with planId but no full body in cache and no body on the
        alt itself is excluded — evaluator needs the full PlanDetailV2."""
        plans = build_backfill_plan_set(
            options={"cdr_plan": None},
            current_plan_id="current_x",
            ranked_alternatives=[{"planId": "MISSING"}],
            plan_cache={},
        )
        assert "alt_MISSING" not in plans

    def test_falls_back_to_alt_body_when_cache_empty(self):
        """If the alt dict itself carries ``electricityContract`` we
        accept it — covers the first-ever backfill before the per-day
        plan cache has been populated."""
        alt_full = {
            "planId": "EAGER",
            "electricityContract": {"pricingModel": "SINGLE_RATE"},
        }
        plans = build_backfill_plan_set(
            options={"cdr_plan": None},
            current_plan_id="x",
            ranked_alternatives=[alt_full],
            plan_cache={},
        )
        assert plans["alt_EAGER"] is alt_full

    def test_returns_empty_when_current_plan_data_missing(self):
        """No current plan data and no alts → returns empty (caller
        treats as no signal)."""
        plans = build_backfill_plan_set(
            options={"cdr_plan": {}},
            current_plan_id="x",
            ranked_alternatives=[],
            plan_cache={},
        )
        assert plans == {}

    def test_handles_non_dict_cdr_plan_envelope(self):
        """``cdr_plan`` shipped as a string / list doesn't raise — the
        current-plan column simply isn't emitted."""
        plans = build_backfill_plan_set(
            options={"cdr_plan": "garbage"},
            current_plan_id="x",
            ranked_alternatives=[],
            plan_cache={},
        )
        assert plans == {}


# ---------------------------------------------------------------------------
# Phase 3.4 — named comparator provider lifecycle (pure helper)
# ---------------------------------------------------------------------------


class TestBuildNamedComparatorProvider:
    """Exercises :func:`build_named_comparator_provider` — the pure-logic
    extraction of the Phase 3.4 named-comparator construction (lives
    outside ``PriceHawkCoordinator.__init__`` so it can be tested
    without HA's app context, same rationale as ``build_backfill_plan_set``).
    """

    def _load_globird_plan(self) -> dict:
        import json
        from pathlib import Path

        fixture = (
            Path(__file__).parent
            / "fixtures"
            / "phase0"
            / "plan_globird_GLO731031MR@VEC.json"
        )
        return json.loads(fixture.read_text())

    def test_returns_provider_when_plan_present(self):
        """``CONF_NAMED_COMPARATOR_PLAN`` set → returns a
        ``CdrPlanProvider`` constructed against the pinned plan body."""
        from custom_components.pricehawk.const import CONF_NAMED_COMPARATOR_PLAN
        from custom_components.pricehawk.providers.cdr_plan import CdrPlanProvider

        plan = self._load_globird_plan()
        provider = build_named_comparator_provider(
            {CONF_NAMED_COMPARATOR_PLAN: plan, "cdr_plan": plan},
        )
        assert provider is not None
        assert isinstance(provider, CdrPlanProvider)

    def test_returns_none_when_option_absent(self):
        """No pin → ``None``. Caller short-circuits the ``"named"``
        key registration in ``_providers``."""
        assert build_named_comparator_provider({"cdr_plan": {}}) is None
        assert build_named_comparator_provider({}) is None

    def test_returns_none_when_pinned_plan_is_not_a_dict(self):
        """Defensive — a malformed options entry (string / list / int
        ending up in storage) doesn't crash setup; coordinator just
        skips the named comparator on this reload."""
        from custom_components.pricehawk.const import CONF_NAMED_COMPARATOR_PLAN

        for bad in ("garbage", 42, [1, 2, 3], None):
            assert (
                build_named_comparator_provider(
                    {CONF_NAMED_COMPARATOR_PLAN: bad},
                )
                is None
            )

    def test_returns_none_when_pinned_plan_is_empty_dict(self):
        """An empty dict ``{}`` is treated as "no pin" rather than
        constructing a provider over an empty CDR envelope (which
        would crash later when the evaluator tries to read
        ``electricityContract``)."""
        from custom_components.pricehawk.const import CONF_NAMED_COMPARATOR_PLAN

        assert (
            build_named_comparator_provider(
                {CONF_NAMED_COMPARATOR_PLAN: {}},
            )
            is None
        )
