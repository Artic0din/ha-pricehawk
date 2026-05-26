"""Tests for the LocalVolts provider — wholesale + buy/sell ceilings."""

import logging
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.pricehawk.localvolts_api import aggregate_to_half_hour
from custom_components.pricehawk.providers.localvolts import LocalVoltsProvider


class TestImportAccumulation:
    def test_import_at_set_rate(self):
        provider = LocalVoltsProvider({})
        provider.set_current_rates(import_c_kwh=22.0, export_c_kwh=5.0)

        t0 = datetime(2026, 5, 1, 12, 0, 0)
        t1 = t0 + timedelta(hours=0.01)
        provider.update(0, t0)
        provider.update(5000, t1)

        assert provider.import_kwh_today == pytest.approx(0.05, abs=1e-6)
        # 0.05 kWh × 22c = 1.1c
        assert provider.import_cost_today_c == pytest.approx(0.05 * 22.0, abs=0.01)

    def test_buy_ceiling_caps_rate(self):
        provider = LocalVoltsProvider({"localvolts_buy_ceiling": 20.0})
        provider.set_current_rates(import_c_kwh=35.0, export_c_kwh=5.0)

        # current_import_rate should be capped at the ceiling
        assert provider.current_import_rate_c_kwh == 20.0


class TestExportAccumulation:
    def test_positive_export_earnings(self):
        provider = LocalVoltsProvider({})
        provider.set_current_rates(import_c_kwh=22.0, export_c_kwh=8.0)

        t0 = datetime(2026, 5, 1, 12, 0, 0)
        t1 = t0 + timedelta(hours=0.01)
        provider.update(0, t0)
        provider.update(-3000, t1)

        assert provider.export_kwh_today == pytest.approx(0.03, abs=1e-6)
        assert provider.export_earnings_today_c == pytest.approx(0.03 * 8.0, abs=0.01)
        assert provider.extras["negative_export_kwh"] == 0.0

    def test_negative_export_tracked_separately(self):
        # Midday solar peak: spot price negative — customer pays to export
        provider = LocalVoltsProvider({})
        provider.set_current_rates(import_c_kwh=22.0, export_c_kwh=-3.0)

        t0 = datetime(2026, 5, 1, 12, 0, 0)
        t1 = t0 + timedelta(hours=0.01)
        provider.update(0, t0)
        provider.update(-3000, t1)

        assert provider.export_earnings_today_c == pytest.approx(
            0.03 * -3.0, abs=0.01
        )
        # Negative-export kWh tracked for diagnostics
        assert provider.extras["negative_export_kwh"] == pytest.approx(
            0.03, abs=1e-6
        )
        assert provider.extras["negative_export_cost_aud"] == pytest.approx(
            (0.03 * 3.0) / 100.0, abs=1e-4
        )

    def test_sell_floor_filters_negative_export(self):
        # Sell floor 5c: when spot is below floor, customer "doesn't sell"
        # → effective export rate clamped to 0 (no earning, no penalty)
        provider = LocalVoltsProvider({"localvolts_sell_floor": 5.0})
        provider.set_current_rates(import_c_kwh=22.0, export_c_kwh=-3.0)

        assert provider.current_export_rate_c_kwh == 0.0


class TestNetDailyCost:
    def test_net_cost_includes_supply_and_export(self):
        provider = LocalVoltsProvider({"localvolts_daily_supply": 110.0})
        provider.set_current_rates(import_c_kwh=22.0, export_c_kwh=8.0)

        t0 = datetime(2026, 5, 1, 12, 0, 0)
        t1 = t0 + timedelta(hours=0.01)
        provider.update(0, t0)
        provider.update(5000, t1)  # import 0.05 kWh

        t2 = t1 + timedelta(hours=0.01)
        provider.update(-3000, t2)  # export 0.03 kWh

        # supply $1.10 + import 1.1c - export 0.24c = $1.10 + 0.0086 = $1.1086
        expected_aud = (110.0 + 0.05 * 22.0 - 0.03 * 8.0) / 100.0
        assert provider.net_daily_cost_aud == pytest.approx(expected_aud, abs=1e-4)


class TestAggregator:
    def _iv(self, end_minutes_ago, kwh, imp, exp):
        end = datetime.now(timezone.utc) - timedelta(minutes=end_minutes_ago)
        return {
            "intervalEnd": end.isoformat().replace("+00:00", "Z"),
            "loadKwh": kwh,
            "costsAllVarRate": imp,
            "earningsAllVarRate": exp,
            "quality": "exp",
        }

    def test_empty_input(self):
        assert aggregate_to_half_hour([]) == (None, None)

    def test_single_recent_interval(self):
        intervals = [self._iv(2, 1.0, 25.0, 5.0)]
        imp, exp = aggregate_to_half_hour(intervals)
        assert imp == pytest.approx(25.0)
        assert exp == pytest.approx(5.0)

    def test_volume_weighted_aggregation(self):
        intervals = [
            self._iv(2, 1.0, 30.0, 0.0),
            self._iv(7, 3.0, 20.0, 0.0),
        ]
        # weighted: (1*30 + 3*20) / 4 = 90/4 = 22.5
        imp, _ = aggregate_to_half_hour(intervals)
        assert imp == pytest.approx(22.5)

    def test_intervals_older_than_30min_excluded(self):
        intervals = [
            self._iv(2, 1.0, 30.0, 5.0),
            self._iv(45, 1.0, 99.0, 99.0),  # outside window — ignored
        ]
        imp, exp = aggregate_to_half_hour(intervals)
        assert imp == pytest.approx(30.0)
        assert exp == pytest.approx(5.0)

    def test_zero_load_falls_back_to_arithmetic_mean(self):
        intervals = [
            self._iv(2, 0.0, 30.0, 5.0),
            self._iv(7, 0.0, 10.0, 1.0),
        ]
        imp, exp = aggregate_to_half_hour(intervals)
        assert imp == pytest.approx(20.0)
        assert exp == pytest.approx(3.0)


class TestAggregatorSkipObservability:
    """Bad intervals must be counted + logged, not silently dropped."""

    _LOGGER_NAME = "custom_components.pricehawk.localvolts_api"

    def _good_iv(self, end_minutes_ago: float, kwh: float, imp: float, exp: float):
        end = datetime.now(timezone.utc) - timedelta(minutes=end_minutes_ago)
        return {
            "intervalEnd": end.isoformat().replace("+00:00", "Z"),
            "loadKwh": kwh,
            "costsAllVarRate": imp,
            "earningsAllVarRate": exp,
            "quality": "exp",
        }

    def test_no_log_when_all_intervals_clean(self, caplog):
        caplog.set_level(logging.DEBUG, logger=self._LOGGER_NAME)
        intervals = [self._good_iv(2, 1.0, 25.0, 5.0)]

        aggregate_to_half_hour(intervals)

        assert not any(
            "skipped" in rec.message
            for rec in caplog.records
            if rec.name == self._LOGGER_NAME
        )

    def test_missing_end_counted_and_debug_logged_below_threshold(self, caplog):
        # 1 bad in 20 = 5% drop → debug, not warning.
        caplog.set_level(logging.DEBUG, logger=self._LOGGER_NAME)
        intervals = [self._good_iv(2, 1.0, 25.0, 5.0) for _ in range(19)]
        intervals.append({"loadKwh": 1.0, "costsAllVarRate": 5.0})  # no end-time

        imp, exp = aggregate_to_half_hour(intervals)

        # Aggregation still succeeds on the 19 good rows.
        assert imp == pytest.approx(25.0)
        assert exp == pytest.approx(5.0)

        skip_records = [
            rec for rec in caplog.records
            if rec.name == self._LOGGER_NAME and "skipped" in rec.message
        ]
        assert len(skip_records) == 1, "expected exactly one summary log line"
        rec = skip_records[0]
        assert rec.levelno == logging.DEBUG
        assert "skipped 1/20" in rec.message
        assert "missing_end=1" in rec.message
        assert "unparseable=0" in rec.message

    def test_unparseable_iso_counted_and_warning_above_threshold(self, caplog):
        # 3 bad in 10 = 30% drop → warning.
        caplog.set_level(logging.DEBUG, logger=self._LOGGER_NAME)
        intervals = [self._good_iv(2, 1.0, 25.0, 5.0) for _ in range(7)]
        intervals.extend([
            {"intervalEnd": "not-an-iso-timestamp", "loadKwh": 1.0},
            {"intervalEnd": 12345, "loadKwh": 1.0},  # int → AttributeError on .replace
            {"intervalEnd": "2026-13-99T99:99:99Z", "loadKwh": 1.0},  # bad month
        ])

        aggregate_to_half_hour(intervals)

        skip_records = [
            rec for rec in caplog.records
            if rec.name == self._LOGGER_NAME and "skipped" in rec.message
        ]
        assert len(skip_records) == 1
        rec = skip_records[0]
        assert rec.levelno == logging.WARNING
        assert "skipped 3/10" in rec.message
        assert "unparseable=3" in rec.message
        assert "drop_rate=30.0%" in rec.message

    def test_exactly_ten_percent_escalates_to_warning(self, caplog):
        # Threshold is >=10%, so 1 in 10 should escalate.
        caplog.set_level(logging.DEBUG, logger=self._LOGGER_NAME)
        intervals = [self._good_iv(2, 1.0, 25.0, 5.0) for _ in range(9)]
        intervals.append({})  # missing end_str

        aggregate_to_half_hour(intervals)

        skip_records = [
            rec for rec in caplog.records
            if rec.name == self._LOGGER_NAME and "skipped" in rec.message
        ]
        assert len(skip_records) == 1
        assert skip_records[0].levelno == logging.WARNING


class TestPersistence:
    def test_roundtrip(self):
        provider = LocalVoltsProvider({"localvolts_daily_supply": 110.0})
        provider.set_current_rates(import_c_kwh=22.0, export_c_kwh=8.0)
        t0 = datetime(2026, 5, 1, 12)
        t1 = t0 + timedelta(hours=0.01)
        provider.update(0, t0)
        provider.update(5000, t1)

        snapshot = provider.to_dict()

        restored = LocalVoltsProvider({"localvolts_daily_supply": 110.0})
        restored.from_dict(snapshot, today=t0.date())

        assert restored.import_kwh_today == pytest.approx(provider.import_kwh_today)
        assert restored.import_cost_today_c == pytest.approx(
            provider.import_cost_today_c
        )
