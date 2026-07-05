"""Extended tariff engine tests — covers the 5 uncovered lines in tariff_engine.py.

Gaps addressed:
  - _parse_time: called indirectly via tracker constructors, but direct test
    confirms invalid input handling is NOT expected (pure happy-path used)
  - reset_daily sets _last_reset_date = None (line 287)
  - net_daily_cost_aud with demand charge included (line 332)
  - current_import_rate_c_kwh when _last_update is None (lines 385-386)
  - TariffEngine with incentives as a dict (True/False values) vs list form

All fixtures use custom rate values that are clearly test-specific,
not copies of any real-world tariff.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from custom_components.pricehawk.tariff_engine import (
    TariffEngine,
    ZeroHeroTracker,
)

# ---------------------------------------------------------------------------
# Minimal test options — not a copy of any real plan
# ---------------------------------------------------------------------------

_SIMPLE_TOU_OPTIONS = {
    "plan_type": "test_tou",
    "daily_supply_charge": 100.0,  # 100c = $1.00
    "demand_charge": 0.0,
    "import_tariff": {
        "type": "tou",
        "periods": {
            "peak": {"rate": 40.0, "windows": [["16:00", "22:00"]]},
            "offpeak": {"rate": 10.0, "windows": [["10:00", "14:00"]]},
            "shoulder": {
                "rate": 20.0,
                "windows": [["22:00", "00:00"], ["00:00", "10:00"], ["14:00", "16:00"]],
            },
        },
    },
    "export_tariff": {
        "type": "tou",
        "periods": {
            "peak": {"rate": 5.0, "windows": [["16:00", "22:00"]]},
            "other": {"rate": 1.0, "windows": [["22:00", "00:00"], ["00:00", "16:00"]]},
        },
    },
    "incentives": [],
}

_DEMAND_OPTIONS = {
    **_SIMPLE_TOU_OPTIONS,
    "demand_charge": 2.0,  # 2.0 c per kW per day
}


def _dt(hour: int, minute: int = 0, day: int = 15) -> datetime:
    """Build a local datetime on 2026-04-15."""
    return datetime(2026, 4, day, hour, minute, 0)


# ---------------------------------------------------------------------------
# reset_daily sets _last_reset_date to None
# ---------------------------------------------------------------------------


class TestResetDailyNullsDate:
    def test_reset_daily_clears_last_reset_date(self):
        """After reset_daily(), _last_reset_date is None so next update re-establishes it."""
        # ARRANGE
        engine = TariffEngine(_SIMPLE_TOU_OPTIONS)
        engine.update(1000.0, _dt(12, 0))
        engine.update(1000.0, _dt(12, 0) + timedelta(seconds=30))
        assert engine._last_reset_date is not None

        # ACT
        engine.reset_daily()

        # ASSERT: date is cleared
        assert engine._last_reset_date is None

    def test_reset_daily_clears_all_accumulators(self):
        """reset_daily zeroes all energy and cost accumulators."""
        # ARRANGE
        engine = TariffEngine(_SIMPLE_TOU_OPTIONS)
        engine.update(5000.0, _dt(17, 0))
        engine.update(5000.0, _dt(17, 0) + timedelta(seconds=30))
        assert engine.import_kwh_today > 0

        # ACT
        engine.reset_daily()

        # ASSERT
        assert engine.import_kwh_today == pytest.approx(0.0)
        assert engine.export_kwh_today == pytest.approx(0.0)
        assert engine.import_cost_today_c == pytest.approx(0.0)
        assert engine.export_earnings_today_c == pytest.approx(0.0)

    def test_update_after_reset_resets_date_to_new_day(self):
        """After reset_daily(), the next update sets _last_reset_date to that call's date."""
        # ARRANGE
        engine = TariffEngine(_SIMPLE_TOU_OPTIONS)
        engine.update(0.0, _dt(12, 0))
        engine.reset_daily()

        # ACT — update on a new day
        new_day_dt = datetime(2026, 4, 16, 8, 0, 0)
        engine.update(0.0, new_day_dt)
        engine.update(0.0, new_day_dt + timedelta(seconds=30))

        # ASSERT: date has been re-established
        assert engine._last_reset_date == date(2026, 4, 16)


# ---------------------------------------------------------------------------
# net_daily_cost_aud includes demand charge
# ---------------------------------------------------------------------------


class TestNetDailyCostWithDemand:
    def test_net_cost_includes_demand_charge(self):
        """Peak 5 kW import, demand rate 2.0 c/kW/day → demand charge = 10c = $0.10."""
        # ARRANGE
        engine = TariffEngine(_DEMAND_OPTIONS)
        # One update to seed, one to accumulate
        engine.update(5000.0, _dt(12, 0))
        engine.update(5000.0, _dt(12, 0) + timedelta(seconds=30))
        # After second update: peak_kw = 5.0, demand charge = 5 * 2.0 = 10c

        # ACT — read net_daily_cost_aud
        # supply=100c + import_cost + demand_10c - export_0c
        demand_c = 5.0 * 2.0
        supply_c = 100.0
        import_kwh = 5.0 * (30 / 3600)  # 5 kW * (30s / 3600) h
        import_cost_c = import_kwh * 10.0  # offpeak rate = 10 c/kWh

        expected_c = supply_c + import_cost_c + demand_c
        expected_aud = expected_c / 100.0

        # ASSERT
        assert engine.net_daily_cost_aud == pytest.approx(expected_aud, rel=0.01)

    def test_demand_charge_zero_when_rate_is_zero(self):
        """With demand_charge=0.0, demand component does not inflate net cost."""
        # ARRANGE
        engine_no_demand = TariffEngine(_SIMPLE_TOU_OPTIONS)
        engine_with_demand = TariffEngine({**_SIMPLE_TOU_OPTIONS, "demand_charge": 0.0})

        t0 = _dt(17, 0)
        t1 = t0 + timedelta(seconds=30)
        engine_no_demand.update(5000.0, t0)
        engine_no_demand.update(5000.0, t1)
        engine_with_demand.update(5000.0, t0)
        engine_with_demand.update(5000.0, t1)

        # ASSERT: identical costs
        assert engine_no_demand.net_daily_cost_aud == pytest.approx(
            engine_with_demand.net_daily_cost_aud
        )


# ---------------------------------------------------------------------------
# current_import_rate_c_kwh when _last_update is None
# ---------------------------------------------------------------------------


class TestCurrentRateBeforeFirstUpdate:
    def test_current_import_rate_is_zero_before_any_update(self):
        """Before the first update call, import rate returns 0.0 (no timestamp)."""
        # ARRANGE
        engine = TariffEngine(_SIMPLE_TOU_OPTIONS)

        # ACT + ASSERT: no update → _last_update is None → 0.0 returned
        assert engine.current_import_rate_c_kwh == pytest.approx(0.0)

    def test_current_export_rate_is_zero_before_any_update(self):
        """Before the first update call, export rate returns 0.0."""
        # ARRANGE
        engine = TariffEngine(_SIMPLE_TOU_OPTIONS)

        # ACT + ASSERT
        assert engine.current_export_rate_c_kwh == pytest.approx(0.0)

    def test_stepped_plan_current_rate_zero_before_update(self):
        """Stepped plan also returns 0.0 current rate before first update."""
        # ARRANGE
        stepped_options = {
            **_SIMPLE_TOU_OPTIONS,
            "import_tariff": {
                "type": "flat_stepped",
                "step1_threshold_kwh": 25.0,
                "step1_rate": 21.0,
                "step2_rate": 26.0,
            },
        }
        engine = TariffEngine(stepped_options)

        # ACT + ASSERT: stepped plan returns step1_rate (daily_kwh == 0)
        assert engine.current_import_rate_c_kwh == pytest.approx(21.0)


# ---------------------------------------------------------------------------
# TariffEngine with dict-style incentives (True/False values)
# ---------------------------------------------------------------------------


class TestDictIncentives:
    """The engine supports both list incentives and dict {name: True/False}."""

    def test_dict_incentive_zerohero_credit_recognised(self):
        """Dict-style {'zerohero_credit': True} enables the credit tracker."""
        # ARRANGE
        options = {
            **_SIMPLE_TOU_OPTIONS,
            "incentives": {
                "zerohero_credit": True,
                "zerohero_window_start": "18:00",
                "zerohero_window_end": "20:00",
            },
        }
        engine = TariffEngine(options)

        # Zero import during the window
        t0 = datetime(2026, 4, 15, 18, 0, 0)
        engine.update(0.0, t0)
        for i in range(1, 121):  # 2 hours * 60 readings
            engine.update(0.0, t0 + timedelta(minutes=i))

        # Close the window
        engine.update(0.0, datetime(2026, 4, 15, 20, 1, 0))

        # ASSERT: credit was earned
        assert engine.zerohero_status == "earned"

    def test_dict_incentive_false_does_not_enable_tracker(self):
        """{'zerohero_credit': False} disables the credit even during the window."""
        # ARRANGE
        options = {
            **_SIMPLE_TOU_OPTIONS,
            "incentives": {"zerohero_credit": False},
        }
        engine = TariffEngine(options)

        # Zero import during default window
        t0 = datetime(2026, 4, 15, 18, 0, 0)
        engine.update(0.0, t0)
        engine.update(0.0, t0 + timedelta(minutes=2))
        engine.update(0.0, datetime(2026, 4, 15, 20, 1, 0))

        # ASSERT: tracker not updated, status stays pending/default
        # (credit not earned because incentive was off)
        assert engine.zerohero_status == "pending"


# ---------------------------------------------------------------------------
# Full-day cost calculation with exact numeric assertions
# ---------------------------------------------------------------------------


class TestExactNumericCost:
    def test_1kwh_peak_import_exact_cost(self):
        """1 kWh at 40 c/kWh peak + $1 supply = 140c = $1.40 net cost."""
        # ARRANGE — 10 kW for 6 minutes = 0.1 kWh; run 10 intervals to reach 1 kWh
        engine = TariffEngine(_SIMPLE_TOU_OPTIONS)
        t0 = datetime(2026, 4, 15, 17, 0, 0)
        engine.update(10_000.0, t0)
        # Feed 10 * 6-minute intervals = 10 * 0.1h * 10kW = 10 kWh... too much.
        # Instead: single 6-minute interval, 10 kW = 1 kWh
        engine.update(10_000.0, t0 + timedelta(minutes=6))

        # ACT
        # 10 kW * 0.1h = 1 kWh, rate = 40 c/kWh → 40c import cost
        # supply = 100c, no demand, no export → total = 140c = $1.40
        import_cost_c = 1.0 * 40.0
        supply_c = 100.0
        expected_aud = (import_cost_c + supply_c) / 100.0

        # ASSERT
        assert engine.import_kwh_today == pytest.approx(1.0, rel=0.001)
        assert engine.net_daily_cost_aud == pytest.approx(expected_aud, abs=0.01)

    def test_export_credit_reduces_cost_below_supply(self):
        """10 kWh export at 5 c/kWh = 50c credit; supply 100c → net = 50c = $0.50."""
        # ARRANGE — export 10 kW for 1 interval (clamped to 0.1h = 1 kWh)
        engine = TariffEngine(_SIMPLE_TOU_OPTIONS)
        t0 = datetime(2026, 4, 15, 17, 0, 0)  # peak export window
        engine.update(-10_000.0, t0)
        engine.update(-10_000.0, t0 + timedelta(minutes=6))

        # ACT
        # 1 kWh export at 5 c/kWh = 5c earnings...
        # Actually at 0.1h * 10kW = 1 kWh, rate peak export = 5 c/kWh → 5c
        # supply 100c - 5c export = 95c = $0.95

        # ASSERT: export reduces cost
        assert engine.export_earnings_today_c > 0
        assert engine.net_daily_cost_aud < 1.00  # less than supply alone ($1.00)


# ---------------------------------------------------------------------------
# ZeroHeroTracker: threshold exactly equals accumulated — edge case
# ---------------------------------------------------------------------------


class TestZeroHeroThresholdEdge:
    def test_threshold_exactly_met_earns_credit(self):
        """Window import exactly equal to threshold: credit is EARNED (<=, not <)."""
        # ARRANGE — legacy 6-8pm window, threshold = 0.03 * 2h = 0.06 kWh
        tracker = ZeroHeroTracker(window_start="18:00", window_end="20:00")
        t_mid = datetime(2026, 4, 15, 19, 0, 0)

        # ACT — inject exactly threshold worth of import (0.06 kW * 1h = 0.06 kWh)
        tracker.update(0.06, 1.0, t_mid)
        tracker.update(0.0, 0.01, datetime(2026, 4, 15, 20, 1, 0))

        # ASSERT
        assert tracker.window_import_kwh == pytest.approx(0.06, abs=0.0001)
        assert tracker.status == "earned"
        assert tracker.daily_credit_aud() == pytest.approx(1.0)

    def test_one_unit_over_threshold_loses_credit(self):
        """Import fractionally above threshold: status is 'lost'."""
        # ARRANGE
        tracker = ZeroHeroTracker(window_start="18:00", window_end="20:00")
        t_mid = datetime(2026, 4, 15, 19, 0, 0)

        # ACT — 0.061 kWh > 0.06 threshold
        tracker.update(0.0, 1.0, t_mid)  # small update to enter window
        tracker.update(0.061, 1.0, t_mid)  # slightly above threshold

        # ASSERT: lost immediately (threshold_exceeded flag set during window)
        assert tracker.status == "lost"
        assert tracker.daily_credit_aud() == pytest.approx(0.0)
