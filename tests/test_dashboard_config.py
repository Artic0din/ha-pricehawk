"""Tests for ``custom_components.pricehawk.dashboard_config``."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.pricehawk import dashboard_config


def _run(coro):
    return asyncio.run(coro)


class TestCopyWwwAssets:
    def test_creates_dest_dir_and_writes_svg_icon(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "www" / "pricehawk")

        async def _exec(func):
            func()

        hass.async_add_executor_job = _exec

        _run(dashboard_config.copy_www_assets(hass))

        dest = tmp_path / "www" / "pricehawk"
        assert dest.is_dir()
        icon_svg = dest / "icon.svg"
        assert icon_svg.exists()
        assert "<svg" in icon_svg.read_text()


class TestGenerateDashboardConfig:
    def test_generate_config_structure(self):
        coordinator = MagicMock()
        coordinator.data = {
            "providers": {
                "amber": {"name": "Amber Electric"},
                "flow_power": {"name": "Flow Power"},
            },
            "current_plan_name": "My GloBird Plan",
        }
        coordinator._current_plan_provider.id = "globird"

        config = dashboard_config.generate_dashboard_config(coordinator)

        assert "views" in config
        view = config["views"][0]
        assert view["title"] == "PriceHawk"
        assert view["path"] == "pricehawk"
        assert view["type"] == "sections"

        sections = view["sections"]
        titles = [s["title"] for s in sections]
        assert "Today's Cost" in titles
        assert "Comparison" in titles
        assert "Current Rates" in titles
        assert "My GloBird Plan Breakdown" in titles
        assert "Amber Electric Breakdown" in titles
        assert "Flow Power Breakdown" not in titles
        assert "Status" in titles
        assert "Cost History" in titles
        assert "Monthly Trend" in titles


class TestLovelaceDashboardSetupRemoval:
    def test_setup_dashboard_already_registered(self):
        mock_frontend = MagicMock()
        mock_store = MagicMock()
        mock_lovelace_storage = MagicMock()

        mock_storage_module = MagicMock()
        mock_storage_module.Store = mock_store

        mock_dashboard_module = MagicMock()
        mock_dashboard_module.LovelaceStorage = mock_lovelace_storage

        mock_const_module = MagicMock()
        mock_const_module.MODE_STORAGE = "storage"

        mock_components_module = MagicMock()
        mock_components_module.frontend = mock_frontend

        sys_modules_patch = {
            "homeassistant.helpers.storage": mock_storage_module,
            "homeassistant.components.lovelace.dashboard": mock_dashboard_module,
            "homeassistant.components.lovelace.const": mock_const_module,
            "homeassistant.components": mock_components_module,
            "homeassistant.components.frontend": mock_frontend,
        }

        with (
            patch.dict("sys.modules", sys_modules_patch),
            patch("custom_components.pricehawk.dashboard_config.copy_www_assets", new=AsyncMock()),
        ):
            hass = MagicMock()
            coordinator = MagicMock()
            coordinator.data = {"providers": {}}
            coordinator._current_plan_provider.id = "globird"

            # Mock Store instance
            store_inst = MagicMock()
            store_inst.async_load = AsyncMock(return_value={"items": [{"url_path": "pricehawk"}]})
            store_inst.async_save = MagicMock()
            mock_store.return_value = store_inst

            # Mock LovelaceStorage instance
            db_store = MagicMock()
            db_store.async_load = AsyncMock(return_value=None)
            db_store.async_save = AsyncMock()
            mock_lovelace_storage.return_value = db_store

            ll_data = MagicMock()
            ll_data.dashboards = {"pricehawk": db_store}
            hass.data = {"lovelace": ll_data}

            # Mock async_add_executor_job to run the inline strings loader
            async def mock_executor(func, *args, **kwargs):
                return func(*args, **kwargs)

            hass.async_add_executor_job = mock_executor

            _run(dashboard_config.setup_lovelace_dashboard(hass, coordinator))

            store_inst.async_save.assert_not_called()
            db_store.async_save.assert_called_once()
            mock_frontend.async_register_built_in_panel.assert_called_once()

    def test_setup_dashboard_creates_if_missing(self):
        mock_frontend = MagicMock()
        mock_store = MagicMock()
        mock_lovelace_storage = MagicMock()

        mock_storage_module = MagicMock()
        mock_storage_module.Store = mock_store

        mock_dashboard_module = MagicMock()
        mock_dashboard_module.LovelaceStorage = mock_lovelace_storage

        mock_const_module = MagicMock()
        mock_const_module.MODE_STORAGE = "storage"

        mock_components_module = MagicMock()
        mock_components_module.frontend = mock_frontend

        sys_modules_patch = {
            "homeassistant.helpers.storage": mock_storage_module,
            "homeassistant.components.lovelace.dashboard": mock_dashboard_module,
            "homeassistant.components.lovelace.const": mock_const_module,
            "homeassistant.components": mock_components_module,
            "homeassistant.components.frontend": mock_frontend,
        }

        with (
            patch.dict("sys.modules", sys_modules_patch),
            patch("custom_components.pricehawk.dashboard_config.copy_www_assets", new=AsyncMock()),
        ):
            hass = MagicMock()
            coordinator = MagicMock()
            coordinator.data = {"providers": {}}
            coordinator._current_plan_provider.id = "globird"

            # Mock Store instance
            store_inst = MagicMock()
            store_inst.async_load = AsyncMock(return_value={"items": []})
            store_inst.async_save = AsyncMock()
            mock_store.return_value = store_inst

            # Mock LovelaceStorage instance
            db_store = MagicMock()
            db_store.async_load = AsyncMock(return_value=None)
            db_store.async_save = AsyncMock()
            mock_lovelace_storage.return_value = db_store

            ll_data = MagicMock()
            ll_data.dashboards = {}
            hass.data = {"lovelace": ll_data}

            # Mock async_add_executor_job to run the inline strings loader
            async def mock_executor(func, *args, **kwargs):
                return func(*args, **kwargs)

            hass.async_add_executor_job = mock_executor

            _run(dashboard_config.setup_lovelace_dashboard(hass, coordinator))

            store_inst.async_save.assert_called_once()
            assert ll_data.dashboards["pricehawk"] == db_store
            db_store.async_save.assert_called_once()
            mock_frontend.async_register_built_in_panel.assert_called_once()

    def test_setup_dashboard_skips_if_not_managed(self):
        mock_frontend = MagicMock()
        mock_store = MagicMock()
        mock_lovelace_storage = MagicMock()

        mock_storage_module = MagicMock()
        mock_storage_module.Store = mock_store

        mock_dashboard_module = MagicMock()
        mock_dashboard_module.LovelaceStorage = mock_lovelace_storage

        mock_const_module = MagicMock()
        mock_const_module.MODE_STORAGE = "storage"

        mock_components_module = MagicMock()
        mock_components_module.frontend = mock_frontend

        sys_modules_patch = {
            "homeassistant.helpers.storage": mock_storage_module,
            "homeassistant.components.lovelace.dashboard": mock_dashboard_module,
            "homeassistant.components.lovelace.const": mock_const_module,
            "homeassistant.components": mock_components_module,
            "homeassistant.components.frontend": mock_frontend,
        }

        with (
            patch.dict("sys.modules", sys_modules_patch),
            patch("custom_components.pricehawk.dashboard_config.copy_www_assets", new=AsyncMock()),
        ):
            hass = MagicMock()
            coordinator = MagicMock()
            coordinator.data = {"providers": {}}
            coordinator._current_plan_provider.id = "globird"

            # Mock Store instance
            store_inst = MagicMock()
            store_inst.async_load = AsyncMock(return_value={"items": [{"url_path": "pricehawk"}]})
            store_inst.async_save = AsyncMock()
            mock_store.return_value = store_inst

            # Mock LovelaceStorage instance with an unmanaged user-customized dashboard
            db_store = MagicMock()
            db_store.async_load = AsyncMock(return_value={"views": [{"title": "User Dashboard"}]})
            db_store.async_save = AsyncMock()
            mock_lovelace_storage.return_value = db_store

            ll_data = MagicMock()
            ll_data.dashboards = {"pricehawk": db_store}
            hass.data = {"lovelace": ll_data}

            # Mock async_add_executor_job
            async def mock_executor(func, *args, **kwargs):
                return func(*args, **kwargs)

            hass.async_add_executor_job = mock_executor

            _run(dashboard_config.setup_lovelace_dashboard(hass, coordinator))

            # It should skip saving (auto-updating) to protect user changes
            db_store.async_save.assert_not_called()

    def test_remove_dashboard(self):
        mock_frontend = MagicMock()
        mock_store = MagicMock()
        mock_lovelace_storage = MagicMock()
        mock_lovelace_storage.return_value.async_load = AsyncMock(
            return_value={"pricehawk_managed": True}
        )

        mock_storage_module = MagicMock()
        mock_storage_module.Store = mock_store

        mock_dashboard_module = MagicMock()
        mock_dashboard_module.LovelaceStorage = mock_lovelace_storage

        mock_components_module = MagicMock()
        mock_components_module.frontend = mock_frontend

        sys_modules_patch = {
            "homeassistant.helpers.storage": mock_storage_module,
            "homeassistant.components.lovelace.dashboard": mock_dashboard_module,
            "homeassistant.components": mock_components_module,
            "homeassistant.components.frontend": mock_frontend,
        }

        with patch.dict("sys.modules", sys_modules_patch):
            hass = MagicMock()

            # Mock Store instance
            store_inst = MagicMock()
            store_inst.async_load = AsyncMock(return_value={"items": [{"url_path": "pricehawk"}]})
            store_inst.async_save = AsyncMock()
            mock_store.return_value = store_inst

            ll_data = MagicMock()
            ll_data.dashboards = {"pricehawk": MagicMock()}
            hass.data = {"lovelace": ll_data}

            _run(dashboard_config.remove_lovelace_dashboard(hass))

            assert "pricehawk" not in ll_data.dashboards
            store_inst.async_save.assert_called_once_with({"items": []})
            mock_frontend.async_remove_panel.assert_called_once_with(hass, "pricehawk")


def test_find_existing_store_scans_collection_by_url_path() -> None:
    hass = MagicMock()

    class MockCollection:
        def __init__(self, items):
            self._items = items

        def __contains__(self, key):
            return key == "pricehawk_2"

        def async_items(self):
            return self._items

        def __getitem__(self, key):
            if key == "pricehawk_2":
                return "store_instance"
            raise KeyError(key)

    items = [{"id": "pricehawk_2", "url_path": "pricehawk"}]
    collection = MockCollection(items)

    res = dashboard_config._find_existing_store(hass, collection, "pricehawk")
    assert res == "store_instance"
