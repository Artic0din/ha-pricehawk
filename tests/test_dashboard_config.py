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
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO = Path(__file__).resolve().parents[1]


def _dashboard_config_source() -> str:
    return (REPO / "custom_components" / "pricehawk" / "dashboard_config.py").read_text()


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

    def test_returns_manifest_version_when_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
        result = _run(dashboard_config._get_manifest_version(hass, default="1"))

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
        caplog.set_level(logging.WARNING, logger="custom_components.pricehawk.dashboard_config")

        result = _run(dashboard_config._get_manifest_version(hass))

        # Behaviour: caller still gets a usable sentinel.
        assert result == "unknown"

        # Observability: WARNING log with the exception text + the
        # sentinel actually returned. exc_info=False keeps the noise
        # down — we don't want a full traceback dumped on every miss,
        # the message itself carries the diagnostic.
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and r.name == "custom_components.pricehawk.dashboard_config"
        ]
        assert warnings, (
            "version lookup failure MUST log a WARNING; found none in dashboard_config logger"
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
        caplog.set_level(logging.WARNING, logger="custom_components.pricehawk.dashboard_config")

        result = _run(dashboard_config._get_manifest_version(hass, default="1"))

        assert result == "1"
        warnings = [
            r
            for r in caplog.records
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
        # raise ImportError. Per the import system, setting
        # ``sys.modules[name] = None`` causes any subsequent import of
        # that name to raise ``ImportError`` — without monkeypatching
        # ``builtins.__import__`` (which is global, racy under xdist
        # parallel execution, and breaks every other import in the
        # process). monkeypatch restores the original value at teardown.
        monkeypatch.setitem(sys.modules, "homeassistant.loader", None)

        hass = MagicMock()
        caplog.set_level(logging.WARNING, logger="custom_components.pricehawk.dashboard_config")

        result = _run(dashboard_config._get_manifest_version(hass))

        assert result == "unknown"
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and r.name == "custom_components.pricehawk.dashboard_config"
        ]
        assert warnings, "ImportError path MUST also log a WARNING"
        # Python's import machinery message when sys.modules[name] is
        # None: "import of <name> halted; None in sys.modules"
        msg = warnings[0].getMessage()
        assert "homeassistant.loader" in msg or "None in sys.modules" in msg


class TestCopyWwwAssets:
    """Cover ``copy_www_assets`` — lines 86-133."""

    def test_creates_dest_dir_and_writes_svg_icon(self, monkeypatch, tmp_path):
        # ARRANGE
        from custom_components.pricehawk import dashboard_config

        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "www" / "pricehawk")

        # Patch executor to run the callable synchronously in-process.
        async def _exec(func):
            func()

        hass.async_add_executor_job = _exec

        # ACT
        _run(dashboard_config.copy_www_assets(hass))

        # ASSERT — dest dir created, SVG icon written
        dest = tmp_path / "www" / "pricehawk"
        assert dest.is_dir()
        icon_svg = dest / "icon.svg"
        assert icon_svg.exists()
        assert "<svg" in icon_svg.read_text()

    def test_missing_source_files_logged_not_raised(self, monkeypatch, tmp_path, caplog):
        # ARRANGE — point src_dir at a temp dir with no www/ subdir so
        # src_html / src_panel_js / src_card_js all fail .exists().
        from custom_components.pricehawk import dashboard_config

        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "dest")

        async def _exec(func):
            func()

        hass.async_add_executor_job = _exec

        # Patch __file__ of the module so src_dir → empty temp dir
        fake_module_file = str(tmp_path / "fake_dashboard_config.py")
        monkeypatch.setattr(dashboard_config, "__file__", fake_module_file)

        caplog.set_level(logging.WARNING, logger="custom_components.pricehawk.dashboard_config")

        # ACT — must not raise
        _run(dashboard_config.copy_www_assets(hass))

        # ASSERT — warnings for the missing sources
        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "dashboard.html source not found" in messages or "source not found" in messages

    def test_executor_exception_logged_not_raised(self, monkeypatch, tmp_path, caplog):
        # ARRANGE
        from custom_components.pricehawk import dashboard_config

        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "dest")

        async def _exec_boom(func):
            raise OSError("disk full")

        hass.async_add_executor_job = _exec_boom

        caplog.set_level(logging.WARNING, logger="custom_components.pricehawk.dashboard_config")

        # ACT
        _run(dashboard_config.copy_www_assets(hass))

        # ASSERT — warning logged, no exception propagated
        warns = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warns, "OSError from executor must be caught and logged"


class TestSetupPanelIframe:
    """Cover ``setup_panel_iframe`` — lines 161-207."""

    def _make_frontend_stub(self):
        frontend = MagicMock()
        frontend.async_register_built_in_panel = MagicMock()
        frontend.async_remove_panel = MagicMock()
        return frontend

    def test_registers_iframe_panel(self, monkeypatch):
        # ARRANGE
        from custom_components.pricehawk import dashboard_config

        integration = SimpleNamespace(manifest={"version": "1.7.0"})
        _install_loader_stub(monkeypatch, integration)

        frontend = self._make_frontend_stub()
        monkeypatch.setitem(
            sys.modules,
            "homeassistant.components.frontend",
            frontend,
        )

        hass = MagicMock()
        entry = MagicMock()

        # ACT
        _run(dashboard_config.setup_panel_iframe(hass, entry))

        # ASSERT — panel registered with correct shape
        frontend.async_register_built_in_panel.assert_called_once()
        call_kwargs = frontend.async_register_built_in_panel.call_args
        assert call_kwargs.kwargs.get("component_name") == "iframe" or (
            len(call_kwargs.args) > 1 and call_kwargs.args[1] == "iframe"
        )
        # URL must contain cache-buster with version
        config_arg = call_kwargs.kwargs.get("config") or call_kwargs.args[-1]
        assert "1.7.0" in config_arg.get("url", "")

    def test_remove_panel_exception_does_not_abort(self, monkeypatch):
        # ARRANGE — async_remove_panel raises; setup_panel_iframe must continue
        from custom_components.pricehawk import dashboard_config

        integration = SimpleNamespace(manifest={"version": "1.0.0"})
        _install_loader_stub(monkeypatch, integration)

        frontend = self._make_frontend_stub()
        frontend.async_remove_panel.side_effect = RuntimeError("not registered")
        monkeypatch.setitem(sys.modules, "homeassistant.components.frontend", frontend)

        hass = MagicMock()
        entry = MagicMock()

        # ACT — must not raise
        _run(dashboard_config.setup_panel_iframe(hass, entry))

        # ASSERT — registration still attempted despite remove error
        frontend.async_register_built_in_panel.assert_called_once()

    def test_register_panel_exception_logged_not_raised(self, monkeypatch, caplog):
        # ARRANGE
        from custom_components.pricehawk import dashboard_config

        integration = SimpleNamespace(manifest={"version": "1.0.0"})
        _install_loader_stub(monkeypatch, integration)

        frontend = self._make_frontend_stub()
        frontend.async_register_built_in_panel.side_effect = RuntimeError("panel exists")
        monkeypatch.setitem(sys.modules, "homeassistant.components.frontend", frontend)

        hass = MagicMock()
        entry = MagicMock()
        caplog.set_level(logging.ERROR, logger="custom_components.pricehawk.dashboard_config")

        # ACT
        _run(dashboard_config.setup_panel_iframe(hass, entry))

        # ASSERT — error logged, no propagation
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, "failed panel registration must be logged at ERROR"


class TestSetupPanelCustomV2:
    """Cover ``setup_panel_custom_v2`` — lines 226-266."""

    def _make_frontend_stub(self):
        frontend = MagicMock()
        frontend.async_register_built_in_panel = MagicMock()
        frontend.async_remove_panel = MagicMock()
        return frontend

    def test_registers_custom_panel(self, monkeypatch):
        # ARRANGE
        from custom_components.pricehawk import dashboard_config

        integration = SimpleNamespace(manifest={"version": "2.0.0"})
        _install_loader_stub(monkeypatch, integration)

        frontend = self._make_frontend_stub()
        monkeypatch.setitem(sys.modules, "homeassistant.components.frontend", frontend)

        hass = MagicMock()

        # ACT
        _run(dashboard_config.setup_panel_custom_v2(hass))

        # ASSERT — registered with component_name "custom"
        frontend.async_register_built_in_panel.assert_called_once()
        call_kwargs = frontend.async_register_built_in_panel.call_args
        assert call_kwargs.kwargs.get("component_name") == "custom" or (
            len(call_kwargs.args) > 1 and call_kwargs.args[1] == "custom"
        )
        config_arg = call_kwargs.kwargs.get("config") or call_kwargs.args[-1]
        assert "pricehawk-panel" in str(config_arg)

    def test_register_exception_logged_not_raised(self, monkeypatch, caplog):
        # ARRANGE
        from custom_components.pricehawk import dashboard_config

        integration = SimpleNamespace(manifest={"version": "2.0.0"})
        _install_loader_stub(monkeypatch, integration)

        frontend = self._make_frontend_stub()
        frontend.async_register_built_in_panel.side_effect = RuntimeError("already registered")
        monkeypatch.setitem(sys.modules, "homeassistant.components.frontend", frontend)

        hass = MagicMock()
        caplog.set_level(logging.ERROR, logger="custom_components.pricehawk.dashboard_config")

        # ACT
        _run(dashboard_config.setup_panel_custom_v2(hass))

        # ASSERT
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, "failed v2 panel registration must be logged at ERROR"


class TestRegisterLovelaceCardResource:
    """Cover ``register_lovelace_card_resource`` — lines 282-327."""

    def test_no_lovelace_component_logs_info_and_returns(self, monkeypatch, caplog):
        # ARRANGE — remove lovelace from sys.modules so import raises ImportError
        from custom_components.pricehawk import dashboard_config

        monkeypatch.setitem(sys.modules, "homeassistant.components.lovelace", None)
        # Also unset hass.data so the function short-circuits at the import
        hass = MagicMock()
        hass.data = {}
        caplog.set_level(logging.INFO, logger="custom_components.pricehawk.dashboard_config")

        # ACT
        _run(dashboard_config.register_lovelace_card_resource(hass))

        # ASSERT — graceful skip: any info or warning referencing lovelace
        records = [r for r in caplog.records if r.levelno <= logging.WARNING]
        assert any("lovelace" in r.getMessage().lower() for r in records)

    def test_no_lovelace_resources_attr_logs_info_and_returns(self, monkeypatch, caplog):
        # ARRANGE — lovelace importable but hass.data["lovelace"].resources is None
        from custom_components.pricehawk import dashboard_config

        integration = SimpleNamespace(manifest={"version": "1.0.0"})
        _install_loader_stub(monkeypatch, integration)

        fake_lovelace = MagicMock()
        monkeypatch.setitem(sys.modules, "homeassistant.components.lovelace", fake_lovelace)
        monkeypatch.setitem(
            sys.modules,
            "homeassistant.components",
            sys.modules.get("homeassistant.components", MagicMock()),
        )

        ll_data = MagicMock()
        ll_data.resources = None
        hass = MagicMock()
        hass.data = {"lovelace": ll_data}

        caplog.set_level(logging.INFO, logger="custom_components.pricehawk.dashboard_config")

        # ACT
        _run(dashboard_config.register_lovelace_card_resource(hass))

        # ASSERT — early return with info about YAML mode
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("resource" in r.getMessage().lower() for r in infos)

    def test_resource_already_registered_skips_create(self, monkeypatch):
        # ARRANGE — existing resource whose URL starts with the card URL
        from custom_components.pricehawk import dashboard_config
        from custom_components.pricehawk.dashboard_config import LOVELACE_CARD_RESOURCE_URL

        integration = SimpleNamespace(manifest={"version": "1.0.0"})
        _install_loader_stub(monkeypatch, integration)

        fake_lovelace = MagicMock()
        monkeypatch.setitem(sys.modules, "homeassistant.components.lovelace", fake_lovelace)

        ll_resources = MagicMock()
        ll_resources.async_items = lambda: [{"url": LOVELACE_CARD_RESOURCE_URL + "?v=1"}]
        ll_resources.async_create_item = AsyncMock()
        ll_data = MagicMock()
        ll_data.resources = ll_resources

        hass = MagicMock()
        hass.data = {"lovelace": ll_data}

        # ACT
        _run(dashboard_config.register_lovelace_card_resource(hass))

        # ASSERT — no duplicate registration
        ll_resources.async_create_item.assert_not_called()

    def test_creates_resource_when_none_registered(self, monkeypatch):
        # ARRANGE — empty existing resources
        from custom_components.pricehawk import dashboard_config

        integration = SimpleNamespace(manifest={"version": "1.5.0"})
        _install_loader_stub(monkeypatch, integration)

        fake_lovelace = MagicMock()
        monkeypatch.setitem(sys.modules, "homeassistant.components.lovelace", fake_lovelace)

        ll_resources = MagicMock()
        ll_resources.async_items = lambda: []
        ll_resources.async_create_item = AsyncMock()
        ll_data = MagicMock()
        ll_data.resources = ll_resources

        hass = MagicMock()
        hass.data = {"lovelace": ll_data}

        # ACT
        _run(dashboard_config.register_lovelace_card_resource(hass))

        # ASSERT — new resource created with version in URL
        ll_resources.async_create_item.assert_awaited_once()
        call_arg = ll_resources.async_create_item.call_args[0][0]
        assert "1.5.0" in call_arg["url"]
        assert call_arg["res_type"] == "module"

    def test_create_exception_logged_as_warning(self, monkeypatch, caplog):
        # ARRANGE
        from custom_components.pricehawk import dashboard_config

        integration = SimpleNamespace(manifest={"version": "1.0.0"})
        _install_loader_stub(monkeypatch, integration)

        fake_lovelace = MagicMock()
        monkeypatch.setitem(sys.modules, "homeassistant.components.lovelace", fake_lovelace)

        ll_resources = MagicMock()
        ll_resources.async_items = lambda: []
        ll_resources.async_create_item = AsyncMock(side_effect=RuntimeError("storage locked"))
        ll_data = MagicMock()
        ll_data.resources = ll_resources

        hass = MagicMock()
        hass.data = {"lovelace": ll_data}

        caplog.set_level(logging.WARNING, logger="custom_components.pricehawk.dashboard_config")

        # ACT
        _run(dashboard_config.register_lovelace_card_resource(hass))

        # ASSERT — warning not a crash
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warns, "create failure must be logged at WARNING"


class TestRemovePanel:
    """Cover ``remove_panel`` — lines 337-344."""

    def test_removes_both_panels(self, monkeypatch):
        # ARRANGE
        from custom_components.pricehawk import dashboard_config
        from custom_components.pricehawk.dashboard_config import PANEL_URL_PATH, PANEL_V2_URL_PATH

        frontend = MagicMock()
        frontend.async_remove_panel = MagicMock()
        monkeypatch.setitem(sys.modules, "homeassistant.components.frontend", frontend)

        hass = MagicMock()

        # ACT
        _run(dashboard_config.remove_panel(hass))

        # ASSERT — both paths removed
        removed_paths = [c.args[1] for c in frontend.async_remove_panel.call_args_list]
        assert PANEL_URL_PATH in removed_paths
        assert PANEL_V2_URL_PATH in removed_paths

    def test_remove_exception_does_not_raise(self, monkeypatch):
        # ARRANGE — removal raises (panel was never registered)
        from custom_components.pricehawk import dashboard_config

        frontend = MagicMock()
        frontend.async_remove_panel = MagicMock(side_effect=RuntimeError("not found"))
        monkeypatch.setitem(sys.modules, "homeassistant.components.frontend", frontend)

        hass = MagicMock()

        # ACT — must not raise
        _run(dashboard_config.remove_panel(hass))


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
