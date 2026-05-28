"""Phase 3.0g — wizard rewrite tests.

The HA config-flow step machinery needs a full HA test harness which
isn't available in the pure-Python mock layer. These tests cover the
pure helpers Phase 3 introduced + extracted from the wizard logic.
"""

from __future__ import annotations

from custom_components.pricehawk.config_flow import (
    NAMED_COMPARATOR_CLEAR_SENTINEL,
    _api_provider_for_brand,
    plan_named_comparator_step,
)
from custom_components.pricehawk.const import (
    CONF_NAMED_COMPARATOR_PLAN,
    CONF_NAMED_COMPARATOR_PLAN_ID,
    PROVIDER_AMBER,
    PROVIDER_FLOW_POWER,
    PROVIDER_LOCALVOLTS,
)


# --- _api_provider_for_brand ---------------------------------------


def test_amber_brand_maps_to_amber_provider():
    assert _api_provider_for_brand("amber") == PROVIDER_AMBER
    assert _api_provider_for_brand("amber-electric") == PROVIDER_AMBER
    assert _api_provider_for_brand("Amber Electric") == PROVIDER_AMBER


def test_flow_power_maps_to_flow_power_provider():
    assert _api_provider_for_brand("flow-power") == PROVIDER_FLOW_POWER
    assert _api_provider_for_brand("flow power") == PROVIDER_FLOW_POWER
    assert _api_provider_for_brand("Flow Power") == PROVIDER_FLOW_POWER


def test_localvolts_maps_to_localvolts_provider():
    assert _api_provider_for_brand("localvolts") == PROVIDER_LOCALVOLTS
    assert _api_provider_for_brand("LocalVolts") == PROVIDER_LOCALVOLTS


def test_globird_returns_none():
    """GloBird has no live consumer API — wizard skips API-connect."""
    assert _api_provider_for_brand("globird") is None


def test_origin_agl_red_return_none():
    """Big traditional retailers — no consumer API in v1.5.x."""
    assert _api_provider_for_brand("origin") is None
    assert _api_provider_for_brand("agl") is None
    assert _api_provider_for_brand("red-energy") is None


def test_unknown_brand_returns_none():
    assert _api_provider_for_brand("unknown-retailer") is None


def test_empty_returns_none():
    assert _api_provider_for_brand("") is None
    assert _api_provider_for_brand("   ") is None


# ---------------------------------------------------------------------------
# Phase 3.4 — named comparator OptionsFlow step (pure decision helper)
# ---------------------------------------------------------------------------
#
# The ``EnergyCompareOptionsFlow`` class becomes a MagicMock under the
# conftest mock tree (its base ``config_entries.OptionsFlowWithReload``
# is a ``_MockModule``), so instance methods on it can't be called
# directly from a test. Phase 3.4 extracted the step's decision tree
# into a pure module-level helper ``plan_named_comparator_step`` for
# this exact reason — these tests pin its behaviour edge-by-edge.


def test_named_comparator_step_aborts_without_alternatives():
    """No ranked_alternatives → ``no_ranked_alternatives`` abort.
    UX path: user opened the step before the daily ranking job ran."""
    kind, payload = plan_named_comparator_step(
        ranked_alternatives=[],
        plan_cache={"P1": {"any": True}},
        user_input=None,
        current_options={},
    )
    assert kind == "abort"
    assert payload == {"reason": "no_ranked_alternatives"}


def test_named_comparator_step_aborts_when_plan_cache_empty():
    """Plan-cache empty (date-rollover edge case — plan §4.2 #3) →
    same abort path as no ranked_alternatives so the UX is uniform."""
    kind, payload = plan_named_comparator_step(
        ranked_alternatives=[{"plan_id": "AGL900", "brand": "AGL", "display_name": "Saver"}],
        plan_cache={},
        user_input=None,
        current_options={},
    )
    assert kind == "abort"
    assert payload == {"reason": "no_ranked_alternatives"}


def test_named_comparator_step_lists_ranked_alternatives_in_form():
    """Happy path — alts present, cache present → form payload carries
    the ``(clear pin)`` sentinel followed by one option per cached alt.
    """
    kind, payload = plan_named_comparator_step(
        ranked_alternatives=[
            {"plan_id": "AGL900", "brand": "AGL", "display_name": "Saver"},
            {"plan_id": "ORG456", "brand": "Origin", "display_name": "Solar"},
        ],
        plan_cache={
            "AGL900": {"data": {"planId": "AGL900"}},
            "ORG456": {"data": {"planId": "ORG456"}},
        },
        user_input=None,
        current_options={},
    )
    assert kind == "form"
    option_values = [opt["value"] for opt in payload["options"]]
    assert option_values == [NAMED_COMPARATOR_CLEAR_SENTINEL, "AGL900", "ORG456"]
    labels = [opt["label"] for opt in payload["options"]]
    assert any("AGL" in label and "Saver" in label for label in labels)
    # Default points at the clear-pin sentinel when no prior pin.
    assert payload["default"] == NAMED_COMPARATOR_CLEAR_SENTINEL


def test_named_comparator_step_skips_alts_missing_from_cache():
    """Alts WITHOUT a cached full body don't appear in the dropdown —
    otherwise the selection would dead-end at ``plan_not_in_cache``.
    """
    kind, payload = plan_named_comparator_step(
        ranked_alternatives=[
            {"plan_id": "AGL900", "brand": "AGL", "display_name": "Saver"},
            {"plan_id": "MISSING", "brand": "X", "display_name": "Y"},
        ],
        plan_cache={"AGL900": {"data": {"planId": "AGL900"}}},
        user_input=None,
        current_options={},
    )
    assert kind == "form"
    option_values = [opt["value"] for opt in payload["options"]]
    assert option_values == [NAMED_COMPARATOR_CLEAR_SENTINEL, "AGL900"]
    assert "MISSING" not in option_values


def test_named_comparator_step_falls_back_to_abort_when_no_cached_alts():
    """Every ranked alt missing from the cache → defensive
    fall-through to the same abort. (``cheap_rank`` should keep both
    in lockstep but the test pins the safety net.)
    """
    kind, payload = plan_named_comparator_step(
        ranked_alternatives=[
            {"plan_id": "A", "brand": "X", "display_name": "Y"},
            {"plan_id": "B", "brand": "X", "display_name": "Y"},
        ],
        plan_cache={"NOT_LISTED": {"data": {"planId": "NOT_LISTED"}}},
        user_input=None,
        current_options={},
    )
    assert kind == "abort"
    assert payload == {"reason": "no_ranked_alternatives"}


def test_named_comparator_step_default_falls_back_when_prior_pin_evicted():
    """Prior pin's planId is no longer in the cache → form default
    resets to the clear-pin sentinel so HA doesn't reject the
    schema for an unknown default."""
    kind, payload = plan_named_comparator_step(
        ranked_alternatives=[{"plan_id": "AGL900", "brand": "AGL", "display_name": "Saver"}],
        plan_cache={"AGL900": {"data": {"planId": "AGL900"}}},
        user_input=None,
        current_options={CONF_NAMED_COMPARATOR_PLAN_ID: "EVICTED_OLD"},
    )
    assert kind == "form"
    assert payload["default"] == NAMED_COMPARATOR_CLEAR_SENTINEL


def test_named_comparator_step_default_uses_current_pin_when_valid():
    """Prior pin still in the cache → form default preselects it so
    the user sees their current pin highlighted."""
    kind, payload = plan_named_comparator_step(
        ranked_alternatives=[{"plan_id": "AGL900", "brand": "AGL", "display_name": "Saver"}],
        plan_cache={"AGL900": {"data": {"planId": "AGL900"}}},
        user_input=None,
        current_options={CONF_NAMED_COMPARATOR_PLAN_ID: "AGL900"},
    )
    assert kind == "form"
    assert payload["default"] == "AGL900"


def test_named_comparator_step_clear_pin_removes_both_keys():
    """Selecting the ``(clear pin)`` sentinel must remove BOTH the
    plan_id and the full plan body so the coordinator's setup
    branches don't try to construct an empty provider."""
    kind, payload = plan_named_comparator_step(
        ranked_alternatives=[{"plan_id": "AGL900", "brand": "AGL", "display_name": "Saver"}],
        plan_cache={"AGL900": {"data": {"planId": "AGL900"}}},
        user_input={CONF_NAMED_COMPARATOR_PLAN_ID: NAMED_COMPARATOR_CLEAR_SENTINEL},
        current_options={
            CONF_NAMED_COMPARATOR_PLAN_ID: "OLD123",
            CONF_NAMED_COMPARATOR_PLAN: {"data": {"planId": "OLD123"}},
            "cdr_plan": {"data": {"planId": "CURRENT"}},
        },
    )
    assert kind == "create_entry"
    new_opts = payload["data"]
    assert CONF_NAMED_COMPARATOR_PLAN_ID not in new_opts
    assert CONF_NAMED_COMPARATOR_PLAN not in new_opts
    # Unrelated options preserved.
    assert new_opts["cdr_plan"] == {"data": {"planId": "CURRENT"}}


def test_named_comparator_step_persists_full_plan_body_for_evaluator():
    """Critical guard — the persisted plan body MUST be the full
    PlanDetailV2 envelope from ``_ranking_plan_cache``, NOT the
    summarised form on the ranked_alternatives sensor attributes
    (which omits ``tariffPeriod`` data the evaluator needs).
    """
    full_body = {
        "data": {
            "planId": "AGL900",
            "displayName": "AGL Saver",
            "brand": "AGL",
            "electricityContract": {
                "tariffPeriod": [
                    {
                        "rateBlockUType": "timeOfUseRates",
                        "timeOfUseRates": [
                            {"type": "PEAK", "rates": [{"unitPrice": "0.40"}]},
                        ],
                    }
                ]
            },
        }
    }
    summarised = {
        "plan_id": "AGL900",
        "brand": "AGL",
        "display_name": "AGL Saver",
        "peak_c_per_kwh": 44.0,  # already inc-GST — no tariffPeriod
    }
    kind, payload = plan_named_comparator_step(
        ranked_alternatives=[summarised],
        plan_cache={"AGL900": full_body},
        user_input={CONF_NAMED_COMPARATOR_PLAN_ID: "AGL900"},
        current_options={},
    )
    assert kind == "create_entry"
    persisted_plan = payload["data"][CONF_NAMED_COMPARATOR_PLAN]
    assert persisted_plan is full_body
    assert "electricityContract" in persisted_plan["data"]
    assert payload["data"][CONF_NAMED_COMPARATOR_PLAN_ID] == "AGL900"


def test_named_comparator_step_aborts_when_user_input_selection_missing_from_cache():
    """Defensive — selection no longer in cache when user submits
    (a daily reset fired while the form was open) → ``plan_not_in_cache``
    abort rather than pin an empty body."""
    kind, payload = plan_named_comparator_step(
        ranked_alternatives=[{"plan_id": "AGL900", "brand": "AGL", "display_name": "Saver"}],
        # Mismatched cache: alt advertises AGL900 but only DIFFERENT
        # is cached. (Pre-cache check passes because both lists are
        # non-empty; selection lookup misses.)
        plan_cache={"DIFFERENT": {"data": {"planId": "DIFFERENT"}}},
        user_input={CONF_NAMED_COMPARATOR_PLAN_ID: "AGL900"},
        current_options={},
    )
    assert kind == "abort"
    assert payload == {"reason": "plan_not_in_cache"}


def test_named_comparator_step_dedupes_alternatives_with_duplicate_plan_ids():
    """If the ranked list has the same planId twice (CDR
    republish window glitch), the dropdown surfaces it only
    once. Otherwise HA would reject the SelectSelector schema."""
    kind, payload = plan_named_comparator_step(
        ranked_alternatives=[
            {"plan_id": "AGL900", "brand": "AGL", "display_name": "Saver"},
            {"plan_id": "AGL900", "brand": "AGL", "display_name": "Saver"},
        ],
        plan_cache={"AGL900": {"data": {"planId": "AGL900"}}},
        user_input=None,
        current_options={},
    )
    assert kind == "form"
    option_values = [opt["value"] for opt in payload["options"]]
    assert option_values.count("AGL900") == 1
