"""PriceHawk integration - compare Amber Electric vs GloBird Energy costs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError, ServiceValidationError

from .const import (
    CONFIG_ENTRY_MINOR_VERSION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from .coordinator import PriceHawkCoordinator
from .dashboard_config import (
    remove_lovelace_dashboard,
    setup_lovelace_dashboard,
)
from .data import PriceHawkConfigEntry, PriceHawkData

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


# ----------------------------------------------------------------------
# Config entry migration — Constitution P16 (Data Integrity)
# ----------------------------------------------------------------------
#
# Registered migrators for the ConfigFlow ``VERSION`` constant. Each
# takes the CURRENTLY-MIGRATED data/options dicts (NOT the raw entry)
# and returns the dicts shaped for the NEXT version up. Add an entry
# here BEFORE bumping ``CONFIG_ENTRY_VERSION`` (and the ``VERSION``
# class attribute on ``ConfigFlow`` in config_flow.py).
#
# Signature: ``async def(hass, data, options) -> tuple[dict, dict]``
# returning ``(new_data, new_options)``. Migrators run sequentially so
# a v1→v3 entry runs the v1→v2 migrator THEN the v2→v3 migrator on
# the v2-transformed payload — never re-reading ``entry.data`` /
# ``entry.options`` between steps. Codex follow-up (2026-05-27): the
# previous signature took ``entry`` and was buggy for v1→v3 skip
# migrations because each step re-read the ORIGINAL payload instead
# of the progressively migrated one.
ConfigEntryMigratorT = Callable[
    [HomeAssistant, dict[str, Any], dict[str, Any]],
    Awaitable[tuple[dict[str, Any], dict[str, Any]]],
]
_CONFIG_ENTRY_MIGRATORS: dict[int, ConfigEntryMigratorT] = {}

# Minor-version migrator chain — parallel to ``_CONFIG_ENTRY_MIGRATORS`` but
# scoped within the CURRENT major. Keys are the OLD minor version (within the
# current major); the function returns ``(data, options)`` shaped for
# ``old_minor + 1``.
#
# Most minor bumps will register an identity migrator (additive optional fields
# get filled by ``.get(key, default)`` at read-time). For semantic minor changes
# the migrator rewrites the affected fields. Same loud-failure contract as the
# major chain: a bump without a paired migrator is programmer error and
# ``async_migrate_entry`` will raise ``ConfigEntryError``.
#
# Codex follow-up #2 (2026-05-27): the previous implementation only walked
# majors, so same-major minor upgrades were skipped — a v1.1 → v1.2 entry
# would slip through migration entirely. Placeholder for future bumps; empty
# at the current version pin (1.1).
_CONFIG_ENTRY_MINOR_MIGRATORS: dict[int, ConfigEntryMigratorT] = {}


async def async_migrate_entry(hass: HomeAssistant, entry: PriceHawkConfigEntry) -> bool:
    """Migrate an older PriceHawk config entry forward to ``CONFIG_ENTRY_VERSION``.

    Home Assistant calls this hook automatically when the integration
    version increases. Per HA platform convention (Constitution P19),
    unrecoverable migration paths raise ``ConfigEntryError`` rather
    than returning ``False`` — HA's setup machinery treats the raised
    exception as a permanent setup failure with the exception message
    surfaced in the UI, whereas a bare ``False`` produces a generic
    "config entry needs migration" notice with no diagnostic detail.

    Behaviour matrix mirrors :class:`PriceHawkStore`:

    * ``entry.version > CONFIG_ENTRY_VERSION`` — major downgrade
      requested. Raise ``ConfigEntryError`` so the UI shows the
      version mismatch and the user knows to either upgrade the
      integration back or remove the entry. A MAJOR-version bump
      signals a breaking schema change; we can't safely down-convert
      because the old code doesn't know the future field shapes.
    * ``entry.version == CONFIG_ENTRY_VERSION and
      entry.minor_version > CONFIG_ENTRY_MINOR_VERSION`` —
      same-major newer-minor. Per HA's documented contract
      (https://developers.home-assistant.io/docs/config_entries_index/#migrating-config-entries)
      minor versions are backward-compatible by design within a major.
      LOAD the entry as-is with a debug log; do NOT raise. Codex
      follow-up #3 (2026-05-27) reversed the earlier loud-refusal
      stance after Codex pointed at the HA convention: a 1.2 entry
      loaded by a 1.1 integration is supposed to work. The 1.2-only
      additive fields are ignored by the older code paths via their
      existing ``.get(key, default)`` reads.
    * ``entry.version < CONFIG_ENTRY_VERSION`` — walk MAJOR migrators
      sequentially; a missing migrator raises ``ConfigEntryError``
      (programmer error — release shouldn't ship without the pair).
      A major bump resets minor to 1; subsequent minor migrators (if
      any) close the gap to ``CONFIG_ENTRY_MINOR_VERSION``.
    * ``entry.version == CONFIG_ENTRY_VERSION and
      entry.minor_version < CONFIG_ENTRY_MINOR_VERSION`` — walk MINOR
      migrators within the current major. Codex follow-up #2: the
      previous implementation only walked majors, so same-major minor
      upgrades were silently skipped.
    * Equal on both axes — HA should not call this hook, but return
      ``True`` defensively so a future HA change doesn't fail-open
      into ``async_setup_entry``.

    See ``docs/architecture.md`` § "Storage migration policy".
    """
    _LOGGER.info(
        "PriceHawk config entry migration: %s.%s → %s.%s (entry=%s)",
        entry.version,
        entry.minor_version,
        CONFIG_ENTRY_VERSION,
        CONFIG_ENTRY_MINOR_VERSION,
        entry.entry_id,
    )

    if entry.version > CONFIG_ENTRY_VERSION:
        msg = (
            f"PriceHawk config entry {entry.entry_id} is version "
            f"{entry.version}.{entry.minor_version} but this "
            f"integration only supports up to "
            f"{CONFIG_ENTRY_VERSION}.{CONFIG_ENTRY_MINOR_VERSION}. "
            "Refusing to downgrade — upgrade the integration back to "
            "the matching version or remove and re-add the entry."
        )
        _LOGGER.error(msg)
        raise ConfigEntryError(msg)

    # Codex follow-up #3 (2026-05-27): per HA's documented config
    # entry contract, minor versions are backward-compatible WITHIN a
    # major. A 1.2 entry loaded by a 1.1 integration MUST load — the
    # older code reads the additive 1.2-only fields via ``.get(key,
    # default)`` and ignores anything it doesn't recognise. Log at
    # debug so support diagnostics can surface the mismatch without
    # spamming the user log. (The earlier follow-up #2 implementation
    # raised here, reversing HA's convention — that was wrong; this
    # restores conformance.) Constitution P19 (Platform Conventions)
    # OVERRIDES the previous loud-refusal stance.
    #
    # Spec: https://developers.home-assistant.io/docs/config_entries_index/#migrating-config-entries
    if entry.version == CONFIG_ENTRY_VERSION and entry.minor_version > CONFIG_ENTRY_MINOR_VERSION:
        _LOGGER.debug(
            "PriceHawk config entry %s carries a newer minor version "
            "(%s.%s) than this integration build (%s.%s); loading as-is "
            "— minor versions are backward-compatible within a major "
            "per HA convention.",
            entry.entry_id,
            entry.version,
            entry.minor_version,
            CONFIG_ENTRY_VERSION,
            CONFIG_ENTRY_MINOR_VERSION,
        )
        return True

    current_version = entry.version
    current_minor = entry.minor_version
    data = dict(entry.data)
    options = dict(entry.options)

    while current_version < CONFIG_ENTRY_VERSION:
        migrator = _CONFIG_ENTRY_MIGRATORS.get(current_version)
        if migrator is None:
            msg = (
                f"No migrator registered for PriceHawk config entry "
                f"version {current_version} → {current_version + 1}. "
                "Add one to _CONFIG_ENTRY_MIGRATORS before bumping "
                "CONFIG_ENTRY_VERSION."
            )
            _LOGGER.error(msg)
            raise ConfigEntryError(msg)
        # Codex follow-up (2026-05-27): pass the PROGRESSIVELY-MIGRATED
        # data/options into each step. The previous call site passed
        # ``entry`` and re-read ``entry.data``/``entry.options`` inside
        # every migrator — for a v1→v3 skip the step-2 migrator would
        # see the v1 payload again instead of the v2-transformed one,
        # silently producing a v3-stamped envelope wrapped around a v1
        # body (Constitution P16 silent-data-corruption).
        data, options = await migrator(hass, data, options)
        current_version += 1
        # A major bump resets minor to 1 — minor migrators are scoped
        # within a single major, so the new major's minor axis starts
        # at 1 regardless of where we came from.
        current_minor = 1

    # Walk MINOR migrators within the current major. Codex follow-up #2
    # (2026-05-27): the previous implementation only advanced on major,
    # so same-major minor upgrades fell through with no migration. Same
    # loud-failure contract as the major chain — a registered bump
    # without a paired migrator is programmer error.
    while current_minor < CONFIG_ENTRY_MINOR_VERSION:
        minor_migrator = _CONFIG_ENTRY_MINOR_MIGRATORS.get(current_minor)
        if minor_migrator is None:
            msg = (
                f"No migrator registered for PriceHawk config entry "
                f"minor version {current_version}.{current_minor} → "
                f"{current_version}.{current_minor + 1}. Add one to "
                "_CONFIG_ENTRY_MINOR_MIGRATORS before bumping "
                "CONFIG_ENTRY_MINOR_VERSION."
            )
            _LOGGER.error(msg)
            raise ConfigEntryError(msg)
        data, options = await minor_migrator(hass, data, options)
        current_minor += 1

    # Persist BOTH major + minor version on the entry. Without the
    # explicit ``minor_version`` HA leaves the entry's stored minor at
    # whatever it was before migration; a later
    # ``CONFIG_ENTRY_VERSION``-equal entry with a forward minor bump
    # would then re-enter ``async_migrate_entry`` against a stale
    # minor and either re-run migrators or fail the equal-version
    # guard. Codex follow-up #2 (2026-05-27): use the live
    # ``CONFIG_ENTRY_MINOR_VERSION`` (= the minor we walked to) rather
    # than a hard-coded ``1`` — the previous call would have stamped
    # the entry at minor=1 even after a 1.0 → 1.5 migration.
    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        version=CONFIG_ENTRY_VERSION,
        minor_version=CONFIG_ENTRY_MINOR_VERSION,
    )
    _LOGGER.info(
        "PriceHawk config entry %s migrated to %s.%s",
        entry.entry_id,
        CONFIG_ENTRY_VERSION,
        CONFIG_ENTRY_MINOR_VERSION,
    )
    return True


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
    # Live UAT 2026-05-23 fix: use async_create_background_task so the
    # ranking job runs OFF the bootstrap-wait path. With async_create_task
    # HA's bootstrap waits up to its timeout for these tasks and logs
    # "Something is blocking Home Assistant from wrapping up the start
    # up phase" listing every other integration as collateral. Background
    # tasks are explicitly excluded from that wait.
    #
    # Codex P1-6 (2026-05-23) — retain the task handles and register
    # cancellation on unload. Without this, reload/unload leaves these
    # tasks running against an unloaded coordinator, which manifests
    # as pytest "coroutine was never awaited" warnings AND as real
    # data corruption when a reload races a still-running ranking pass.
    ranking_task = hass.async_create_background_task(
        coordinator.async_run_ranking_job(),
        name=f"pricehawk_initial_ranking_{entry.entry_id}",
    )
    entry.runtime_data.background_tasks.append(ranking_task)

    # Cancel-only callback retained as belt-and-braces for HA-internal
    # unload paths that bypass our async_unload_entry. The real cancel
    # + gather happens explicitly in async_unload_entry (issue #115).
    # ``async_on_unload`` expects ``Callable[[], Coroutine | None]``;
    # ``Task.cancel`` returns ``bool``, so wrap it in a sync callback that
    # discards the return value (the cancel side effect is all we need).
    @callback
    def _cancel_ranking_task() -> None:
        ranking_task.cancel()

    entry.async_on_unload(_cancel_ranking_task)

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

    backfill_task = hass.async_create_background_task(
        _backfill_after_ranking(),
        name=f"pricehawk_initial_backfill_{entry.entry_id}",
    )
    entry.runtime_data.background_tasks.append(backfill_task)

    @callback
    def _cancel_backfill_task() -> None:
        backfill_task.cancel()

    entry.async_on_unload(_cancel_backfill_task)

    # Set up Lovelace dashboard natively
    await setup_lovelace_dashboard(hass, coordinator)

    # OptionsFlowWithReload handles reloading automatically —
    # do NOT add an update_listener here (HA 2026.3+ forbids combining them).

    # Codex P1-2 (2026-05-23): register the three services exactly once
    # across all entries, resolving the target entry at call time. The
    # previous implementation registered them inside async_setup_entry
    # with handlers that closed over the current ``entry`` — multi-entry
    # installs ended up routing every service call to whichever entry
    # was set up last, because HA's service registry stores ONE handler
    # per (domain, name) and each re-registration silently overwrote
    # the previous one. async_unload_entry already cleans up the
    # singletons on the LAST entry's unload (lines 240-247), so this
    # idempotent-register pairs cleanly with that teardown.
    _register_services_once(hass)

    _LOGGER.info("PriceHawk integration setup complete")
    return True


# ----------------------------------------------------------------------
# Service registration helpers — Codex P1-2 fix
# ----------------------------------------------------------------------


def _resolve_service_target_entry(hass: HomeAssistant, call: ServiceCall) -> PriceHawkConfigEntry:
    """Pick the PriceHawk config entry a service call should run against.

    Service call data may include ``entry_id`` to disambiguate when
    multiple PriceHawk entries are loaded. If omitted, default to the
    single loaded entry; raise ``ServiceValidationError`` if there's
    more than one so the caller has to be explicit.
    """
    target_id = call.data.get("entry_id")
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if getattr(e, "runtime_data", None) is not None
    ]
    if target_id:
        for entry in entries:
            if entry.entry_id == target_id:
                return entry
        raise ServiceValidationError(
            f"PriceHawk entry {target_id!r} is not loaded "
            f"(loaded entries: {[e.entry_id for e in entries]})"
        )
    if not entries:
        raise HomeAssistantError(
            "No PriceHawk entry is currently loaded — service call cannot "
            "be routed. Add an entry via Settings → Devices & Services."
        )
    if len(entries) > 1:
        raise ServiceValidationError(
            f"Multiple PriceHawk entries loaded "
            f"({[e.entry_id for e in entries]}); pass 'entry_id' in the "
            "service call data to choose which one runs."
        )
    return entries[0]


def _register_services_once(hass: HomeAssistant) -> None:
    """Idempotent — only registers if the singleton handlers aren't
    already in HA's service registry. Codex P1-2 fix.
    """
    if hass.services.has_service(DOMAIN, "backfill_history"):
        return

    async def handle_backfill(call: ServiceCall) -> None:
        # Phase 8 PR-9 (HA Silver) — action-exceptions rule.
        target = _resolve_service_target_entry(hass, call)
        data: PriceHawkData | None = getattr(target, "runtime_data", None)
        coord: PriceHawkCoordinator | None = data.coordinator if data is not None else None
        if coord is None:
            raise HomeAssistantError(
                "PriceHawk coordinator not available — entry may have "
                "unloaded. Reload the integration."
            )
        raw_days = call.data.get("days", 30)
        try:
            days_back = max(1, min(int(raw_days), 90))
        except (TypeError, ValueError) as err:
            raise ServiceValidationError(
                f"backfill_history: 'days' must be an integer between 1 and 90 (got {raw_days!r})"
            ) from err
        await coord.async_run_backfill(days_back=days_back)

    async def handle_rank_alternatives(call: ServiceCall) -> None:
        # Phase 8 PR-9 (HA Silver) — action-exceptions rule.
        target = _resolve_service_target_entry(hass, call)
        data: PriceHawkData | None = getattr(target, "runtime_data", None)
        coord: PriceHawkCoordinator | None = data.coordinator if data is not None else None
        if coord is None:
            raise HomeAssistantError(
                "PriceHawk coordinator not available — entry may have "
                "unloaded. Reload the integration."
            )
        raw = call.data.get("top_k", 20)
        try:
            top_k = int(raw)
        except (TypeError, ValueError) as err:
            raise ServiceValidationError(
                f"rank_alternatives: 'top_k' must be an integer between 1 and 100 (got {raw!r})"
            ) from err
        top_k = max(1, min(top_k, 100))
        result = await coord.async_run_ranking_job(top_k=top_k)
        _LOGGER.info(
            "rank_alternatives service: ran successfully, %d result(s)",
            len(result),
        )

    async def handle_reset_today(call: ServiceCall) -> None:
        """Zero every registered provider's daily accumulators NOW.

        Use case: after a code-level cost-math bugfix lands mid-day, the
        DWT provider's ``_import_cost_today_c`` still carries inflated
        values accumulated under the prior bug. Without this service the
        user has to wait until the midnight rollover to see clean data.
        Live UAT 2026-05-24 — added after the AEMO RRP-row-type fix
        (beta.7) left ``current_plan_cost_today=$66.78`` stuck on a real
        spend of <$2.
        """
        del call  # Service takes no parameters; resets every entry.
        # Retro-review of #152 (gemini, 2026-05-24): Silver action-exceptions
        # rule says service handlers should raise HomeAssistantError when they
        # cannot perform the requested action. Returning silently after finding
        # no entries makes the failure invisible — the user would refresh their
        # dashboard, see the residual untouched, and have no idea the service
        # ran. Collect entries first so we can detect the empty case explicitly.
        entries_with_runtime = [
            (entry, data)
            for entry in hass.config_entries.async_entries(DOMAIN)
            if (data := getattr(entry, "runtime_data", None)) is not None
        ]
        if not entries_with_runtime:
            raise HomeAssistantError(
                "reset_today: no PriceHawk entries with active runtime data. "
                "Reload the integration first."
            )
        for entry, data in entries_with_runtime:
            coord = data.coordinator
            for provider in coord._providers.values():
                try:
                    provider.reset_daily()
                except Exception as exc:  # noqa: BLE001 — never sink the batch
                    _LOGGER.warning(
                        "reset_today: %s.reset_daily failed: %s",
                        getattr(provider, "id", "?"),
                        exc,
                    )
            await coord.async_persist_state()
            _LOGGER.info(
                "reset_today: zeroed daily accumulators for %d provider(s) on entry %s",
                len(coord._providers),
                entry.entry_id,
            )

    hass.services.async_register(DOMAIN, "backfill_history", handle_backfill)
    hass.services.async_register(DOMAIN, "rank_alternatives", handle_rank_alternatives)
    hass.services.async_register(DOMAIN, "reset_today", handle_reset_today)


async def async_unload_entry(hass: HomeAssistant, entry: PriceHawkConfigEntry) -> bool:
    """Unload a config entry.

    Order matters: platform-unload runs FIRST. If it fails, the coordinator
    and runtime_data are left intact so HA can retry. Only on success do we
    cancel timers, persist state, and (if this was the last entry) tear down
    the singleton services.
    """
    _LOGGER.info("Unloading PriceHawk integration (entry=%s)", entry.entry_id)

    # Issue #115 — cancel + AWAIT the initial ranking/backfill background
    # tasks BEFORE platform-unload so they cannot race the coordinator
    # teardown with mid-flight DB writes or `_ranking_lock` access. Cancel
    # callbacks registered via entry.async_on_unload only call .cancel()
    # without awaiting, which leaves a race window between cancellation
    # request and actual task termination at the next await point.
    pending_data: PriceHawkData | None = getattr(entry, "runtime_data", None)
    if pending_data is not None and pending_data.background_tasks:
        for task in pending_data.background_tasks:
            task.cancel()
        await asyncio.gather(*pending_data.background_tasks, return_exceptions=True)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    data: PriceHawkData | None = getattr(entry, "runtime_data", None)
    if data is not None:
        data.coordinator.cancel_persist()
        data.coordinator.cancel_ranking()
        await data.coordinator.async_persist_state()

    await remove_lovelace_dashboard(hass)

    # Multi-entry sentinel: only unregister the singleton services when THIS
    # is the last remaining entry. Uses the config-entries registry — NOT
    # hass.data, which is no longer maintained. The entry being unloaded may
    # or may not still appear in async_entries(DOMAIN) depending on HA
    # version, so filter it out explicitly.
    remaining = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id
    ]
    if not remaining:
        hass.services.async_remove(DOMAIN, "backfill_history")
        hass.services.async_remove(DOMAIN, "rank_alternatives")
        hass.services.async_remove(DOMAIN, "reset_today")

    return True
