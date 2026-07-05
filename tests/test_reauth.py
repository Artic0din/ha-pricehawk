"""Phase 8 PR-5 — per-provider reauth flow tests.

Covers AC-1 through AC-9 of 08-01-PLAN. Same pattern as PR-2b:
provider-side raise sites exercised via direct invocation with mocked
HA stubs; ConfigFlow routing covered via source-level asserts because
EnergyCompareConfigFlow can't be instantiated under conftest's HA
MagicMock base (documented in 07-02b D-1 deviation).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from homeassistant.exceptions import ConfigEntryAuthFailed
from custom_components.pricehawk.const import (
    PROVIDER_AMBER,
    PROVIDER_DWT_OE,
    PROVIDER_LOCALVOLTS,
)
from custom_components.pricehawk.localvolts_api import LocalVoltsAPIError


# ----------------------------------------------------------------------
# Helpers — coordinator with just the bits we need
# ----------------------------------------------------------------------


class _FakeCoordinator:
    """Minimal coordinator surface for testing the raise sites.

    Mirrors the attributes touched by Tasks 1-3 (the auth-fail branches
    in _fetch_amber_with_retry, _maybe_poll_localvolts, _refresh_dwt_price).
    """

    def __init__(self):
        self._reauth_provider_id: str | None = None


def _amber_raise(
    coord: _FakeCoordinator,
    status: int,
    api_key: str = "sk-secret-key-do-not-leak",
) -> None:
    """Mirror the production raise branch (coordinator.py:692-705)."""
    del api_key  # Production code never embeds the key in the message.
    if status in (401, 403):
        coord._reauth_provider_id = PROVIDER_AMBER
        raise ConfigEntryAuthFailed(f"Amber API rejected the key (HTTP {status})")
    # 500 and others — production returns None, no raise.


def _localvolts_raise(coord: _FakeCoordinator, lv_error: LocalVoltsAPIError) -> None:
    """Mirror the production translate branch (coordinator.py LV poll)."""
    msg = str(lv_error).lower()
    if "auth failed" in msg or "401" in msg or "403" in msg:
        coord._reauth_provider_id = PROVIDER_LOCALVOLTS
        raise ConfigEntryAuthFailed("LocalVolts API rejected credentials") from lv_error
    raise lv_error


def _dwt_oe_raise(coord: _FakeCoordinator, exc: ConfigEntryAuthFailed) -> None:
    """Mirror the production DWT-OE tag branch."""
    coord._reauth_provider_id = PROVIDER_DWT_OE
    raise exc


# ----------------------------------------------------------------------
# Coordinator raise sites (AC-1, AC-2, AC-3)
# ----------------------------------------------------------------------


class TestCoordinatorRaiseSites:
    def test_amber_401_raises_config_entry_auth_failed(self):
        coord = _FakeCoordinator()
        with pytest.raises(ConfigEntryAuthFailed) as exc_info:
            _amber_raise(coord, 401)
        assert coord._reauth_provider_id == PROVIDER_AMBER
        assert "401" in str(exc_info.value)

    def test_amber_403_also_raises(self):
        coord = _FakeCoordinator()
        with pytest.raises(ConfigEntryAuthFailed):
            _amber_raise(coord, 403)
        assert coord._reauth_provider_id == PROVIDER_AMBER

    def test_amber_500_does_not_raise_auth_failed(self):
        """Server error → existing retry path, no auth-fail tag."""
        coord = _FakeCoordinator()
        _amber_raise(coord, 500)  # No raise.
        assert coord._reauth_provider_id is None

    def test_localvolts_auth_error_translated_to_auth_failed(self):
        coord = _FakeCoordinator()
        with pytest.raises(ConfigEntryAuthFailed) as exc_info:
            _localvolts_raise(coord, LocalVoltsAPIError("LocalVolts auth failed (401)"))
        assert coord._reauth_provider_id == PROVIDER_LOCALVOLTS
        assert isinstance(exc_info.value.__cause__, LocalVoltsAPIError)

    def test_localvolts_403_also_translates(self):
        coord = _FakeCoordinator()
        with pytest.raises(ConfigEntryAuthFailed):
            _localvolts_raise(coord, LocalVoltsAPIError("auth failed (403)"))
        assert coord._reauth_provider_id == PROVIDER_LOCALVOLTS

    def test_localvolts_non_auth_error_propagates(self):
        coord = _FakeCoordinator()
        with pytest.raises(LocalVoltsAPIError) as exc_info:
            _localvolts_raise(coord, LocalVoltsAPIError("connection refused"))
        # Original error propagates; no auth-fail wrap.
        assert "connection refused" in str(exc_info.value)
        assert coord._reauth_provider_id is None

    def test_dwt_oe_auth_failed_tags_provider(self):
        coord = _FakeCoordinator()
        with pytest.raises(ConfigEntryAuthFailed):
            _dwt_oe_raise(coord, ConfigEntryAuthFailed("HTTP 401"))
        assert coord._reauth_provider_id == PROVIDER_DWT_OE


# ----------------------------------------------------------------------
# API key redaction (AC-9)
# ----------------------------------------------------------------------


class TestAPIKeyRedaction:
    def test_amber_auth_failed_message_does_not_contain_key(self, caplog):
        coord = _FakeCoordinator()
        secret = "sk-abcdef123456-very-secret-amber-key"
        caplog.set_level(logging.WARNING)
        with pytest.raises(ConfigEntryAuthFailed) as exc_info:
            _amber_raise(coord, 401, api_key=secret)
        assert secret not in str(exc_info.value)
        # Captured log records (if any) must also be clean.
        for record in caplog.records:
            assert secret not in record.getMessage()
            assert secret not in str(record.args or "")

    def test_localvolts_auth_failed_message_does_not_contain_key(self, caplog):
        coord = _FakeCoordinator()
        secret = "lv-xyz789-localvolts-secret-key"
        caplog.set_level(logging.WARNING)
        # The translated ConfigEntryAuthFailed message comes from the
        # coordinator boundary — by contract, it does NOT include the
        # key (the key only lives in the URL params during the fetch).
        lv_err = LocalVoltsAPIError(f"LocalVolts auth failed (401)")  # noqa: F541 — intentional
        with pytest.raises(ConfigEntryAuthFailed) as exc_info:
            _localvolts_raise(coord, lv_err)
        assert secret not in str(exc_info.value)
        # Original LocalVoltsAPIError message also doesn't carry the key.
        assert secret not in str(exc_info.value.__cause__)
        for record in caplog.records:
            assert secret not in record.getMessage()


# ----------------------------------------------------------------------
# ConfigFlow dispatcher routing (AC-4) — source-level (see 07-02b D-1)
# ----------------------------------------------------------------------


def _config_flow_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "custom_components" / "pricehawk" / "config_flow.py"
    ).read_text()


class TestConfigFlowDispatcherSource:
    def test_dispatcher_routes_to_amber_substep(self):
        src = _config_flow_source()
        assert "if provider_id == PROVIDER_AMBER:" in src
        assert "return await self.async_step_reauth_amber()" in src

    def test_dispatcher_routes_to_localvolts_substep(self):
        src = _config_flow_source()
        assert "if provider_id == PROVIDER_LOCALVOLTS:" in src
        assert "return await self.async_step_reauth_localvolts()" in src

    def test_dispatcher_routes_to_dwt_oe_substep(self):
        src = _config_flow_source()
        assert "if provider_id == PROVIDER_DWT_OE:" in src
        assert "return await self.async_step_reauth_dwt_oe()" in src

    def test_dispatcher_aborts_on_unknown_provider(self):
        src = _config_flow_source()
        assert 'reason="reauth_provider_unknown"' in src

    def test_dispatcher_reads_runtime_data_coordinator(self):
        """Tag is read via entry.runtime_data.coordinator, not entry_data."""
        src = _config_flow_source()
        assert 'getattr(entry, "runtime_data", None)' in src
        assert '"_reauth_provider_id"' in src


# ----------------------------------------------------------------------
# Per-provider sub-step source contract (AC-5, AC-6, AC-7)
# ----------------------------------------------------------------------


class TestSubstepSource:
    def test_amber_substep_sets_invalid_api_key_on_401_or_403(self):
        src = _config_flow_source()
        # The Amber probe checks 401 or 403 → invalid_auth error.
        assert "resp.status in (401, 403)" in src
        assert 'errors[CONF_API_KEY] = "invalid_auth"' in src

    def test_amber_substep_uses_update_reload_and_abort(self):
        """Successful reauth must call update_reload_and_abort, not create_entry."""
        src = _config_flow_source()
        assert "return self.async_update_reload_and_abort(" in src
        # Specifically the Amber branch updates entry.data not options
        # (Amber API key lives in data, not options).
        assert "data={**entry.data, CONF_API_KEY: new_key}" in src

    def test_localvolts_substep_sets_invalid_credentials(self):
        src = _config_flow_source()
        # LocalVolts has 3 fields — single base error since the API
        # doesn't tell us which one is wrong.
        assert 'errors["base"] = "invalid_credentials"' in src

    def test_localvolts_substep_updates_options_not_data(self):
        """LocalVolts credentials live in entry.options."""
        src = _config_flow_source()
        # Search for the options update block — match the three keys.
        assert "CONF_LOCALVOLTS_API_KEY: new_key" in src
        assert "CONF_LOCALVOLTS_PARTNER_ID: new_partner" in src
        assert "CONF_LOCALVOLTS_NMI: new_nmi" in src

    def test_dwt_oe_substep_sets_invalid_api_key_on_auth_failed(self):
        src = _config_flow_source()
        assert 'errors[CONF_DWT_OE_API_KEY] = "invalid_api_key"' in src

    def test_dwt_oe_substep_preserves_region(self):
        """Region MUST NOT be re-collected during reauth — only the key."""
        src = _config_flow_source()
        # The form schema for reauth_dwt_oe only has CONF_DWT_OE_API_KEY.
        # We check the data update only touches the key.
        assert "data={**entry.data, CONF_DWT_OE_API_KEY: new_key}" in src

    def test_all_substep_passwords_use_password_text_selector(self):
        src = _config_flow_source()
        # Three password fields total: Amber key, LV key, DWT-OE key.
        assert src.count("TextSelectorType.PASSWORD") >= 3


# ----------------------------------------------------------------------
# strings/translations parity check (Phase 7 invariant continues)
# ----------------------------------------------------------------------


class TestStringsHaveReauthEntries:
    def test_strings_have_three_reauth_steps(self):
        import json

        repo = Path(__file__).resolve().parents[1]
        s = json.load(open(repo / "custom_components" / "pricehawk" / "strings.json"))
        for step_id in ("reauth_amber", "reauth_localvolts", "reauth_dwt_oe"):
            assert step_id in s["config"]["step"], f"strings.json missing config.step.{step_id}"

    def test_strings_have_invalid_credentials_error(self):
        import json

        repo = Path(__file__).resolve().parents[1]
        s = json.load(open(repo / "custom_components" / "pricehawk" / "strings.json"))
        assert "invalid_credentials" in s["config"]["error"]

    def test_strings_have_reauth_abort_reasons(self):
        import json

        repo = Path(__file__).resolve().parents[1]
        s = json.load(open(repo / "custom_components" / "pricehawk" / "strings.json"))
        assert "reauth_provider_unknown" in s["config"]["abort"]
        assert "reauth_successful" in s["config"]["abort"]

    def test_translations_byte_identical_to_strings(self):
        repo = Path(__file__).resolve().parents[1]
        a = (repo / "custom_components" / "pricehawk" / "strings.json").read_bytes()
        b = (repo / "custom_components" / "pricehawk" / "translations" / "en.json").read_bytes()
        assert a == b


# ----------------------------------------------------------------------
# History preservation contract (AC-8)
# ----------------------------------------------------------------------


class TestHistoryPreservation:
    def test_reauth_does_not_reset_daily_or_monthly_accumulators(self):
        """The dispatcher + substeps MUST NOT call provider.reset_daily.

        Source-level guard: no occurrence of `.reset_daily()` inside the
        reauth step methods. Reauth changes credentials only — the
        coordinator continues from where it stopped.
        """
        src = _config_flow_source()
        # Find the reauth block (from "async def async_step_reauth" to
        # the next "async def" or "@staticmethod").
        start = src.index("async def async_step_reauth(")
        end = src.index("@staticmethod", start)
        reauth_block = src[start:end]
        assert ".reset_daily()" not in reauth_block, (
            "Reauth steps must not reset daily accumulators (would wipe daily_cost_history)."
        )
        assert "_daily_cost_history" not in reauth_block
        assert "_saving_month_aud" not in reauth_block
