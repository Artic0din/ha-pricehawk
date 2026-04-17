"""PriceHawk integration - compare Amber Electric vs GloBird Energy costs."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AMBER_NETWORK_DAILY_CHARGE,
    CONF_AMBER_SUBSCRIPTION_FEE,
    CONF_API_KEY,
    CONF_GRID_POWER_SENSOR,
    CONF_SITE_ID,
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

    # Register backfill service
    async def handle_backfill(call: object) -> None:
        """Backfill daily cost history from HA recorder + Amber API."""
        days_back = call.data.get("days", 30)  # type: ignore[attr-defined]
        days_back = max(1, min(days_back, 90))  # Clamp to 1-90

        # 1. Get grid sensor entity ID from config
        grid_sensor = entry.options.get(CONF_GRID_POWER_SENSOR, "")
        if not grid_sensor:
            _LOGGER.error("No grid sensor configured — cannot backfill")
            return

        # 2. Fetch history from HA recorder API
        from datetime import timedelta  # noqa: PLC0415

        from homeassistant.components.recorder import get_instance  # noqa: PLC0415
        from homeassistant.components.recorder.history import (  # noqa: PLC0415
            state_changes_during_period,
        )
        from homeassistant.util import dt as dt_util  # noqa: PLC0415

        end_time = dt_util.now()
        start_time = end_time - timedelta(days=days_back)

        history = await get_instance(hass).async_add_executor_job(
            state_changes_during_period,
            hass,
            start_time,
            end_time,
            grid_sensor,
        )

        if not history or grid_sensor not in history:
            _LOGGER.warning("No history found for %s", grid_sensor)
            return

        states = history[grid_sensor]

        # 3. Fetch Amber price history
        api_key = entry.data.get(CONF_API_KEY, "")
        site_id = entry.data.get(CONF_SITE_ID, "")

        if not api_key or not site_id:
            _LOGGER.error("No Amber API key or site ID configured")
            return

        from .backfill import (  # noqa: PLC0415
            backfill_from_history,
            fetch_amber_price_history,
        )

        amber_prices = await hass.async_add_executor_job(
            fetch_amber_price_history, api_key, site_id, start_time, end_time
        )

        # 4. Convert HA state objects to simple dicts
        history_data: list[dict] = []
        for state in states:
            if state.state in ("unavailable", "unknown", ""):
                continue
            try:
                history_data.append({
                    "state": float(state.state),
                    "last_changed": state.last_changed.isoformat(),
                    "unit": state.attributes.get("unit_of_measurement", "W"),
                })
            except (ValueError, TypeError):
                continue

        if not history_data:
            _LOGGER.warning("No valid states found for %s", grid_sensor)
            return

        # 5. Run backfill
        options = dict(entry.options)
        network_c = options.get(CONF_AMBER_NETWORK_DAILY_CHARGE, 0.0)
        subscription_c = options.get(CONF_AMBER_SUBSCRIPTION_FEE, 0.0)
        existing = coordinator.data.get("daily_cost_history", [])

        result = backfill_from_history(
            history_data,
            amber_prices,
            options,
            network_c,
            subscription_c,
            existing,
        )

        coordinator._daily_cost_history = result
        coordinator.data["daily_cost_history"] = result
        coordinator.async_set_updated_data(coordinator.data)
        await coordinator.async_persist_state()

        _LOGGER.info("Backfill complete: %d days of history", len(result))

    hass.services.async_register(DOMAIN, "backfill_history", handle_backfill)

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

    # Unregister services if no more entries remain
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, "analyze_csv")
        hass.services.async_remove(DOMAIN, "backfill_history")

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
