"""Tests for vpp_rebate.py (Phase 2.11.5)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from custom_components.pricehawk.cdr.incentive_parsers.common.vpp_rebate import (
    REBATE_RE,
    TRIGGER_RE,
    apply_rule,
    parse_from_incentives,
    parse_rule,
)


@dataclass
class FakeBreakdown:
    incentive_aud_inc_gst: Decimal = Decimal("0")
    trace: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --- Regex coverage ----------------------------------------------------


def test_trigger_matches_vpp_acronym():
    assert TRIGGER_RE.search("Participate in our VPP and earn $15/month")


def test_trigger_matches_virtual_power_plant():
    assert TRIGGER_RE.search("Enrol in our Virtual Power Plant programme")


def test_trigger_matches_PowerResponse():
    assert TRIGGER_RE.search("PowerResponse: enrol your battery for $20/month")


def test_trigger_matches_enrol_battery():
    assert TRIGGER_RE.search("Enrol your battery to receive monthly credit")


def test_trigger_does_not_match_ev_offpeak():
    assert not TRIGGER_RE.search("$0.045/kWh usage charge between midnight and 6am")


def test_rebate_matches_monthly_per_battery():
    m = REBATE_RE.search("$15 monthly credit per battery")
    assert m
    assert m.group("rebate") == "15"


def test_rebate_matches_slash_month():
    m = REBATE_RE.search("$20/month per battery enrolled")
    assert m
    assert m.group("rebate") == "20"


def test_rebate_matches_per_month():
    m = REBATE_RE.search("$25 per month per battery")
    assert m
    assert m.group("rebate") == "25"


# --- parse_rule -------------------------------------------------------


def test_parse_rule_engie_canonical():
    rule = parse_rule(
        "$15 monthly credit per battery for participating in our VPP."
    )
    assert rule is not None
    assert rule["monthly_rebate_aud"] == Decimal("15")
    assert rule["batteries_enrolled"] == 0  # opt-in default


def test_parse_rule_ea_powerresponse():
    rule = parse_rule(
        "Enrol your battery in PowerResponse and receive $20 per month per battery.",
        batteries_enrolled=1,
    )
    assert rule is not None
    assert rule["monthly_rebate_aud"] == Decimal("20")
    assert rule["batteries_enrolled"] == 1


def test_parse_rule_no_trigger_returns_none():
    """Rebate without VPP context (e.g., sign-up bonus) should not match."""
    assert parse_rule("$50 sign-up credit on first bill.") is None


def test_parse_rule_trigger_but_no_rebate_returns_none():
    assert parse_rule("Enrol in our VPP programme today!") is None


def test_parse_rule_empty_returns_none():
    assert parse_rule("") is None


# --- apply_rule -------------------------------------------------------


def test_apply_rule_no_op_when_batteries_zero():
    bd = FakeBreakdown()
    rule = {
        "monthly_rebate_aud": Decimal("15"),
        "batteries_enrolled": 0,
        "source": "",
    }
    apply_rule(rule, [], bd)
    assert bd.incentive_aud_inc_gst == Decimal("0")
    assert bd.trace == []


def test_apply_rule_credits_one_battery():
    """$15/mo × 1 battery / 30 days = $0.50/day."""
    bd = FakeBreakdown()
    rule = {
        "monthly_rebate_aud": Decimal("15"),
        "batteries_enrolled": 1,
        "source": "test",
    }
    apply_rule(rule, [], bd)
    assert bd.incentive_aud_inc_gst == -Decimal("0.5")
    assert len(bd.trace) == 1
    assert bd.trace[0]["incentive"] == "vpp_rebate"


def test_apply_rule_scales_with_batteries():
    """3 batteries × $15/mo / 30 = $1.50/day."""
    bd = FakeBreakdown()
    rule = {
        "monthly_rebate_aud": Decimal("15"),
        "batteries_enrolled": 3,
        "source": "",
    }
    apply_rule(rule, [], bd)
    assert bd.incentive_aud_inc_gst == -Decimal("1.5")


def test_apply_rule_no_op_when_rebate_zero():
    bd = FakeBreakdown()
    rule = {
        "monthly_rebate_aud": Decimal("0"),
        "batteries_enrolled": 1,
        "source": "",
    }
    apply_rule(rule, [], bd)
    assert bd.incentive_aud_inc_gst == Decimal("0")


# --- parse_from_incentives -------------------------------------------


def test_parse_from_incentives_finds_vpp():
    incs = [
        {
            "displayName": "PowerResponse VPP",
            "eligibility": "$15 monthly credit per battery for participating in our VPP.",
        },
        {
            "displayName": "Sign-Up",
            "eligibility": "$50 credit on first bill.",
        },
    ]
    rules = parse_from_incentives(incs)
    assert len(rules) == 1
    assert rules[0]["monthly_rebate_aud"] == Decimal("15")


def test_parse_from_incentives_propagates_batteries():
    incs = [
        {
            "displayName": "VPP",
            "eligibility": "$15 monthly credit per battery for participating in our VPP.",
        }
    ]
    rules = parse_from_incentives(incs, batteries_enrolled=2)
    assert len(rules) == 1
    assert rules[0]["batteries_enrolled"] == 2


def test_parse_from_incentives_falls_back_to_description():
    incs = [
        {
            "displayName": "VPP",
            "description": "Enrol your battery in our Virtual Power Plant for $20/month per battery.",
            "eligibility": "",
        }
    ]
    rules = parse_from_incentives(incs)
    assert len(rules) == 1
    assert rules[0]["monthly_rebate_aud"] == Decimal("20")


def test_parse_from_incentives_empty():
    assert parse_from_incentives([]) == []
