"""Phase 3.0g — tests for coordinator-level pure helpers.

CodeRabbit + Sourcery flagged the inline peak-rate derivation in
`_build_data_dict` as brittle. Extracted to module-level
`_extract_peak_rate_c_inc_gst(cdr_plan)` and pinned with edge cases.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import logging
from datetime import datetime

import pytest

from custom_components.pricehawk.coordinator import (
    _extract_cdr_daily_supply_aud_ex_gst,
    _extract_peak_rate_c_inc_gst,
    _resolve,
    _resolve_str,
    _resolve_str_with_options,
    _resolve_with_options,

    _find_perkwh_in_intervals,
    _tally,
    build_backfill_plan_set,
    build_named_comparator_provider,
)

_COORDINATOR_LOGGER = "custom_components.pricehawk.coordinator"


def _entry(options: dict[str, Any], data: dict[str, Any]) -> Any:
    """Minimal ConfigEntry-shaped stub.

    ``_resolve`` only touches ``entry.options`` and ``entry.data`` — no
    need to pull in HA's full ConfigEntry constructor (which requires a
    HomeAssistant + version + domain + unique_id + … and isn't worth the
    fixture weight for a 1-line helper test).
    """
    return SimpleNamespace(options=options, data=data)


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


# ---------------------------------------------------------------------------
# Constitution P14 — module-level ``_resolve`` options→data fallback helper
# ---------------------------------------------------------------------------


class TestResolve:
    """Exercises :func:`_resolve` — the centralised options-over-data
    lookup that replaced six near-identical inline duplicates across
    ``coordinator.py``.

    Precedence contract: ``entry.options.get(key, entry.data.get(key, default))``.
    Critical edge case: a key *present* in ``entry.options`` (even when
    its stored value is falsy) shadows ``entry.data`` — the options flow
    is the explicit user-edit channel and a falsy override must not
    silently fall through to a stale ``entry.data`` value.
    """

    def test_resolve_prefers_options_over_data(self):
        """Options wins when the key exists in BOTH ``entry.options`` and
        ``entry.data``. Matches the options-flow override semantics."""
        entry = _entry(options={"k": "from-options"}, data={"k": "from-data"})
        assert _resolve(entry, "k") == "from-options"

    def test_resolve_falls_back_to_data(self):
        """Key absent from ``entry.options`` → falls through to
        ``entry.data``. Covers entries that completed initial setup but
        never visited the options flow (the Live UAT 2026-05-24 case)."""
        entry = _entry(options={}, data={"k": "from-data"})
        assert _resolve(entry, "k") == "from-data"

    def test_resolve_returns_default_when_neither(self):
        """Key absent from BOTH layers → returns the supplied default.
        The default is explicit (no surprise ``None`` for callers that
        rely on, e.g., the ``""`` sentinel for empty string fields)."""
        entry = _entry(options={}, data={})
        assert _resolve(entry, "k", default="fallback") == "fallback"
        # Default of ``None`` is the documented signature default.
        assert _resolve(entry, "k") is None

    def test_resolve_options_none_shadows_data(self):
        """Edge case: ``entry.options[key] is None`` — the key IS present,
        just stored as ``None``. Per ``dict.get(key, default)`` semantics,
        the explicit ``None`` wins over the ``entry.data`` fallback.

        This is intentional: options-flow validators may store ``None``
        to mean "user cleared this field". Treating it as "fall through
        to data" would resurrect stale data the user just deleted.
        """
        entry = _entry(options={"k": None}, data={"k": "from-data"})
        assert _resolve(entry, "k", default="default") is None


class TestResolveWithOptions:
    """Exercises :func:`_resolve_with_options` — the rebuild_engine variant
    that takes an externally-supplied options dict (HA hands one in
    before mirroring it onto the ConfigEntry).

    Same precedence as ``_resolve`` but the options layer comes from the
    argument, not ``entry.options``.
    """

    def test_prefers_new_options_over_entry_data(self):
        """``new_options`` wins when present in both layers."""
        entry = _entry(options={"k": "stale"}, data={"k": "from-data"})
        # rebuild_engine passes a fresh ``new_options`` dict that has NOT
        # yet been mirrored onto ``entry.options``. The fresh dict wins.
        assert (
            _resolve_with_options(
                {"k": "from-new-options"}, entry, "k"
            )
            == "from-new-options"
        )

    def test_falls_back_to_entry_data(self):
        """Key absent from ``new_options`` → falls through to ``entry.data``."""
        entry = _entry(options={}, data={"k": "from-data"})
        assert _resolve_with_options({}, entry, "k") == "from-data"

    def test_returns_default_when_neither(self):
        entry = _entry(options={}, data={})
        assert (
            _resolve_with_options({}, entry, "k", default="fallback")
            == "fallback"
        )
        assert _resolve_with_options({}, entry, "k") is None


# ---------------------------------------------------------------------------
# PR #164 Linus audit — _resolve_str / _resolve_str_with_options variants
# ---------------------------------------------------------------------------


class TestResolveStr:
    """Exercises :func:`_resolve_str` — the string-coerced variant that
    coerces ``None`` → ``default`` so call sites assigning to ``str``-typed
    attributes never receive ``None``. Replaces the ``_resolve(...) or ""``
    pattern that contradicted the documented "None-shadows-data" semantic.

    Contract:
      - returns the resolved value coerced to ``str`` when not None
      - returns ``default`` (defaults to "") when resolved value is None
      - obeys the same options→data precedence as :func:`_resolve`
    """

    def test_prefers_options_over_data(self):
        entry = _entry(options={"k": "from-options"}, data={"k": "from-data"})
        assert _resolve_str(entry, "k") == "from-options"

    def test_falls_back_to_data(self):
        entry = _entry(options={}, data={"k": "from-data"})
        assert _resolve_str(entry, "k") == "from-data"

    def test_returns_empty_default_when_neither(self):
        """Default default is ``""`` — matches the typed-as-str attribute
        contract at the call sites (``self._grid_power_entity: str``)."""
        entry = _entry(options={}, data={})
        assert _resolve_str(entry, "k") == ""

    def test_returns_custom_default_when_neither(self):
        entry = _entry(options={}, data={})
        assert _resolve_str(entry, "k", default="fallback") == "fallback"

    def test_options_none_coerces_to_default(self):
        """The semantic fix: options-flow stored ``None`` (user cleared
        the field) → :func:`_resolve` would shadow ``entry.data`` with
        ``None``; :func:`_resolve_str` coerces that to the default so
        the str-typed attribute stays a string. Critically this does
        NOT resurrect the ``entry.data`` value — the user cleared the
        field, so the field IS cleared (returns ``""``)."""
        entry = _entry(options={"k": None}, data={"k": "stale-data"})
        assert _resolve_str(entry, "k") == ""
        assert _resolve_str(entry, "k", default="my-default") == "my-default"

    def test_coerces_non_string_resolved_values(self):
        """Defensive — config storage might have integers/floats. The
        attribute is typed ``str`` so coerce to str."""
        entry = _entry(options={"k": 42}, data={})
        assert _resolve_str(entry, "k") == "42"


class TestResolveStrWithOptions:
    """Exercises :func:`_resolve_str_with_options` — the ``rebuild_engine``
    variant taking an externally-supplied fresh options dict."""

    def test_prefers_new_options_over_entry_data(self):
        entry = _entry(options={"k": "stale"}, data={"k": "from-data"})
        assert (
            _resolve_str_with_options({"k": "from-new"}, entry, "k")
            == "from-new"
        )

    def test_falls_back_to_entry_data(self):
        entry = _entry(options={}, data={"k": "from-data"})
        assert _resolve_str_with_options({}, entry, "k") == "from-data"

    def test_returns_default_when_neither(self):
        entry = _entry(options={}, data={})
        assert _resolve_str_with_options({}, entry, "k") == ""
        assert (
            _resolve_str_with_options({}, entry, "k", default="x") == "x"
        )

    def test_new_options_none_coerces_to_default(self):
        """Fresh options-flow dict with ``None`` (user just cleared the
        field) → returns default, not the stale ``entry.data`` value."""
        entry = _entry(options={}, data={"k": "stale-data"})
        assert _resolve_str_with_options({"k": None}, entry, "k") == ""

# Constitution P09 / P20 — every swallow path emits a DEBUG line so the
# silent ``except: return None`` failure mode of Phase 3.0e regressions
# can be diagnosed from a single ``logger: custom_components.pricehawk:
# debug`` toggle. The hot-loop swallows (Amber replay) aggregate counts
# and emit one DEBUG line per category, NOT per row (Constitution P18).

# Constitution P11 (Define "Done" Explicitly — observability/logging is
# part of the deliverable, not an afterthought) + P20 (Explain
# Architectural Consequences) — every swallow path emits a DEBUG line
# so the silent ``except: return None`` failure mode of Phase 3.0e
# regressions can be diagnosed from a single
# ``logger: custom_components.pricehawk: debug`` toggle. The hot-loop
# swallows (Amber replay) aggregate counts and emit one DEBUG line per
# category, NOT per row (Constitution P18 — performance).
# ---------------------------------------------------------------------------


class TestExtractPeakRateSwallowLogging:
    """Cover both ``except`` branches in ``_extract_peak_rate_c_inc_gst``."""

    def test_tariff_period_walk_swallow_logs_debug(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``cdr_plan["data"]`` shaped as a string raises ``AttributeError``
        on ``.get(...)``. The helper must swallow, return None, and emit
        a DEBUG line tagged with the helper + step name."""
        bad_plan = {"data": "not-a-dict"}
        with caplog.at_level(logging.DEBUG, logger=_COORDINATOR_LOGGER):
            assert _extract_peak_rate_c_inc_gst(bad_plan) is None
        matched = [
            r for r in caplog.records
            if r.name == _COORDINATOR_LOGGER
            and r.levelno == logging.DEBUG
            and "_extract_peak_rate_c_inc_gst.tariff_period_walk" in r.getMessage()
            and "swallowed AttributeError" in r.getMessage()
        ]
        assert matched, f"expected swallow DEBUG, got {caplog.records!r}"

    def test_unit_price_cast_swallow_logs_debug(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Non-numeric ``unitPrice`` raises ``ValueError`` — swallow path
        must log under the ``unit_price_cast`` step tag."""
        plan = _plan("not-a-number")
        with caplog.at_level(logging.DEBUG, logger=_COORDINATOR_LOGGER):
            assert _extract_peak_rate_c_inc_gst(plan) is None
        matched = [
            r for r in caplog.records
            if r.name == _COORDINATOR_LOGGER
            and r.levelno == logging.DEBUG
            and "_extract_peak_rate_c_inc_gst.unit_price_cast" in r.getMessage()
            and "swallowed ValueError" in r.getMessage()
        ]
        assert matched, f"expected swallow DEBUG, got {caplog.records!r}"


class TestExtractCdrDailySupply:
    """Cover ``_extract_cdr_daily_supply_aud_ex_gst`` (extracted from
    ``_build_data_dict`` — line 1433 in the pre-fix coordinator)."""

    def test_happy_path_returns_supply_ex_gst(self) -> None:
        plan = {
            "data": {
                "electricityContract": {
                    "tariffPeriod": [{"dailySupplyCharge": "1.2345"}],
                },
            },
        }
        assert _extract_cdr_daily_supply_aud_ex_gst(plan) == pytest.approx(1.2345)

    def test_empty_plan_returns_none(self) -> None:
        assert _extract_cdr_daily_supply_aud_ex_gst(None) is None
        assert _extract_cdr_daily_supply_aud_ex_gst({}) is None

    def test_missing_tariff_period_returns_none(self) -> None:
        plan: dict[str, Any] = {
            "data": {"electricityContract": {"tariffPeriod": []}}
        }
        assert _extract_cdr_daily_supply_aud_ex_gst(plan) is None

    def test_malformed_envelope_swallow_logs_debug(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``data`` shaped as a string raises ``AttributeError`` on the
        nested ``.get`` chain — swallow path must emit DEBUG."""
        bad = {"data": "garbage"}
        with caplog.at_level(logging.DEBUG, logger=_COORDINATOR_LOGGER):
            assert _extract_cdr_daily_supply_aud_ex_gst(bad) is None
        matched = [
            r for r in caplog.records
            if r.name == _COORDINATOR_LOGGER
            and r.levelno == logging.DEBUG
            and "_extract_cdr_daily_supply_aud_ex_gst" in r.getMessage()
            and "swallowed AttributeError" in r.getMessage()
        ]
        assert matched, f"expected swallow DEBUG, got {caplog.records!r}"

    def test_tariff_period_as_dict_swallow_logs_debug(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``tariffPeriod`` shipped as a truthy dict instead of a list
        — ``tp[0]`` raises ``KeyError`` (not ``IndexError``). Guard tuple
        MUST include ``KeyError`` so the swallow path emits DEBUG and
        returns ``None`` rather than propagating to the caller."""
        bad: dict[str, Any] = {
            "data": {
                "electricityContract": {
                    "tariffPeriod": {"foo": "bar"},  # dict, not list
                },
            },
        }
        with caplog.at_level(logging.DEBUG, logger=_COORDINATOR_LOGGER):
            assert _extract_cdr_daily_supply_aud_ex_gst(bad) is None
        matched = [
            r for r in caplog.records
            if r.name == _COORDINATOR_LOGGER
            and r.levelno == logging.DEBUG
            and "_extract_cdr_daily_supply_aud_ex_gst" in r.getMessage()
            and "swallowed KeyError" in r.getMessage()
        ]
        assert matched, f"expected swallow DEBUG, got {caplog.records!r}"

    def test_non_numeric_supply_charge_swallow_logs_debug(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``dailySupplyCharge`` shipped as a non-numeric string raises
        ``ValueError`` on ``float(...)`` — swallow path must emit DEBUG."""
        plan = {
            "data": {
                "electricityContract": {
                    "tariffPeriod": [{"dailySupplyCharge": "not-a-number"}],
                },
            },
        }
        with caplog.at_level(logging.DEBUG, logger=_COORDINATOR_LOGGER):
            assert _extract_cdr_daily_supply_aud_ex_gst(plan) is None
        matched = [
            r for r in caplog.records
            if r.name == _COORDINATOR_LOGGER
            and r.levelno == logging.DEBUG
            and "_extract_cdr_daily_supply_aud_ex_gst" in r.getMessage()
            and "swallowed ValueError" in r.getMessage()
        ]
        assert matched, f"expected swallow DEBUG, got {caplog.records!r}"


class TestTallyHelper:
    """``_tally`` is the DRY extraction of the inline ``counter[name] =
    counter.get(name, 0) + 1`` pattern used in both swallow sites
    inside ``_replay_amber_today_from_api``. Cover the contract here so
    the integration tests can rely on the helper's semantics."""

    def test_increments_existing_count(self) -> None:
        counter: dict[str, int] = {"ValueError": 2}
        _tally(counter, ValueError("boom"))
        assert counter == {"ValueError": 3}

    def test_initialises_new_exception_type(self) -> None:
        counter: dict[str, int] = {}
        _tally(counter, TypeError("nope"))
        assert counter == {"TypeError": 1}

    def test_none_counter_is_a_noop(self) -> None:
        """``swallow_counter=None`` callers must not crash — the helper
        short-circuits before any subscript access."""
        _tally(None, ValueError("ignored"))  # must not raise

    def test_handles_mixed_exception_types_independently(self) -> None:
        counter: dict[str, int] = {}
        _tally(counter, ValueError("a"))
        _tally(counter, ValueError("b"))
        _tally(counter, TypeError("c"))
        _tally(counter, KeyError("d"))
        assert counter == {"ValueError": 2, "TypeError": 1, "KeyError": 1}


class TestFindPerkwhInIntervals:
    """Cover ``_find_perkwh_in_intervals`` (extracted from the inner
    ``_rate_at`` closure of ``_replay_amber_today_from_api`` — lines
    1732 + 1743 in the pre-fix coordinator). Constitution P18: the hot
    loop tallies cast failures into a caller-owned counter so the
    coordinator can emit a single aggregated DEBUG line post-loop
    instead of one per row."""

    def _intervals(self, per_kwh: object) -> list[dict]:
        return [{
            "startTime": "2026-05-26T00:00:00+10:00",
            "endTime": "2026-05-26T00:30:00+10:00",
            "perKwh": per_kwh,
        }]

    def test_returns_float_when_interval_matches(self) -> None:
        intervals = self._intervals(12.34)
        rate = _find_perkwh_in_intervals(
            intervals, "2026-05-26T00:15:00+10:00",
        )
        assert rate == pytest.approx(12.34)

    def test_returns_none_when_no_interval_matches(self) -> None:
        intervals = self._intervals(12.34)
        rate = _find_perkwh_in_intervals(
            intervals, "2026-05-26T01:00:00+10:00",
        )
        assert rate is None

    def test_non_numeric_perkwh_tallies_into_counter(self) -> None:
        """Cast failure must accumulate into the caller's swallow_counter
        keyed by exception type — caller emits the aggregated DEBUG line
        after the loop completes."""
        intervals = self._intervals("garbage")
        counter: dict[str, int] = {}
        rate = _find_perkwh_in_intervals(
            intervals, "2026-05-26T00:15:00+10:00", counter,
        )
        assert rate is None
        assert counter == {"ValueError": 1}

    def test_missing_perkwh_tallies_keyerror(self) -> None:
        intervals = [{
            "startTime": "2026-05-26T00:00:00+10:00",
            "endTime": "2026-05-26T00:30:00+10:00",
        }]
        counter: dict[str, int] = {}
        rate = _find_perkwh_in_intervals(
            intervals, "2026-05-26T00:15:00+10:00", counter,
        )
        assert rate is None
        assert counter == {"KeyError": 1}

    def test_counter_accumulates_across_calls(self) -> None:
        """Hot loop semantics — repeated bad rows aggregate into the
        same counter so the caller's post-loop log says ``swallowed N``
        with one DEBUG line, not N lines."""
        bad = self._intervals("garbage")
        missing = [{
            "startTime": "2026-05-26T00:00:00+10:00",
            "endTime": "2026-05-26T00:30:00+10:00",
        }]
        counter: dict[str, int] = {}
        for _ in range(5):
            _find_perkwh_in_intervals(
                bad, "2026-05-26T00:15:00+10:00", counter,
            )
        for _ in range(3):
            _find_perkwh_in_intervals(
                missing, "2026-05-26T00:15:00+10:00", counter,
            )
        assert counter == {"ValueError": 5, "KeyError": 3}

    def test_swallow_without_counter_is_silent(self) -> None:
        """Passing ``swallow_counter=None`` (default) means the helper
        returns None without tallying — used by ad-hoc non-hot callers
        that don't want the bookkeeping overhead."""
        intervals = self._intervals("garbage")
        rate = _find_perkwh_in_intervals(
            intervals, "2026-05-26T00:15:00+10:00",
        )
        assert rate is None


# ---------------------------------------------------------------------------
# Constitution P11 (Define "Done" Explicitly) + P17 (Tests Are Part of the
# Fix) — the unit tests above cover the helpers in isolation. The whole
# point of PR #167 is the *aggregated post-loop DEBUG lines* fired from
# inside :py:meth:`PriceHawkCoordinator._replay_amber_today_from_api`.
# Without driving the coordinator method end-to-end with a mixed-quality
# states stream + intervals, we'd ship the observability change with
# zero integration coverage on the actual seam — exactly the silent
# failure mode the PR is supposed to eliminate.
# ---------------------------------------------------------------------------


class _FakeAmberProvider:
    """Test double for :class:`AmberProvider` — records ``reset_daily``,
    ``set_current_rates``, and ``update`` calls so we can verify the
    coordinator drives the provider correctly on the happy path while
    still exercising the swallow seams on the unhappy ones."""

    def __init__(self) -> None:
        self.reset_calls = 0
        self.updates: list[tuple[float, datetime]] = []
        self.rates: list[tuple[float, float]] = []
        self._import_c: float | None = None
        self._export_c: float | None = None

    def reset_daily(self) -> None:
        self.reset_calls += 1

    def set_current_rates(self, import_c: float, export_c: float) -> None:
        self._import_c = import_c
        self._export_c = export_c
        self.rates.append((import_c, export_c))

    def update(self, power_w: float, ts: datetime) -> None:
        self.updates.append((power_w, ts))


def _state(ts: datetime, value: object, unit: str = "W"):
    """Build a recorder-style ``State`` mock with the attributes
    :func:`_replay_seed_amber_from_states` reads (``state``,
    ``last_changed``, ``attributes['unit_of_measurement']``)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        state=value,
        last_changed=ts,
        attributes={"unit_of_measurement": unit},
    )


class TestReplaySeedAmberFromStates:
    """Drive :func:`_replay_seed_amber_from_states` — the module-level
    extraction of the Amber-replay hot loop — with mixed-quality
    ``states`` + ``intervals`` and assert BOTH aggregated DEBUG lines
    fire exactly once with the correct rolled-up counts.

    The function ships with ``PriceHawkCoordinator._replay_amber_today_from_api``
    as its sole production caller; it was extracted to module level
    specifically so the observability seam is testable without an HA
    runtime (the coordinator's ``DataUpdateCoordinator[T]`` base gets
    mocked away by ``tests/conftest.py``, making direct coordinator
    method invocation impossible — same constraint that drove the
    earlier extractions of :func:`_extract_peak_rate_c_inc_gst`,
    :func:`build_backfill_plan_set`, :func:`build_named_comparator_provider`)."""

    def test_mixed_quality_stream_emits_both_aggregated_debug_lines(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Mixed states + mixed intervals → loop swallows on both
        seams → MUST emit one DEBUG line per category with the
        rolled-up counts (Constitution P18: one log line, not N)."""
        from datetime import timedelta, timezone

        from custom_components.pricehawk.coordinator import (
            _replay_seed_amber_from_states,
        )

        aest = timezone(timedelta(hours=10))
        midnight = datetime(2026, 5, 26, 0, 0, 0, tzinfo=aest)

        # Mixed-quality grid-power history: 2 valid float strings, 2
        # cast failures (one ValueError via "unavailable", one
        # TypeError via None state).
        states = [
            _state(midnight + timedelta(hours=1), "1500.0"),  # good
            _state(midnight + timedelta(hours=2), "unavailable"),  # ValueError
            _state(midnight + timedelta(hours=3), "2000.0"),  # good (rate-bad)
            _state(midnight + timedelta(hours=4), None),  # TypeError
        ]

        # Valid intervals cover 01:00..01:30 on BOTH channels — drives
        # one seeded row from the first good state. Malformed intervals
        # cover 03:00..03:30 on BOTH channels — drives 2 tally hits
        # (general + feedIn) into the rate-lookup swallow counter.
        valid_start = (midnight + timedelta(hours=1)).isoformat()
        valid_end = (midnight + timedelta(hours=1, minutes=30)).isoformat()
        bad_start = (midnight + timedelta(hours=3)).isoformat()
        bad_end = (midnight + timedelta(hours=3, minutes=30)).isoformat()

        general: list[dict[str, Any]] = [
            {"startTime": valid_start, "endTime": valid_end, "perKwh": 25.0},
            {"startTime": bad_start, "endTime": bad_end, "perKwh": "garbage"},
        ]
        feed: list[dict[str, Any]] = [
            {"startTime": valid_start, "endTime": valid_end, "perKwh": 8.0},
            {"startTime": bad_start, "endTime": bad_end, "perKwh": "garbage"},
        ]

        amber = _FakeAmberProvider()
        with caplog.at_level(logging.DEBUG, logger=_COORDINATOR_LOGGER):
            seeded = _replay_seed_amber_from_states(states, general, feed, amber)

        # Happy-path side effects — one good state matched both valid
        # intervals → one seeded row + one rate/update pair.
        assert seeded == 1
        assert amber.reset_calls == 1
        assert amber.updates == [(1500.0, midnight + timedelta(hours=1))]
        assert amber.rates == [(25.0, 8.0)]

        # Aggregated DEBUG line #1 — power_value_cast: 1 ValueError +
        # 1 TypeError = 2 rows swallowed.
        power_lines = [
            r for r in caplog.records
            if r.name == _COORDINATOR_LOGGER
            and r.levelno == logging.DEBUG
            and "_replay_amber_today_from_api.power_value_cast" in r.getMessage()
            and "swallowed 2 rows" in r.getMessage()
        ]
        assert power_lines, (
            f"expected ONE aggregated power_value_cast DEBUG line with "
            f"swallowed=2; got {[r.getMessage() for r in caplog.records]!r}"
        )
        assert len(power_lines) == 1, (
            "aggregated DEBUG must fire exactly once per replay"
        )
        # Sanity-check the rolled-up exception-type counts surface in
        # the message — without these, an operator toggling DEBUG can't
        # tell ValueError from TypeError.
        msg = power_lines[0].getMessage()
        assert "ValueError" in msg
        assert "TypeError" in msg

        # Aggregated DEBUG line #2 — rate-lookup: the third good state
        # (03:00) hits the malformed interval on BOTH general + feedIn
        # → 2 tally hits.
        rate_lines = [
            r for r in caplog.records
            if r.name == _COORDINATOR_LOGGER
            and r.levelno == logging.DEBUG
            and "_replay_amber_today_from_api._find_perkwh_in_intervals"
                in r.getMessage()
            and "swallowed 2 intervals" in r.getMessage()
        ]
        assert rate_lines, (
            f"expected ONE aggregated _find_perkwh_in_intervals DEBUG "
            f"line with swallowed=2; got "
            f"{[r.getMessage() for r in caplog.records]!r}"
        )
        assert len(rate_lines) == 1
        assert "ValueError" in rate_lines[0].getMessage()

    def test_clean_stream_emits_zero_aggregated_debug_lines(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Empty swallow counters MUST NOT log — the post-loop blocks
        are guarded by ``if power_cast_swallows`` / ``if rate_at_swallows``
        so a healthy replay stays log-silent at DEBUG."""
        from datetime import timedelta, timezone

        from custom_components.pricehawk.coordinator import (
            _replay_seed_amber_from_states,
        )

        aest = timezone(timedelta(hours=10))
        midnight = datetime(2026, 5, 26, 0, 0, 0, tzinfo=aest)

        states = [
            _state(midnight + timedelta(hours=1), "1500.0"),
            _state(midnight + timedelta(hours=2), "2000.0"),
        ]
        start = midnight.isoformat()
        end = (midnight + timedelta(days=1)).isoformat()
        general = [{"startTime": start, "endTime": end, "perKwh": 25.0}]
        feed = [{"startTime": start, "endTime": end, "perKwh": 8.0}]

        amber = _FakeAmberProvider()
        with caplog.at_level(logging.DEBUG, logger=_COORDINATOR_LOGGER):
            seeded = _replay_seed_amber_from_states(states, general, feed, amber)

        assert seeded == 2
        swallow_lines = [
            r for r in caplog.records
            if r.name == _COORDINATOR_LOGGER
            and r.levelno == logging.DEBUG
            and "swallowed" in r.getMessage()
            and "_replay_amber_today_from_api" in r.getMessage()
        ]
        assert swallow_lines == [], (
            f"clean replay must not log aggregated swallows; got "
            f"{[r.getMessage() for r in swallow_lines]!r}"
        )

    def test_kw_unit_is_scaled_to_watts(self) -> None:
        """Regression guard for the ``unit_of_measurement == 'kW'``
        branch — power_w must be multiplied by 1000 before being
        passed to ``amber.update``."""
        from datetime import timedelta, timezone

        from custom_components.pricehawk.coordinator import (
            _replay_seed_amber_from_states,
        )

        aest = timezone(timedelta(hours=10))
        midnight = datetime(2026, 5, 26, 0, 0, 0, tzinfo=aest)
        ts = midnight + timedelta(hours=1)
        states = [_state(ts, "1.5", unit="kW")]  # 1.5 kW → 1500 W
        start = midnight.isoformat()
        end = (midnight + timedelta(days=1)).isoformat()
        general = [{"startTime": start, "endTime": end, "perKwh": 25.0}]
        feed = [{"startTime": start, "endTime": end, "perKwh": 8.0}]

        amber = _FakeAmberProvider()
        seeded = _replay_seed_amber_from_states(states, general, feed, amber)
        assert seeded == 1
        assert amber.updates == [(1500.0, ts)]

    def test_missing_perkwh_key_tallies_as_keyerror(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Intervals without a ``perKwh`` key raise ``KeyError`` inside
        :func:`_find_perkwh_in_intervals` — must roll up under the same
        aggregated DEBUG line, tagged with ``KeyError`` in the dict
        rendering so an operator can distinguish missing keys from bad
        casts."""
        from datetime import timedelta, timezone

        from custom_components.pricehawk.coordinator import (
            _replay_seed_amber_from_states,
        )

        aest = timezone(timedelta(hours=10))
        midnight = datetime(2026, 5, 26, 0, 0, 0, tzinfo=aest)
        ts = midnight + timedelta(hours=1)
        states = [_state(ts, "1500.0")]
        start = ts.isoformat()
        end = (ts + timedelta(minutes=30)).isoformat()
        # No ``perKwh`` key — _find_perkwh_in_intervals raises KeyError
        # inside the try/except → tallied as "KeyError".
        general = [{"startTime": start, "endTime": end}]
        feed = [{"startTime": start, "endTime": end}]

        amber = _FakeAmberProvider()
        with caplog.at_level(logging.DEBUG, logger=_COORDINATOR_LOGGER):
            _replay_seed_amber_from_states(states, general, feed, amber)

        rate_lines = [
            r for r in caplog.records
            if r.name == _COORDINATOR_LOGGER
            and r.levelno == logging.DEBUG
            and "_replay_amber_today_from_api._find_perkwh_in_intervals"
                in r.getMessage()
        ]
        assert rate_lines
        assert "KeyError" in rate_lines[0].getMessage()
