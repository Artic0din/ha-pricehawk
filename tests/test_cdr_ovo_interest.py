"""Tests for ovo_interest.py (Phase 2.11.7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from custom_components.pricehawk.cdr.incentive_parsers.common.ovo_interest import (
    INTEREST_RE,
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


def test_trigger_matches_credit_balance():
    assert TRIGGER_RE.search("interest paid on credit balance monthly")


def test_trigger_matches_interest_reward():
    assert TRIGGER_RE.search("Interest Rewards program")


def test_trigger_matches_account_balance():
    assert TRIGGER_RE.search("3% on your account balance")


def test_trigger_does_not_match_ev_offpeak():
    """ev_offpeak text should not trigger interest parser."""
    assert not TRIGGER_RE.search("$0.045/kWh usage charge between midnight and 6am")


def test_interest_regex_matches_3_percent_interest():
    m = INTEREST_RE.search("3% interest paid")
    assert m
    assert m.group("pct") == "3"


def test_interest_regex_matches_5_percent_APR():
    m = INTEREST_RE.search("5% APR on balance")
    assert m
    assert m.group("pct") == "5"


def test_interest_regex_matches_decimal_rate():
    m = INTEREST_RE.search("3.5% annual rate")
    assert m
    assert m.group("pct") == "3.5"


# --- parse_rule -------------------------------------------------------


def test_parse_rule_ovo_canonical():
    rule = parse_rule("3% interest on credit balances. Paid monthly to your OVO account.")
    assert rule is not None
    assert rule["annual_rate_pct"] == Decimal("3")
    assert rule["balance_aud"] == Decimal("0")  # opt-in default


def test_parse_rule_with_balance_opt_in():
    rule = parse_rule(
        "3% interest on credit balances.",
        balance_aud=Decimal("250"),
    )
    assert rule is not None
    assert rule["balance_aud"] == Decimal("250")


def test_parse_rule_no_trigger_returns_none():
    assert parse_rule("$50 sign-up credit on first bill.") is None


def test_parse_rule_no_pct_returns_none():
    """Trigger present but no rate."""
    assert parse_rule("Interest paid on credit balance.") is None


def test_parse_rule_empty_returns_none():
    assert parse_rule("") is None


# --- apply_rule -------------------------------------------------------


def test_apply_rule_no_op_when_balance_zero():
    bd = FakeBreakdown()
    rule = {"annual_rate_pct": Decimal("3"), "balance_aud": Decimal("0"), "source": ""}
    apply_rule(rule, [], bd)
    assert bd.incentive_aud_inc_gst == Decimal("0")
    assert bd.trace == []


def test_apply_rule_credits_daily_interest():
    """$100 × 3% / 365 = $0.00822/day."""
    bd = FakeBreakdown()
    rule = {
        "annual_rate_pct": Decimal("3"),
        "balance_aud": Decimal("100"),
        "source": "test",
    }
    apply_rule(rule, [], bd)
    # Credit = -0.00822 (negative = user gain)
    expected = -(Decimal("100") * Decimal("3") / Decimal("100") / Decimal("365"))
    assert bd.incentive_aud_inc_gst == expected
    assert len(bd.trace) == 1
    assert bd.trace[0]["incentive"] == "ovo_interest"
    assert bd.trace[0]["balance_aud"] == 100.0


def test_apply_rule_higher_balance_scales_linearly():
    bd = FakeBreakdown()
    rule = {
        "annual_rate_pct": Decimal("3"),
        "balance_aud": Decimal("500"),
        "source": "test",
    }
    apply_rule(rule, [], bd)
    # $500 × 3% / 365 = $0.0411/day
    expected_daily = Decimal("500") * Decimal("3") / Decimal("100") / Decimal("365")
    assert bd.incentive_aud_inc_gst == -expected_daily


def test_apply_rule_no_op_when_rate_zero():
    bd = FakeBreakdown()
    rule = {"annual_rate_pct": Decimal("0"), "balance_aud": Decimal("500"), "source": ""}
    apply_rule(rule, [], bd)
    assert bd.incentive_aud_inc_gst == Decimal("0")


# --- parse_from_incentives -------------------------------------------


def test_parse_from_incentives_finds_one():
    incs = [
        {
            "displayName": "Interest Rewards",
            "eligibility": "3% interest on credit balances.",
        },
        {
            "displayName": "EV Off-Peak",
            "eligibility": "$0.045/kWh usage charge between midnight and 6am.",
        },
    ]
    rules = parse_from_incentives(incs)
    assert len(rules) == 1
    assert rules[0]["annual_rate_pct"] == Decimal("3")
    assert rules[0]["source_displayName"] == "Interest Rewards"


def test_parse_from_incentives_propagates_balance():
    incs = [
        {
            "displayName": "Interest Rewards",
            "eligibility": "3% interest on credit balances.",
        }
    ]
    rules = parse_from_incentives(incs, balance_aud=Decimal("300"))
    assert len(rules) == 1
    assert rules[0]["balance_aud"] == Decimal("300")


def test_parse_from_incentives_empty():
    assert parse_from_incentives([]) == []
