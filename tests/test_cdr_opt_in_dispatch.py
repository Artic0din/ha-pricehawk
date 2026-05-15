"""End-to-end test that Phase 2.12.1 opt-in fields flow from
entry_options through apply_retailer_incentives to the per-retailer
parsers and activate the math.

We hit the dispatch boundary directly (not the full evaluator) — the
evaluator-level integration is covered indirectly by the streaming
engine tests gated on pydantic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from custom_components.pricehawk.cdr.incentive_parsers import (
    apply_retailer_incentives,
)


@dataclass
class _StubBreakdown:
    incentive_aud_inc_gst: Decimal = Decimal("0")
    notes: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)


def _slot_in_window_stub(*_a, **_kw):
    return False


# --- OVO interest opt-in ---------------------------------------------


def _ovo_plan_with_interest() -> dict:
    return {
        "brand": "ovo-energy",
        "electricityContract": {
            "incentives": [
                {
                    "displayName": "Interest Rewards",
                    "eligibility": "3% interest on credit balances. Paid monthly to your OVO account.",
                },
            ],
            "tariffPeriod": [],
        },
    }


def test_ovo_interest_no_op_when_balance_zero():
    """Default entry_options → balance 0 → no credit."""
    bd = _StubBreakdown()
    apply_retailer_incentives(
        _ovo_plan_with_interest(), [], bd,
        slot_in_window=_slot_in_window_stub,
        entry_options={},
    )
    # No interest trace entry (apply_rule short-circuits at balance=0).
    interest_traces = [t for t in bd.trace if t.get("incentive") == "ovo_interest"]
    assert interest_traces == []


def test_ovo_interest_credits_when_balance_set():
    """Opt-in balance flows to ovo_interest.apply_rule and credits."""
    bd = _StubBreakdown()
    apply_retailer_incentives(
        _ovo_plan_with_interest(), [], bd,
        slot_in_window=_slot_in_window_stub,
        entry_options={"ovo_interest_balance_aud": 500},
    )
    # $500 × 3% / 365 = $0.0411/day
    expected_daily = Decimal("500") * Decimal("3") / Decimal("100") / Decimal("365")
    assert bd.incentive_aud_inc_gst == -expected_daily
    interest_traces = [t for t in bd.trace if t.get("incentive") == "ovo_interest"]
    assert len(interest_traces) == 1
    assert interest_traces[0]["balance_aud"] == 500.0


# --- VPP rebate opt-in -----------------------------------------------


def _engie_plan_with_vpp() -> dict:
    return {
        "brand": "engie-au",
        "electricityContract": {
            "incentives": [
                {
                    "displayName": "PowerResponse VPP",
                    "eligibility": "$15 monthly credit per battery for participating in our VPP.",
                },
            ],
            "tariffPeriod": [],
        },
    }


def test_vpp_no_op_when_batteries_zero():
    bd = _StubBreakdown()
    apply_retailer_incentives(
        _engie_plan_with_vpp(), [], bd,
        slot_in_window=_slot_in_window_stub,
        entry_options={},
    )
    vpp_traces = [t for t in bd.trace if t.get("incentive") == "vpp_rebate"]
    assert vpp_traces == []


def test_vpp_credits_when_one_battery_enrolled():
    bd = _StubBreakdown()
    apply_retailer_incentives(
        _engie_plan_with_vpp(), [], bd,
        slot_in_window=_slot_in_window_stub,
        entry_options={"vpp_batteries_enrolled": 1},
    )
    # $15/mo × 1 battery / 30 days = $0.50/day credit
    assert bd.incentive_aud_inc_gst == -Decimal("0.5")
    vpp_traces = [t for t in bd.trace if t.get("incentive") == "vpp_rebate"]
    assert len(vpp_traces) == 1
    assert vpp_traces[0]["batteries_enrolled"] == 1


def test_vpp_credits_scale_with_battery_count():
    bd = _StubBreakdown()
    apply_retailer_incentives(
        _engie_plan_with_vpp(), [], bd,
        slot_in_window=_slot_in_window_stub,
        entry_options={"vpp_batteries_enrolled": 3},
    )
    # $15 × 3 / 30 = $1.50/day
    assert bd.incentive_aud_inc_gst == -Decimal("1.5")


# --- GloBird unaffected ----------------------------------------------


def test_globird_ignores_opt_in_kwargs():
    """GloBird has no opt-in fields — should absorb entry_options
    silently and not crash, regardless of values set."""
    plan = {
        "brand": "globird",
        "electricityContract": {
            "incentives": [],
            "tariffPeriod": [],
        },
    }
    bd = _StubBreakdown()
    apply_retailer_incentives(
        plan, [], bd,
        slot_in_window=_slot_in_window_stub,
        entry_options={"ovo_interest_balance_aud": 999, "vpp_batteries_enrolled": 5},
    )
    # No GloBird incentives in this plan → no traces; no crash from absorbing kwargs.
    assert bd.trace == []
