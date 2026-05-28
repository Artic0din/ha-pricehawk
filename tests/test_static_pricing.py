"""Phase 7 PR-4 — static_pricing helpers tests.

Covers AC-2 (PRD tariffPeriod → rate lookup) and AC-6 (back-compat mode
resolver). Mode-dispatch coordinator tests live in test_coordinator.py
plus the existing test_dynamic_wholesale_tariff_provider.py harness.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.pricehawk.const import (
    CONF_AMBER_ENABLED,
    CONF_AMBER_PRICING_MODE,
    PRICING_MODE_LIVE_API,
    PRICING_MODE_OFF,
    PRICING_MODE_STATIC_PRD,
)
from custom_components.pricehawk.static_pricing import (
    evaluate_static_rates,
    resolve_pricing_mode,
)


# Australian Eastern Standard Time (no DST) — matches AEMO NEM-time anchor.
AEST = ZoneInfo("Australia/Brisbane")
SYDNEY = ZoneInfo("Australia/Sydney")


def _all_days() -> list[str]:
    return ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def _tou_plan(
    *,
    peak_rate: str = "0.40",
    offpeak_rate: str = "0.15",
    fit_rate: str = "0.05",
) -> dict:
    """Build a minimal CDR PRD envelope with peak/off-peak TOU + flat FIT."""
    return {
        "data": {
            "electricityContract": {
                "tariffPeriod": [
                    {
                        "dailySupplyCharge": "1.10",
                        "rateBlockUType": "timeOfUseRates",
                        "timeOfUseRates": [
                            {
                                "type": "PEAK",
                                "rates": [{"unitPrice": peak_rate}],
                                "timeOfUse": [
                                    {
                                        "days": _all_days(),
                                        "startTime": "16:00",
                                        "endTime": "21:00",
                                    }
                                ],
                            },
                            {
                                "type": "OFF_PEAK",
                                "rates": [{"unitPrice": offpeak_rate}],
                                "timeOfUse": [
                                    {
                                        "days": _all_days(),
                                        "startTime": "21:00",
                                        "endTime": "16:00",
                                    }
                                ],
                            },
                        ],
                    }
                ],
                "solarFeedInTariff": [
                    {
                        "tariffUType": "singleTariff",
                        "singleTariff": {
                            "rates": [{"unitPrice": fit_rate}],
                        },
                    }
                ],
            }
        }
    }


def _stepped_plan(*, step1_rate: str = "0.22", fit_rate: str = "0.05") -> dict:
    return {
        "data": {
            "electricityContract": {
                "tariffPeriod": [
                    {
                        "dailySupplyCharge": "0.88",
                        "rateBlockUType": "singleRate",
                        "singleRate": {
                            "rates": [
                                {"unitPrice": step1_rate, "volume": "15.0"},
                                {"unitPrice": "0.28"},
                            ]
                        },
                    }
                ],
                "solarFeedInTariff": [
                    {
                        "tariffUType": "singleTariff",
                        "singleTariff": {
                            "rates": [{"unitPrice": fit_rate}],
                        },
                    }
                ],
            }
        }
    }


# ----------------------------------------------------------------------
# resolve_pricing_mode — back-compat resolver (AC-6)
# ----------------------------------------------------------------------


class TestResolvePricingMode:
    def test_explicit_mode_wins(self):
        mode = resolve_pricing_mode(
            options={CONF_AMBER_PRICING_MODE: PRICING_MODE_STATIC_PRD, CONF_AMBER_ENABLED: True},
            data={},
            mode_key=CONF_AMBER_PRICING_MODE,
            legacy_enabled_key=CONF_AMBER_ENABLED,
        )
        assert mode == PRICING_MODE_STATIC_PRD

    def test_legacy_enabled_true_maps_to_live_api(self):
        mode = resolve_pricing_mode(
            options={CONF_AMBER_ENABLED: True},
            data={},
            mode_key=CONF_AMBER_PRICING_MODE,
            legacy_enabled_key=CONF_AMBER_ENABLED,
        )
        assert mode == PRICING_MODE_LIVE_API

    def test_legacy_enabled_in_data_maps_to_live_api(self):
        # Phase 2.x — some keys lived in entry.data not entry.options.
        mode = resolve_pricing_mode(
            options={},
            data={CONF_AMBER_ENABLED: True},
            mode_key=CONF_AMBER_PRICING_MODE,
            legacy_enabled_key=CONF_AMBER_ENABLED,
        )
        assert mode == PRICING_MODE_LIVE_API

    def test_legacy_enabled_false_maps_to_off(self):
        mode = resolve_pricing_mode(
            options={CONF_AMBER_ENABLED: False},
            data={},
            mode_key=CONF_AMBER_PRICING_MODE,
            legacy_enabled_key=CONF_AMBER_ENABLED,
        )
        assert mode == PRICING_MODE_OFF

    def test_absent_maps_to_off(self):
        mode = resolve_pricing_mode(
            options={},
            data={},
            mode_key=CONF_AMBER_PRICING_MODE,
            legacy_enabled_key=CONF_AMBER_ENABLED,
        )
        assert mode == PRICING_MODE_OFF

    def test_unknown_explicit_falls_through_to_legacy(self):
        # Defensive: an invalid mode string ignores explicit and resolves
        # via the legacy path.
        mode = resolve_pricing_mode(
            options={CONF_AMBER_PRICING_MODE: "garbage", CONF_AMBER_ENABLED: True},
            data={},
            mode_key=CONF_AMBER_PRICING_MODE,
            legacy_enabled_key=CONF_AMBER_ENABLED,
        )
        assert mode == PRICING_MODE_LIVE_API


# ----------------------------------------------------------------------
# evaluate_static_rates — PRD tariffPeriod → (import, export) (AC-2)
# ----------------------------------------------------------------------


class TestEvaluateStaticRates:
    def test_peak_window(self):
        plan = _tou_plan(peak_rate="0.40", fit_rate="0.05")
        now = datetime(2026, 5, 21, 18, 30, tzinfo=AEST)  # peak
        imp, exp = evaluate_static_rates(plan, now)
        # 0.40 $/kWh ex-GST → 0.40 * 1.10 * 100 = 44.0 c/kWh inc-GST
        assert imp == pytest.approx(44.0, rel=1e-6)
        # 0.05 $/kWh ex-GST → 5.5 c/kWh inc-GST
        assert exp == pytest.approx(5.5, rel=1e-6)

    def test_offpeak_window(self):
        plan = _tou_plan(peak_rate="0.40", offpeak_rate="0.15", fit_rate="0.05")
        now = datetime(2026, 5, 21, 22, 0, tzinfo=AEST)  # off-peak
        imp, exp = evaluate_static_rates(plan, now)
        # 0.15 * 1.10 * 100 = 16.5 c/kWh
        assert imp == pytest.approx(16.5, rel=1e-6)
        assert exp == pytest.approx(5.5, rel=1e-6)

    def test_stepped_uses_first_tier(self):
        """Static-PRD reflects step1 rate; live_api needed for accurate stepping."""
        plan = _stepped_plan(step1_rate="0.22", fit_rate="0.05")
        now = datetime(2026, 5, 21, 12, 0, tzinfo=AEST)
        imp, exp = evaluate_static_rates(plan, now)
        # 0.22 * 1.10 * 100 = 24.2 c/kWh
        assert imp == pytest.approx(24.2, rel=1e-6)
        assert exp == pytest.approx(5.5, rel=1e-6)

    def test_empty_envelope_returns_zero(self):
        assert evaluate_static_rates(None, datetime(2026, 5, 21, 12, 0, tzinfo=AEST)) == (0.0, 0.0)
        assert evaluate_static_rates({}, datetime(2026, 5, 21, 12, 0, tzinfo=AEST)) == (0.0, 0.0)
        assert evaluate_static_rates(
            {"data": {"electricityContract": {}}},
            datetime(2026, 5, 21, 12, 0, tzinfo=AEST),
        ) == (0.0, 0.0)

    def test_no_matching_window_returns_zero_import(self):
        """TOU rates with windows that don't cover now_local → 0.0 import."""
        plan = {
            "data": {
                "electricityContract": {
                    "tariffPeriod": [
                        {
                            "rateBlockUType": "timeOfUseRates",
                            "timeOfUseRates": [
                                {
                                    "rates": [{"unitPrice": "0.40"}],
                                    "timeOfUse": [
                                        {
                                            "days": ["SUN"],
                                            "startTime": "01:00",
                                            "endTime": "02:00",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                    "solarFeedInTariff": [
                        {
                            "tariffUType": "singleTariff",
                            "singleTariff": {"rates": [{"unitPrice": "0.05"}]},
                        }
                    ],
                }
            }
        }
        # Thursday 18:30 — outside the SUN 01:00-02:00 window
        now = datetime(2026, 5, 21, 18, 30, tzinfo=AEST)
        imp, exp = evaluate_static_rates(plan, now)
        assert imp == 0.0
        assert exp == pytest.approx(5.5, rel=1e-6)

    def test_export_time_varying_tariff(self):
        """solarFeedInTariff timeVaryingTariffs → window-matched export rate."""
        plan = {
            "data": {
                "electricityContract": {
                    "tariffPeriod": [
                        {
                            "rateBlockUType": "singleRate",
                            "singleRate": {"rates": [{"unitPrice": "0.30"}]},
                        }
                    ],
                    "solarFeedInTariff": [
                        {
                            "tariffUType": "timeVaryingTariffs",
                            "timeVaryingTariffs": [
                                {
                                    "rates": [{"unitPrice": "0.15"}],
                                    "timeVariations": [
                                        {
                                            "days": _all_days(),
                                            "startTime": "10:00",
                                            "endTime": "14:00",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            }
        }
        # Noon → in the 10-14 window
        now = datetime(2026, 5, 21, 12, 0, tzinfo=AEST)
        _, exp = evaluate_static_rates(plan, now)
        assert exp == pytest.approx(0.15 * 1.10 * 100, rel=1e-6)

    def test_negative_export_rate(self):
        """Some retailers publish negative FiT (rare). Math must not crash."""
        plan = _tou_plan(peak_rate="0.40", fit_rate="-0.02")
        now = datetime(2026, 5, 21, 18, 30, tzinfo=AEST)
        _, exp = evaluate_static_rates(plan, now)
        assert exp == pytest.approx(-0.02 * 1.10 * 100, rel=1e-6)
        assert exp < 0

    def test_dst_boundary(self):
        """Sydney crosses AEDT/AEST at 02:00 AEDT first Sun Apr; brisbane stays AEST.

        Static rate lookup uses local-clock matching, so the rate returned
        depends on the `now_local` timezone passed in. This documents the
        contract — callers must pass a `now_local` in the HA-configured tz.
        """
        plan = _tou_plan(peak_rate="0.40", offpeak_rate="0.15", fit_rate="0.05")
        # Sunday 2026-04-05 02:30 in Sydney is during DST end (rate-wise,
        # off-peak per the all-day off-peak window).
        sydney_dst_end = datetime(2026, 4, 5, 2, 30, tzinfo=SYDNEY)
        imp, _ = evaluate_static_rates(plan, sydney_dst_end)
        assert imp == pytest.approx(16.5, rel=1e-6)

    def test_singleRate_no_rates_returns_zero(self):
        plan = {
            "data": {
                "electricityContract": {
                    "tariffPeriod": [
                        {
                            "rateBlockUType": "singleRate",
                            "singleRate": {"rates": []},
                        }
                    ],
                }
            }
        }
        imp, exp = evaluate_static_rates(plan, datetime(2026, 5, 21, 12, 0, tzinfo=AEST))
        assert imp == 0.0
        assert exp == 0.0

    def test_envelope_without_data_wrapper(self):
        """Some callers pass the inner dict directly; helper tolerates both."""
        envelope_inner = _tou_plan()["data"]
        now = datetime(2026, 5, 21, 18, 30, tzinfo=AEST)
        imp, exp = evaluate_static_rates(envelope_inner, now)
        assert imp == pytest.approx(44.0, rel=1e-6)
        assert exp == pytest.approx(5.5, rel=1e-6)
