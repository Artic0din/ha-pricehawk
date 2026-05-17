"""PriceHawk integration - compare Amber Electric vs GloBird Energy costs."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AMBER_NETWORK_DAILY_CHARGE,
    CONF_AMBER_SUBSCRIPTION_FEE,
    CONF_GRID_POWER_SENSOR,
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

    # Phase 3.1 — schedule daily multi-plan ranking job at 00:30 local.
    # First run also fires immediately so the alternatives sensor isn't
    # empty until midnight on a fresh install.
    coordinator.schedule_daily_ranking()
    hass.async_create_task(coordinator.async_run_ranking_job())

    # Phase 3.2 — kick off the universal HA-history backfill once,
    # AFTER the first ranking job finishes so the plan-set includes
    # the top-K alternatives (otherwise the first backfill would only
    # carry the current plan's column). Reuses ``_ranking_lock`` so
    # we never race the ranking job that's mutating
    # ``_daily_cost_history`` from the daily rollover path.
    async def _backfill_after_ranking() -> None:
        # Wait for the first ranking run to release the lock — at that
        # point the alternatives list is populated and the plan cache
        # has the full bodies needed for the evaluator replay.
        async with coordinator._ranking_lock:
            pass
        await coordinator.async_run_backfill(days_back=30)

    hass.async_create_task(_backfill_after_ranking())

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
        """Backfill daily cost history from HA recorder via multi-plan replay.

        Phase 3.2 commit 2: Amber-API-specific backfill removed. The
        service now runs the universal HA-history backfill against the
        user's current CDR plan (Phase 3.2 commit 3 adds top-K ranked
        alternatives via a coordinator helper; commit 4 shrinks this
        handler to a one-line delegate).
        """
        days_back = call.data.get("days", 30)  # type: ignore[attr-defined]
        days_back = max(1, min(days_back, 90))  # Clamp to 1-90

        grid_sensor = entry.options.get(CONF_GRID_POWER_SENSOR, "")
        if not grid_sensor:
            _LOGGER.error("No grid sensor configured — cannot backfill")
            return

        cdr_plan = entry.options.get("cdr_plan") or {}
        plan_data = cdr_plan.get("data") if isinstance(cdr_plan, dict) else None
        if not isinstance(plan_data, dict):
            _LOGGER.error("No CDR plan in options — cannot backfill")
            return

        from .backfill import backfill_daily_cost_history  # noqa: PLC0415

        current_plan_id = coordinator._current_plan_provider.id
        plans: dict[str, dict] = {current_plan_id: plan_data}

        existing = coordinator.data.get("daily_cost_history", [])
        result = await backfill_daily_cost_history(
            hass,
            grid_sensor,
            plans,
            days_back=days_back,
            entry_options=dict(entry.options),
            existing_history=list(existing),
        )

        coordinator._daily_cost_history = result
        coordinator.data["daily_cost_history"] = result
        coordinator.async_set_updated_data(coordinator.data)
        await coordinator.async_persist_state()

        _LOGGER.info("Backfill complete: %d days of history", len(result))

    hass.services.async_register(DOMAIN, "backfill_history", handle_backfill)

    # Phase 3.1 commit 5 — manual ranking trigger. Lets users force-run
    # the ranking pipeline from Developer Tools → Services without
    # waiting for the next 00:30 schedule fire. Most useful right after
    # switching plans (so the alternatives ranking reflects the new
    # distributor / postcode immediately).
    async def handle_rank_alternatives(call: object) -> None:
        # CR-fix: malformed service payload (e.g. ``top_k: "abc"`` from
        # a typo in a YAML automation) would raise ValueError/TypeError
        # and fail the call. Coerce defensively + fall back to default.
        raw = call.data.get("top_k", 20)  # type: ignore[attr-defined]
        try:
            top_k = int(raw)
        except (TypeError, ValueError):
            _LOGGER.warning(
                "rank_alternatives: invalid top_k=%r, using default 20", raw
            )
            top_k = 20
        top_k = max(1, min(top_k, 100))
        result = await coordinator.async_run_ranking_job(top_k=top_k)
        _LOGGER.info(
            "rank_alternatives service: ran successfully, %d result(s)",
            len(result),
        )

    hass.services.async_register(
        DOMAIN, "rank_alternatives", handle_rank_alternatives
    )

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
        coordinator.cancel_ranking()
        await coordinator.async_persist_state()

    await remove_panel(hass)

    # Unregister services if no more entries remain
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, "analyze_csv")
        hass.services.async_remove(DOMAIN, "backfill_history")
        hass.services.async_remove(DOMAIN, "rank_alternatives")

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
