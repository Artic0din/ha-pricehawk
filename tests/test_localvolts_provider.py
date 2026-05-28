"""Tests for the LocalVolts provider — wholesale + buy/sell ceilings."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.pricehawk.localvolts_api import (
    LocalVoltsAPIError,
    aggregate_to_half_hour,
    fetch_recent_intervals,
)
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

        assert provider.export_earnings_today_c == pytest.approx(0.03 * -3.0, abs=0.01)
        # Negative-export kWh tracked for diagnostics
        assert provider.extras["negative_export_kwh"] == pytest.approx(0.03, abs=1e-6)
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


class TestLocalVoltsProviderEdgeCases:
    """Cover lines 77 (update no-op without rates) and 140 (daily_fixed_charges)."""

    def test_update_without_rates_is_noop(self):
        # ARRANGE — no set_current_rates called
        provider = LocalVoltsProvider({})
        t0 = datetime(2026, 5, 28, 12, 0, 0)
        t1 = t0 + timedelta(hours=0.01)

        # ACT
        provider.update(5000, t0)
        provider.update(5000, t1)

        # ASSERT — nothing accumulated
        assert provider.import_kwh_today == pytest.approx(0.0)
        assert provider.import_cost_today_c == pytest.approx(0.0)

    def test_daily_fixed_charges_reflects_supply_c(self):
        # ARRANGE — supply set to 110c/day = $1.10/day
        provider = LocalVoltsProvider({"localvolts_daily_supply": 110.0})

        # ACT / ASSERT
        assert provider.daily_fixed_charges_aud == pytest.approx(1.10)


class TestAggregator:
    def _iv(self, end_minutes_ago, kwh, imp, exp):
        from datetime import timezone

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


class TestFetchRecentIntervals:
    """Cover ``fetch_recent_intervals`` async paths — lines 50-111."""

    def _session_with_status(self, status: int, body=None) -> MagicMock:
        @asynccontextmanager
        async def _get(url, **_kw):
            resp = MagicMock()
            resp.status = status
            resp.json = AsyncMock(return_value=body if body is not None else [])
            yield resp

        session = MagicMock()
        session.get = _get
        return session

    def test_returns_exp_intervals_on_200(self):
        # ARRANGE
        intervals = [
            {"quality": "exp", "costsAllVarRate": 22.0, "intervalEnd": "2026-05-28T08:00:00Z"},
            {"quality": "est", "costsAllVarRate": 99.0, "intervalEnd": "2026-05-28T07:55:00Z"},
        ]
        session = self._session_with_status(200, body=intervals)

        # ACT
        result = asyncio.run(fetch_recent_intervals(session, "api-key", "partner-id", "6407786010"))

        # ASSERT — only "exp" quality intervals returned
        assert len(result) == 1
        assert result[0]["quality"] == "exp"

    def test_non_list_response_returns_empty(self):
        # ARRANGE — API returns a dict instead of a list (malformed)
        session = self._session_with_status(200, body={"error": "bad"})

        # ACT
        result = asyncio.run(fetch_recent_intervals(session, "key", "partner", "NMI123"))

        # ASSERT
        assert result == []

    def test_401_raises_api_error(self):
        # ARRANGE
        session = self._session_with_status(401)

        # ACT / ASSERT
        with pytest.raises(LocalVoltsAPIError, match="auth failed"):
            asyncio.run(fetch_recent_intervals(session, "bad-key", "partner", "NMI"))

    def test_403_raises_api_error(self):
        session = self._session_with_status(403)
        with pytest.raises(LocalVoltsAPIError, match="auth failed"):
            asyncio.run(fetch_recent_intervals(session, "bad-key", "partner", "NMI"))

    def test_404_returns_empty(self):
        # ARRANGE — unexpected non-retryable status
        session = self._session_with_status(404)

        # ACT
        result = asyncio.run(fetch_recent_intervals(session, "key", "partner", "NMI"))

        # ASSERT
        assert result == []

    def test_500_retries_then_returns_empty(self):
        # ARRANGE
        session = self._session_with_status(500)

        with patch(
            "custom_components.pricehawk.localvolts_api.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = asyncio.run(fetch_recent_intervals(session, "key", "partner", "NMI"))

        assert result == []

    def test_429_retries_then_returns_empty(self):
        # ARRANGE — rate limit
        session = self._session_with_status(429)

        with patch(
            "custom_components.pricehawk.localvolts_api.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = asyncio.run(fetch_recent_intervals(session, "key", "partner", "NMI"))

        assert result == []

    def test_client_error_exhausted_returns_empty(self):
        @asynccontextmanager
        async def _get(url, **_kw):
            if False:  # satisfy async generator requirement
                yield  # pragma: no cover
            raise aiohttp.ClientConnectionError("network down")

        session = MagicMock()
        session.get = _get

        with patch(
            "custom_components.pricehawk.localvolts_api.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = asyncio.run(fetch_recent_intervals(session, "key", "partner", "NMI"))

        assert result == []


class TestAggregatorEdgeCases:
    """Cover lines 146, 150-152, 164 — bad timestamps + all-old intervals."""

    def _iv_raw(self, end_str, kwh, imp, exp):
        return {
            "intervalEnd": end_str,
            "loadKwh": kwh,
            "costsAllVarRate": imp,
            "earningsAllVarRate": exp,
            "quality": "exp",
        }

    def test_unparseable_timestamp_skipped(self):
        # ARRANGE — one interval with a bad timestamp, one good recent one
        from datetime import timezone

        end = datetime.now(timezone.utc) - timedelta(minutes=2)
        intervals = [
            self._iv_raw("NOT_A_DATE", 1.0, 99.0, 0.0),
            self._iv_raw(end.isoformat().replace("+00:00", "Z"), 1.0, 25.0, 5.0),
        ]

        # ACT
        imp, exp = aggregate_to_half_hour(intervals)

        # ASSERT — only valid interval used
        assert imp == pytest.approx(25.0)
        assert exp == pytest.approx(5.0)

    def test_no_timestamp_field_intervals_excluded(self):
        # ARRANGE — intervals without any timestamp key
        intervals = [
            {"loadKwh": 1.0, "costsAllVarRate": 50.0, "earningsAllVarRate": 10.0},
        ]

        # ACT
        result = aggregate_to_half_hour(intervals)

        # ASSERT — treated as upstream-filtered → no recent → None, None
        assert result == (None, None)

    def test_all_intervals_older_than_30min_returns_none(self):
        # ARRANGE — all end times are >30 min ago
        from datetime import timezone

        old_end = datetime.now(timezone.utc) - timedelta(minutes=60)
        intervals = [
            self._iv_raw(old_end.isoformat().replace("+00:00", "Z"), 1.0, 25.0, 5.0),
        ]

        # ACT
        result = aggregate_to_half_hour(intervals)

        # ASSERT
        assert result == (None, None)


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
        assert restored.import_cost_today_c == pytest.approx(provider.import_cost_today_c)
