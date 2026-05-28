"""Tests for the Flow Power provider — Happy Hour FiT and PEA calculation."""

from datetime import datetime, timedelta

import pytest

from custom_components.pricehawk.providers.flow_power import (
    FLOW_POWER_BENCHMARK_C,
    FLOW_POWER_DEFAULT_BASE_RATE_C,
    FLOW_POWER_MARKET_AVG_C,
    FlowPowerProvider,
    calculate_pea,
    happy_hour_rate_for_region,
    is_happy_hour,
)


class TestPEACalculation:
    def test_legacy_pea_with_default_twap(self):
        # PEA = wholesale - market_avg - benchmark
        result = calculate_pea(wholesale_c_kwh=15.0)
        expected = 15.0 - FLOW_POWER_MARKET_AVG_C - FLOW_POWER_BENCHMARK_C
        assert result == pytest.approx(expected)

    def test_pea_negative_when_wholesale_below_average(self):
        result = calculate_pea(wholesale_c_kwh=5.0, twap_c_kwh=8.0)
        assert result == pytest.approx(5.0 - 8.0 - FLOW_POWER_BENCHMARK_C)
        assert result < 0  # discount applied

    def test_pea_with_explicit_twap(self):
        result = calculate_pea(wholesale_c_kwh=20.0, twap_c_kwh=12.0)
        assert result == pytest.approx(20.0 - 12.0 - FLOW_POWER_BENCHMARK_C)


class TestHappyHourWindow:
    @pytest.mark.parametrize(
        "hour,minute,expected",
        [
            (17, 29, False),  # one minute before window
            (17, 30, True),
            (18, 0, True),
            (19, 29, True),
            (19, 30, False),  # exactly at end is excluded
            (12, 0, False),
            (8, 0, False),
        ],
    )
    def test_happy_hour_boundaries(self, hour, minute, expected):
        dt = datetime(2026, 5, 1, hour, minute)
        assert is_happy_hour(dt) is expected

    def test_region_export_rates(self):
        assert happy_hour_rate_for_region("NSW1") == 45.0
        assert happy_hour_rate_for_region("QLD1") == 45.0
        assert happy_hour_rate_for_region("SA1") == 45.0
        assert happy_hour_rate_for_region("VIC1") == 35.0
        assert happy_hour_rate_for_region("TAS1") == 0.0
        assert happy_hour_rate_for_region("UNKNOWN") == 0.0


class TestImportAccumulation:
    def test_no_update_without_wholesale(self):
        provider = FlowPowerProvider({})
        provider.update(grid_power_w=5000, now_local=datetime(2026, 5, 1, 12))
        assert provider.import_kwh_today == 0.0

    def test_import_at_default_rate(self):
        provider = FlowPowerProvider(
            {"flow_power_pea_enabled": False}
        )  # disable PEA → import = base_rate
        provider.set_wholesale_rate(spot_c_kwh=10.0)

        t0 = datetime(2026, 5, 1, 12, 0, 0)
        t1 = t0 + timedelta(hours=0.01)  # 36 s
        provider.update(0, t0)  # seed
        provider.update(5000, t1)

        assert provider.import_kwh_today == pytest.approx(0.05, abs=1e-6)
        # 0.05 kWh × 34c = 1.7c
        assert provider.import_cost_today_c == pytest.approx(
            0.05 * FLOW_POWER_DEFAULT_BASE_RATE_C, abs=0.01
        )

    def test_pea_applied_to_import(self):
        # wholesale 15c, default TWAP 8c, BPEA 1.7c → PEA = 5.3c
        # Final import = 34 + 5.3 = 39.3c
        provider = FlowPowerProvider({})
        provider.set_wholesale_rate(spot_c_kwh=15.0)

        # Force last_update so current_import_rate_c_kwh has a context
        provider._last_update = datetime(2026, 5, 1, 12)

        expected_pea = 15.0 - FLOW_POWER_MARKET_AVG_C - FLOW_POWER_BENCHMARK_C
        assert provider.current_import_rate_c_kwh == pytest.approx(
            FLOW_POWER_DEFAULT_BASE_RATE_C + expected_pea
        )


class TestExportAccumulation:
    def test_no_export_outside_happy_hour(self):
        provider = FlowPowerProvider({"flow_power_region": "NSW1"})
        provider.set_wholesale_rate(spot_c_kwh=10.0)

        # 12pm — not in Happy Hour
        t0 = datetime(2026, 5, 1, 12, 0, 0)
        t1 = t0 + timedelta(hours=0.01)
        provider.update(0, t0)
        provider.update(-3000, t1)  # exporting 3 kW

        assert provider.export_kwh_today == pytest.approx(0.03, abs=1e-6)
        assert provider.export_earnings_today_c == pytest.approx(0.0)

    def test_export_in_happy_hour_nsw(self):
        provider = FlowPowerProvider({"flow_power_region": "NSW1"})
        provider.set_wholesale_rate(spot_c_kwh=10.0)

        # 6pm — in Happy Hour, NSW = 45c/kWh
        t0 = datetime(2026, 5, 1, 18, 0, 0)
        t1 = t0 + timedelta(hours=0.01)
        provider.update(0, t0)
        provider.update(-3000, t1)

        assert provider.export_kwh_today == pytest.approx(0.03, abs=1e-6)
        # 0.03 kWh × 45c = 1.35c
        assert provider.export_earnings_today_c == pytest.approx(0.03 * 45.0, abs=0.01)
        assert provider.extras["happy_hour_export_kwh"] == pytest.approx(0.03, abs=1e-6)

    def test_export_in_happy_hour_vic(self):
        provider = FlowPowerProvider({"flow_power_region": "VIC1"})
        provider.set_wholesale_rate(spot_c_kwh=10.0)
        t0 = datetime(2026, 5, 1, 18, 0, 0)
        t1 = t0 + timedelta(hours=0.01)
        provider.update(0, t0)
        provider.update(-3000, t1)
        assert provider.export_earnings_today_c == pytest.approx(0.03 * 35.0, abs=0.01)


class TestPersistence:
    def test_roundtrip(self):
        provider = FlowPowerProvider({"flow_power_region": "NSW1"})
        provider.set_wholesale_rate(spot_c_kwh=12.0)
        t0 = datetime(2026, 5, 1, 18, 0, 0)
        t1 = t0 + timedelta(hours=0.01)
        provider.update(0, t0)
        provider.update(-3000, t1)

        snapshot = provider.to_dict()

        restored = FlowPowerProvider({"flow_power_region": "NSW1"})
        restored.from_dict(snapshot, today=t0.date())

        assert restored.import_kwh_today == pytest.approx(provider.import_kwh_today)
        assert restored.export_kwh_today == pytest.approx(provider.export_kwh_today)
        assert restored.export_earnings_today_c == pytest.approx(provider.export_earnings_today_c)

    def test_stale_day_resets_accumulators(self):
        provider = FlowPowerProvider({})
        provider.set_wholesale_rate(spot_c_kwh=10.0)
        t0 = datetime(2026, 5, 1, 12)
        t1 = t0 + timedelta(hours=0.01)
        provider.update(0, t0)
        provider.update(5000, t1)
        snapshot = provider.to_dict()

        # Restore on a different day — accumulators should NOT be loaded
        restored = FlowPowerProvider({})
        restored.from_dict(snapshot, today=t0.date() + timedelta(days=1))
        assert restored.import_kwh_today == 0.0


class TestNetDailyCost:
    def test_net_cost_includes_supply(self):
        provider = FlowPowerProvider(
            {
                "flow_power_pea_enabled": False,
                "flow_power_daily_supply": 100.0,  # $1/day
            }
        )
        provider.set_wholesale_rate(spot_c_kwh=10.0)

        t0 = datetime(2026, 5, 1, 12, 0, 0)
        t1 = t0 + timedelta(hours=0.01)
        provider.update(0, t0)
        provider.update(5000, t1)

        # supply $1.00 + import 0.05 kWh × 34c = 1.7c → $1.017
        assert provider.net_daily_cost_aud == pytest.approx(
            (100.0 + 0.05 * FLOW_POWER_DEFAULT_BASE_RATE_C) / 100.0, abs=1e-4
        )
