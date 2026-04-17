"""Tests for backfill module — pure Python, no HA dependencies."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.pricehawk.backfill import (
    _build_amber_price_index,
    _find_amber_rate,
    _parse_history_states,
    backfill_from_history,
)


# ---------------------------------------------------------------------------
# Helpers — build test fixtures
# ---------------------------------------------------------------------------

AEST = timezone(timedelta(hours=10))


def _make_history_states(
    start: datetime,
    count: int,
    interval_s: int = 30,
    power_w: float = 2000.0,
    unit: str = "W",
) -> list[dict]:
    """Generate mock HA history states at regular intervals."""
    states = []
    for i in range(count):
        ts = start + timedelta(seconds=i * interval_s)
        states.append({
            "state": power_w,
            "last_changed": ts.isoformat(),
            "unit": unit,
        })
    return states


def _make_amber_prices(
    start: datetime,
    hours: int,
    import_rate: float = 25.0,
    export_rate: float = 5.0,
) -> list[dict]:
    """Generate mock Amber 30-min price intervals for N hours."""
    prices = []
    for i in range(hours * 2):  # 30-min intervals
        slot_start = start + timedelta(minutes=i * 30)
        slot_end = slot_start + timedelta(minutes=30)
        prices.append({
            "channelType": "general",
            "perKwh": import_rate,
            "startTime": slot_start.isoformat(),
            "endTime": slot_end.isoformat(),
        })
        prices.append({
            "channelType": "feedIn",
            "perKwh": export_rate,
            "startTime": slot_start.isoformat(),
            "endTime": slot_end.isoformat(),
        })
    return prices


# Minimal GloBird options for testing (flat rate)
_GLOBIRD_OPTIONS: dict = {
    "import_tariff": {
        "type": "flat_stepped",
        "step1_threshold_kwh": 25.0,
        "step1_rate": 22.0,
        "step2_rate": 28.0,
    },
    "export_tariff": {
        "type": "tou",
        "periods": {
            "peak": {"rate": 5.0, "windows": [["16:00", "21:00"]]},
            "offpeak": {"rate": 2.0, "windows": [["10:00", "16:00"]]},
            "shoulder": {
                "rate": 1.0,
                "windows": [["21:00", "00:00"], ["00:00", "10:00"]],
            },
        },
    },
    "daily_supply_charge": 100.0,  # c/day
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBackfillBasic:
    """Test basic backfill computation produces daily costs."""

    def test_backfill_basic(self) -> None:
        """Feed constant 2kW import for 24h, verify daily cost produced."""
        start = datetime(2026, 4, 10, 0, 0, 0, tzinfo=AEST)

        # 2kW import for 24 hours at 30s intervals = 2880 readings
        history = _make_history_states(start, count=2880, interval_s=30, power_w=2000.0)
        amber_prices = _make_amber_prices(start, hours=24, import_rate=25.0)

        result = backfill_from_history(
            history_states=history,
            amber_prices=amber_prices,
            globird_options=_GLOBIRD_OPTIONS,
            amber_network_daily_c=50.0,
            amber_subscription_daily_c=10.0,
            existing_history=[],
        )

        assert len(result) == 1
        assert result[0]["date"] == "2026-04-10"

        # Amber: ~48 kWh * 25 c/kWh + 60c daily = ~1260c = ~$12.60
        # (Gap protection clamps to 6 min max, 30s intervals fit fine)
        assert result[0]["amber"] > 0

        # GloBird: ~48 kWh * 22 c/kWh + 100c supply = ~1156c = ~$11.56
        assert result[0]["globird"] > 0

    def test_backfill_export_reduces_cost(self) -> None:
        """Negative power (export) should reduce daily cost."""
        start = datetime(2026, 4, 10, 12, 0, 0, tzinfo=AEST)

        # -3kW export for 4 hours at 30s intervals
        history = _make_history_states(
            start, count=480, interval_s=30, power_w=-3000.0
        )
        amber_prices = _make_amber_prices(start, hours=4, export_rate=8.0)

        result = backfill_from_history(
            history_states=history,
            amber_prices=amber_prices,
            globird_options=_GLOBIRD_OPTIONS,
            amber_network_daily_c=50.0,
            amber_subscription_daily_c=10.0,
            existing_history=[],
        )

        assert len(result) == 1
        # With export credits, Amber cost should be below daily fees
        # 12 kWh * 8c = 96c credit, plus 60c daily = -36c = -$0.36
        assert result[0]["amber"] < 1.0  # Should be near zero or negative


class TestBackfillMerge:
    """Test merging with existing history."""

    def test_backfill_merges_with_existing(self) -> None:
        """Existing history preserved, new days added."""
        start = datetime(2026, 4, 10, 0, 0, 0, tzinfo=AEST)
        history = _make_history_states(start, count=2880, interval_s=30, power_w=1000.0)
        amber_prices = _make_amber_prices(start, hours=24)

        existing = [
            {"date": "2026-04-09", "amber": 5.50, "globird": 4.80},
        ]

        result = backfill_from_history(
            history, amber_prices, _GLOBIRD_OPTIONS, 50.0, 10.0, existing
        )

        # Should have both existing day and new day
        dates = [r["date"] for r in result]
        assert "2026-04-09" in dates
        assert "2026-04-10" in dates

        # Existing entry should be untouched
        apr9 = next(r for r in result if r["date"] == "2026-04-09")
        assert apr9["amber"] == 5.50
        assert apr9["globird"] == 4.80

    def test_backfill_overwrites_existing(self) -> None:
        """Backfill always overwrites existing data with fresh calculations."""
        start = datetime(2026, 4, 10, 0, 0, 0, tzinfo=AEST)
        history = _make_history_states(start, count=2880, interval_s=30, power_w=1000.0)
        amber_prices = _make_amber_prices(start, hours=24)

        existing = [
            {"date": "2026-04-10", "amber": 99.99, "globird": 88.88},
        ]

        result = backfill_from_history(
            history, amber_prices, _GLOBIRD_OPTIONS, 50.0, 10.0, existing
        )

        # Existing entry for 2026-04-10 should be REPLACED with backfill data
        apr10 = next(r for r in result if r["date"] == "2026-04-10")
        assert apr10["amber"] != 99.99  # overwritten
        assert apr10["globird"] != 88.88  # overwritten


class TestBackfillCap:
    """Test the 180-entry cap."""

    def test_backfill_caps_at_180(self) -> None:
        """Verify result is capped at 180 entries."""
        # Create 200 existing entries
        existing = [
            {"date": f"2025-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}", "amber": 5.0, "globird": 4.0}
            for i in range(175)
        ]

        start = datetime(2026, 4, 10, 0, 0, 0, tzinfo=AEST)
        # Create 10 days of history
        history: list[dict] = []
        amber_prices: list[dict] = []
        for d in range(10):
            day_start = start + timedelta(days=d)
            history.extend(
                _make_history_states(day_start, count=120, interval_s=30, power_w=1500.0)
            )
            amber_prices.extend(_make_amber_prices(day_start, hours=1))

        result = backfill_from_history(
            history, amber_prices, _GLOBIRD_OPTIONS, 50.0, 10.0, existing
        )

        assert len(result) <= 180


class TestBackfillKwUnit:
    """Test kW unit conversion."""

    def test_backfill_handles_kw_unit(self) -> None:
        """kW values should be converted to W correctly."""
        start = datetime(2026, 4, 10, 0, 0, 0, tzinfo=AEST)

        # 2 kW in "kW" unit should equal 2000W
        history_kw = _make_history_states(
            start, count=120, interval_s=30, power_w=2.0, unit="kW"
        )
        history_w = _make_history_states(
            start, count=120, interval_s=30, power_w=2000.0, unit="W"
        )
        amber_prices = _make_amber_prices(start, hours=1)

        result_kw = backfill_from_history(
            history_kw, amber_prices, _GLOBIRD_OPTIONS, 50.0, 10.0, []
        )
        result_w = backfill_from_history(
            history_w, amber_prices, _GLOBIRD_OPTIONS, 50.0, 10.0, []
        )

        assert len(result_kw) == 1
        assert len(result_w) == 1
        # Costs should be identical
        assert result_kw[0]["amber"] == result_w[0]["amber"]
        assert result_kw[0]["globird"] == result_w[0]["globird"]


class TestBackfillSkipsUnavailable:
    """Test that unavailable/unknown states are filtered."""

    def test_backfill_skips_unavailable(self) -> None:
        """States with non-numeric values should be silently skipped."""
        start = datetime(2026, 4, 10, 0, 0, 0, tzinfo=AEST)
        amber_prices = _make_amber_prices(start, hours=1)

        history = [
            {"state": "unavailable", "last_changed": start.isoformat(), "unit": "W"},
            {"state": "unknown", "last_changed": (start + timedelta(seconds=30)).isoformat(), "unit": "W"},
            {"state": "", "last_changed": (start + timedelta(seconds=60)).isoformat(), "unit": "W"},
            {"state": "not_a_number", "last_changed": (start + timedelta(seconds=90)).isoformat(), "unit": "W"},
        ]

        result = backfill_from_history(
            history, amber_prices, _GLOBIRD_OPTIONS, 50.0, 10.0, []
        )

        # No valid readings = no days produced
        assert result == []

    def test_backfill_mixed_valid_and_invalid(self) -> None:
        """Mix of valid and invalid states — only valid ones used."""
        start = datetime(2026, 4, 10, 0, 0, 0, tzinfo=AEST)
        amber_prices = _make_amber_prices(start, hours=1)

        history = [
            {"state": "unavailable", "last_changed": start.isoformat(), "unit": "W"},
            {"state": 1500.0, "last_changed": (start + timedelta(seconds=30)).isoformat(), "unit": "W"},
            {"state": 1600.0, "last_changed": (start + timedelta(seconds=60)).isoformat(), "unit": "W"},
            {"state": "unknown", "last_changed": (start + timedelta(seconds=90)).isoformat(), "unit": "W"},
            {"state": 1700.0, "last_changed": (start + timedelta(seconds=120)).isoformat(), "unit": "W"},
        ]

        result = backfill_from_history(
            history, amber_prices, _GLOBIRD_OPTIONS, 50.0, 10.0, []
        )

        # Should have one day with computed costs from valid readings
        assert len(result) == 1
        assert result[0]["amber"] > 0


class TestAmberPriceIndex:
    """Test the Amber price interval lookup."""

    def test_build_price_index(self) -> None:
        """Verify price index groups by channel and sorts."""
        start = datetime(2026, 4, 10, 0, 0, 0, tzinfo=AEST)
        prices = _make_amber_prices(start, hours=2)

        index = _build_amber_price_index(prices)

        assert "general" in index
        assert "feedIn" in index
        assert len(index["general"]) == 4  # 2 hours * 2 intervals/hour
        assert len(index["feedIn"]) == 4

        # Verify sorted
        for i in range(len(index["general"]) - 1):
            assert index["general"][i]["start"] <= index["general"][i + 1]["start"]

    def test_find_amber_rate(self) -> None:
        """Find rate for a timestamp within an interval."""
        start = datetime(2026, 4, 10, 0, 0, 0, tzinfo=AEST)
        prices = _make_amber_prices(start, hours=1, import_rate=30.0)
        index = _build_amber_price_index(prices)

        # Timestamp in the middle of first interval
        ts = start + timedelta(minutes=15)
        rate = _find_amber_rate(index["general"], ts)
        assert rate == 30.0

    def test_find_amber_rate_not_found(self) -> None:
        """Return None when timestamp is outside all intervals."""
        start = datetime(2026, 4, 10, 0, 0, 0, tzinfo=AEST)
        prices = _make_amber_prices(start, hours=1)
        index = _build_amber_price_index(prices)

        # Timestamp before any interval
        ts = start - timedelta(hours=2)
        rate = _find_amber_rate(index["general"], ts)
        assert rate is None


class TestParseHistoryStates:
    """Test history state parsing."""

    def test_parse_valid_states(self) -> None:
        """Valid states parsed into (timestamp, power_w) tuples."""
        start = datetime(2026, 4, 10, 0, 0, 0, tzinfo=AEST)
        states = _make_history_states(start, count=5, power_w=1500.0)
        readings = _parse_history_states(states)

        assert len(readings) == 5
        assert readings[0][1] == 1500.0

    def test_parse_kw_conversion(self) -> None:
        """kW unit states converted to W."""
        states = [{
            "state": 2.5,
            "last_changed": datetime(2026, 4, 10, 0, 0, 0, tzinfo=AEST).isoformat(),
            "unit": "kW",
        }]
        readings = _parse_history_states(states)

        assert len(readings) == 1
        assert readings[0][1] == 2500.0

    def test_parse_filters_invalid(self) -> None:
        """Non-numeric and missing states filtered out."""
        states = [
            {"state": "unavailable", "last_changed": "2026-04-10T00:00:00+10:00", "unit": "W"},
            {"state": None, "last_changed": "2026-04-10T00:01:00+10:00", "unit": "W"},
            {"state": 1000.0, "last_changed": "2026-04-10T00:02:00+10:00", "unit": "W"},
        ]
        readings = _parse_history_states(states)

        assert len(readings) == 1
        assert readings[0][1] == 1000.0
