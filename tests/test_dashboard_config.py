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
        assert "Flow Power Breakdown" in titles
        assert "Status" in titles
        assert "Cost History" in titles
        assert "Monthly Trend" in titles


class TestLovelaceDashboardSetupRemoval:
    @patch("homeassistant.helpers.storage.Store")
    @patch("homeassistant.components.lovelace.dashboard.LovelaceStorage")
    @patch("homeassistant.components.frontend")
    def test_setup_dashboard_already_registered(
        self, mock_frontend, mock_lovelace_storage, mock_store
    ):
        hass = MagicMock()
        coordinator = MagicMock()
        coordinator.data = {"providers": {}}
        coordinator._current_plan_provider.id = "globird"

        # Mock Store
        store_inst = MagicMock()
        store_inst.async_load = AsyncMock(return_value={"items": [{"url_path": "pricehawk"}]})
        store_inst.async_save = AsyncMock()
        mock_store.return_value = store_inst

        # Mock LovelaceStorage
        db_store = MagicMock()
        db_store.async_save = AsyncMock()
        mock_lovelace_storage.return_value = db_store

        ll_data = MagicMock()
        ll_data.dashboards = {"pricehawk": db_store}
        hass.data = {"lovelace": ll_data}

        _run(dashboard_config.setup_lovelace_dashboard(hass, coordinator))

        store_inst.async_save.assert_not_called()
        db_store.async_save.assert_called_once()
        mock_frontend.async_register_built_in_panel.assert_called_once()

    @patch("homeassistant.helpers.storage.Store")
    @patch("homeassistant.components.lovelace.dashboard.LovelaceStorage")
    @patch("homeassistant.components.frontend")
    def test_setup_dashboard_creates_if_missing(
        self, mock_frontend, mock_lovelace_storage, mock_store
    ):
        hass = MagicMock()
        coordinator = MagicMock()
        coordinator.data = {"providers": {}}
        coordinator._current_plan_provider.id = "globird"

        # Mock Store
        store_inst = MagicMock()
        store_inst.async_load = AsyncMock(return_value={"items": []})
        store_inst.async_save = AsyncMock()
        mock_store.return_value = store_inst

        # Mock LovelaceStorage
        db_store = MagicMock()
        db_store.async_save = AsyncMock()
        mock_lovelace_storage.return_value = db_store

        ll_data = MagicMock()
        ll_data.dashboards = {}
        hass.data = {"lovelace": ll_data}

        _run(dashboard_config.setup_lovelace_dashboard(hass, coordinator))

        store_inst.async_save.assert_called_once()
        assert ll_data.dashboards["pricehawk"] == db_store
        db_store.async_save.assert_called_once()
        mock_frontend.async_register_built_in_panel.assert_called_once()

    @patch("homeassistant.helpers.storage.Store")
    @patch("homeassistant.components.frontend")
    def test_remove_dashboard(self, mock_frontend, mock_store):
        hass = MagicMock()

        # Mock Store
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
