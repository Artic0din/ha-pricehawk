"""PriceHawk integration - compare Amber Electric vs GloBird Energy costs."""

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

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
from .data import PriceHawkConfigEntry, PriceHawkData

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: PriceHawkConfigEntry) -> bool:
    """Set up PriceHawk from a config entry."""
    _LOGGER.info("Setting up PriceHawk integration (entry=%s)", entry.entry_id)

    coordinator = PriceHawkCoordinator(hass, entry)
    await coordinator.async_restore_state()
    # Phase 9 PR-10 — one-shot external stats backfill from the restored
    # daily_cost_history. Must run AFTER state restore + BEFORE the first
    # refresh so the cumulative-sum tracker is warm for tick-driven pushes.
    await coordinator.async_setup_stats()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = PriceHawkData(coordinator=coordinator)

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

    # Service handlers re-resolve the coordinator from entry.runtime_data on
    # every invocation. The entry object survives OptionsFlowWithReload, but
    # the coordinator inside runtime_data is replaced — a captured closure
    # reference would silently point at the dead instance after reload.
    def _resolve_coordinator() -> PriceHawkCoordinator | None:
        data: PriceHawkData | None = getattr(entry, "runtime_data", None)
        return data.coordinator if data is not None else None

    # Register CSV analysis service
    async def handle_analyze_csv(call: object) -> None:
        """Handle the analyze_csv service call from dashboard.

        Accepts pre-parsed CSV rows from the dashboard JavaScript and runs
        them through the user's CONFIGURED tariff rates (not plan defaults).
        """
        # Phase 8 PR-9 (HA Silver) — action-exceptions rule.
        coord = _resolve_coordinator()
        if coord is None:
            raise HomeAssistantError(
                "PriceHawk coordinator not available — entry may have "
                "unloaded. Reload the integration."
            )
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
        coord.data["csv_comparison"] = result
        coord.async_set_updated_data(coord.data)

        _LOGGER.info(
            "CSV analysis complete: %s saves $%.2f",
            result.get("savings_direction", "unknown"),
            result.get("savings_aud", 0.0),
        )

    hass.services.async_register(DOMAIN, "analyze_csv", handle_analyze_csv)

    # Register backfill service — Phase 3.2 commit 4: thin delegate
    # to ``coordinator.async_run_backfill``. All recorder pulls, plan
    # composition, status tracking, and persistence happen inside the
    # coordinator method; status surfaces via
    # ``sensor.pricehawk_backfill_status``.
    async def handle_backfill(call: object) -> None:
        # Phase 8 PR-9 (HA Silver) — action-exceptions rule.
        coord = _resolve_coordinator()
        if coord is None:
            raise HomeAssistantError(
                "PriceHawk coordinator not available — entry may have "
                "unloaded. Reload the integration."
            )
        raw_days = call.data.get("days", 30)  # type: ignore[attr-defined]
        try:
            days_back = max(1, min(int(raw_days), 90))
        except (TypeError, ValueError) as err:
            raise ServiceValidationError(
                f"backfill_history: 'days' must be an integer "
                f"between 1 and 90 (got {raw_days!r})"
            ) from err
        await coord.async_run_backfill(days_back=days_back)

    hass.services.async_register(DOMAIN, "backfill_history", handle_backfill)

    # Phase 3.1 commit 5 — manual ranking trigger. Lets users force-run
    # the ranking pipeline from Developer Tools → Services without
    # waiting for the next 00:30 schedule fire. Most useful right after
    # switching plans (so the alternatives ranking reflects the new
    # distributor / postcode immediately).
    async def handle_rank_alternatives(call: object) -> None:
        # Phase 8 PR-9 (HA Silver) — action-exceptions rule. Was: warn +
        # default-fallback for invalid top_k; now surfaces the bad input
        # to the caller via ServiceValidationError.
        coord = _resolve_coordinator()
        if coord is None:
            raise HomeAssistantError(
                "PriceHawk coordinator not available — entry may have "
                "unloaded. Reload the integration."
            )
        raw = call.data.get("top_k", 20)  # type: ignore[attr-defined]
        try:
            top_k = int(raw)
        except (TypeError, ValueError) as err:
            raise ServiceValidationError(
                f"rank_alternatives: 'top_k' must be an integer "
                f"between 1 and 100 (got {raw!r})"
            ) from err
        top_k = max(1, min(top_k, 100))
        result = await coord.async_run_ranking_job(top_k=top_k)
        _LOGGER.info(
            "rank_alternatives service: ran successfully, %d result(s)",
            len(result),
        )

    hass.services.async_register(
        DOMAIN, "rank_alternatives", handle_rank_alternatives
    )

    _LOGGER.info("PriceHawk integration setup complete")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PriceHawkConfigEntry) -> bool:
    """Unload a config entry.

    Order matters: platform-unload runs FIRST. If it fails, the coordinator
    and runtime_data are left intact so HA can retry. Only on success do we
    cancel timers, persist state, and (if this was the last entry) tear down
    the singleton services.
    """
    _LOGGER.info("Unloading PriceHawk integration (entry=%s)", entry.entry_id)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    data: PriceHawkData | None = getattr(entry, "runtime_data", None)
    if data is not None:
        data.coordinator.cancel_persist()
        data.coordinator.cancel_ranking()
        await data.coordinator.async_persist_state()

    await remove_panel(hass)

    # Multi-entry sentinel: only unregister the singleton services when THIS
    # is the last remaining entry. Uses the config-entries registry — NOT
    # hass.data, which is no longer maintained. The entry being unloaded may
    # or may not still appear in async_entries(DOMAIN) depending on HA
    # version, so filter it out explicitly.
    remaining = [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]
    if not remaining:
        hass.services.async_remove(DOMAIN, "analyze_csv")
        hass.services.async_remove(DOMAIN, "backfill_history")
        hass.services.async_remove(DOMAIN, "rank_alternatives")

    return True
