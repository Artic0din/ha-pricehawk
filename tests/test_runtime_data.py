"""Regression tests for typed runtime data (Phase 7 / PR-1).

Covers the contract surface introduced by ``custom_components.pricehawk.data``:
single-entry setup/unload, multi-entry service lifecycle, OptionsFlowWithReload
round-trip, service-handler closure freshness, and a static grep belt-and-braces
guard against the legacy ``hass.data[DOMAIN]`` pattern.

Async pattern matches the rest of the suite: ``asyncio.run(...)`` inside sync
tests, not ``pytest-asyncio``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.pricehawk import (
    _resolve_service_target_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.pricehawk.data import PriceHawkData


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_coordinator() -> MagicMock:
    """Build a stub PriceHawkCoordinator with the methods __init__.py touches."""
    coord = MagicMock()
    coord.async_restore_state = AsyncMock()
    coord.async_config_entry_first_refresh = AsyncMock()
    # Phase 9 PR-10 — async_setup_entry calls async_setup_stats after restore.
    coord.async_setup_stats = AsyncMock()
    coord.async_run_ranking_job = AsyncMock(return_value=[])
    coord.async_run_backfill = AsyncMock()
    coord.async_persist_state = AsyncMock()
    coord.async_set_updated_data = MagicMock()
    coord.schedule_persist = MagicMock()
    coord.schedule_daily_ranking = MagicMock()
    coord.cancel_persist = MagicMock()
    coord.cancel_ranking = MagicMock()
    coord._ranking_lock = asyncio.Lock()
    coord.data = {}
    return coord


def _make_hass(
    registered_entries: list[Any] | None = None,
    unload_platforms_result: bool = True,
) -> MagicMock:
    """Build a stub HomeAssistant with the surfaces __init__.py reaches into."""
    hass = MagicMock()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=unload_platforms_result)
    hass.config_entries.async_entries = MagicMock(return_value=list(registered_entries or []))
    hass.services = MagicMock()
    hass.services.async_register = MagicMock()
    hass.services.async_remove = MagicMock()
    # Codex P1-2: __init__.py uses ``hass.services.has_service`` as an
    # idempotency guard before registering the singleton services. A
    # default MagicMock would return a truthy MagicMock and skip the
    # registration, breaking the closure-freshness test. Default to
    # False here so service-registration paths run.
    hass.services.has_service = MagicMock(return_value=False)

    # async_create_task: swallow the coroutine cleanly so we don't get
    # "coroutine was never awaited" warnings during teardown.
    def _swallow(coro: Any) -> MagicMock:
        if hasattr(coro, "close"):
            coro.close()
        return MagicMock()

    hass.async_create_task = MagicMock(side_effect=_swallow)

    # Issue #115 — async_create_background_task now feeds the runtime_data
    # background_tasks list which async_unload_entry awaits with gather().
    # The fixture must produce something that:
    #   1. has .cancel() (production code calls it during unload)
    #   2. is loop-agnostic — tests call asyncio.run() multiple times,
    #      each spinning a fresh event loop, and a Task pre-created on
    #      the setup loop would raise "future belongs to a different
    #      loop" when gather() runs on the unload loop (Python 3.14+
    #      enforces this strictly; earlier versions were silently
    #      permissive).
    # A tiny awaitable with __await__ returning an empty iterator
    # satisfies both: gather() wraps it via _wrap_awaitable on the
    # current loop, completes immediately, and returns None.
    class _DoneAwaitable:
        def __init__(self) -> None:
            self.cancel_calls = 0

        def cancel(self) -> None:
            self.cancel_calls += 1

        def __await__(self):
            return iter(())

    def _bg_task(coro: Any, name: str | None = None) -> Any:
        if hasattr(coro, "close"):
            coro.close()
        return _DoneAwaitable()

    hass.async_create_background_task = MagicMock(side_effect=_bg_task)
    hass.async_add_executor_job = AsyncMock()
    return hass


def _make_entry(entry_id: str = "entry-A") -> MagicMock:
    """Build a stub ConfigEntry — runtime_data starts None, set by setup."""
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.options = {}
    entry.runtime_data = None
    return entry


def _patch_deps(coord: MagicMock):
    """Context-manager bundle for the collaborators we patch."""
    return (
        patch(
            "custom_components.pricehawk.PriceHawkCoordinator",
            return_value=coord,
        ),
        patch("custom_components.pricehawk.setup_lovelace_dashboard", new=AsyncMock()),
        patch("custom_components.pricehawk.remove_lovelace_dashboard", new=AsyncMock()),
        patch("custom_components.pricehawk.copy_www_assets", new=AsyncMock(), create=True),
        patch("custom_components.pricehawk.setup_panel_iframe", new=AsyncMock(), create=True),
        patch("custom_components.pricehawk.setup_panel_custom_v2", new=AsyncMock(), create=True),
    )


def _patch_deps_iter(coords: list[MagicMock]):
    """Same as _patch_deps but each setup pulls the next coordinator from the list."""
    coords_iter = iter(coords)

    def _next_coord(*_args: Any, **_kwargs: Any) -> MagicMock:
        return next(coords_iter)

    return (
        patch(
            "custom_components.pricehawk.PriceHawkCoordinator",
            side_effect=_next_coord,
        ),
        patch("custom_components.pricehawk.setup_lovelace_dashboard", new=AsyncMock()),
        patch("custom_components.pricehawk.remove_lovelace_dashboard", new=AsyncMock()),
        patch("custom_components.pricehawk.copy_www_assets", new=AsyncMock(), create=True),
        patch("custom_components.pricehawk.setup_panel_iframe", new=AsyncMock(), create=True),
        patch("custom_components.pricehawk.setup_panel_custom_v2", new=AsyncMock(), create=True),
    )


# ---------------------------------------------------------------------------
# AC-1 / AC-2: setup writes typed runtime_data
# ---------------------------------------------------------------------------


def test_setup_writes_runtime_data():
    """After async_setup_entry, entry.runtime_data is a PriceHawkData with coordinator."""
    coord = _make_coordinator()
    hass = _make_hass()
    entry = _make_entry()

    p1, p2, p3, p4, p5, p6 = _patch_deps(coord)
    with p1, p2, p3, p4, p5, p6:
        result = asyncio.run(async_setup_entry(hass, entry))

    assert result is True
    assert isinstance(entry.runtime_data, PriceHawkData)
    assert entry.runtime_data.coordinator is coord


# ---------------------------------------------------------------------------
# AC-4: unload runs platform-unload FIRST
# ---------------------------------------------------------------------------


def test_unload_runs_platform_unload_first():
    """If async_unload_platforms returns False, coordinator is NOT torn down."""
    coord = _make_coordinator()
    hass = _make_hass(unload_platforms_result=False)
    entry = _make_entry()

    p1, p2, p3, p4, p5, p6 = _patch_deps(coord)
    with p1, p2, p3, p4, p5, p6:
        asyncio.run(async_setup_entry(hass, entry))
        assert entry.runtime_data is not None
        original_data = entry.runtime_data

        result = asyncio.run(async_unload_entry(hass, entry))

    assert result is False
    assert coord.cancel_persist.call_count == 0
    assert coord.cancel_ranking.call_count == 0
    assert coord.async_persist_state.call_count == 0
    assert entry.runtime_data is original_data, (
        "runtime_data must survive failed platform-unload so HA can retry"
    )


# ---------------------------------------------------------------------------
# AC-2: unload never touches hass.data
# ---------------------------------------------------------------------------


def test_unload_does_not_touch_hass_data():
    """Successful unload leaves hass.data untouched."""
    coord = _make_coordinator()
    hass = _make_hass(unload_platforms_result=True)
    entry = _make_entry()

    p1, p2, p3, p4, p5, p6 = _patch_deps(coord)
    with p1, p2, p3, p4, p5, p6:
        asyncio.run(async_setup_entry(hass, entry))
        hass_data_snapshot = dict(hass.data)
        result = asyncio.run(async_unload_entry(hass, entry))

    assert result is True
    assert hass.data == hass_data_snapshot, (
        "Unload must not mutate hass.data — runtime_data is the only storage now"
    )
    coord.cancel_persist.assert_called_once()
    coord.cancel_ranking.assert_called_once()
    coord.async_persist_state.assert_awaited_once()


# ---------------------------------------------------------------------------
# AC-2b: multi-entry service lifecycle
# ---------------------------------------------------------------------------


def test_multi_entry_service_lifecycle():
    """Two entries: services persist after first unload, removed after second."""
    entry_a = _make_entry("entry-A")
    entry_b = _make_entry("entry-B")

    coord_a = _make_coordinator()
    coord_b = _make_coordinator()

    hass = _make_hass()

    p1, p2, p3, p4, p5, p6 = _patch_deps_iter([coord_a, coord_b])
    with p1, p2, p3, p4, p5, p6:
        asyncio.run(async_setup_entry(hass, entry_a))
        asyncio.run(async_setup_entry(hass, entry_b))

        # First unload: entry_b still registered, services must stay.
        hass.config_entries.async_entries.return_value = [entry_a, entry_b]
        hass.services.async_remove.reset_mock()
        asyncio.run(async_unload_entry(hass, entry_a))
        assert hass.services.async_remove.call_count == 0, (
            "Services must remain registered while another entry exists"
        )

        # Second unload: last entry → services removed exactly once each.
        hass.config_entries.async_entries.return_value = [entry_b]
        hass.services.async_remove.reset_mock()
        asyncio.run(async_unload_entry(hass, entry_b))
        removed = {call.args[1] for call in hass.services.async_remove.call_args_list}
        assert removed == {"backfill_history", "rank_alternatives", "reset_today"}
        assert hass.services.async_remove.call_count == 3


# ---------------------------------------------------------------------------
# Issue #115: background tasks tracked, cancelled + awaited before platform unload
# ---------------------------------------------------------------------------


def test_setup_tracks_background_tasks_on_runtime_data():
    """async_setup_entry must append both initial ranking + backfill tasks to
    PriceHawkData.background_tasks so async_unload_entry can cancel + gather
    them before tearing down the coordinator (issue #115).
    """
    coord = _make_coordinator()
    hass = _make_hass()
    entry = _make_entry()

    p1, p2, p3, p4, p5, p6 = _patch_deps(coord)
    with p1, p2, p3, p4, p5, p6:
        asyncio.run(async_setup_entry(hass, entry))

    assert entry.runtime_data is not None
    assert len(entry.runtime_data.background_tasks) == 2, (
        "Both ranking_task and backfill_task must be tracked on "
        "PriceHawkData.background_tasks. Issue #115."
    )


def test_unload_cancels_and_awaits_background_tasks_before_platform_unload():
    """async_unload_entry must cancel AND await the background tasks BEFORE
    calling async_unload_platforms. The previous P1-6 fix registered
    ``task.cancel`` via entry.async_on_unload, which fires but does not
    await — leaving a race window between cancellation request and actual
    task termination at the next await point. Issue #115.
    """
    coord = _make_coordinator()
    hass = _make_hass(unload_platforms_result=True)
    entry = _make_entry()

    call_order: list[str] = []

    p1, p2, p3, p4, p5, p6 = _patch_deps(coord)
    with p1, p2, p3, p4, p5, p6:
        asyncio.run(async_setup_entry(hass, entry))

        # Sentinel: capture call order. The fixture returns _DoneAwaitable
        # instances from _bg_task; wrap their .cancel() to record ordering
        # against async_unload_platforms.
        for task in entry.runtime_data.background_tasks:
            real_cancel = task.cancel

            def _spy_cancel(*args: Any, _real=real_cancel, **kwargs: Any) -> Any:
                call_order.append("cancel")
                return _real(*args, **kwargs)

            task.cancel = _spy_cancel  # type: ignore[method-assign]

        original_unload = hass.config_entries.async_unload_platforms

        async def _spy_unload(*args: Any, **kwargs: Any) -> Any:
            call_order.append("platform_unload")
            return await original_unload(*args, **kwargs)

        hass.config_entries.async_unload_platforms = AsyncMock(side_effect=_spy_unload)

        result = asyncio.run(async_unload_entry(hass, entry))

    assert result is True
    assert call_order, "Expected cancel + platform_unload to be recorded"
    assert call_order[0] == "cancel", (
        "Background tasks must be cancelled before platform unload — "
        "otherwise mid-flight DB writes or _ranking_lock access race "
        "the coordinator teardown. Issue #115."
    )
    assert "platform_unload" in call_order
    # All cancel events must come before the platform_unload event.
    platform_idx = call_order.index("platform_unload")
    cancels_before = call_order[:platform_idx]
    assert cancels_before.count("cancel") == 2, (
        "Both background_tasks must be cancelled before platform_unload."
    )


# ---------------------------------------------------------------------------
# AC-4b: OptionsFlowWithReload round-trip preserves contract
# ---------------------------------------------------------------------------


def test_options_flow_reload_cycle():
    """unload → setup yields a NEW coordinator in runtime_data; old timers cancelled."""
    entry = _make_entry()
    hass = _make_hass(unload_platforms_result=True)

    coord_v1 = _make_coordinator()
    coord_v2 = _make_coordinator()

    p1, p2, p3, p4, p5, p6 = _patch_deps_iter([coord_v1, coord_v2])
    with p1, p2, p3, p4, p5, p6:
        asyncio.run(async_setup_entry(hass, entry))
        assert entry.runtime_data.coordinator is coord_v1

        hass.config_entries.async_entries.return_value = [entry]
        asyncio.run(async_unload_entry(hass, entry))
        coord_v1.cancel_persist.assert_called_once()
        coord_v1.cancel_ranking.assert_called_once()

        # HA resets runtime_data between unload + re-setup on a reload cycle.
        entry.runtime_data = None

        asyncio.run(async_setup_entry(hass, entry))
        assert entry.runtime_data.coordinator is coord_v2
        assert coord_v2 is not coord_v1


# ---------------------------------------------------------------------------
# Closure freshness: service handlers re-resolve coordinator on each call
# ---------------------------------------------------------------------------


def test_service_handlers_resolve_fresh_coordinator():
    """After runtime_data is swapped, registered handlers see the NEW
    coordinator. Codex P1-2 (2026-05-23) reworked the handlers to
    resolve the target entry from ``hass.config_entries.async_entries``
    at call time instead of capturing it in a closure — this test
    therefore needs hass.config_entries to return our test entry."""
    original_coord = _make_coordinator()
    entry = _make_entry()
    # Codex P1-2: service handlers iterate config_entries.async_entries(DOMAIN)
    # to find the target entry. Pre-populate the mock so the test entry is
    # visible to the handler.
    hass = _make_hass(registered_entries=[entry])

    p1, p2, p3, p4, p5, p6 = _patch_deps(original_coord)
    with p1, p2, p3, p4, p5, p6:
        asyncio.run(async_setup_entry(hass, entry))

    # Capture the rank_alternatives handler from the registration call.
    rank_handler = None
    for call in hass.services.async_register.call_args_list:
        # args: (domain, name, handler)
        if call.args[1] == "rank_alternatives":
            rank_handler = call.args[2]
            break
    assert rank_handler is not None

    # Simulate OptionsFlowWithReload swap: replace runtime_data with a new
    # PriceHawkData containing a fresh coordinator. Reset original's mock so
    # we can assert it sees ZERO additional invocations after the swap
    # (setup itself fires async_run_ranking_job once via hass.async_create_task).
    original_coord.async_run_ranking_job.reset_mock()
    new_coord = _make_coordinator()
    entry.runtime_data = PriceHawkData(coordinator=new_coord)

    call_obj = SimpleNamespace(data={"top_k": 5})
    asyncio.run(rank_handler(call_obj))

    new_coord.async_run_ranking_job.assert_awaited_once_with(top_k=5)
    original_coord.async_run_ranking_job.assert_not_called()


# ---------------------------------------------------------------------------
# _resolve_service_target_entry — direct coverage (Constitution P17)
#
# This helper is the single point of entry routing for every PriceHawk
# service handler (analyze_csv, backfill_history, rank_alternatives). The
# prior test suite exercised it only indirectly via handler invocations,
# so the explicit-vs-default and zero/one/many entry branches were not
# isolated. The tests below pin the contract:
#
#   - explicit entry_id is honoured when it matches a loaded entry
#   - explicit entry_id raises ServiceValidationError when no match
#   - implicit (no entry_id) returns the sole loaded entry
#   - implicit with multiple loaded entries raises ServiceValidationError
#   - zero loaded entries raises HomeAssistantError (distinct: ops, not user)
#
# Only entries with non-None runtime_data are considered "loaded" — that's
# the gate the production code applies and the tests mirror it.
# ---------------------------------------------------------------------------


def _loaded_entry(entry_id: str) -> MagicMock:
    """Build an entry that ``_resolve_service_target_entry`` treats as loaded."""
    entry = _make_entry(entry_id)
    # Production gate: ``getattr(e, "runtime_data", None) is not None``. Any
    # non-None sentinel satisfies it — we don't need a real PriceHawkData
    # because the resolver never touches the field beyond the truthiness check.
    entry.runtime_data = object()
    return entry


def _resolver_hass(entries: list[MagicMock]) -> MagicMock:
    """HA stub whose ``config_entries.async_entries`` returns the given entries."""
    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=list(entries))
    return hass


def test_resolve_target_with_explicit_entry_id_returns_match():
    """call.data['entry_id'] picks the matching loaded entry from many."""
    entry_a = _loaded_entry("entry-A")
    entry_b = _loaded_entry("entry-B")
    hass = _resolver_hass([entry_a, entry_b])

    call = SimpleNamespace(data={"entry_id": "entry-B"})
    result = _resolve_service_target_entry(hass, call)

    assert result is entry_b


def test_resolve_target_with_explicit_entry_id_unknown_raises_SVE():
    """Unknown entry_id is a caller mistake — ServiceValidationError, not HAE.

    Distinction matters: ServiceValidationError surfaces in the HA UI as a
    user-fixable validation issue, while HomeAssistantError is treated as an
    ops-level failure. A wrong/typo'd entry_id is user-fixable. The error
    message must include the bad id so the user can self-diagnose — pinned
    via the ``match`` regex.
    """
    from homeassistant.exceptions import ServiceValidationError

    entry_a = _loaded_entry("entry-A")
    hass = _resolver_hass([entry_a])

    call = SimpleNamespace(data={"entry_id": "entry-does-not-exist"})

    with pytest.raises(ServiceValidationError, match="entry-does-not-exist"):
        _resolve_service_target_entry(hass, call)


def test_resolve_target_without_entry_id_single_loaded_entry_returns_it():
    """No entry_id + exactly one loaded entry → return it (default route)."""
    entry_a = _loaded_entry("entry-A")
    hass = _resolver_hass([entry_a])

    # call.data is an empty dict (HA passes a ServiceCall whose .data may
    # omit entry_id entirely — the resolver must not require it).
    call = SimpleNamespace(data={})
    result = _resolve_service_target_entry(hass, call)

    assert result is entry_a


def test_resolve_target_without_entry_id_multiple_loaded_raises_SVE():
    """No entry_id + multiple loaded entries → ServiceValidationError.

    Silently defaulting to one of them would be a correctness bug — a service
    that mutates state (reset_today, backfill_history) MUST target an explicit
    entry when ambiguity exists. Caller fixes by adding entry_id to data.

    The error message must list the candidate IDs and name the missing
    parameter (``entry_id``) so the caller knows what to add. Each of those
    three substrings is pinned via a dedicated ``pytest.raises`` block —
    ``match`` only accepts a single regex, and combining all three into one
    alternation would weaken the assertion (one match would satisfy it).
    """
    from homeassistant.exceptions import ServiceValidationError

    entry_a = _loaded_entry("entry-A")
    entry_b = _loaded_entry("entry-B")
    hass = _resolver_hass([entry_a, entry_b])

    call = SimpleNamespace(data={})

    with pytest.raises(ServiceValidationError, match="entry-A") as exc_info:
        _resolve_service_target_entry(hass, call)
    msg = str(exc_info.value)
    assert "entry-B" in msg, (
        "Error message must list every loaded entry ID so the caller knows "
        "the candidate set to choose from."
    )
    assert "entry_id" in msg, "Error message must name the parameter the caller is missing."


def test_resolve_target_no_entries_loaded_raises_HAE():
    """Zero loaded entries → HomeAssistantError (ops failure, not validation).

    Distinct from the unknown-id case: there's nothing the caller can pass to
    fix this — the integration itself isn't loaded. HomeAssistantError tells
    HA the system isn't in a state to serve the request.

    This test keeps the manual try/except (rather than ``pytest.raises``)
    because ``ServiceValidationError`` is a subclass of ``HomeAssistantError``
    — a bare ``pytest.raises(HomeAssistantError)`` would also accept the
    subclass, silently allowing a future refactor to downgrade the error
    class. The post-catch ``isinstance`` check is the belt-and-braces pin.
    """
    from homeassistant.exceptions import (
        HomeAssistantError,
        ServiceValidationError,
    )

    # Entry exists in the registry but is NOT loaded (runtime_data is None).
    # Production filters these out — the resolver must treat the result as
    # "no entries loaded" regardless of the registry's raw size.
    unloaded = _make_entry("entry-failed-load")
    assert unloaded.runtime_data is None
    hass = _resolver_hass([unloaded])

    call = SimpleNamespace(data={})

    raised: HomeAssistantError | None = None
    try:
        _resolve_service_target_entry(hass, call)
    except HomeAssistantError as exc:
        raised = exc
    assert raised is not None, (
        "No loaded entries must raise HomeAssistantError so HA logs an "
        "ops-level failure instead of treating it as caller validation."
    )
    # Belt-and-braces: must NOT be a ServiceValidationError subclass route.
    # (ServiceValidationError inherits from HomeAssistantError in HA, so a
    # bare ``except HomeAssistantError`` would catch both — pin the exact
    # type so future refactors can't silently downgrade the error class.)
    assert not isinstance(raised, ServiceValidationError), (
        "Zero-entries case must raise HomeAssistantError, not "
        "ServiceValidationError — there is no caller-side fix."
    )


# ---------------------------------------------------------------------------
# Static grep belt-and-braces against legacy patterns
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# handle_reset_today — behavioural coverage (Constitution P17)
#
# The silver-checklist test only verifies that the handler raises
# HomeAssistantError syntactically. None of the existing suite exercises
# the handler's actual side effects (calling reset_daily on each provider,
# persisting state, surviving a per-provider exception). These four tests
# fill that gap so future refactors of __init__.py:264 cannot regress the
# behaviour silently.
# ---------------------------------------------------------------------------


def _capture_reset_today_handler(hass: MagicMock) -> MagicMock:
    """Pull the reset_today handler out of the service-registration calls."""
    for call in hass.services.async_register.call_args_list:
        if call.args[1] == "reset_today":
            return call.args[2]
    raise AssertionError("reset_today handler was never registered")


def _make_provider(provider_id: str, *, raises: Exception | None = None) -> MagicMock:
    """Stub provider exposing the surface handle_reset_today reaches into."""
    provider = MagicMock()
    provider.id = provider_id
    if raises is not None:
        provider.reset_daily = MagicMock(side_effect=raises)
    else:
        provider.reset_daily = MagicMock()
    return provider


def test_handle_reset_today_raises_home_assistant_error_when_no_entries():
    """No PriceHawk entries with active runtime_data → HomeAssistantError.

    Beta.9 retro-review of PR #152: silent success is unacceptable when the
    service cannot perform its job. The user must be told to reload the
    integration. This is the contract; do not weaken to a log-and-return.

    Assertion pins the exact user-visible message — fuzzy substring matches
    let copy regressions slip past. The string lives at __init__.py:289.
    """
    coord = _make_coordinator()
    hass = _make_hass()
    entry = _make_entry()

    p1, p2, p3, p4, p5, p6 = _patch_deps(coord)
    with p1, p2, p3, p4, p5, p6:
        asyncio.run(async_setup_entry(hass, entry))

    reset_handler = _capture_reset_today_handler(hass)

    # Drop the entry from the registry AND clear runtime_data so the handler
    # finds no entries-with-runtime-data candidates.
    hass.config_entries.async_entries.return_value = []
    entry.runtime_data = None

    call_obj = SimpleNamespace(data={})
    try:
        asyncio.run(reset_handler(call_obj))
    except HomeAssistantError as exc:
        assert "no PriceHawk entries with active runtime data" in str(exc)
    else:
        raise AssertionError(
            "handle_reset_today must raise HomeAssistantError when no entries "
            "with runtime_data are loaded (Silver action-exceptions rule, PR #152)."
        )


def test_handle_reset_today_zeros_each_provider_daily_accumulators():
    """Two providers registered on coord._providers → reset_daily called on each.

    The handler iterates coord._providers.values(); verify every entry's
    reset_daily fires exactly once per service call.
    """
    coord = _make_coordinator()
    provider_a = _make_provider("dwt")
    provider_b = _make_provider("amber")
    coord._providers = {"dwt": provider_a, "amber": provider_b}

    entry = _make_entry()
    hass = _make_hass(registered_entries=[entry])

    p1, p2, p3, p4, p5, p6 = _patch_deps(coord)
    with p1, p2, p3, p4, p5, p6:
        asyncio.run(async_setup_entry(hass, entry))

    reset_handler = _capture_reset_today_handler(hass)
    call_obj = SimpleNamespace(data={})
    asyncio.run(reset_handler(call_obj))

    provider_a.reset_daily.assert_called_once_with()
    provider_b.reset_daily.assert_called_once_with()


def test_handle_reset_today_persists_state_after_reset():
    """After zeroing the providers, coord.async_persist_state must be awaited
    so the cleared accumulators survive an HA restart. Without persistence
    the user would see the residual untouched values reappear on next
    restore — defeating the purpose of the service.
    """
    coord = _make_coordinator()
    coord._providers = {"dwt": _make_provider("dwt")}

    entry = _make_entry()
    hass = _make_hass(registered_entries=[entry])

    p1, p2, p3, p4, p5, p6 = _patch_deps(coord)
    with p1, p2, p3, p4, p5, p6:
        asyncio.run(async_setup_entry(hass, entry))

    # Reset to ignore the setup-time persistence (schedule_persist + first
    # refresh don't await persist_state, but be defensive against future
    # changes that might).
    coord.async_persist_state.reset_mock()

    reset_handler = _capture_reset_today_handler(hass)
    call_obj = SimpleNamespace(data={})
    asyncio.run(reset_handler(call_obj))

    coord.async_persist_state.assert_awaited_once()


def test_handle_reset_today_continues_when_one_provider_reset_raises():
    """Provider A's reset_daily raises → Provider B still gets reset.

    The handler wraps each provider call in try/except (noqa: BLE001 —
    "never sink the batch") so a single bad provider cannot prevent the
    rest from being cleaned up. Also verifies persist_state still runs.
    """
    coord = _make_coordinator()
    provider_a = _make_provider("dwt", raises=RuntimeError("simulated failure"))
    provider_b = _make_provider("amber")
    # Dict order is insertion order in 3.7+; A iterates before B so we
    # actually test that B runs AFTER A raised.
    coord._providers = {"dwt": provider_a, "amber": provider_b}

    entry = _make_entry()
    hass = _make_hass(registered_entries=[entry])

    p1, p2, p3, p4, p5, p6 = _patch_deps(coord)
    with p1, p2, p3, p4, p5, p6:
        asyncio.run(async_setup_entry(hass, entry))

    coord.async_persist_state.reset_mock()

    reset_handler = _capture_reset_today_handler(hass)
    call_obj = SimpleNamespace(data={})
    # Must NOT propagate — the handler swallows per-provider exceptions.
    asyncio.run(reset_handler(call_obj))

    provider_a.reset_daily.assert_called_once_with()
    provider_b.reset_daily.assert_called_once_with()
    coord.async_persist_state.assert_awaited_once()


def test_no_legacy_hass_data_reads():
    """No file in the integration may reference the legacy hass.data[DOMAIN] pattern."""
    pkg = Path(__file__).resolve().parents[1] / "custom_components" / "pricehawk"
    forbidden = (
        "hass.data[DOMAIN]",
        "hass.data.get(DOMAIN)",
        "hass.data.setdefault(DOMAIN",
        'hasattr(entry, "runtime_data")',
    )
    offenders: list[tuple[Path, str]] = []
    for py_file in pkg.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in text:
                offenders.append((py_file.relative_to(pkg.parent.parent), needle))
    assert not offenders, f"Legacy patterns leaked back in: {offenders}"


# ---------------------------------------------------------------------------
# Constitution P16 (Data Integrity) — config-entry + Store migration
# ---------------------------------------------------------------------------


def test_async_migrate_entry_handles_unknown_version():
    """``async_migrate_entry`` must refuse downgrades and refuse unknown
    upgrades — silent success on either path would leave the user with
    a config entry the integration doesn't actually understand, which
    surfaces as confusing runtime errors much later in the lifecycle.

    Constitution P19 (Platform Conventions): HA's setup machinery
    expects unrecoverable migration paths to raise ``ConfigEntryError``
    so the exception message lands in the UI. Returning ``False``
    triggers a generic "needs migration" notice with no diagnostic.
    """
    import pytest

    from homeassistant.exceptions import ConfigEntryError

    from custom_components.pricehawk import (
        CONFIG_ENTRY_VERSION,
        _CONFIG_ENTRY_MIGRATORS,
        async_migrate_entry,
    )

    hass = _make_hass()

    # Case 1: entry from a NEWER version than this integration supports.
    # Refuse — we cannot safely down-convert because we don't know
    # which fields exist in the future schema.
    future_entry = _make_entry("future-entry")
    future_entry.version = CONFIG_ENTRY_VERSION + 5
    future_entry.minor_version = 1
    future_entry.data = {"some": "data"}
    future_entry.options = {}

    with pytest.raises(ConfigEntryError, match="downgrade"):
        asyncio.run(async_migrate_entry(hass, future_entry))

    # Case 2: entry from an OLDER version with no migrator registered.
    # Either we forgot to add one before bumping the version, or the
    # entry is from an unsupported antique. Refuse loudly.
    if CONFIG_ENTRY_VERSION > 1:
        # Pick a gap version — guaranteed missing because we never
        # register pre-test migrators.
        gap_version = CONFIG_ENTRY_VERSION - 1
        # Defensive: skip if the project happens to have registered
        # this migrator legitimately (the test would still pass after
        # a real bump+migrator-add, just becomes redundant).
        if gap_version not in _CONFIG_ENTRY_MIGRATORS:
            ancient_entry = _make_entry("ancient-entry")
            ancient_entry.version = gap_version
            ancient_entry.minor_version = 1
            ancient_entry.data = {}
            ancient_entry.options = {}
            with pytest.raises(ConfigEntryError, match="No migrator registered"):
                asyncio.run(async_migrate_entry(hass, ancient_entry))

    # Case 3: entry already at current version — return True
    # defensively (HA usually doesn't call us in this case, but a
    # future HA change shouldn't break setup).
    current_entry = _make_entry("current-entry")
    current_entry.version = CONFIG_ENTRY_VERSION
    current_entry.minor_version = 1
    current_entry.data = {}
    current_entry.options = {}
    result = asyncio.run(async_migrate_entry(hass, current_entry))
    assert result is True


def test_store_migration_preserves_known_fields():
    """``PriceHawkStore._async_migrate_func`` must NEVER discard known
    fields when walking versions forward. The previous behaviour
    (silent discard on version mismatch in coordinator.async_restore_state)
    would have wiped accumulated daily_cost_history, monthly savings,
    ranked alternatives, and every provider accumulator on the FIRST
    deliberate STORAGE_VERSION bump.

    Constitution P16: persistence changes must consider migrations
    and rollback safety. Test contract:

    * Round-trip at current version — identity migration preserves
      every field, stamps ``_storage_version``.
    * Future fictional v→v+1 migrator (registered for the test) runs
      cleanly and preserves all input fields it does not explicitly
      rewrite.
    * Refuses to downgrade.
    """
    import importlib

    from custom_components.pricehawk import const as const_mod
    from custom_components.pricehawk import storage as storage_mod

    # Re-import to make sure we're testing live module state, not
    # a stale cached copy from a previous test session.
    importlib.reload(storage_mod)

    sample_payload: dict[str, Any] = {
        "_storage_version": storage_mod.STORAGE_VERSION,
        "globird": {"import_kwh_today": 12.5, "import_cost_today_c": 350.0},
        "amber": {"import_kwh_today": 8.1, "import_cost_today_c": 110.0},
        "amber_import_c": 23.4,
        "amber_export_c": 5.1,
        "wholesale_c": 8.7,
        "saving_month_aud": 14.22,
        "last_month": 5,
        "last_date": "2026-05-26",
        "price_history": [{"ts": "2026-05-26T10:00:00", "c": 9.1}],
        "daily_wins": {"globird": 3, "amber": 5},
        "daily_cost_history": [
            {"date": "2026-05-25", "globird": 4.10, "amber": 3.85},
        ],
        "today_schedule": [{"start": "00:00", "end": "06:00", "c": 7.0}],
        "last_explanation": {"winner": "amber", "delta": 0.25},
    }

    hass = _make_hass()
    store = storage_mod.PriceHawkStore(hass)

    # Identity round-trip — same major + minor in/out, must be
    # a no-op apart from the version stamp.
    migrated = asyncio.run(
        store._async_migrate_func(
            const_mod.STORAGE_VERSION,
            const_mod.STORAGE_MINOR_VERSION,
            sample_payload,
        )
    )
    for key, value in sample_payload.items():
        assert migrated[key] == value, (
            f"Identity migration dropped/altered key {key!r}: {value!r} → {migrated.get(key)!r}"
        )
    assert migrated["_storage_version"] == const_mod.STORAGE_VERSION

    # Refuse downgrade. A persisted payload from version N+1 must
    # not be down-converted — we don't know the future schema.
    try:
        asyncio.run(
            store._async_migrate_func(
                const_mod.STORAGE_VERSION + 1,
                1,
                sample_payload,
            )
        )
    except ValueError as exc:
        assert "newer" in str(exc).lower()
    else:
        raise AssertionError(
            "Downgrade migration must raise ValueError, not silently "
            "rewrite into a stale schema. Constitution P16."
        )

    # Simulate a future major bump WITH a migrator registered.
    # Verify the migrator chain walks forward and the input payload
    # survives intact aside from whatever the migrator explicitly
    # rewrites. Sentinel field ``__migrated_marker`` proves the
    # migrator ran; every other known field must round-trip.
    fake_old_major = const_mod.STORAGE_VERSION - 1
    if fake_old_major < 1:
        # Can't simulate "older major" when current major is 1; the
        # identity round-trip above already covers same-version. The
        # downgrade-refusal test covers the future case. This branch
        # is only reachable once STORAGE_VERSION is bumped to ≥2.
        return

    async def _fake_migrator(old: dict[str, Any]) -> dict[str, Any]:
        new = dict(old)
        new["__migrated_marker"] = True
        return new

    # Save-and-restore the slot so a real registered migrator (e.g.
    # v1→v2 identity) survives the test. ``del + put back`` would lose
    # the real one if the test errored between the two operations.
    _SENTINEL: dict[str, Any] = {}
    previous = storage_mod._MAJOR_MIGRATORS.get(fake_old_major, _SENTINEL)
    storage_mod._MAJOR_MIGRATORS[fake_old_major] = _fake_migrator
    try:
        migrated = asyncio.run(store._async_migrate_func(fake_old_major, 1, sample_payload))
    finally:
        if previous is _SENTINEL:
            del storage_mod._MAJOR_MIGRATORS[fake_old_major]
        else:
            storage_mod._MAJOR_MIGRATORS[fake_old_major] = previous

    assert migrated.get("__migrated_marker") is True
    for key in (
        "globird",
        "amber",
        "amber_import_c",
        "saving_month_aud",
        "daily_wins",
        "daily_cost_history",
        "today_schedule",
        "last_explanation",
    ):
        assert migrated[key] == sample_payload[key], (
            f"Major-version migrator must preserve {key!r}; "
            f"Constitution P16 forbids silent data loss across "
            f"schema bumps."
        )
    assert migrated["_storage_version"] == const_mod.STORAGE_VERSION


def test_store_migration_runs_through_async_load_envelope():
    """End-to-end envelope check: when a user upgrades the integration,
    HA calls ``Store.async_load`` and the engine dispatches through
    ``_async_migrate_func`` on version mismatch BEFORE returning the
    payload. Tests that poke ``_async_migrate_func`` directly miss the
    envelope path (version-check dispatch + post-migration save).

    This test seeds a v1 payload on the stub store, configures the
    store for the current STORAGE_VERSION, and asserts that:

    * ``async_load`` returns a payload migrated forward.
    * The registered ``_MAJOR_MIGRATORS[1]`` (the v1→v2 no-op) ran
      (verified by version stamp + payload integrity).
    * Subsequent loads are stable (already-migrated, no re-migration).

    Constitution P11 — proves the migrator chain actually works
    end-to-end on real persisted state, not just under direct call.
    """
    import importlib

    from custom_components.pricehawk import const as const_mod
    from custom_components.pricehawk import storage as storage_mod

    # Refresh modules so the in-code constants/registries reflect the
    # current source (in case another test mutated them).
    importlib.reload(storage_mod)

    # The current chain ships with v1 → v2 (no-op). The envelope test
    # is meaningful only when there's at least one major migrator
    # registered — guard so the assertion stays valid through future
    # bumps (any STORAGE_VERSION > 1 with the chain populated).
    assert const_mod.STORAGE_VERSION >= 2, (
        "Once a real v1→v2 migrator ships, the envelope test must be "
        "kept exercising the lowest registered major; update this guard."
    )
    assert 1 in storage_mod._MAJOR_MIGRATORS, (
        "v1→v2 migrator missing — every install on disk is v1, the chain must cover them."
    )

    legacy_v1_payload: dict[str, Any] = {
        # Pre-Constitution-P16 payload: stamped with the LEGACY version
        # 1 sentinel (matches coordinator.async_persist_state behaviour
        # in production before this PR).
        "_storage_version": 1,
        "globird": {"import_kwh_today": 7.7, "import_cost_today_c": 213.4},
        "amber": {"import_kwh_today": 5.0, "import_cost_today_c": 88.0},
        "daily_cost_history": [
            {"date": "2026-05-25", "globird": 4.10, "amber": 3.85},
        ],
        "saving_month_aud": 9.12,
    }

    hass = _make_hass()
    store = storage_mod.PriceHawkStore(hass)

    # Plant a v1 payload — the next async_load must run the migrator.
    store.seed_stored(legacy_v1_payload, major=1, minor=1)

    loaded = asyncio.run(store.async_load())

    assert loaded is not None
    # Version stamp must reflect the post-migration shape; this is the
    # ONLY contract async_load post-migration commits to (HA itself
    # also resaves with the new version on the next async_save).
    assert loaded["_storage_version"] == const_mod.STORAGE_VERSION
    # v1→v2 is a no-op identity migrator: every input field round-trips
    # unchanged. If a future v2→v3 mutates fields, this assertion will
    # need to be updated alongside that migrator.
    for key in ("globird", "amber", "daily_cost_history", "saving_month_aud"):
        assert loaded[key] == legacy_v1_payload[key], (
            f"Envelope migration dropped/altered {key!r}: "
            f"{legacy_v1_payload[key]!r} → {loaded.get(key)!r}"
        )

    # Second load is stable — the stub store re-saved at the current
    # version, so no further migration runs. This mirrors HA's
    # behaviour: a re-loaded migrated payload is now native-version.
    loaded_again = asyncio.run(store.async_load())
    assert loaded_again is not None
    assert loaded_again["_storage_version"] == const_mod.STORAGE_VERSION


def test_async_migrate_entry_succeeds_at_current_version():
    """Smoke check: at-version entries return True without raising,
    even after the downgrade/missing-migrator paths switched from
    ``return False`` to ``raise ConfigEntryError``. Guards against a
    regression where the equal-version branch accidentally falls into
    the raise paths."""
    from custom_components.pricehawk import (
        CONFIG_ENTRY_VERSION,
        async_migrate_entry,
    )

    hass = _make_hass()
    entry = _make_entry("at-version-entry")
    entry.version = CONFIG_ENTRY_VERSION
    entry.minor_version = 1
    entry.data = {"some": "data"}
    entry.options = {}

    result = asyncio.run(async_migrate_entry(hass, entry))
    assert result is True


# ---------------------------------------------------------------------------
# Codex follow-up (2026-05-27) — migrator chaining + minor_version persisted
# ---------------------------------------------------------------------------


def test_async_migrate_entry_chains_migrators_progressively():
    """v1→v3 skip migration: step 2 must see the v1→v2-TRANSFORMED
    payload, NOT a re-read of ``entry.data``.

    Constitution P16: silent data corruption is unacceptable. The
    previous implementation passed the raw entry into every migrator
    callable, so each step re-read ``entry.data`` / ``entry.options``
    and discarded its predecessor's output. A v1→v3 entry would land
    stamped as v3 wrapped around a v1-shaped body.
    """
    from custom_components.pricehawk import (
        _CONFIG_ENTRY_MIGRATORS,
        async_migrate_entry,
    )

    hass = _make_hass()
    entry = _make_entry("chain-entry")
    entry.version = 1
    entry.minor_version = 1
    entry.data = {"v": 1, "field": "original"}
    entry.options = {"opt_v": 1}

    seen_by_v2: dict[str, Any] = {}
    seen_by_v3: dict[str, Any] = {}

    async def v1_to_v2(
        _hass: Any,
        data: dict[str, Any],
        options: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        seen_by_v2["data"] = dict(data)
        seen_by_v2["options"] = dict(options)
        new_data = dict(data)
        new_data["v"] = 2
        new_data["field"] = "after_v2"
        new_options = dict(options)
        new_options["opt_v"] = 2
        return new_data, new_options

    async def v2_to_v3(
        _hass: Any,
        data: dict[str, Any],
        options: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        seen_by_v3["data"] = dict(data)
        seen_by_v3["options"] = dict(options)
        new_data = dict(data)
        new_data["v"] = 3
        new_data["field"] = "after_v3"
        new_options = dict(options)
        new_options["opt_v"] = 3
        return new_data, new_options

    # Patch the global CONFIG_ENTRY_VERSION sentinel + register both
    # migrators so async_migrate_entry walks 1 → 2 → 3. Save/restore
    # the registry so the test doesn't leak state into siblings.
    saved_1 = _CONFIG_ENTRY_MIGRATORS.get(1)
    saved_2 = _CONFIG_ENTRY_MIGRATORS.get(2)
    _CONFIG_ENTRY_MIGRATORS[1] = v1_to_v2
    _CONFIG_ENTRY_MIGRATORS[2] = v2_to_v3
    try:
        with patch("custom_components.pricehawk.CONFIG_ENTRY_VERSION", 3):
            result = asyncio.run(async_migrate_entry(hass, entry))
    finally:
        if saved_1 is None:
            _CONFIG_ENTRY_MIGRATORS.pop(1, None)
        else:
            _CONFIG_ENTRY_MIGRATORS[1] = saved_1
        if saved_2 is None:
            _CONFIG_ENTRY_MIGRATORS.pop(2, None)
        else:
            _CONFIG_ENTRY_MIGRATORS[2] = saved_2

    assert result is True
    # Step 1 receives the original payload.
    assert seen_by_v2["data"] == {"v": 1, "field": "original"}
    assert seen_by_v2["options"] == {"opt_v": 1}
    # Step 2 receives the v1→v2-TRANSFORMED payload (not a re-read
    # of entry.data). This is the entire point of the fix.
    assert seen_by_v3["data"] == {"v": 2, "field": "after_v2"}, (
        "v2→v3 migrator received the raw entry payload instead of "
        "the v1→v2-transformed payload — migration chain is broken. "
        "Constitution P16."
    )
    assert seen_by_v3["options"] == {"opt_v": 2}

    # And the persisted shape reflects the FULL chain output.
    update_call = hass.config_entries.async_update_entry.call_args
    assert update_call is not None
    assert update_call.kwargs["data"] == {"v": 3, "field": "after_v3"}
    assert update_call.kwargs["options"] == {"opt_v": 3}


def test_async_migrate_entry_persists_minor_version():
    """``async_update_entry`` must be called with an explicit
    ``minor_version`` kwarg so HA stamps both axes on the entry. Without
    it, a future minor bump would re-enter migration against a stale
    stored minor.
    """
    from custom_components.pricehawk import (
        _CONFIG_ENTRY_MIGRATORS,
        async_migrate_entry,
    )

    hass = _make_hass()
    entry = _make_entry("minor-version-entry")
    entry.version = 1
    entry.minor_version = 1
    entry.data = {}
    entry.options = {}

    async def v1_to_v2(
        _hass: Any,
        data: dict[str, Any],
        options: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return dict(data), dict(options)

    saved_1 = _CONFIG_ENTRY_MIGRATORS.get(1)
    _CONFIG_ENTRY_MIGRATORS[1] = v1_to_v2
    try:
        with patch("custom_components.pricehawk.CONFIG_ENTRY_VERSION", 2):
            asyncio.run(async_migrate_entry(hass, entry))
    finally:
        if saved_1 is None:
            _CONFIG_ENTRY_MIGRATORS.pop(1, None)
        else:
            _CONFIG_ENTRY_MIGRATORS[1] = saved_1

    update_call = hass.config_entries.async_update_entry.call_args
    assert update_call is not None
    assert "minor_version" in update_call.kwargs, (
        "async_update_entry must be called with an explicit "
        "minor_version kwarg so HA persists both version axes."
    )
    assert update_call.kwargs["minor_version"] == 1
    assert update_call.kwargs["version"] == 2


# ---------------------------------------------------------------------------
# Codex follow-up (2026-05-27) — storage: non-dict payload + newer-minor
# ---------------------------------------------------------------------------


def test_store_migrate_returns_empty_state_on_non_dict_payload(caplog):
    """Non-dict payloads (corruption signal) must NOT abort
    ``async_load`` — they must log + return an empty migrated state
    so integration setup proceeds.

    Codex follow-up #3 (2026-05-27): reverses follow-up #2's loud
    raise. ``_async_migrate_func`` raising aborts ``async_load``
    which aborts integration setup — the user would lose a working
    integration to surface a single corrupt blob. HA's Store contract
    (Constitution P19) is "be resilient on the read path". The
    coordinator restore path treats the empty payload as a fresh-
    install state and re-accumulates from live data.
    """
    import logging

    from custom_components.pricehawk import const as const_mod
    from custom_components.pricehawk import storage as storage_mod

    hass = _make_hass()
    store = storage_mod.PriceHawkStore(hass)

    bogus_payloads: list[Any] = [[1, 2, 3], "totally not a dict", 42, None]
    for bogus in bogus_payloads:
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger="custom_components.pricehawk.storage"):
            migrated = asyncio.run(
                store._async_migrate_func(
                    const_mod.STORAGE_VERSION,
                    const_mod.STORAGE_MINOR_VERSION,
                    bogus,  # type: ignore[arg-type]
                )
            )

        # Empty migrated state — the post-migration save will overwrite
        # the corrupt blob with a fresh envelope.
        assert isinstance(migrated, dict)
        assert migrated.get("_storage_version_major") == const_mod.STORAGE_VERSION
        assert migrated.get("_storage_version_minor") == const_mod.STORAGE_MINOR_VERSION
        # Loud error log so the corruption is visible in diagnostics.
        error_logs = [r for r in caplog.records if r.levelname == "ERROR"]
        assert any(
            "not a dict" in r.message and type(bogus).__name__ in r.message for r in error_logs
        ), (
            f"Expected an ERROR log naming the payload type "
            f"({type(bogus).__name__}); got {[r.message for r in error_logs]}"
        )


def test_async_migrate_entry_runs_minor_chain_when_major_matches():
    """Same-major minor upgrades must walk the minor-migrator chain.

    Codex follow-up #2 (2026-05-27): the previous implementation only
    advanced on major. A 1.1 → 1.3 entry would slip through with the
    legacy minor body silently stamped as 1.3 — Constitution P16
    silent-data-corruption.

    Patch ``CONFIG_ENTRY_MINOR_VERSION`` forward, register two minor
    migrators, and assert each step receives the previous step's
    output (chaining), not a re-read of ``entry.data``.
    """
    from custom_components.pricehawk import (
        _CONFIG_ENTRY_MINOR_MIGRATORS,
        async_migrate_entry,
    )

    hass = _make_hass()
    entry = _make_entry("minor-chain-entry")
    entry.version = 1
    entry.minor_version = 1
    entry.data = {"m": 1, "field": "original"}
    entry.options = {"opt_m": 1}

    seen_by_minor_2: dict[str, Any] = {}
    seen_by_minor_3: dict[str, Any] = {}

    async def m1_to_m2(
        _hass: Any,
        data: dict[str, Any],
        options: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        seen_by_minor_2["data"] = dict(data)
        seen_by_minor_2["options"] = dict(options)
        new_data = dict(data)
        new_data["m"] = 2
        new_data["field"] = "after_m2"
        new_options = dict(options)
        new_options["opt_m"] = 2
        return new_data, new_options

    async def m2_to_m3(
        _hass: Any,
        data: dict[str, Any],
        options: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        seen_by_minor_3["data"] = dict(data)
        seen_by_minor_3["options"] = dict(options)
        new_data = dict(data)
        new_data["m"] = 3
        new_data["field"] = "after_m3"
        new_options = dict(options)
        new_options["opt_m"] = 3
        return new_data, new_options

    saved_1 = _CONFIG_ENTRY_MINOR_MIGRATORS.get(1)
    saved_2 = _CONFIG_ENTRY_MINOR_MIGRATORS.get(2)
    _CONFIG_ENTRY_MINOR_MIGRATORS[1] = m1_to_m2
    _CONFIG_ENTRY_MINOR_MIGRATORS[2] = m2_to_m3
    try:
        with patch("custom_components.pricehawk.CONFIG_ENTRY_MINOR_VERSION", 3):
            result = asyncio.run(async_migrate_entry(hass, entry))
    finally:
        if saved_1 is None:
            _CONFIG_ENTRY_MINOR_MIGRATORS.pop(1, None)
        else:
            _CONFIG_ENTRY_MINOR_MIGRATORS[1] = saved_1
        if saved_2 is None:
            _CONFIG_ENTRY_MINOR_MIGRATORS.pop(2, None)
        else:
            _CONFIG_ENTRY_MINOR_MIGRATORS[2] = saved_2

    assert result is True
    # Step 1 receives the original payload.
    assert seen_by_minor_2["data"] == {"m": 1, "field": "original"}
    assert seen_by_minor_2["options"] == {"opt_m": 1}
    # Step 2 receives the m1→m2 TRANSFORMED payload, NOT a re-read
    # of entry.data — same chaining contract as the major chain.
    assert seen_by_minor_3["data"] == {"m": 2, "field": "after_m2"}, (
        "m2→m3 migrator received the raw entry payload instead of "
        "the m1→m2-transformed payload — minor chain is broken. "
        "Constitution P16."
    )
    assert seen_by_minor_3["options"] == {"opt_m": 2}

    # Persisted shape reflects the full minor chain.
    update_call = hass.config_entries.async_update_entry.call_args
    assert update_call is not None
    assert update_call.kwargs["data"] == {"m": 3, "field": "after_m3"}
    assert update_call.kwargs["options"] == {"opt_m": 3}


def test_async_migrate_entry_persists_target_minor_version():
    """``async_update_entry`` must be called with ``minor_version`` set
    to the CURRENT ``CONFIG_ENTRY_MINOR_VERSION``, not a hard-coded 1.

    Codex follow-up #2 (2026-05-27): the previous call hard-coded
    ``minor_version=1`` regardless of how far the migrator walked. A
    1.0 → 1.5 migration would have left the entry stamped at 1.1 (or
    1.0, depending on HA's behaviour), so the next load would re-enter
    migration and re-run every minor migrator. Constitution P16.
    """
    from custom_components.pricehawk import (
        _CONFIG_ENTRY_MINOR_MIGRATORS,
        async_migrate_entry,
    )

    hass = _make_hass()
    entry = _make_entry("minor-target-entry")
    entry.version = 1
    entry.minor_version = 1
    entry.data = {}
    entry.options = {}

    async def identity(
        _hass: Any,
        data: dict[str, Any],
        options: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return dict(data), dict(options)

    saved_1 = _CONFIG_ENTRY_MINOR_MIGRATORS.get(1)
    _CONFIG_ENTRY_MINOR_MIGRATORS[1] = identity
    try:
        with patch("custom_components.pricehawk.CONFIG_ENTRY_MINOR_VERSION", 2):
            asyncio.run(async_migrate_entry(hass, entry))
    finally:
        if saved_1 is None:
            _CONFIG_ENTRY_MINOR_MIGRATORS.pop(1, None)
        else:
            _CONFIG_ENTRY_MINOR_MIGRATORS[1] = saved_1

    update_call = hass.config_entries.async_update_entry.call_args
    assert update_call is not None
    assert update_call.kwargs["minor_version"] == 2, (
        "async_update_entry must persist the TARGET minor_version "
        "(CONFIG_ENTRY_MINOR_VERSION), not a hard-coded 1. Codex "
        "follow-up #2."
    )


def test_async_migrate_entry_allows_newer_minor_same_major_with_debug_log(caplog):
    """Same-major newer-minor entries MUST load — per HA's documented
    config entry contract, minor versions are backward-compatible
    within a major.

    Codex follow-up #3 (2026-05-27): reverses follow-up #2's loud
    refusal after Codex correctly pointed at the HA convention at
    https://developers.home-assistant.io/docs/config_entries_index/#migrating-config-entries.
    A 1.2 entry loaded by a 1.1 integration is supposed to work. The
    older code reads 1.2-only fields via ``.get(key, default)`` and
    ignores anything it doesn't recognise.

    Constitution P19 (Platform Conventions) OVERRIDES the earlier
    hard-stop guidance.
    """
    import logging

    from custom_components.pricehawk import (
        CONFIG_ENTRY_MINOR_VERSION,
        CONFIG_ENTRY_VERSION,
        async_migrate_entry,
    )

    hass = _make_hass()
    entry = _make_entry("newer-minor-entry")
    entry.version = CONFIG_ENTRY_VERSION
    entry.minor_version = CONFIG_ENTRY_MINOR_VERSION + 1
    entry.data = {"some": "data"}
    entry.options = {}

    with caplog.at_level(logging.DEBUG, logger="custom_components.pricehawk"):
        result = asyncio.run(async_migrate_entry(hass, entry))

    assert result is True, (
        "Same-major newer-minor entries must LOAD (return True) — "
        "HA's documented contract makes minor versions backward-"
        "compatible within a major. Codex follow-up #3."
    )
    # async_update_entry must NOT be called — we did not migrate, we
    # accepted the entry as-is.
    assert hass.config_entries.async_update_entry.call_count == 0, (
        "Newer-minor entries must not be re-stamped by the older "
        "integration — that would silently downgrade the minor."
    )
    # Diagnostic trail: a debug log records the mismatch so support
    # can spot it without the user log being noisy.
    assert any(
        "newer minor version" in record.message
        for record in caplog.records
        if record.levelname == "DEBUG"
    ), "Newer-minor load must emit a debug log for support diagnostics."


def test_store_migrate_allows_newer_minor_same_major_with_debug_log(caplog):
    """Same-major newer-minor Store payloads MUST load — HA's
    documented Store contract makes minor versions backward-
    compatible within a major.

    Codex follow-up #3 (2026-05-27): reverses follow-up #2's loud
    refusal. The older code reads additive fields via ``.get(key,
    default)`` so a newer-minor payload simply has fields the older
    code ignores.
    """
    import logging

    from custom_components.pricehawk import const as const_mod
    from custom_components.pricehawk import storage as storage_mod

    hass = _make_hass()
    store = storage_mod.PriceHawkStore(hass)

    payload = {
        "_storage_version": const_mod.STORAGE_VERSION,
        "globird": {"import_kwh_today": 1.5},
        "amber": {"import_kwh_today": 2.0},
        "future_minor_only_field": "added in a future minor bump",
    }

    with caplog.at_level(logging.DEBUG, logger="custom_components.pricehawk.storage"):
        migrated = asyncio.run(
            store._async_migrate_func(
                const_mod.STORAGE_VERSION,
                const_mod.STORAGE_MINOR_VERSION + 1,
                payload,
            )
        )

    # All known fields round-trip; the future-only field is preserved
    # too (we don't strip anything we don't recognise — older code
    # ignores via .get).
    assert migrated["globird"] == payload["globird"]
    assert migrated["amber"] == payload["amber"]
    assert migrated["future_minor_only_field"] == payload["future_minor_only_field"]
    assert migrated["_storage_version"] == const_mod.STORAGE_VERSION
    # Debug log confirms the diagnostic trail is in place.
    assert any(
        "newer minor version" in record.message
        for record in caplog.records
        if record.levelname == "DEBUG"
    ), "Newer-minor Store load must emit a debug log."
