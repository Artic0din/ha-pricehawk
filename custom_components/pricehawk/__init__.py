"""PriceHawk integration - compare Amber Electric vs GloBird Energy costs."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
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

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
