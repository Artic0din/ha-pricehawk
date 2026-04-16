"""Tests for the GloBird tariff calculation engine."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from custom_components.pricehawk.tariff_engine import (
    DemandTracker,
    SuperExportTracker,
    TariffEngine,
    ZeroHeroTracker,
    calc_stepped_cost,
    get_current_tou_period,
    get_stepped_import_rate,
)

# ---------------------------------------------------------------------------
# Fixtures — use ZEROHERO and BOOST plan defaults
# ---------------------------------------------------------------------------

ZEROHERO_IMPORT_PERIODS = {
    "peak": {"rate": 38.50, "windows": [["16:00", "23:00"]]},
    "shoulder": {"rate": 26.95, "windows": [["23:00", "00:00"], ["00:00", "11:00"], ["14:00", "16:00"]]},
    "offpeak": {"rate": 0.00, "windows": [["11:00", "14:00"]]},
}

ZEROHERO_EXPORT_PERIODS = {
    "peak": {"rate": 3.00, "windows": [["16:00", "21:00"]]},
    "shoulder": {"rate": 0.30, "windows": [["21:00", "00:00"], ["00:00", "10:00"], ["14:00", "16:00"]]},
    "offpeak": {"rate": 0.00, "windows": [["10:00", "14:00"]]},
}

BOOST_IMPORT_TARIFF = {
    "type": "flat_stepped",
    "step1_threshold_kwh": 25.0,
    "step1_rate": 21.67,
    "step2_rate": 25.30,
}

ZEROHERO_OPTIONS = {
    "plan_type": "zerohero",
    "daily_supply_charge": 113.30,
    "demand_charge": 0.0,
    "import_tariff": {
        "type": "tou",
        "periods": ZEROHERO_IMPORT_PERIODS,
    },
    "export_tariff": {
        "type": "tou",
        "periods": ZEROHERO_EXPORT_PERIODS,
    },
    "incentives": ["zerohero_credit", "super_export", "free_power_window"],
}

BOOST_OPTIONS = {
    "plan_type": "boost",
    "daily_supply_charge": 111.10,
    "demand_charge": 0.0,
    "import_tariff": BOOST_IMPORT_TARIFF,
    "export_tariff": {
        "type": "tou",
        "periods": {
            "peak": {"rate": 3.00, "windows": [["16:00", "21:00"]]},
            "shoulder": {"rate": 0.10, "windows": [["21:00", "00:00"], ["00:00", "10:00"], ["14:00", "16:00"]]},
            "offpeak": {"rate": 0.00, "windows": [["10:00", "14:00"]]},
        },
    },
    "incentives": [],
}


def _dt(hour: int, minute: int = 0, day: int = 29) -> datetime:
    """Helper to build a local datetime on 2026-03-29."""
    return datetime(2026, 3, day, hour, minute, 0)


# ---------------------------------------------------------------------------
# TOU window matching tests
# ---------------------------------------------------------------------------

class TestTOUMatching:
    def test_tou_peak(self):
        """17:00 should be peak at 38.50 c/kWh."""
        name, rate = get_current_tou_period(ZEROHERO_IMPORT_PERIODS, _dt(17, 0))
        assert name == "peak"
        assert rate == 38.50

    def test_tou_shoulder_midnight(self):
        """23:30 should be shoulder (23:00-00:00 window)."""
        name, rate = get_current_tou_period(ZEROHERO_IMPORT_PERIODS, _dt(23, 30))
        assert name == "shoulder"
        assert rate == 26.95

    def test_tou_shoulder_early_morning(self):
        """05:00 should be shoulder (00:00-11:00 window)."""
        name, rate = get_current_tou_period(ZEROHERO_IMPORT_PERIODS, _dt(5, 0))
        assert name == "shoulder"
        assert rate == 26.95

    def test_tou_offpeak(self):
        """12:00 should be offpeak at 0.00 c/kWh."""
        name, rate = get_current_tou_period(ZEROHERO_IMPORT_PERIODS, _dt(12, 0))
        assert name == "offpeak"
        assert rate == 0.00

    def test_tou_boundary_peak_start(self):
        """16:00 exact should be peak (start-inclusive)."""
        name, rate = get_current_tou_period(ZEROHERO_IMPORT_PERIODS, _dt(16, 0))
        assert name == "peak"
        assert rate == 38.50

    def test_tou_boundary_shoulder_start(self):
        """23:00 exact should be shoulder (start-inclusive)."""
        name, rate = get_current_tou_period(ZEROHERO_IMPORT_PERIODS, _dt(23, 0))
        assert name == "shoulder"
        assert rate == 26.95

    def test_tou_boundary_offpeak_start(self):
        """11:00 exact should be offpeak."""
        name, rate = get_current_tou_period(ZEROHERO_IMPORT_PERIODS, _dt(11, 0))
        assert name == "offpeak"
        assert rate == 0.00

    def test_tou_shoulder_afternoon(self):
        """14:30 should be shoulder (14:00-16:00 window)."""
        name, rate = get_current_tou_period(ZEROHERO_IMPORT_PERIODS, _dt(14, 30))
        assert name == "shoulder"
        assert rate == 26.95

    def test_tou_midnight_exact(self):
        """00:00 should be shoulder (00:00-11:00 window)."""
        name, rate = get_current_tou_period(ZEROHERO_IMPORT_PERIODS, _dt(0, 0))
        assert name == "shoulder"
        assert rate == 26.95

    def test_tou_full_24h_coverage(self):
        """Every hour of the day should resolve to a known period."""
        for hour in range(24):
            name, _ = get_current_tou_period(ZEROHERO_IMPORT_PERIODS, _dt(hour, 0))
            assert name != "unknown", f"Hour {hour} fell through to unknown"


# ---------------------------------------------------------------------------
# Stepped pricing tests
# ---------------------------------------------------------------------------

class TestSteppedPricing:
    def test_stepped_below_threshold(self):
        """10 kWh (below 25 kWh limit) should return step1_rate."""
        rate = get_stepped_import_rate(BOOST_IMPORT_TARIFF, 10.0)
        assert rate == 21.67

    def test_stepped_above_threshold(self):
        """30 kWh (above 25 kWh limit) should return step2_rate."""
        rate = get_stepped_import_rate(BOOST_IMPORT_TARIFF, 30.0)
        assert rate == 25.30

    def test_stepped_at_threshold(self):
        """Exactly 25 kWh should still return step2_rate (marginal)."""
        rate = get_stepped_import_rate(BOOST_IMPORT_TARIFF, 25.0)
        assert rate == 25.30

    def test_stepped_cost_below_threshold(self):
        """Total cost for 10 kWh at step1_rate."""
        cost = calc_stepped_cost(BOOST_IMPORT_TARIFF, 10.0)
        assert cost == pytest.approx(10.0 * 21.67)

    def test_stepped_cost_above_threshold(self):
        """Total cost for 30 kWh crossing the threshold."""
        cost = calc_stepped_cost(BOOST_IMPORT_TARIFF, 30.0)
        expected = 25.0 * 21.67 + 5.0 * 25.30
        assert cost == pytest.approx(expected)

    def test_stepped_cost_at_threshold(self):
        """Total cost for exactly 25 kWh — all at step1_rate."""
        cost = calc_stepped_cost(BOOST_IMPORT_TARIFF, 25.0)
        assert cost == pytest.approx(25.0 * 21.67)

    def test_stepped_cost_zero(self):
        cost = calc_stepped_cost(BOOST_IMPORT_TARIFF, 0.0)
        assert cost == 0.0


# ---------------------------------------------------------------------------
# ZeroHero tracker tests
# ---------------------------------------------------------------------------

class TestZeroHeroTracker:
    def test_zerohero_earned(self):
        """Credit earned when import below threshold in window."""
        tracker = ZeroHeroTracker()  # Legacy 6-8pm, threshold 0.06
        # Small import during window: 0.01 kW for 2 hours = 0.02 kWh < 0.06
        for minute in range(0, 120, 1):
            now = _dt(18, 0) + timedelta(minutes=minute)
            tracker.update(0.01, 1 / 60, now)  # 0.01 kW, 1 minute
        # Close window
        tracker.update(0.0, 0.01, _dt(20, 1))
        assert tracker.status == "earned"
        assert tracker.daily_credit_aud() == 1.0

    def test_zerohero_lost(self):
        """Credit lost when import exceeds threshold."""
        tracker = ZeroHeroTracker()
        tracker.update(1.0, 1.0, _dt(18, 30))
        tracker.update(0.0, 0.01, _dt(20, 1))
        assert tracker.status == "lost"
        assert tracker.daily_credit_aud() == 0.0

    def test_zerohero_lost_during_window(self):
        """Status transitions to lost during window when threshold exceeded."""
        tracker = ZeroHeroTracker()
        tracker.update(1.0, 1.0, _dt(18, 30))
        assert tracker.status == "lost"

    def test_zerohero_pending_before_window(self):
        """Status is pending before the window closes."""
        tracker = ZeroHeroTracker()
        tracker.update(0.0, 0.01, _dt(17, 0))
        assert tracker.status == "pending"

    def test_zerohero_pending_during_window(self):
        """Status is pending during window when threshold not exceeded."""
        tracker = ZeroHeroTracker()
        tracker.update(0.001, 0.01, _dt(18, 30))
        assert tracker.status == "pending"

    def test_zerohero_threshold_exact(self):
        """Exactly at threshold should still earn credit (<=)."""
        tracker = ZeroHeroTracker()
        tracker.update(0.06, 1.0, _dt(18, 30))
        tracker.update(0.0, 0.01, _dt(20, 1))
        assert tracker.status == "earned"

    def test_zerohero_reset(self):
        tracker = ZeroHeroTracker()
        tracker.update(1.0, 1.0, _dt(18, 30))
        assert tracker.status == "lost"
        tracker.reset()
        assert tracker.status == "pending"
        assert tracker.window_import_kwh == 0.0

    def test_zerohero_custom_window_6_9pm(self):
        """Custom 6-9pm window: threshold scales to 0.09 kWh (3 hrs * 0.03)."""
        tracker = ZeroHeroTracker(window_start="18:00", window_end="21:00")
        # 0.08 kWh < 0.09 threshold — should earn
        tracker.update(0.08, 1.0, _dt(19, 0))
        tracker.update(0.0, 0.01, _dt(21, 1))
        assert tracker.status == "earned"

    def test_zerohero_custom_window_6_9pm_lost(self):
        """Custom 6-9pm window: 0.1 kWh > 0.09 threshold — lost."""
        tracker = ZeroHeroTracker(window_start="18:00", window_end="21:00")
        tracker.update(0.1, 1.0, _dt(19, 0))
        assert tracker.status == "lost"


# ---------------------------------------------------------------------------
# Super Export tracker tests
# ---------------------------------------------------------------------------

class TestSuperExportTracker:
    def test_super_export_replacement_legacy(self):
        """Legacy defaults: in window and under cap returns 15.0 c/kWh."""
        tracker = SuperExportTracker()
        rate = tracker.get_export_rate(_dt(18, 30))
        assert rate == 15.0

    def test_super_export_cap_legacy(self):
        """Legacy defaults: after 10 kWh exported, returns None."""
        tracker = SuperExportTracker()
        tracker.record_export(10.0, _dt(18, 30))
        rate = tracker.get_export_rate(_dt(18, 31))
        assert rate is None

    def test_super_export_outside_window(self):
        """Outside window returns None."""
        tracker = SuperExportTracker()
        assert tracker.get_export_rate(_dt(15, 0)) is None
        assert tracker.get_export_rate(_dt(20, 0)) is None

    def test_super_export_partial_cap(self):
        """Partially used cap still returns rate."""
        tracker = SuperExportTracker()
        tracker.record_export(5.0, _dt(18, 30))
        assert tracker.get_export_rate(_dt(18, 31)) == 15.0

    def test_super_export_cap_clamps_legacy(self):
        """Legacy defaults: export recording clamps at 10 kWh."""
        tracker = SuperExportTracker()
        tracker.record_export(15.0, _dt(18, 30))
        assert tracker.window_export_kwh == 10.0

    def test_super_export_outside_window_no_record(self):
        """Exports outside window are not recorded."""
        tracker = SuperExportTracker()
        tracker.record_export(5.0, _dt(15, 0))
        assert tracker.window_export_kwh == 0.0

    def test_super_export_custom_cap_15kwh(self):
        """Custom 15 kWh cap: allows up to 15 kWh."""
        tracker = SuperExportTracker(cap_kwh=15.0)
        tracker.record_export(12.0, _dt(18, 30))
        assert tracker.get_export_rate(_dt(18, 31)) == 15.0
        tracker.record_export(5.0, _dt(18, 32))
        assert tracker.window_export_kwh == 15.0
        assert tracker.get_export_rate(_dt(18, 33)) is None

    def test_super_export_custom_window_6_9pm(self):
        """Custom window 6-9pm: rate available at 8:30pm (outside legacy 6-8pm)."""
        tracker = SuperExportTracker(window_start="18:00", window_end="21:00")
        assert tracker.get_export_rate(_dt(20, 30)) == 15.0
        assert tracker.get_export_rate(_dt(21, 0)) is None

    def test_super_export_custom_rate(self):
        """Custom rate of 20 c/kWh."""
        tracker = SuperExportTracker(rate_c=20.0)
        assert tracker.get_export_rate(_dt(18, 30)) == 20.0


# ---------------------------------------------------------------------------
# DemandTracker tests
# ---------------------------------------------------------------------------

class TestDemandTracker:
    def test_demand_peak_tracking(self):
        tracker = DemandTracker()
        tracker.update(3.0)
        tracker.update(5.0)
        tracker.update(2.0)
        assert tracker.peak_kw_billing == 5.0

    def test_demand_charge_calculation(self):
        tracker = DemandTracker()
        tracker.update(5.0)
        charge = tracker.daily_demand_charge_cents(2.0)
        assert charge == pytest.approx(10.0)

    def test_demand_not_reset_by_lower(self):
        tracker = DemandTracker()
        tracker.update(5.0)
        tracker.update(1.0)
        assert tracker.peak_kw_billing == 5.0

    def test_demand_billing_reset(self):
        tracker = DemandTracker()
        tracker.update(5.0)
        tracker.reset_billing()
        assert tracker.peak_kw_billing == 0.0


# ---------------------------------------------------------------------------
# TariffEngine integration tests
# ---------------------------------------------------------------------------

class TestTariffEngine:
    def test_midnight_reset(self):
        """Midnight crossing resets daily accumulators."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        # Day 1 at 23:59
        engine.update(1000.0, _dt(23, 59, day=28))
        engine.update(1000.0, _dt(23, 59, day=28) + timedelta(seconds=30))
        assert engine.import_kwh_today > 0

        # Day 2 — should reset
        engine.update(0.0, _dt(0, 1, day=29))
        assert engine.import_kwh_today == 0.0

    def test_gap_protection_clamps(self):
        """Large time gap is clamped to 0.1h — accumulates some energy."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        engine.update(5000.0, _dt(10, 0))
        engine.update(5000.0, _dt(10, 30))  # 30 min gap, clamped to 6 min
        # 5kW * 0.1h = 0.5 kWh (clamped, not full 30 min)
        assert engine.import_kwh_today == pytest.approx(0.5)

    def test_import_accumulation(self):
        """Import power accumulates cost correctly."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        # 1000W import at 17:00 (peak, 38.50 c/kWh)
        engine.update(1000.0, _dt(17, 0))
        engine.update(1000.0, _dt(17, 0) + timedelta(seconds=60))
        # 1 kW * (60s/3600) = 1/60 kWh
        expected_kwh = 1.0 / 60.0
        assert engine.import_kwh_today == pytest.approx(expected_kwh, rel=1e-4)
        expected_cost_c = expected_kwh * 38.50
        assert engine._import_cost_today_c == pytest.approx(expected_cost_c, rel=1e-4)

    def test_export_accumulation(self):
        """Export power accumulates earnings correctly."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        # -2000W (export) at 17:00 (peak export rate 3.00, but super export = 15.0)
        engine.update(-2000.0, _dt(18, 0))
        engine.update(-2000.0, _dt(18, 0) + timedelta(seconds=60))
        expected_kwh = 2.0 / 60.0
        assert engine.export_kwh_today == pytest.approx(expected_kwh, rel=1e-4)
        # Super export active: 15.0 c/kWh
        expected_earnings_c = expected_kwh * 15.0
        assert engine._export_earnings_today_c == pytest.approx(expected_earnings_c, rel=1e-4)

    def test_export_outside_super_window(self):
        """Export outside super export window uses TOU rate."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        # -1000W at 15:00 (shoulder export, 0.30 c/kWh, shoulder window 14:00-16:00)
        engine.update(-1000.0, _dt(15, 0))
        engine.update(-1000.0, _dt(15, 0) + timedelta(seconds=60))
        expected_kwh = 1.0 / 60.0
        expected_earnings_c = expected_kwh * 0.30
        assert engine._export_earnings_today_c == pytest.approx(expected_earnings_c, rel=1e-4)

    def test_supply_charge_in_net_cost(self):
        """Net daily cost includes full supply charge."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        engine.update(0.0, _dt(12, 0))
        engine.update(0.0, _dt(12, 0) + timedelta(seconds=60))
        # Supply charge is full-day (not prorated) in net_daily_cost_aud
        assert engine.net_daily_cost_aud > 0

    def test_net_daily_cost_includes_supply(self):
        """Net daily cost includes supply charge even with zero power."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        engine.update(0.0, _dt(12, 0))
        engine.update(0.0, _dt(12, 0) + timedelta(seconds=60))
        assert engine.net_daily_cost_aud > 0

    def test_first_update_sets_baseline(self):
        """First update only sets the timestamp, no accumulation."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        engine.update(5000.0, _dt(12, 0))
        assert engine.import_kwh_today == 0.0

    def test_negative_delta_skipped(self):
        """Out-of-order timestamps produce no accumulation."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        engine.update(1000.0, _dt(12, 0))
        engine.update(1000.0, _dt(11, 59))  # Earlier timestamp
        assert engine.import_kwh_today == 0.0

    def test_boost_stepped_import(self):
        """BOOST plan uses stepped import pricing."""
        engine = TariffEngine(BOOST_OPTIONS)
        # First update sets baseline
        engine.update(1000.0, _dt(12, 0))
        # Second update: 1kW for 60s = 1/60 kWh, below threshold -> step1_rate
        engine.update(1000.0, _dt(12, 0) + timedelta(seconds=60))
        expected_kwh = 1.0 / 60.0
        expected_cost_c = expected_kwh * 21.67
        assert engine._import_cost_today_c == pytest.approx(expected_cost_c, rel=1e-4)

    def test_current_rates(self):
        """Current rate properties reflect the right time."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        engine.update(0.0, _dt(17, 0))
        assert engine.current_import_rate_c_kwh == 38.50

    def test_current_export_rate_super(self):
        """Super export rate overrides during window."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        engine.update(0.0, _dt(18, 30))
        assert engine.current_export_rate_c_kwh == 15.0

    def test_current_export_rate_normal(self):
        """Normal TOU export rate outside super window."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        engine.update(0.0, _dt(15, 0))
        assert engine.current_export_rate_c_kwh == 0.30

    def test_demand_not_reset_at_midnight(self):
        """Demand tracker persists across midnight reset."""
        engine = TariffEngine({**ZEROHERO_OPTIONS, "demand_charge": 1.0})
        engine.update(5000.0, _dt(23, 59, day=28))
        engine.update(5000.0, _dt(23, 59, day=28) + timedelta(seconds=30))
        assert engine._demand.peak_kw_billing == 5.0
        # Midnight reset
        engine.update(0.0, _dt(0, 1, day=29))
        assert engine._demand.peak_kw_billing == 5.0


# ---------------------------------------------------------------------------
# Serialization round-trip tests
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_to_dict_from_dict_round_trip(self):
        """to_dict/from_dict preserves all state when same day."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        engine.update(1000.0, datetime(2026, 3, 29, 17, 0))
        engine.update(1000.0, datetime(2026, 3, 29, 17, 0, 30))

        snapshot = engine.to_dict()
        restored = TariffEngine.from_dict(ZEROHERO_OPTIONS, snapshot, today=date(2026, 3, 29))

        assert restored.import_kwh_today == pytest.approx(engine.import_kwh_today)
        assert restored._import_cost_today_c == pytest.approx(engine._import_cost_today_c)
        assert restored._export_earnings_today_c == pytest.approx(engine._export_earnings_today_c)

    def test_from_dict_stale_date_resets_daily(self):
        """from_dict with a different day does not restore daily accumulators."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        engine.update(1000.0, datetime(2026, 3, 28, 17, 0))
        engine.update(1000.0, datetime(2026, 3, 28, 17, 0, 30))

        snapshot = engine.to_dict()
        # On a different day, daily values should not be restored
        restored = TariffEngine.from_dict(ZEROHERO_OPTIONS, snapshot, today=date(2026, 3, 29))
        assert restored.import_kwh_today == 0.0
        assert restored._import_cost_today_c == 0.0

    def test_from_dict_preserves_demand(self):
        """Demand tracker is always restored regardless of date."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        engine.update(5000.0, datetime(2026, 3, 28, 17, 0))
        engine.update(5000.0, datetime(2026, 3, 28, 17, 0, 30))

        snapshot = engine.to_dict()
        restored = TariffEngine.from_dict(ZEROHERO_OPTIONS, snapshot, today=date(2026, 3, 29))
        assert restored._demand.peak_kw_billing == engine._demand.peak_kw_billing

    def test_zerohero_tracker_serialization(self):
        """ZeroHero tracker round-trips through dict."""
        tracker = ZeroHeroTracker()
        tracker.update(1.0, 1.0, _dt(18, 30))
        data = tracker.to_dict()
        restored = ZeroHeroTracker()
        restored.from_dict(data)
        assert restored.window_import_kwh == tracker.window_import_kwh
        assert restored._threshold_exceeded == tracker._threshold_exceeded

    def test_super_export_tracker_serialization(self):
        """SuperExport tracker round-trips through dict."""
        tracker = SuperExportTracker()
        tracker.record_export(5.0, _dt(18, 30))
        data = tracker.to_dict()
        restored = SuperExportTracker()
        restored.from_dict(data)
        assert restored.window_export_kwh == 5.0

    def test_demand_tracker_serialization(self):
        """Demand tracker round-trips through dict."""
        tracker = DemandTracker()
        tracker.update(7.5)
        data = tracker.to_dict()
        restored = DemandTracker()
        restored.from_dict(data)
        assert restored.peak_kw_billing == 7.5


# ---------------------------------------------------------------------------
# Edge case tests (AEGIS audit DA-006)
# ---------------------------------------------------------------------------

class TestTOUEdgeCases:
    def test_empty_windows_returns_unknown(self):
        """Empty windows list should return 'unknown' period."""
        periods = {"peak": {"rate": 10.0, "windows": []}}
        name, rate = get_current_tou_period(periods, _dt(12, 0))
        assert name == "unknown"
        assert rate == 0.0

    def test_no_periods_returns_unknown(self):
        """Empty periods dict should return 'unknown'."""
        name, rate = get_current_tou_period({}, _dt(12, 0))
        assert name == "unknown"
        assert rate == 0.0

    def test_midnight_crossing_window(self):
        """Window 23:00-01:00 should match at 23:30."""
        periods = {
            "night": {"rate": 5.0, "windows": [["23:00", "01:00"]]},
            "day": {"rate": 20.0, "windows": [["01:00", "23:00"]]},
        }
        name, rate = get_current_tou_period(periods, _dt(23, 30))
        assert name == "night"
        assert rate == 5.0

    def test_midnight_crossing_at_0001(self):
        """Window 23:00-01:00 should match at 00:01."""
        periods = {
            "night": {"rate": 5.0, "windows": [["23:00", "01:00"]]},
            "day": {"rate": 20.0, "windows": [["01:00", "23:00"]]},
        }
        name, rate = get_current_tou_period(periods, _dt(0, 1))
        assert name == "night"
        assert rate == 5.0

    def test_2359_boundary(self):
        """23:59 in a 16:00-00:00 window should match."""
        periods = {
            "peak": {"rate": 38.5, "windows": [["16:00", "00:00"]]},
        }
        name, rate = get_current_tou_period(periods, _dt(23, 59))
        assert name == "peak"
        assert rate == 38.5


class TestDemandEdgeCases:
    def test_zero_demand_charge_rate(self):
        """Zero demand rate produces zero charge."""
        tracker = DemandTracker()
        tracker.update(10.0)
        assert tracker.daily_demand_charge_cents(0.0) == 0.0

    def test_zero_power_no_peak_update(self):
        """Zero grid power doesn't set a new peak."""
        tracker = DemandTracker()
        tracker.update(0.0)
        assert tracker.peak_kw_billing == 0.0


class TestSteppedEdgeCases:
    def test_zero_threshold(self):
        """Zero threshold means all energy at step2."""
        tariff = {"step1_threshold_kwh": 0.0, "step1_rate": 10.0, "step2_rate": 20.0}
        assert get_stepped_import_rate(tariff, 5.0) == 20.0
        assert calc_stepped_cost(tariff, 5.0) == pytest.approx(100.0)

    def test_very_large_consumption(self):
        """100 kWh with 25 kWh threshold."""
        tariff = {"step1_threshold_kwh": 25.0, "step1_rate": 20.0, "step2_rate": 30.0}
        cost = calc_stepped_cost(tariff, 100.0)
        expected = 25.0 * 20.0 + 75.0 * 30.0
        assert cost == pytest.approx(expected)
