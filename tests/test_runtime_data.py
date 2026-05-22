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

    # async_create_task: swallow the coroutine cleanly so we don't get
    # "coroutine was never awaited" warnings during teardown.
    def _swallow(coro: Any) -> MagicMock:
        if hasattr(coro, "close"):
            coro.close()
        return MagicMock()

    hass.async_create_task = MagicMock(side_effect=_swallow)
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
    from custom_components.pricehawk import async_setup_entry
    from custom_components.pricehawk.data import PriceHawkData

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
    from custom_components.pricehawk import async_setup_entry, async_unload_entry

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
    from custom_components.pricehawk import async_setup_entry, async_unload_entry

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
    from custom_components.pricehawk import async_setup_entry, async_unload_entry

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
# AC-4b: OptionsFlowWithReload round-trip preserves contract
# ---------------------------------------------------------------------------


def test_options_flow_reload_cycle():
    """unload → setup yields a NEW coordinator in runtime_data; old timers cancelled."""
    from custom_components.pricehawk import async_setup_entry, async_unload_entry

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
    """After runtime_data is swapped, registered handlers see the NEW coordinator."""
    from custom_components.pricehawk import async_setup_entry
    from custom_components.pricehawk.data import PriceHawkData

    original_coord = _make_coordinator()
    hass = _make_hass()
    entry = _make_entry()

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
# Static grep belt-and-braces against legacy patterns
# ---------------------------------------------------------------------------


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
