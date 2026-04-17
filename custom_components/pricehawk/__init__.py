"""PriceHawk integration - compare Amber Electric vs GloBird Energy costs."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AMBER_NETWORK_DAILY_CHARGE,
    CONF_AMBER_SUBSCRIPTION_FEE,
    DOMAIN,
)
from .coordinator import PriceHawkCoordinator
from .dashboard_config import (
    copy_www_assets,
    remove_panel,
    setup_panel_iframe,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PriceHawk from a config entry."""
    _LOGGER.info("Setting up PriceHawk integration (entry=%s)", entry.entry_id)
    hass.data.setdefault(DOMAIN, {})

    coordinator = PriceHawkCoordinator(hass, entry)
    await coordinator.async_restore_state()
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Schedule periodic state persistence
    coordinator.schedule_persist()

    # Copy www assets (icon + HTML) and register sidebar panel
    await copy_www_assets(hass)
    await setup_panel_iframe(hass, entry)

    # OptionsFlowWithReload handles reloading automatically —
    # do NOT add an update_listener here (HA 2026.3+ forbids combining them).

    # Register CSV analysis service
    async def handle_analyze_csv(call: object) -> None:
        """Handle the analyze_csv service call from dashboard.

        Accepts pre-parsed CSV rows from the dashboard JavaScript and runs
        them through the user's CONFIGURED tariff rates (not plan defaults).
        """
        rows = call.data.get("rows", [])  # type: ignore[attr-defined]
        if not rows:
            _LOGGER.error("No CSV rows provided to analyze_csv service")
            return

        options = dict(entry.options)
        network_daily_c = options.get(CONF_AMBER_NETWORK_DAILY_CHARGE, 0.0)
        subscription_daily_c = options.get(CONF_AMBER_SUBSCRIPTION_FEE, 0.0)

        from .csv_analyzer import analyze_csv_data  # noqa: PLC0415

        result = await hass.async_add_executor_job(
            analyze_csv_data, rows, options, network_daily_c, subscription_daily_c
        )

        # Store in coordinator for dashboard access via entity attributes
        coordinator.data["csv_comparison"] = result
        coordinator.async_set_updated_data(coordinator.data)

        _LOGGER.info(
            "CSV analysis complete: %s saves $%.2f",
            result.get("savings_direction", "unknown"),
            result.get("savings_aud", 0.0),
        )

    hass.services.async_register(DOMAIN, "analyze_csv", handle_analyze_csv)

    _LOGGER.info("PriceHawk integration setup complete")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading PriceHawk integration (entry=%s)", entry.entry_id)
    coordinator: PriceHawkCoordinator | None = hass.data[DOMAIN].pop(
        entry.entry_id, None
    )
    if coordinator:
        coordinator.cancel_persist()
        await coordinator.async_persist_state()

    await remove_panel(hass)

    # Unregister service if no more entries remain
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, "analyze_csv")

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
