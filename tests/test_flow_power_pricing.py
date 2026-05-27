"""Smoke tests for the vendored Flow Power pricing module.

These exist to catch import breakage and verify the public API surface
is reachable after vendoring. Behaviour tests live upstream; replicating
them here would create maintenance debt against a third-party codebase.

The PEA formula values are derived from the upstream PDS examples and
serve as anchor points — if these change, the SHA bump procedure in
``NOTICES.md`` must be followed (the formula is the contract Flow Power
publishes to customers).
"""

from __future__ import annotations

from datetime import datetime, time

import pytest

from custom_components.pricehawk.wholesale.flow_power import pricing
from custom_components.pricehawk.wholesale.flow_power.const import (
    FLOW_POWER_BENCHMARK,
    FLOW_POWER_DEFAULT_BASE_RATE,
    FLOW_POWER_EXPORT_RATES,
    FLOW_POWER_GST,
    FLOW_POWER_MARKET_AVG,
    HAPPY_HOUR_END,
    HAPPY_HOUR_START,
)


def test_constants_match_pds() -> None:
    """PDS-published values. Changing these means re-vendoring."""
    assert FLOW_POWER_MARKET_AVG == 8.0
    assert FLOW_POWER_BENCHMARK == 1.7
    assert FLOW_POWER_DEFAULT_BASE_RATE == 34.0
    assert FLOW_POWER_GST == 1.1
    assert HAPPY_HOUR_START == time(17, 30)
    assert HAPPY_HOUR_END == time(19, 30)


def test_export_rates_cover_all_nem_regions() -> None:
    """Every mainland NEM region has a Happy Hour rate; TAS is zero by policy."""
    assert FLOW_POWER_EXPORT_RATES == {
        "NSW1": 0.45,
        "QLD1": 0.45,
        "SA1": 0.45,
        "VIC1": 0.35,
        "TAS1": 0.00,
    }


def test_calculate_pea_legacy_formula() -> None:
    """PEA = Wholesale - TWAP - BPEA when network tariff params omitted."""
    # Wholesale 10, default TWAP 8.0, BPEA 1.7 → 10 - 8 - 1.7 = 0.3
    assert pricing.calculate_pea(wholesale_cents=10.0) == pytest.approx(0.3)
    # Explicit TWAP override.
    assert pricing.calculate_pea(wholesale_cents=10.0, twap=5.0) == pytest.approx(3.3)


def test_calculate_pea_v2_formula() -> None:
    """V2 formula activates only when both network tariff params provided."""
    # PEA = GST*W + tariff - GST*TWAP - avg_tariff - BPEA
    # = 1.1*10 + 5 - 1.1*8 - 4 - 1.7 = 11 + 5 - 8.8 - 4 - 1.7 = 1.5
    result = pricing.calculate_pea(
        wholesale_cents=10.0,
        twap=8.0,
        network_tariff_rate=5.0,
        avg_daily_tariff=4.0,
    )
    assert result == pytest.approx(1.5)


def test_calculate_import_price_clamps_negative_to_zero() -> None:
    """Tesla restriction: final price never goes below zero."""
    # Very low base, large negative PEA: should clamp at 0.0.
    result = pricing.calculate_import_price(
        wholesale_cents=-100.0,
        base_rate=5.0,
        pea_enabled=True,
    )
    assert result["final_cents"] == 0.0
    assert result["final_dollars"] == 0.0


def test_calculate_export_price_happy_hour_nsw() -> None:
    """Happy Hour 18:00 NSW1 returns 45 c/kWh."""
    result = pricing.calculate_export_price(
        region="NSW1",
        current_time=datetime(2026, 5, 27, 18, 0),
    )
    assert result["is_happy_hour"] is True
    assert result["export_cents"] == 45.0


def test_calculate_export_price_outside_happy_hour() -> None:
    """Outside 17:30-19:30 returns zero regardless of region."""
    result = pricing.calculate_export_price(
        region="NSW1",
        current_time=datetime(2026, 5, 27, 12, 0),
    )
    assert result["is_happy_hour"] is False
    assert result["export_cents"] == 0.0


def test_calculate_forecast_prices_handles_aemo_perkwh_field() -> None:
    """Forecast input uses AEMO's perKwh key; output has price_cents/dollars."""
    forecast = [
        {"nemTime": "2026/05/27 18:00:00", "perKwh": 12.0},
        {"nemTime": "2026/05/27 18:30:00", "perKwh": 6.0},
    ]
    out = pricing.calculate_forecast_prices(forecast)
    assert len(out) == 2
    assert all("price_cents" in p and "price_dollars" in p for p in out)
    assert out[0]["wholesale_cents"] == 12.0
