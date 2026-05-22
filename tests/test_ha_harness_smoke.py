"""Phase 11 PR-16 — smoke tests for the new HA-harness fixtures.

Validates that the ``ha_fixtures`` module exports the expected helper
shapes. These tests don't yet drive an actual ``pytest-homeassistant-
custom-component`` ``hass`` fixture — that migration is per-module per
D-P11-1 (dual-mode strategy). For now we just sanity-check the mock
shapes so future tests can rely on them.
"""

from __future__ import annotations

import asyncio

from tests.ha_fixtures import (
    mock_config_entry_data,
    mock_nemweb_client,
    mock_openelectricity_client,
    recorder_mock_external_statistics,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestMockOEClient:
    def test_default_returns_wholesale_price(self):
        client = mock_openelectricity_client()
        result = _run(client.fetch_current_price("NSW1"))
        assert result.price_aud_per_mwh == 85.42
        assert result.region == "NSW1"

    def test_custom_price_propagated(self):
        client = mock_openelectricity_client(price_aud_per_mwh=42.0)
        result = _run(client.fetch_current_price("VIC1"))
        assert result.price_aud_per_mwh == 42.0

    def test_last_good_returns_same_price(self):
        client = mock_openelectricity_client(price_aud_per_mwh=100.0)
        assert client.last_good("NSW1").price_aud_per_mwh == 100.0


class TestMockNEMWebClient:
    def test_default_c_kwh_to_aud_per_mwh_conversion(self):
        client = mock_nemweb_client(price_c_kwh=8.5)
        result = _run(client.fetch_current_price("NSW1"))
        # 8.5 c/kWh = 85 $/MWh
        assert result.price_aud_per_mwh == 85.0

    def test_attribution_is_nemweb(self):
        client = mock_nemweb_client()
        result = _run(client.fetch_current_price("NSW1"))
        assert "NEMWeb" in result.attribution


class TestRecorderMockExternalStatistics:
    def test_call_records_metadata_and_stats(self):
        mock, calls = recorder_mock_external_statistics()
        mock(None, {"statistic_id": "test:foo"}, [{"start": "x", "state": 5.0, "sum": 5.0}])
        assert len(calls) == 1
        metadata, stats = calls[0]
        assert metadata["statistic_id"] == "test:foo"
        assert stats[0]["state"] == 5.0

    def test_multiple_calls_accumulate(self):
        mock, calls = recorder_mock_external_statistics()
        for _ in range(3):
            mock(None, {}, [])
        assert len(calls) == 3


class TestConfigEntryData:
    def test_default_is_dwt_oe(self):
        entry = mock_config_entry_data()
        assert entry["data"]["current_provider"] == "dwt_openelectricity"
        assert entry["options"]["dwt_oe_enabled"] is True

    def test_pricing_mode_override(self):
        entry = mock_config_entry_data(pricing_mode="static_prd")
        assert entry["options"]["amber_pricing_mode"] == "static_prd"

    def test_entry_id_override(self):
        entry = mock_config_entry_data(entry_id="custom-id-123")
        assert entry["entry_id"] == "custom-id-123"
