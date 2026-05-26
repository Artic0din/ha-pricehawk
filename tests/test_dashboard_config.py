"""Tests for ``custom_components.pricehawk.dashboard_config``.

Constitution P20 — observability: version-lookup failures used to be
swallowed silently (``except Exception: version = "unknown"``) which
left a user-visible ``unknown`` cache-buster with zero diagnostic
trail. The ``_get_manifest_version`` helper now logs at WARNING on
any failure. These tests assert that contract and the public-API
return values for both the success and failure paths.

P14 — systemic fix: there are three call sites in ``dashboard_config``
(``setup_panel_iframe``, ``setup_panel_custom_v2``,
``register_lovelace_card_resource``). All three now route through the
helper, so the helper's behaviour transitively covers every site.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[1]


def _dashboard_config_source() -> str:
    return (
        REPO / "custom_components" / "pricehawk" / "dashboard_config.py"
    ).read_text()


def _install_loader_stub(monkeypatch: pytest.MonkeyPatch, integration_or_exc):
    """Install a fake ``homeassistant.loader`` module.

    Pass an ``integration`` object (``manifest`` dict attached) for the
    success path, or pass an ``Exception`` subclass instance to make
    ``async_get_integration`` raise that exception.
    """
    fake_loader = MagicMock()

    async def _async_get_integration(_hass, _domain):
        if isinstance(integration_or_exc, BaseException):
            raise integration_or_exc
        return integration_or_exc

    fake_loader.async_get_integration = _async_get_integration
    monkeypatch.setitem(sys.modules, "homeassistant.loader", fake_loader)
    # Wire onto the parent stub so attribute access works too.
    ha_root = sys.modules["homeassistant"]
    monkeypatch.setattr(ha_root, "loader", fake_loader, raising=False)
    return fake_loader


def _run(coro):
    return asyncio.run(coro)


class TestGetManifestVersionSuccess:
    """Success path: helper returns the manifest's reported version."""

    def test_returns_manifest_version_when_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.pricehawk import dashboard_config

        integration = SimpleNamespace(manifest={"version": "1.6.0-beta.9"})
        _install_loader_stub(monkeypatch, integration)

        hass = MagicMock()
        result = _run(dashboard_config._get_manifest_version(hass))

        assert result == "1.6.0-beta.9"

    def test_returns_supplied_default_when_version_key_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.pricehawk import dashboard_config

        # Manifest exists but has no "version" — must fall through to default
        # without raising, and without logging a warning (no exception).
        integration = SimpleNamespace(manifest={})
        _install_loader_stub(monkeypatch, integration)

        hass = MagicMock()
        result = _run(
            dashboard_config._get_manifest_version(hass, default="1")
        )

        assert result == "1"


class TestGetManifestVersionFailureLogsWarning:
    """P20 contract: failure logs a WARNING and returns the default."""

    def test_loader_exception_logs_warning_and_returns_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from custom_components.pricehawk import dashboard_config

        boom = RuntimeError("integration not loaded yet")
        _install_loader_stub(monkeypatch, boom)

        hass = MagicMock()
        caplog.set_level(
            logging.WARNING, logger="custom_components.pricehawk.dashboard_config"
        )

        result = _run(dashboard_config._get_manifest_version(hass))

        # Behaviour: caller still gets a usable sentinel.
        assert result == "unknown"

        # Observability: WARNING log with the exception text + the
        # sentinel actually returned. exc_info=False keeps the noise
        # down — we don't want a full traceback dumped on every miss,
        # the message itself carries the diagnostic.
        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and r.name == "custom_components.pricehawk.dashboard_config"
        ]
        assert warnings, (
            "version lookup failure MUST log a WARNING; "
            "found none in dashboard_config logger"
        )
        msg = warnings[0].getMessage()
        assert "version lookup failed" in msg
        assert "integration not loaded yet" in msg

    def test_custom_default_is_returned_and_quoted_in_log(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Lovelace card path passes default="1"; confirm both sites
        share the same logging contract."""
        from custom_components.pricehawk import dashboard_config

        boom = OSError("loader IO error")
        _install_loader_stub(monkeypatch, boom)

        hass = MagicMock()
        caplog.set_level(
            logging.WARNING, logger="custom_components.pricehawk.dashboard_config"
        )

        result = _run(
            dashboard_config._get_manifest_version(hass, default="1")
        )

        assert result == "1"
        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and r.name == "custom_components.pricehawk.dashboard_config"
        ]
        assert warnings, "lovelace-card-path failure MUST also log a WARNING"
        msg = warnings[0].getMessage()
        assert "version lookup failed" in msg
        assert "loader IO error" in msg
        # The default sentinel that was returned should appear in the
        # log so operators can correlate the user-visible value with
        # the log line. repr() quotes the string.
        assert "'1'" in msg

    def test_missing_loader_module_logs_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Defence-in-depth: if ``homeassistant.loader`` itself cannot
        be imported, the helper must still surface a WARNING rather
        than crashing the caller."""
        from custom_components.pricehawk import dashboard_config

        # Force the local ``from homeassistant.loader import ...`` to
        # fail by removing the module from sys.modules and shadowing
        # ``homeassistant`` so the attribute lookup also fails.
        monkeypatch.delitem(sys.modules, "homeassistant.loader", raising=False)

        # Stub a builtins.__import__ that refuses homeassistant.loader.
        import builtins
        real_import = builtins.__import__

        def _refuse(name, *args, **kwargs):
            if name == "homeassistant.loader":
                raise ImportError("homeassistant.loader unavailable")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _refuse)

        hass = MagicMock()
        caplog.set_level(
            logging.WARNING, logger="custom_components.pricehawk.dashboard_config"
        )

        result = _run(dashboard_config._get_manifest_version(hass))

        assert result == "unknown"
        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and r.name == "custom_components.pricehawk.dashboard_config"
        ]
        assert warnings, "ImportError path MUST also log a WARNING"
        assert "homeassistant.loader unavailable" in warnings[0].getMessage()


class TestNoSilentSwallowSourcePattern:
    """Regression source-asserts — keep the silent-swallow pattern out.

    Constitution P14: the three former call sites used to copy-paste
    the same ``except Exception: version = "unknown"`` block. After
    extraction there must be only ONE try/except for version lookup
    in the entire file, inside ``_get_manifest_version``.
    """

    def test_only_one_loader_import_in_module(self) -> None:
        src = _dashboard_config_source()
        # Two should be expected — none in callers, one in the helper.
        assert src.count("from homeassistant.loader import") == 1, (
            "version-lookup must be centralised; "
            "found multiple homeassistant.loader imports — re-introducing "
            "the duplicated try/except defeats P14"
        )

    def test_only_one_version_unknown_default_string(self) -> None:
        src = _dashboard_config_source()
        # Two former call sites used the literal string ``"unknown"``
        # as the swallow target. After the fix only the helper's
        # signature default + the module constant should remain.
        # Allow up to 2 (constant declaration + signature default).
        unknown_count = src.count('"unknown"')
        assert unknown_count <= 2, (
            f'expected <=2 literal "unknown" occurrences after extraction, '
            f"got {unknown_count} — duplicated swallow pattern may have "
            "crept back in"
        )

    def test_helper_logs_warning_on_failure_in_source(self) -> None:
        src = _dashboard_config_source()
        assert "version lookup failed" in src, (
            "P20 observability message must be present in source — "
            'operators grep for "version lookup failed" to diagnose '
            "stale dashboards"
        )
