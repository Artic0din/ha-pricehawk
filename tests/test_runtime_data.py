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
    hass.config_entries.async_unload_platforms = AsyncMock(
        return_value=unload_platforms_result
    )
    hass.config_entries.async_entries = MagicMock(
        return_value=list(registered_entries or [])
    )
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
    """Context-manager bundle for the four collaborators we always patch."""
    return (
        patch(
            "custom_components.pricehawk.PriceHawkCoordinator",
            return_value=coord,
        ),
        patch("custom_components.pricehawk.copy_www_assets", new=AsyncMock()),
        patch("custom_components.pricehawk.setup_panel_iframe", new=AsyncMock()),
        patch(
            "custom_components.pricehawk.setup_panel_custom_v2",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.pricehawk.register_lovelace_card_resource",
            new=AsyncMock(),
        ),
        patch("custom_components.pricehawk.remove_panel", new=AsyncMock()),
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
        patch("custom_components.pricehawk.copy_www_assets", new=AsyncMock()),
        patch("custom_components.pricehawk.setup_panel_iframe", new=AsyncMock()),
        patch(
            "custom_components.pricehawk.setup_panel_custom_v2",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.pricehawk.register_lovelace_card_resource",
            new=AsyncMock(),
        ),
        patch("custom_components.pricehawk.remove_panel", new=AsyncMock()),
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
        assert removed == {"analyze_csv", "backfill_history", "rank_alternatives"}
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

        hass.config_entries.async_unload_platforms = AsyncMock(
            side_effect=_spy_unload
        )

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
    assert "entry_id" in msg, (
        "Error message must name the parameter the caller is missing."
    )


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

# Constitution-01: analyze_csv with empty rows must raise SVE, not return
# ---------------------------------------------------------------------------


def test_analyze_csv_empty_rows_raises_service_validation_error():
    """Engineering Constitution P3 (No Silent Scope Reduction) + P5
    (Production Standards Apply Universally) + HA Silver action-exceptions.

    The prior implementation logged at ERROR and silently ``return``ed when
    the caller passed no rows — HA's service-call machinery treated that as
    success, so the dashboard saw no failure and the user was left wondering
    why the comparison numbers never updated. The handler must raise
    ``ServiceValidationError`` so the empty-input case is visible to the UI.
    """
    import pytest
    from homeassistant.exceptions import ServiceValidationError

    from custom_components.pricehawk import async_setup_entry

    coord = _make_coordinator()
    entry = _make_entry()
    hass = _make_hass(registered_entries=[entry])

    p1, p2, p3, p4, p5, p6 = _patch_deps(coord)
    with p1, p2, p3, p4, p5, p6:
        asyncio.run(async_setup_entry(hass, entry))

    analyze_handler = None
    for call in hass.services.async_register.call_args_list:
        if call.args[1] == "analyze_csv":
            analyze_handler = call.args[2]
            break
    assert analyze_handler is not None, "analyze_csv handler not registered"

    # Empty list — the silent-return case under test. ``match=`` pins the
    # user-visible string so any future copy-edit that loses the call to
    # action ("Re-upload the file via the dashboard.") trips this test.
    sve_match = (
        r"analyze_csv: 'rows' is required and must be a non-empty list of "
        r"pre-parsed CSV rows\. Re-upload the file via the dashboard\."
    )
    call_obj = SimpleNamespace(data={"rows": []})
    with pytest.raises(ServiceValidationError, match=sve_match):
        asyncio.run(analyze_handler(call_obj))

    # Omitted ``rows`` key — defaults to [] inside the handler, must also raise.
    call_obj = SimpleNamespace(data={})
    with pytest.raises(ServiceValidationError, match=sve_match):
        asyncio.run(analyze_handler(call_obj))

    # Coordinator must NOT have been touched on the failed path — proves we
    # short-circuit before the executor job runs. The fix-up commit also
    # makes ``analyze_csv_data`` raise ``ValueError`` on empty rows
    # (Constitution P12 — root-cause at the function boundary). The handler
    # short-circuits before reaching that layer; the inner contract is
    # pinned by ``test_empty_rows_raises_value_error`` in
    # ``test_csv_analyzer.py``.
    assert coord.async_set_updated_data.call_count == 0
    hass.async_add_executor_job.assert_not_called()


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

    Constitution P16: data integrity > convenience. Returning False
    here puts HA into the "config entry needs migration" failure
    state, which is loud and actionable.
    """
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

    result = asyncio.run(async_migrate_entry(hass, future_entry))
    assert result is False, (
        "Downgrade attempt must return False, not silently succeed. "
        "Constitution P16."
    )

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
        if gap_version in _CONFIG_ENTRY_MIGRATORS:
            return
        ancient_entry = _make_entry("ancient-entry")
        ancient_entry.version = gap_version
        ancient_entry.minor_version = 1
        ancient_entry.data = {}
        ancient_entry.options = {}
        result = asyncio.run(async_migrate_entry(hass, ancient_entry))
        assert result is False, (
            "Missing migrator must return False, not silently succeed. "
            "Constitution P16."
        )

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
            f"Identity migration dropped/altered key {key!r}: "
            f"{value!r} → {migrated.get(key)!r}"
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

    storage_mod._MAJOR_MIGRATORS[fake_old_major] = _fake_migrator
    try:
        migrated = asyncio.run(
            store._async_migrate_func(fake_old_major, 1, sample_payload)
        )
    finally:
        del storage_mod._MAJOR_MIGRATORS[fake_old_major]

    assert migrated.get("__migrated_marker") is True
    for key in (
        "globird", "amber", "amber_import_c", "saving_month_aud",
        "daily_wins", "daily_cost_history", "today_schedule",
        "last_explanation",
    ):
        assert migrated[key] == sample_payload[key], (
            f"Major-version migrator must preserve {key!r}; "
            f"Constitution P16 forbids silent data loss across "
            f"schema bumps."
        )
    assert migrated["_storage_version"] == const_mod.STORAGE_VERSION
