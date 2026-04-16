"""Tests for shared helper functions."""

from datetime import date, datetime, timedelta

import pytest

from custom_components.pricehawk.helpers import (
    compute_delta_h,
    should_reset_daily,
    split_grid_power,
)


class TestComputeDeltaH:
    def test_none_last_update(self):
        now = datetime(2026, 3, 29, 12, 0, 0)
        assert compute_delta_h(now, None) is None

    def test_normal_30s_interval(self):
        last = datetime(2026, 3, 29, 12, 0, 0)
        now = last + timedelta(seconds=30)
        result = compute_delta_h(now, last)
        assert result == pytest.approx(30 / 3600)

    def test_zero_delta(self):
        now = datetime(2026, 3, 29, 12, 0, 0)
        assert compute_delta_h(now, now) is None

    def test_negative_delta(self):
        now = datetime(2026, 3, 29, 12, 0, 0)
        future = now + timedelta(seconds=10)
        assert compute_delta_h(now, future) is None

    def test_gap_too_large_clamped(self):
        """Large gaps are clamped to 0.1 hours instead of discarded."""
        last = datetime(2026, 3, 29, 12, 0, 0)
        now = last + timedelta(minutes=10)  # 10 min > 6 min threshold
        result = compute_delta_h(now, last)
        assert result == pytest.approx(0.1)

    def test_exactly_at_threshold(self):
        last = datetime(2026, 3, 29, 12, 0, 0)
        # 0.1 hours = 360 seconds; delta_h > 0.1 is False when exactly 0.1
        now = last + timedelta(seconds=360)
        result = compute_delta_h(now, last)
        assert result == pytest.approx(0.1)

    def test_just_over_threshold_clamped(self):
        """Just over threshold is clamped to 0.1 hours."""
        last = datetime(2026, 3, 29, 12, 0, 0)
        now = last + timedelta(seconds=361)
        result = compute_delta_h(now, last)
        assert result == pytest.approx(0.1)

    def test_just_under_threshold(self):
        last = datetime(2026, 3, 29, 12, 0, 0)
        now = last + timedelta(seconds=359)
        result = compute_delta_h(now, last)
        assert result is not None
        assert result == pytest.approx(359 / 3600)


class TestSplitGridPower:
    def test_importing(self):
        import_kw, export_kw = split_grid_power(5000)
        assert import_kw == pytest.approx(5.0)
        assert export_kw == pytest.approx(0.0)

    def test_exporting(self):
        import_kw, export_kw = split_grid_power(-3000)
        assert import_kw == pytest.approx(0.0)
        assert export_kw == pytest.approx(3.0)

    def test_zero(self):
        import_kw, export_kw = split_grid_power(0)
        assert import_kw == pytest.approx(0.0)
        assert export_kw == pytest.approx(0.0)

    def test_small_import(self):
        import_kw, export_kw = split_grid_power(100)
        assert import_kw == pytest.approx(0.1)
        assert export_kw == pytest.approx(0.0)


class TestShouldResetDaily:
    def test_none_last_reset(self):
        assert should_reset_daily(date(2026, 3, 29), None) is True

    def test_same_day(self):
        assert should_reset_daily(date(2026, 3, 29), date(2026, 3, 29)) is False

    def test_different_day(self):
        assert should_reset_daily(date(2026, 3, 30), date(2026, 3, 29)) is True
