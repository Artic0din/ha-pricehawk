"""Tests for Amber Electric cost calculator."""

from datetime import date, datetime, timedelta

import pytest

from custom_components.pricehawk.amber_calculator import AmberCalculator


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
