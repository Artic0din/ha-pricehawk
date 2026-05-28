"""Tests for Amber Electric cost calculator and AmberProvider adapter."""

from datetime import date, datetime, timedelta

import pytest

from custom_components.pricehawk.amber_calculator import AmberCalculator
from custom_components.pricehawk.providers.amber import AmberProvider


def _make_dt(hour=12, minute=0, second=0, day=29):
    """Helper to create datetimes for March 2026."""
    return datetime(2026, 3, day, hour, minute, second)


class TestImportAccumulation:
    def test_single_import_update(self):
        """5000W import for 0.01h at 30 c/kWh -> 0.05 kWh, 1.5c cost."""
        calc = AmberCalculator()
        t0 = _make_dt(12, 0, 0)
        t1 = t0 + timedelta(hours=0.01)  # 36 seconds

        calc.update(0, 30.0, 8.0, t0)  # seed last_update
        calc.update(5000, 30.0, 8.0, t1)

        assert calc.import_kwh_today == pytest.approx(0.05, abs=1e-6)
        assert calc.import_cost_today_c == pytest.approx(1.5, abs=1e-4)
        assert calc.export_kwh_today == pytest.approx(0.0)

    def test_multiple_imports_accumulate(self):
        calc = AmberCalculator()
        t0 = _make_dt(12, 0, 0)
        delta = timedelta(seconds=36)  # 0.01h

        calc.update(0, 30.0, 8.0, t0)
        calc.update(5000, 30.0, 8.0, t0 + delta)
        calc.update(5000, 30.0, 8.0, t0 + 2 * delta)

        assert calc.import_kwh_today == pytest.approx(0.10, abs=1e-6)
        assert calc.import_cost_today_c == pytest.approx(3.0, abs=1e-4)


class TestExportAccumulation:
    def test_single_export_update(self):
        """-3000W export for 0.01h at 8 c/kWh -> 0.03 kWh, 0.24c earnings."""
        calc = AmberCalculator()
        t0 = _make_dt(12, 0, 0)
        t1 = t0 + timedelta(hours=0.01)

        calc.update(0, 30.0, 8.0, t0)
        calc.update(-3000, 30.0, 8.0, t1)

        assert calc.export_kwh_today == pytest.approx(0.03, abs=1e-6)
        assert calc.export_earnings_today_c == pytest.approx(0.24, abs=1e-4)
        assert calc.import_kwh_today == pytest.approx(0.0)


class TestMidnightReset:
    def test_accumulators_reset_on_day_change(self):
        calc = AmberCalculator()
        # Accumulate some energy on day 29
        t0 = _make_dt(23, 50, 0, day=29)
        t1 = _make_dt(23, 50, 36, day=29)  # +36s = 0.01h
        calc.update(5000, 30.0, 8.0, t0)
        calc.update(5000, 30.0, 8.0, t1)
        assert calc.import_kwh_today > 0

        # Cross midnight with a gap > 6 min — gap is clamped to 0.1h
        t2 = _make_dt(0, 10, 0, day=30)
        calc.update(5000, 30.0, 8.0, t2)

        # Daily accumulators reset at midnight, then gap-clamped accumulation
        # 5kW * 0.1h = 0.5 kWh at 30 c/kWh = 15c
        assert calc.import_kwh_today == pytest.approx(0.5)
        assert calc.export_kwh_today == pytest.approx(0.0)
        assert calc.import_cost_today_c == pytest.approx(15.0)
        assert calc.export_earnings_today_c == pytest.approx(0.0)


class TestGapProtection:
    def test_large_gap_clamped(self):
        """Large gap is clamped to 0.1h, not discarded."""
        calc = AmberCalculator()
        t0 = _make_dt(12, 0, 0)
        t1 = t0 + timedelta(minutes=10)  # 10 min gap, clamped to 6 min

        calc.update(5000, 30.0, 8.0, t0)
        calc.update(5000, 30.0, 8.0, t1)

        # 5kW * 0.1h = 0.5 kWh
        assert calc.import_kwh_today == pytest.approx(0.5)
        assert calc.import_cost_today_c == pytest.approx(0.5 * 30.0)

    def test_normal_interval_accumulates(self):
        calc = AmberCalculator()
        t0 = _make_dt(12, 0, 0)
        t1 = t0 + timedelta(seconds=30)  # 30s is well within threshold

        calc.update(5000, 30.0, 8.0, t0)
        calc.update(5000, 30.0, 8.0, t1)

        assert calc.import_kwh_today > 0


class TestNetCost:
    def test_import_exceeds_export(self):
        """Import 50c, export 30c -> net = 0.20 AUD."""
        calc = AmberCalculator()
        calc._import_cost_today_c = 50.0
        calc._export_earnings_today_c = 30.0
        assert calc.net_daily_cost_aud == pytest.approx(0.20)

    def test_export_exceeds_import(self):
        """Export > import -> negative net (earning day)."""
        calc = AmberCalculator()
        calc._import_cost_today_c = 30.0
        calc._export_earnings_today_c = 80.0
        assert calc.net_daily_cost_aud == pytest.approx(-0.50)

    def test_zero_cost(self):
        calc = AmberCalculator()
        assert calc.net_daily_cost_aud == pytest.approx(0.0)


class TestRateStorage:
    def test_rates_stored_after_update(self):
        calc = AmberCalculator()
        t0 = _make_dt(12, 0, 0)
        calc.update(5000, 25.0, 7.0, t0)

        assert calc.current_import_rate_c_kwh == pytest.approx(25.0)
        assert calc.current_export_rate_c_kwh == pytest.approx(7.0)

    def test_rates_update_on_each_call(self):
        calc = AmberCalculator()
        t0 = _make_dt(12, 0, 0)
        t1 = t0 + timedelta(seconds=30)

        calc.update(5000, 25.0, 7.0, t0)
        calc.update(5000, 35.0, 10.0, t1)

        assert calc.current_import_rate_c_kwh == pytest.approx(35.0)
        assert calc.current_export_rate_c_kwh == pytest.approx(10.0)


class TestNegativeFeedIn:
    def test_negative_export_rate_uses_abs(self):
        """Feed-in sensor may return negative values — abs() is used."""
        calc = AmberCalculator()
        t0 = _make_dt(12, 0, 0)
        t1 = t0 + timedelta(hours=0.01)

        calc.update(0, 30.0, -8.0, t0)
        calc.update(-3000, 30.0, -8.0, t1)

        # abs(-8.0) = 8.0, so 0.03 kWh * 8 c/kWh = 0.24c
        assert calc.export_earnings_today_c == pytest.approx(0.24, abs=1e-4)


class TestSerialization:
    def test_round_trip_same_day(self):
        calc = AmberCalculator()
        t0 = _make_dt(12, 0, 0)
        t1 = t0 + timedelta(seconds=30)

        calc.update(5000, 25.0, 7.0, t0)
        calc.update(5000, 25.0, 7.0, t1)

        data = calc.to_dict()

        calc2 = AmberCalculator()
        calc2.from_dict(data, today=date(2026, 3, 29))

        assert calc2.import_kwh_today == pytest.approx(calc.import_kwh_today)
        assert calc2.export_kwh_today == pytest.approx(calc.export_kwh_today)
        assert calc2.import_cost_today_c == pytest.approx(calc.import_cost_today_c)
        assert calc2.export_earnings_today_c == pytest.approx(calc.export_earnings_today_c)
        assert calc2.current_import_rate_c_kwh == pytest.approx(25.0)
        assert calc2.current_export_rate_c_kwh == pytest.approx(7.0)

    def test_stale_date_does_not_restore_accumulators(self):
        """from_dict with yesterday's date does NOT restore daily accumulators."""
        calc = AmberCalculator()
        t0 = _make_dt(12, 0, 0)
        t1 = t0 + timedelta(seconds=30)

        calc.update(5000, 25.0, 7.0, t0)
        calc.update(5000, 25.0, 7.0, t1)

        data = calc.to_dict()

        calc2 = AmberCalculator()
        # Pretend today is March 30 (data is from March 29)
        calc2.from_dict(data, today=date(2026, 3, 30))

        # Daily accumulators should NOT be restored
        assert calc2.import_kwh_today == pytest.approx(0.0)
        assert calc2.export_kwh_today == pytest.approx(0.0)
        assert calc2.import_cost_today_c == pytest.approx(0.0)
        assert calc2.export_earnings_today_c == pytest.approx(0.0)

        # But rates should still be restored
        assert calc2.current_import_rate_c_kwh == pytest.approx(25.0)
        assert calc2.current_export_rate_c_kwh == pytest.approx(7.0)

    def test_to_dict_contains_all_fields(self):
        calc = AmberCalculator()
        t0 = _make_dt(12, 0, 0)
        calc.update(5000, 25.0, 7.0, t0)

        data = calc.to_dict()
        expected_keys = {
            "import_kwh_today",
            "export_kwh_today",
            "import_cost_today_c",
            "export_earnings_today_c",
            "current_import_rate_c",
            "current_export_rate_c",
            "last_update",
            "last_reset_date",
        }
        assert set(data.keys()) == expected_keys

    def test_from_dict_empty(self):
        """from_dict with empty dict doesn't crash."""
        calc = AmberCalculator()
        calc.from_dict({}, today=date(2026, 3, 29))
        assert calc.current_import_rate_c_kwh == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Edge case tests (AEGIS audit DA-006)
# ---------------------------------------------------------------------------


class TestAmberEdgeCases:
    def test_negative_export_rate(self):
        """Amber can have negative feed-in rates — abs() should handle."""
        calc = AmberCalculator()
        t0 = _make_dt(12, 0)
        t1 = t0 + timedelta(hours=0.01)

        calc.update(0, 30.0, -5.0, t0)  # seed
        calc.update(-3000, 30.0, -5.0, t1)  # 3kW export at -5 c/kWh

        # abs(export_rate) should be used
        expected_kwh = 3.0 * 0.01
        assert calc.export_kwh_today == pytest.approx(expected_kwh, abs=1e-6)
        assert calc.export_earnings_today_c == pytest.approx(expected_kwh * 5.0, abs=1e-4)

    def test_zero_rates(self):
        """Zero import and export rates produce zero cost."""
        calc = AmberCalculator()
        t0 = _make_dt(12, 0)
        t1 = t0 + timedelta(hours=0.01)

        calc.update(0, 0.0, 0.0, t0)
        calc.update(5000, 0.0, 0.0, t1)

        assert calc.import_cost_today_c == pytest.approx(0.0)

    def test_net_cost_with_fixed_charges(self):
        """Net daily cost includes fixed charges even with no energy."""
        calc = AmberCalculator(amber_network_daily_c=100.0, amber_subscription_daily_c=50.0)
        assert calc.daily_fixed_charges_aud == pytest.approx(1.50)
        assert calc.net_daily_cost_aud == pytest.approx(1.50)


# ---------------------------------------------------------------------------
# AmberProvider adapter (providers/amber.py) — lines 36-94
# ---------------------------------------------------------------------------


def _make_provider(network_c: float = 0.0, subscription_c: float = 0.0) -> AmberProvider:
    return AmberProvider(
        amber_network_daily_c=network_c,
        amber_subscription_daily_c=subscription_c,
    )


class TestAmberProviderIdentity:
    def test_id_and_name(self):
        # ARRANGE / ACT
        provider = _make_provider()

        # ASSERT
        assert provider.id == "amber"
        assert provider.name == "Amber Electric"


class TestAmberProviderRatesBeforeSet:
    """Properties return safe defaults before set_current_rates is called."""

    def test_import_rate_default_zero(self):
        provider = _make_provider()
        assert provider.current_import_rate_c_kwh == pytest.approx(0.0)

    def test_export_rate_default_zero(self):
        provider = _make_provider()
        assert provider.current_export_rate_c_kwh == pytest.approx(0.0)


class TestAmberProviderSetCurrentRates:
    """set_current_rates drives the rate properties."""

    def test_import_rate_reflects_set_value(self):
        # ARRANGE
        provider = _make_provider()

        # ACT
        provider.set_current_rates(import_c_kwh=28.5, export_c_kwh=7.0)

        # ASSERT
        assert provider.current_import_rate_c_kwh == pytest.approx(28.5)

    def test_export_rate_reflects_set_value(self):
        provider = _make_provider()
        provider.set_current_rates(import_c_kwh=28.5, export_c_kwh=7.0)
        assert provider.current_export_rate_c_kwh == pytest.approx(7.0)

    def test_none_rates_stay_none_default_zero(self):
        provider = _make_provider()
        provider.set_current_rates(import_c_kwh=None, export_c_kwh=None)
        # Property must return 0.0 (not None) for safe sensor reads
        assert provider.current_import_rate_c_kwh == pytest.approx(0.0)
        assert provider.current_export_rate_c_kwh == pytest.approx(0.0)


class TestAmberProviderUpdateSkipsWhenRatesNone:
    """update() must be a no-op when rates not yet set (lines 40-42)."""

    def test_no_accumulation_without_rates(self):
        # ARRANGE
        provider = _make_provider()
        t0 = datetime(2026, 5, 28, 12, 0, 0)
        t1 = t0 + timedelta(hours=0.01)

        # ACT — update with no rates set
        provider.update(5000, t0)
        provider.update(5000, t1)

        # ASSERT — nothing accumulated
        assert provider.import_kwh_today == pytest.approx(0.0)
        assert provider.import_cost_today_c == pytest.approx(0.0)


class TestAmberProviderAccumulation:
    """update() with rates set accumulates correctly (lines 45, 49, 53, 57)."""

    def test_import_accumulates(self):
        # ARRANGE
        provider = _make_provider()
        provider.set_current_rates(import_c_kwh=30.0, export_c_kwh=8.0)
        t0 = datetime(2026, 5, 28, 12, 0, 0)
        t1 = t0 + timedelta(hours=0.01)

        # ACT
        provider.update(0, t0)
        provider.update(5000, t1)  # 5000W × 0.01h = 0.05 kWh

        # ASSERT
        assert provider.import_kwh_today == pytest.approx(0.05, abs=1e-6)
        assert provider.import_cost_today_c == pytest.approx(1.5, abs=1e-4)

    def test_export_accumulates(self):
        # ARRANGE
        provider = _make_provider()
        provider.set_current_rates(import_c_kwh=30.0, export_c_kwh=8.0)
        t0 = datetime(2026, 5, 28, 12, 0, 0)
        t1 = t0 + timedelta(hours=0.01)

        # ACT
        provider.update(0, t0)
        provider.update(-3000, t1)  # -3000W × 0.01h = 0.03 kWh export

        # ASSERT
        assert provider.export_kwh_today == pytest.approx(0.03, abs=1e-6)
        assert provider.export_earnings_today_c == pytest.approx(0.24, abs=1e-4)


class TestAmberProviderFixedChargesAndNet:
    """daily_fixed_charges_aud and net_daily_cost_aud (lines 61, 65, 69, 73)."""

    def test_daily_fixed_charges_from_constructor(self):
        # ARRANGE
        provider = _make_provider(network_c=110.0, subscription_c=55.0)

        # ACT / ASSERT — 165c = $1.65
        assert provider.daily_fixed_charges_aud == pytest.approx(1.65)

    def test_net_daily_cost_includes_fixed(self):
        # ARRANGE
        provider = _make_provider(network_c=100.0, subscription_c=0.0)

        # ACT / ASSERT — no energy → cost = supply only
        assert provider.net_daily_cost_aud == pytest.approx(1.0)


class TestAmberProviderExtrasAndDict:
    """extras, to_dict, from_dict (lines 77, 81, 84, 87, 94)."""

    def test_extras_is_empty_dict(self):
        provider = _make_provider()
        assert provider.extras == {}

    def test_to_dict_returns_dict(self):
        provider = _make_provider()
        result = provider.to_dict()
        assert isinstance(result, dict)

    def test_from_dict_restores_state(self):
        # ARRANGE — accumulate some energy, snapshot, restore
        provider = _make_provider()
        provider.set_current_rates(import_c_kwh=30.0, export_c_kwh=8.0)
        t0 = datetime(2026, 5, 28, 12, 0, 0)
        t1 = t0 + timedelta(hours=0.01)
        provider.update(0, t0)
        provider.update(5000, t1)

        snapshot = provider.to_dict()
        today = t0.date()

        # ACT
        restored = _make_provider()
        restored.from_dict(snapshot, today=today)

        # ASSERT
        assert restored.import_kwh_today == pytest.approx(provider.import_kwh_today)
        assert restored.import_cost_today_c == pytest.approx(provider.import_cost_today_c)

    def test_calculator_property_returns_amber_calculator(self):
        provider = _make_provider()
        assert isinstance(provider.calculator, AmberCalculator)


class TestAmberProviderResetDaily:
    """reset_daily delegates to calculator (line 45)."""

    def test_reset_clears_accumulation(self):
        # ARRANGE
        provider = _make_provider()
        provider.set_current_rates(import_c_kwh=30.0, export_c_kwh=8.0)
        t0 = datetime(2026, 5, 28, 12, 0, 0)
        t1 = t0 + timedelta(hours=0.01)
        provider.update(0, t0)
        provider.update(5000, t1)
        assert provider.import_kwh_today > 0

        # ACT
        provider.reset_daily()

        # ASSERT
        assert provider.import_kwh_today == pytest.approx(0.0)
        assert provider.import_cost_today_c == pytest.approx(0.0)
