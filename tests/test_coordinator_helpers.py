"""Phase 3.0g — tests for coordinator-level pure helpers.

CodeRabbit + Sourcery flagged the inline peak-rate derivation in
`_build_data_dict` as brittle. Extracted to module-level
`_extract_peak_rate_c_inc_gst(cdr_plan)` and pinned with edge cases.
"""
from __future__ import annotations

from custom_components.pricehawk.coordinator import (
    _extract_peak_rate_c_inc_gst,
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
