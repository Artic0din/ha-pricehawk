"""Phase 8 PR-7 — diagnostics platform tests.

The conftest stubs HA's async_redact_data with a behaviour-equivalent
helper (real impl walks the dict and replaces matched keys with
"**REDACTED**"). Tests verify the redaction list hits every API key
+ HA token + large-but-not-secret plan envelope per D-P8-3.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from custom_components.pricehawk.const import (
    CONF_API_KEY,
    CONF_CDR_PLAN,
    CONF_DWT_OE_API_KEY,
    CONF_HA_TOKEN,
    CONF_LOCALVOLTS_API_KEY,
    CONF_NAMED_COMPARATOR_PLAN,
)
from custom_components.pricehawk.diagnostics import (
    TO_REDACT,
    async_get_config_entry_diagnostics,
)


def _entry(*, data: dict, options: dict, coordinator=None):
    runtime_data = SimpleNamespace(coordinator=coordinator) if coordinator else None
    return SimpleNamespace(
        entry_id="test-entry-xyz",
        data=data,
        options=options,
        runtime_data=runtime_data,
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestRedactionList:
    def test_to_redact_covers_every_api_key_field(self):
        for field in (
            CONF_API_KEY,
            CONF_DWT_OE_API_KEY,
            CONF_LOCALVOLTS_API_KEY,
            CONF_HA_TOKEN,
        ):
            assert field in TO_REDACT, f"{field} not in TO_REDACT — would leak in diagnostics"

    def test_to_redact_includes_large_plan_envelopes(self):
        """D-P8-3: plan envelopes redacted for size, not secrecy."""
        assert CONF_CDR_PLAN in TO_REDACT
        assert CONF_NAMED_COMPARATOR_PLAN in TO_REDACT
        assert "amber_static_plan" in TO_REDACT
        assert "localvolts_static_plan" in TO_REDACT


class TestDiagnosticsOutput:
    def test_api_key_redacted_in_entry_data(self):
        entry = _entry(
            data={CONF_API_KEY: "sk-leaked-secret-amber-key"},
            options={},
        )
        out = _run(async_get_config_entry_diagnostics(None, entry))
        assert "sk-leaked-secret-amber-key" not in json.dumps(out)
        assert out["entry_data"][CONF_API_KEY] == "**REDACTED**"
        assert out["_redaction_count"] >= 1

    def test_dwt_oe_api_key_redacted(self):
        entry = _entry(
            data={CONF_DWT_OE_API_KEY: "oe-secret-key-abc123"},
            options={},
        )
        out = _run(async_get_config_entry_diagnostics(None, entry))
        assert "oe-secret-key-abc123" not in json.dumps(out)

    def test_localvolts_api_key_redacted(self):
        entry = _entry(
            data={},
            options={CONF_LOCALVOLTS_API_KEY: "lv-secret-xyz"},
        )
        out = _run(async_get_config_entry_diagnostics(None, entry))
        assert "lv-secret-xyz" not in json.dumps(out)

    def test_ha_token_redacted(self):
        entry = _entry(
            data={CONF_HA_TOKEN: "ha-jwt-token-long-string"},
            options={},
        )
        out = _run(async_get_config_entry_diagnostics(None, entry))
        assert "ha-jwt-token-long-string" not in json.dumps(out)

    def test_cdr_plan_envelope_redacted_for_size(self):
        """D-P8-3: redacted not for secrecy but to keep output small."""
        entry = _entry(
            data={},
            options={CONF_CDR_PLAN: {"data": {"planId": "BIG-12345"}}},
        )
        out = _run(async_get_config_entry_diagnostics(None, entry))
        assert "BIG-12345" not in json.dumps(out)

    def test_output_is_json_serialisable(self):
        entry = _entry(
            data={CONF_API_KEY: "secret"},
            options={"some_other": "value"},
        )
        out = _run(async_get_config_entry_diagnostics(None, entry))
        json.dumps(out)  # raises if not serialisable

    def test_runtime_state_empty_when_no_coordinator(self):
        entry = _entry(data={}, options={})
        out = _run(async_get_config_entry_diagnostics(None, entry))
        assert out["runtime_state"] == {}

    def test_runtime_state_populated_when_coordinator_present(self):
        coord = SimpleNamespace(
            _amber_mode="live_api",
            _flow_power_mode="off",
            _localvolts_mode="static_prd",
            _reauth_provider_id=None,
            _providers={"amber": object(), "globird": object()},
            _wholesale_settlement="2026-05-22 12:30:00",
            _wholesale_c=5.5,
            _amber_import_c=33.0,
            _amber_export_c=5.5,
            _saving_month_aud=12.34,
            _daily_cost_history=[{}] * 30,
            _ranking_last_run_at=None,
            _backfill_status="idle",
            _dwt_provider=None,
        )
        entry = _entry(data={}, options={}, coordinator=coord)
        out = _run(async_get_config_entry_diagnostics(None, entry))
        rs = out["runtime_state"]
        assert rs["amber_mode"] == "live_api"
        assert rs["localvolts_mode"] == "static_prd"
        assert sorted(rs["registered_provider_ids"]) == ["amber", "globird"]
        assert rs["daily_cost_history_len"] == 30
        assert rs["saving_month_aud"] == 12.34

    def test_no_secret_in_repr(self):
        """Even the repr() of the output dict has nothing leaking."""
        entry = _entry(
            data={CONF_API_KEY: "ultra-secret-12345"},
            options={CONF_LOCALVOLTS_API_KEY: "ultra-secret-67890"},
        )
        out = _run(async_get_config_entry_diagnostics(None, entry))
        assert "ultra-secret-12345" not in repr(out)
        assert "ultra-secret-67890" not in repr(out)

    def test_redaction_count_zero_when_no_secrets(self):
        entry = _entry(
            data={"some_non_secret": "value"},
            options={"another_non_secret": "v"},
        )
        out = _run(async_get_config_entry_diagnostics(None, entry))
        assert out["_redaction_count"] == 0

    def test_dwt_provider_snapshot_included_when_present(self):
        # ARRANGE — coordinator with a DWT provider that has last_price
        from datetime import datetime, timezone

        last_price = SimpleNamespace(
            price_aud_per_mwh=96.16,
            interval_end_utc=datetime(2026, 5, 28, 8, 0, 0, tzinfo=timezone.utc),
            attribution="AEMO NEMWeb VIC1",
        )
        dwt = SimpleNamespace(region="VIC1", last_price=last_price)
        coord = SimpleNamespace(
            _amber_mode=None,
            _flow_power_mode=None,
            _localvolts_mode=None,
            _reauth_provider_id=None,
            _providers={},
            _wholesale_settlement="",
            _wholesale_c=None,
            _amber_import_c=None,
            _amber_export_c=None,
            _saving_month_aud=None,
            _daily_cost_history=[],
            _ranking_last_run_at=None,
            _backfill_status=None,
            _dwt_provider=dwt,
        )
        entry = _entry(data={}, options={}, coordinator=coord)

        # ACT
        out = _run(async_get_config_entry_diagnostics(None, entry))

        # ASSERT — DWT snapshot in runtime_state
        dwt_snap = out["runtime_state"]["dwt"]
        assert dwt_snap["region"] == "VIC1"
        assert dwt_snap["last_price_aud_per_mwh"] == pytest.approx(96.16)
        assert "2026-05-28" in dwt_snap["last_price_interval_end_utc"]
        assert dwt_snap["attribution"] == "AEMO NEMWeb VIC1"

    def test_dwt_provider_with_no_last_price(self):
        # ARRANGE — DWT provider exists but last_price is None
        dwt = SimpleNamespace(region="NSW1", last_price=None)
        coord = SimpleNamespace(
            _amber_mode=None,
            _flow_power_mode=None,
            _localvolts_mode=None,
            _reauth_provider_id=None,
            _providers={},
            _wholesale_settlement="",
            _wholesale_c=None,
            _amber_import_c=None,
            _amber_export_c=None,
            _saving_month_aud=None,
            _daily_cost_history=[],
            _ranking_last_run_at=None,
            _backfill_status=None,
            _dwt_provider=dwt,
        )
        entry = _entry(data={}, options={}, coordinator=coord)

        # ACT
        out = _run(async_get_config_entry_diagnostics(None, entry))

        # ASSERT — nones throughout, no crash
        dwt_snap = out["runtime_state"]["dwt"]
        assert dwt_snap["last_price_aud_per_mwh"] is None
        assert dwt_snap["last_price_interval_end_utc"] is None


class TestSafeIso:
    """Cover ``_safe_iso`` — lines 103-111."""

    def test_none_returns_none(self):
        from custom_components.pricehawk.diagnostics import _safe_iso

        assert _safe_iso(None) is None

    def test_datetime_returns_iso_string(self):
        from datetime import datetime, timezone

        from custom_components.pricehawk.diagnostics import _safe_iso

        dt = datetime(2026, 5, 28, 8, 0, 0, tzinfo=timezone.utc)
        result = _safe_iso(dt)
        assert isinstance(result, str)
        assert "2026-05-28" in result

    def test_non_callable_isoformat_returns_none(self):
        from custom_components.pricehawk.diagnostics import _safe_iso

        # Object with isoformat as a non-callable attribute
        obj = SimpleNamespace(isoformat="not_callable")
        assert _safe_iso(obj) is None

    def test_object_without_isoformat_returns_none(self):
        from custom_components.pricehawk.diagnostics import _safe_iso

        assert _safe_iso("just a string") is None
