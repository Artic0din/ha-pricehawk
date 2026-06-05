"""Lovelace dashboard configuration and programmatic registration for PriceHawk."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Inline SVG icon (PriceHawk hawk logo)
PRICEHAWK_ICON_SVG = """\
<svg width="512" height="512" viewBox="0 0 512 512" xmlns="http://www.w3.org/2000/svg">
  <rect width="512" height="512" rx="108" fill="#111111"/>
  <path d="M 100 200 C 130 130, 300 95, 390 145 C 425 165, 415 215, 370 228 L 285 242 L 308 182 C 265 162, 195 168, 162 202 Z" fill="#FF8C00"/>
  <path d="M 285 242 L 232 325 L 272 325 L 195 435 L 342 300 L 294 300 L 370 228 Z" fill="#FFD600"/>
  <path d="M 285 242 L 294 300 L 370 228 Z" fill="#FFB300"/>
</svg>
"""


async def copy_www_assets(hass: HomeAssistant) -> None:
    """Copy PriceHawk icon SVG and PNG to www/pricehawk directory.

    Retained for integration branding and dashboard icon usage.
    """
    src_dir = os.path.dirname(__file__)
    src_icon_png = os.path.join(src_dir, "icon.png")
    dest_dir = hass.config.path("www", "pricehawk")
    icon_svg_path = os.path.join(dest_dir, "icon.svg")
    icon_png_path = os.path.join(dest_dir, "icon.png")

    def _copy_assets() -> None:
        import shutil

        os.makedirs(dest_dir, exist_ok=True)
        # Write SVG icon
        with open(icon_svg_path, "w", encoding="utf-8") as f:
            f.write(PRICEHAWK_ICON_SVG)
        # Copy PNG icon
        if os.path.exists(src_icon_png):
            shutil.copy2(src_icon_png, icon_png_path)

    try:
        await hass.async_add_executor_job(_copy_assets)
        _LOGGER.info("PriceHawk: www assets copied to %s", dest_dir)
    except Exception:  # noqa: BLE001
        _LOGGER.warning("PriceHawk: could not copy www assets to %s", dest_dir, exc_info=True)


def generate_dashboard_config(
    coordinator: Any, dashboard_strings: dict[str, str] | None = None
) -> dict[str, Any]:
    """Generate the Lovelace dashboard configuration dynamically.

    Constructs a native Sections layout based on active comparators.
    """
    if dashboard_strings is None:
        dashboard_strings = {}

    providers = coordinator.data.get("providers", {}) if coordinator.data else {}
    if not providers:
        # Fallback to coordinator's active providers dict
        providers = {
            pid: {"name": p.name} for pid, p in getattr(coordinator, "_providers", {}).items()
        }

    current_plan_id = (
        coordinator._current_plan_provider.id
        if hasattr(coordinator, "_current_plan_provider")
        else None
    )

    sections = []

    # 1. Today's Cost section
    cost_cards = []
    # Current plan cost card
    current_name = coordinator.data.get("current_plan_name") if coordinator.data else None
    if not current_name and hasattr(coordinator, "_current_plan_provider"):
        current_name = coordinator._current_plan_provider.name
    current_name = current_name or "Current Plan"

    cost_cards.append(
        {
            "type": "tile",
            "entity": "sensor.pricehawk_current_plan_cost_today",
            "name": current_name,
            "color": "pink",
            "icon": "mdi:lightning-bolt",
        }
    )

    # Comparator cost cards
    for pid, p_info in providers.items():
        if pid == current_plan_id or pid == "named":
            continue
        color_map = {"amber": "green", "flow_power": "orange", "localvolts": "blue"}
        cost_cards.append(
            {
                "type": "tile",
                "entity": f"sensor.pricehawk_{pid}_cost_today",
                "name": p_info.get("name", pid.title()),
                "color": color_map.get(pid, "indigo"),
                "icon": "mdi:lightning-bolt",
            }
        )

    # Named comparator cost card
    if "named" in providers:
        named_name = "Pinned Plan"
        if hasattr(coordinator, "_named_comparator") and coordinator._named_comparator:
            named_name = coordinator._named_comparator.name
        cost_cards.append(
            {
                "type": "tile",
                "entity": "sensor.pricehawk_named_comparator_cost_today",
                "name": named_name,
                "color": "purple",
                "icon": "mdi:lightning-bolt",
            }
        )

    sections.append(
        {
            "type": "grid",
            "title": dashboard_strings.get("title_todays_cost", "Today's Cost"),
            "cards": cost_cards,
        }
    )

    # 2. Comparison section
    sections.append(
        {
            "type": "grid",
            "title": dashboard_strings.get("title_comparison", "Comparison"),
            "cards": [
                {
                    "type": "tile",
                    "entity": "sensor.pricehawk_saving_today",
                    "name": "Difference Today",
                    "color": "green",
                    "icon": "mdi:swap-horizontal",
                },
                {
                    "type": "tile",
                    "entity": "sensor.pricehawk_saving_month",
                    "name": "Difference This Month",
                    "color": "green",
                    "icon": "mdi:calendar-month",
                },
                {
                    "type": "entity",
                    "entity": "sensor.pricehawk_best_provider",
                    "name": "Best Provider Now",
                    "icon": "mdi:trophy",
                },
                {
                    "type": "entity",
                    "entity": "sensor.pricehawk_metrics_won",
                    "name": "Metrics Won",
                    "icon": "mdi:chart-bar",
                },
                {
                    "type": "entity",
                    "entity": "sensor.pricehawk_winner_explanation",
                    "name": "Winner Explanation",
                    "icon": "mdi:information",
                },
            ],
        }
    )

    # 3. Current Rates section
    rate_cards = []
    # Current plan rates
    rate_cards.append(
        {
            "type": "tile",
            "entity": "sensor.pricehawk_current_plan_import_rate",
            "name": f"{current_name} Import",
            "color": "pink",
            "icon": "mdi:lightning-bolt",
        }
    )
    rate_cards.append(
        {
            "type": "tile",
            "entity": "sensor.pricehawk_current_plan_export_rate",
            "name": f"{current_name} Feed-in",
            "color": "pink",
            "icon": "mdi:solar-power",
        }
    )

    # Comparator rates
    for pid, p_info in providers.items():
        if pid == current_plan_id or pid == "named":
            continue
        p_name = p_info.get("name", pid.title())
        color_map = {"amber": "green", "flow_power": "orange", "localvolts": "blue"}
        color = color_map.get(pid, "indigo")
        rate_cards.append(
            {
                "type": "tile",
                "entity": f"sensor.pricehawk_{pid}_import_rate",
                "name": f"{p_name} Import",
                "color": color,
                "icon": "mdi:lightning-bolt",
            }
        )
        rate_cards.append(
            {
                "type": "tile",
                "entity": f"sensor.pricehawk_{pid}_export_rate",
                "name": f"{p_name} Feed-in",
                "color": color,
                "icon": "mdi:solar-power",
            }
        )

    sections.append(
        {
            "type": "grid",
            "title": dashboard_strings.get("title_current_rates", "Current Rates"),
            "cards": rate_cards,
        }
    )

    # 4. Breakdowns
    # Current Plan breakdown
    sections.append(
        {
            "type": "grid",
            "title": dashboard_strings.get("title_breakdown", "{name} Breakdown").format(
                name=current_name
            ),
            "cards": [
                {
                    "type": "entity",
                    "entity": "sensor.pricehawk_current_plan_import_cost",
                    "name": "Import Charges",
                    "icon": "mdi:cart",
                },
                {
                    "type": "entity",
                    "entity": "sensor.pricehawk_current_plan_export_credit",
                    "name": "Export Credit",
                    "icon": "mdi:cash-refund",
                },
                {
                    "type": "entity",
                    "entity": "sensor.pricehawk_current_plan_daily_supply",
                    "name": "Daily Supply",
                    "icon": "mdi:calendar-today",
                },
            ],
        }
    )

    # Comparator breakdowns (only Amber has breakdown entities)
    for pid, p_info in providers.items():
        if pid == current_plan_id or pid == "named":
            continue
        if pid != "amber":
            continue
        p_name = p_info.get("name", pid.title())
        import_cost_entity = "sensor.pricehawk_amber_import_cost"
        export_credit_entity = "sensor.pricehawk_amber_export_credit"
        daily_supply_entity = "sensor.pricehawk_amber_daily_charges"

        sections.append(
            {
                "type": "grid",
                "title": dashboard_strings.get("title_breakdown", "{name} Breakdown").format(
                    name=p_name
                ),
                "cards": [
                    {
                        "type": "entity",
                        "entity": import_cost_entity,
                        "name": "Import Charges",
                        "icon": "mdi:cart",
                    },
                    {
                        "type": "entity",
                        "entity": export_credit_entity,
                        "name": "Export Credit",
                        "icon": "mdi:cash-refund",
                    },
                    {
                        "type": "entity",
                        "entity": daily_supply_entity,
                        "name": "Daily Supply",
                        "icon": "mdi:calendar-today",
                    },
                ],
            }
        )

    # 5. Status section
    status_cards = [
        {
            "type": "entity",
            "entity": "sensor.pricehawk_last_updated",
            "name": "Last Updated",
            "icon": "mdi:clock-outline",
        }
    ]
    if coordinator.data and coordinator.data.get("current_plan_zerohero_status") is not None:
        status_cards.append(
            {
                "type": "entity",
                "entity": "sensor.pricehawk_zerohero_status",
                "name": "ZeroHero Status",
                "icon": "mdi:lightning-bolt-circle",
            }
        )
    status_cards.append(
        {
            "type": "entity",
            "entity": "sensor.pricehawk_backfill_status",
            "name": "History Backfill Status",
            "icon": "mdi:history",
        }
    )

    sections.append(
        {
            "type": "grid",
            "title": dashboard_strings.get("title_status", "Status"),
            "cards": status_cards,
        }
    )

    # 6. Cost History Graph (7 days)
    history_entities = []
    # Current plan daily cost
    history_entities.append(
        {
            "entity": "sensor.pricehawk_current_plan_cost_today",
            "name": f"{current_name} Cost",
        }
    )
    # Comparator daily costs
    for pid, p_info in providers.items():
        if pid == current_plan_id or pid == "amber":
            # Amber daily cost is represented by sensor.pricehawk_amber_cost_today
            continue
        if pid == "named":
            # Pinned Named comparator today rollup
            history_entities.append(
                {
                    "entity": "sensor.pricehawk_named_comparator_cost_today",
                    "name": f"{p_info.get('name', 'Pinned Plan')} Cost",
                }
            )
            continue
        history_entities.append(
            {
                "entity": f"sensor.pricehawk_{pid}_cost_today",
                "name": f"{p_info.get('name', pid.title())} Cost",
            }
        )

    if "amber" in providers:
        history_entities.append(
            {
                "entity": "sensor.pricehawk_amber_cost_today",
                "name": "Amber Cost",
            }
        )

    sections.append(
        {
            "type": "grid",
            "title": dashboard_strings.get("title_cost_history", "Cost History"),
            "cards": [
                {
                    "type": "statistics-graph",
                    "entities": history_entities,
                    "period": "day",
                    "stat_types": ["change"],
                    "days_to_show": 7,
                }
            ],
        }
    )

    # 7. Monthly Trend Graph
    sections.append(
        {
            "type": "grid",
            "title": dashboard_strings.get("title_monthly_trend", "Monthly Trend"),
            "cards": [
                {
                    "type": "statistics-graph",
                    "entities": [
                        {
                            "entity": "sensor.pricehawk_saving_month",
                            "name": "Monthly Difference",
                        }
                    ],
                    "period": "day",
                    "stat_types": ["state"],
                    "days_to_show": 30,
                }
            ],
        }
    )

    return {
        "views": [
            {
                "title": "PriceHawk",
                "path": "pricehawk",
                "icon": "mdi:flash",
                "type": "sections",
                "max_columns": 2,
                "sections": sections,
            }
        ]
    }


async def setup_lovelace_dashboard(hass: HomeAssistant, coordinator: Any) -> None:
    """Register the PriceHawk dashboard natively in Lovelace.

    Creates the dashboard config, registers it in the lovelace_dashboards store,
    sets up the LovelaceStorage object, registers the frontend panel, and saves
    the dynamically generated sections configuration.
    """
    try:
        from homeassistant.helpers.storage import Store
        from homeassistant.components.lovelace.dashboard import LovelaceStorage
        from homeassistant.components.lovelace.const import MODE_STORAGE
        from homeassistant.components import frontend
    except ImportError:
        _LOGGER.warning(
            "PriceHawk dashboard: Lovelace core components not available; skipping setup."
        )
        return

    # Check if storage registry is available
    ll_data = hass.data.get("lovelace")
    if ll_data is None:
        _LOGGER.warning("PriceHawk dashboard: Lovelace storage data not available; skipping setup.")
        return

    # Load strings.json asynchronously via executor
    def _load_strings() -> dict[str, Any]:
        try:
            strings_path = os.path.join(os.path.dirname(__file__), "strings.json")
            with open(strings_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("PriceHawk dashboard: failed to load strings.json for localization")
            return {}

    strings_data = await hass.async_add_executor_job(_load_strings)
    dashboard_strings = strings_data.get("dashboard", {})

    await copy_www_assets(hass)

    url_path = "pricehawk"
    dashboard_item = {
        "id": url_path,
        "url_path": url_path,
        "title": "PriceHawk",
        "icon": "mdi:flash",
        "show_in_sidebar": True,
        "require_admin": False,
        "mode": "storage",
    }

    # 1. Persist to lovelace_dashboards store
    try:
        store = Store(hass, 1, "lovelace_dashboards")
        data = await store.async_load()
        items = data.get("items", []) if data else []

        if not any(item.get("url_path") == url_path for item in items):
            items.append(dashboard_item)
            await store.async_save({"items": items})
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "PriceHawk dashboard: failed to save to lovelace_dashboards store", exc_info=True
        )
        return

    # 2. Get dashboards mapping from ll_data. Either dict-like or attribute access
    dashboards = getattr(ll_data, "dashboards", None)
    if dashboards is None:
        _LOGGER.warning("PriceHawk dashboard: dashboards registry not available; skipping setup.")
        return

    if url_path not in dashboards:
        _LOGGER.info("PriceHawk dashboard: registering under path /%s", url_path)
        try:
            lovelace_store = LovelaceStorage(hass, dashboard_item)
            dashboards[url_path] = lovelace_store
        except Exception:  # noqa: BLE001
            _LOGGER.exception("PriceHawk dashboard: failed to create LovelaceStorage")
            return
    else:
        lovelace_store = dashboards[url_path]

    # 3. Register frontend panel for immediate use (without HA restart)
    try:
        # Clean up legacy panel entries to prevent duplicate sidebar items
        for legacy_path in ("pricehawk-dashboard", "pricehawk_custom", "pricehawk"):
            try:
                frontend.async_remove_panel(hass, legacy_path)
            except Exception:  # noqa: BLE001, S110
                pass

        frontend.async_register_built_in_panel(
            hass,
            "lovelace",
            frontend_url_path=url_path,
            sidebar_title="PriceHawk",
            sidebar_icon="mdi:flash",
            config={"mode": MODE_STORAGE},
            require_admin=False,
        )
    except Exception:  # noqa: BLE001
        _LOGGER.warning("PriceHawk dashboard: failed to register built-in panel", exc_info=True)

    # 4. Overwrite the dashboard config dynamically based on current coordinator providers
    try:
        from homeassistant.components.lovelace.const import ConfigNotFound

        try:
            existing_config = await lovelace_store.async_load(force=False)
        except ConfigNotFound:
            existing_config = None

        if existing_config and not existing_config.get("pricehawk_managed"):
            _LOGGER.info(
                "PriceHawk dashboard: existing user-customized dashboard found at /%s; skipping auto-update to protect changes",
                url_path,
            )
        else:
            config = generate_dashboard_config(coordinator, dashboard_strings)
            config["pricehawk_managed"] = True
            await lovelace_store.async_save(config)
            _LOGGER.info("PriceHawk dashboard: updated configuration dynamically")
    except Exception:  # noqa: BLE001
        _LOGGER.exception("PriceHawk dashboard: failed to save configuration to store")


async def remove_lovelace_dashboard(hass: HomeAssistant) -> None:
    """Unregister the PriceHawk dashboard from Lovelace on unload."""
    try:
        from homeassistant.helpers.storage import Store
        from homeassistant.components import frontend
        from homeassistant.components.lovelace.dashboard import LovelaceStorage
    except ImportError:
        return

    url_path = "pricehawk"
    dashboard_item = {"id": url_path, "mode": "storage"}
    try:
        lovelace_store = LovelaceStorage(hass, dashboard_item)
        config = await lovelace_store.async_load(force=False)
    except Exception:  # noqa: BLE001
        config = None

    if config is not None and not config.get("pricehawk_managed"):
        _LOGGER.info(
            "PriceHawk dashboard: leaving user-customized dashboard at /%s intact on unload",
            url_path,
        )
        return

    ll_data = hass.data.get("lovelace")
    if ll_data is None:
        return

    dashboards = getattr(ll_data, "dashboards", None)

    if dashboards is not None and url_path in dashboards:
        _LOGGER.info("PriceHawk dashboard: removing from Lovelace registry")
        dashboards.pop(url_path, None)

    # Remove from lovelace_dashboards store
    try:
        store = Store(hass, 1, "lovelace_dashboards")
        data = await store.async_load()
        items = data.get("items", []) if data else []

        new_items = [item for item in items if item.get("url_path") != url_path]
        if len(new_items) != len(items):
            await store.async_save({"items": new_items})
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "PriceHawk dashboard: failed to remove from lovelace_dashboards store", exc_info=True
        )

    # Remove frontend panel
    try:
        frontend.async_remove_panel(hass, url_path)
    except Exception:  # noqa: BLE001
        _LOGGER.warning("PriceHawk dashboard: failed to remove panel", exc_info=True)
