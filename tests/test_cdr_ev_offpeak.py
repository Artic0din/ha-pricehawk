"""Tests for ev_offpeak.py (Phase 2.11.6).

Covers the EV midnight-6am rate-override parser. Math delegated to
free_window.apply_rule — we test parse + integration only here, since
free_window has its own test_cdr_free_window.py covering apply math.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from custom_components.pricehawk.cdr.incentive_parsers.common.ev_offpeak import (
    RATE_RE,
    TRIGGER_RE,
    WINDOW_RE,
    _token_to_minutes,
    parse_from_incentives,
    parse_rule,
)


# --- Time-token parser -------------------------------------------------


def test_token_midnight():
    assert _token_to_minutes("midnight") == 0


def test_token_noon():
    assert _token_to_minutes("noon") == 12 * 60


def test_token_6am():
    assert _token_to_minutes("6am") == 360


def test_token_11_30pm():
    assert _token_to_minutes("11:30pm") == 23 * 60 + 30


def test_token_12am_is_zero():
    assert _token_to_minutes("12am") == 0


def test_token_12pm_is_noon():
    assert _token_to_minutes("12pm") == 12 * 60


def test_token_invalid_raises():
    with pytest.raises(ValueError):
        _token_to_minutes("3 o'clock")


# --- Regex coverage ----------------------------------------------------


def test_trigger_matches_usage_charge():
    assert TRIGGER_RE.search("$0.045/kWh usage charge between midnight and 6am")


def test_trigger_matches_applied_to_import():
    assert TRIGGER_RE.search("$0.08/kWh between midnight and 7am, applied to all import")


def test_trigger_matches_overnight():
    assert TRIGGER_RE.search("overnight rate of $0.10/kWh from 12am to 6am")


def test_trigger_does_not_match_freeword():
    # free_window territory — should NOT trigger ev_offpeak.
    assert not TRIGGER_RE.search("Free electricity between 11am and 2pm everyday")


def test_window_matches_midnight_to_6am():
    m = WINDOW_RE.search("between midnight and 6am")
    assert m
    assert m.group("start") == "midnight"
    assert m.group("end") == "6am"


def test_window_matches_from_12am_to_6am():
    m = WINDOW_RE.search("from 12am to 6am")
    assert m
    assert m.group("start") == "12am"
    assert m.group("end") == "6am"


def test_rate_matches_dollar_decimal_per_kwh():
    m = RATE_RE.search("$0.045/kWh")
    assert m
    assert m.group("rate") == "0.045"


def test_rate_matches_dollar_no_per_kwh():
    m = RATE_RE.search("flat $0.10 overnight")
    assert m
    assert m.group("rate") == "0.10"


# --- parse_rule end-to-end --------------------------------------------


def test_parse_rule_ovo_canonical():
    """Canonical OVO eligibility wording."""
    rule = parse_rule("$0.045/kWh usage charge between midnight and 6am.")
    assert rule is not None
    assert rule["rate_c_per_kwh"] == Decimal("4.5")
    assert rule["windows"] == [(0, 360)]


def test_parse_rule_engie_with_disclaimer():
    """ENGIE wording (applied-to-import trigger) with controlled-load disclaimer."""
    rule = parse_rule(
        "$0.08/kWh between midnight and 7am, applied to all import. "
        "Does not apply to controlled loads."
    )
    assert rule is not None
    assert rule["rate_c_per_kwh"] == Decimal("8.0")
    assert rule["windows"] == [(0, 420)]


def test_parse_rule_overnight_keyword():
    rule = parse_rule("Flat $0.10/kWh overnight from 12am to 6am.")
    assert rule is not None
    assert rule["rate_c_per_kwh"] == Decimal("10.0")
    assert rule["windows"] == [(0, 360)]


def test_parse_rule_empty_returns_none():
    assert parse_rule("") is None


def test_parse_rule_no_trigger_returns_none():
    # Has rate + window but no ev_offpeak trigger word.
    assert parse_rule("$0.05/kWh between midnight and 6am") is None


def test_parse_rule_no_window_returns_none():
    assert parse_rule("$0.045/kWh usage charge applies") is None


def test_parse_rule_freeword_falls_through():
    """Free-window pattern doesn't trigger ev_offpeak."""
    assert parse_rule("Free electricity between 11am and 2pm everyday.") is None


def test_parse_rule_source_truncated():
    long_text = "$0.045/kWh usage charge between midnight and 6am. " + "x" * 300
    rule = parse_rule(long_text)
    assert rule is not None
    assert len(rule["source"]) <= 200


# --- parse_from_incentives integration -------------------------------


def test_parse_from_incentives_finds_eligibility_field():
    incs = [
        {
            "displayName": "EV Off-Peak",
            "eligibility": "$0.045/kWh usage charge between midnight and 6am.",
        }
    ]
    rules = parse_from_incentives(incs)
    assert len(rules) == 1
    assert rules[0]["rate_c_per_kwh"] == Decimal("4.5")
    assert rules[0]["source_displayName"] == "EV Off-Peak"


def test_parse_from_incentives_falls_back_to_description():
    incs = [
        {
            "displayName": "EV Charging",
            "description": "$0.08/kWh between midnight and 7am for vehicle charging.",
            "eligibility": "",
        }
    ]
    rules = parse_from_incentives(incs)
    assert len(rules) == 1
    assert rules[0]["rate_c_per_kwh"] == Decimal("8.0")


def test_parse_from_incentives_skips_unrelated():
    """Non-EV incentives should be skipped."""
    incs = [
        {
            "displayName": "Free 3",
            "eligibility": "Free electricity between 11am and 2pm everyday.",
        },
        {
            "displayName": "Sign-Up Credit",
            "eligibility": "$50 credit on first bill.",
        },
    ]
    assert parse_from_incentives(incs) == []


def test_parse_from_incentives_multiple_rules():
    incs = [
        {"displayName": "EV", "eligibility": "$0.045/kWh usage charge between midnight and 6am."},
        {"displayName": "EV2", "eligibility": "$0.08/kWh applied to import between 12am and 7am."},
    ]
    rules = parse_from_incentives(incs)
    assert len(rules) == 2
