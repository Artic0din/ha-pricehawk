"""Phase 3.2 — HA-side adapter for universal multi-plan history backfill.

This module is the thin coordination layer between Home Assistant's
recorder and the pure-logic ``cdr.history_replay`` fan-out. Reads
historical grid-power state changes day-by-day, converts them to
evaluator-shaped slots, replays each day through the current plan +
top-K ranked alternatives + (Phase 3.4) named comparator, and merges
the per-day cost rows into the coordinator's ``daily_cost_history``.

Phase 3.2 removes the Amber-API-only backfill that this module used
to be. Amber's role narrowed to a *truth overlay* — the live
coordinator loop already writes one row/day with Amber's actual cost
once a day, so the backfill no longer needs to re-fetch Amber prices
or compute Amber-specific costs. The multi-plan replay handles the
user's current plan + N alternatives through a single code path.

Public API:
    async def backfill_daily_cost_history(
        hass, grid_sensor_entity, plans,
        *, days_back=30, entry_options=None,
        existing_history=None,
    ) -> list[dict[str, Any]]

Implementation notes:
  - **Day-by-day recorder queries** (NOT one big query). A single
    30-day ``state_changes_during_period`` on a 1 Hz grid sensor can
    return 100K+ State objects and >100 MB of RAM. Per-day queries
    keep peak memory bounded and let the status sensor surface
    progress. The recorder's SQLite index on ``last_changed`` means
    30 small queries are not meaningfully slower than 1 big one;
    they are NOT parallelised — HA's recorder uses a single executor
    pool, so concurrent queries serialise anyway and just bloat task
    count.
  - **AEST-safe date grouping** via the local-date prefix of
    ``ts_local`` (which preserves the timezone of the input
    datetime). Never uses ``toISOString().split('T')[0]``-style
    construction (CLAUDE.md AEGIS rule).
  - **Caps at 180 entries** after merge — matches the live
    coordinator's ``_daily_cost_history`` slice.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .cdr.history_replay import (
    daily_slot_iterator,
    fan_out_replay,
    widen_window_for_slot_alignment,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Legacy Amber price-history helper.
# Used ONLY by ``coordinator._replay_amber_today_from_api`` to seed the
# Amber accumulator on a fresh install / mid-day comparator enable. The
# Phase 3.2 backfill itself no longer fetches Amber prices — Amber is
# now a *truth overlay* written once daily by the live coordinator
# rollover, and ``backfill_daily_cost_history`` operates on the user's
# CDR plan(s) via the evaluator instead.
# ---------------------------------------------------------------------------


def fetch_amber_price_history(
    api_key: str,
    site_id: str,
    start_time: datetime,
    end_time: datetime,
) -> list[dict[str, Any]]:
    """Fetch price history from Amber API (sync, urllib). Returns list of intervals.

    Amber API: ``GET /v1/sites/{site_id}/prices?startDate=...&endDate=...``
    Max 7 days per request, 90 days max history.

    Used inside an executor pool by the coordinator's Amber replay
    helper. Not invoked by Phase 3.2's multi-plan backfill.
    """
    prices: list[dict[str, Any]] = []
    current = start_time
    while current < end_time:
        chunk_end = min(current + timedelta(days=7), end_time)
        start_y, start_m, start_d = current.year, f"{current.month:02d}", f"{current.day:02d}"
        end_y, end_m, end_d = chunk_end.year, f"{chunk_end.month:02d}", f"{chunk_end.day:02d}"
        url = (
            f"https://api.amber.com.au/v1/sites/{site_id}/prices"
            f"?startDate={start_y}-{start_m}-{start_d}"
            f"&endDate={end_y}-{end_m}-{end_d}"
        )
        req = urllib.request.Request(  # noqa: S310 — fixed scheme via constant URL
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                if resp.status == 200:
                    prices.extend(json.loads(resp.read().decode("utf-8")))
                else:
                    _LOGGER.warning(
                        "Amber API returned %s for %s..%s",
                        resp.status,
                        f"{start_y}-{start_m}-{start_d}",
                        f"{end_y}-{end_m}-{end_d}",
                    )
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
            _LOGGER.warning("Amber API request failed: %s", err)
        current = chunk_end
    _LOGGER.info("Fetched %d Amber price intervals", len(prices))
    return prices

# Daily cost history cap — matches ``coordinator.py:_async_update_data``
# slice ``[-180:]`` so backfill output can't grow the persisted history
# beyond what the live loop will eventually trim.
_HISTORY_CAP = 180


def _local_date_string(d: datetime) -> str:
    """Format a datetime to ``YYYY-MM-DD`` without UTC drift (AEST-safe).

    CLAUDE.md rule: never use ``toISOString().split('T')[0]``-style
    constructions in JS, and never use ``.isoformat()[:10]`` on UTC
    datetimes in Python — both silently flip the date around midnight.
    Read .year/.month/.day from the (already-localised) datetime
    instead.
    """
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


def _states_to_tuples(
    states: list[Any],
) -> list[tuple[datetime, Any, str]]:
    """Convert HA State objects to ``(ts, state_value, unit)`` tuples.

    The HA recorder returns either ``State`` objects (preferred) or
    raw dicts (legacy / test fixtures). Both shapes are accepted —
    the slot builder downstream tolerates string state values.
    ``last_changed`` is a tz-aware datetime on the State object so
    no parsing is required; dict-shaped fixtures use the same
    ``last_changed`` key and the iso parsing is handled there.

    Filters out unavailable / unknown / empty states so downstream
    ``float()`` doesn't see junk values.
    """
    out: list[tuple[datetime, Any, str]] = []
    for s in states:
        # State object path (production).
        state_val = getattr(s, "state", None)
        ts: datetime | None = None
        unit: str = "W"
        if state_val is None:
            # Dict-shaped fallback (legacy fixtures + simple test mocks).
            if not isinstance(s, dict):
                continue
            state_val = s.get("state")
            ts_raw = s.get("last_changed")
            if isinstance(ts_raw, str):
                try:
                    ts = datetime.fromisoformat(ts_raw)
                except ValueError:
                    continue
            elif isinstance(ts_raw, datetime):
                ts = ts_raw
            else:
                continue
            raw_unit = (s.get("unit")
                        or s.get("attributes", {}).get("unit_of_measurement")
                        or "W")
            unit = raw_unit if isinstance(raw_unit, str) else "W"
        else:
            if state_val in ("unavailable", "unknown", ""):
                continue
            raw_ts = getattr(s, "last_changed", None)
            if not isinstance(raw_ts, datetime):
                continue
            ts = raw_ts
            attrs = getattr(s, "attributes", None) or {}
            if isinstance(attrs, dict):
                raw_unit = attrs.get("unit_of_measurement", "W")
                if isinstance(raw_unit, str):
                    unit = raw_unit
        out.append((ts, state_val, unit))
    return out


async def _fetch_states_for_window(
    hass: HomeAssistant,
    grid_sensor_entity: str,
    start: datetime,
    end: datetime,
) -> list[Any]:
    """Pull one window of recorder history via the executor pool.

    Lazy-imports the recorder so this module remains importable when
    the recorder integration isn't loaded (rare but possible in unit
    tests). Returns ``[]`` on import failure or empty history rather
    than raising — the caller treats that day as "no data".
    """
    try:
        # Lazy imports avoid loading the recorder at module import time.
        # CR-note: needed even though the function is async — top-level
        # imports would force the recorder to be available the moment
        # ``backfill.py`` is imported by ``coordinator.py``.
        from homeassistant.components.recorder import get_instance  # noqa: PLC0415
        from homeassistant.components.recorder.history import (  # noqa: PLC0415
            state_changes_during_period,
        )
    except ImportError:
        _LOGGER.warning("backfill: HA recorder not available")
        return []

    try:
        history = await get_instance(hass).async_add_executor_job(
            state_changes_during_period,
            hass,
            start,
            end,
            grid_sensor_entity,
        )
    except Exception:  # noqa: BLE001 — recorder errors must not sink the run
        _LOGGER.exception(
            "backfill: recorder query failed for %s [%s..%s]",
            grid_sensor_entity, start, end,
        )
        return []

    if not history or grid_sensor_entity not in history:
        return []
    return list(history[grid_sensor_entity])


def _merge_into_history(
    new_rows: dict[str, dict[str, float]],
    existing_history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge per-date backfill rows into ``existing_history``.

    For each existing row, ADD any new plan_key entries from the
    backfill row (so per-day Amber overlay survives backfill).
    Existing same-key values are preserved when backfill produced
    nothing for that key. This is safe because backfill rows only
    carry ``{plan_key: aud}`` entries and never the ``"date"`` field
    itself (which is written by ``_merge_into_history``).

    Newly-backfilled dates that don't exist in history are inserted.
    Final list is sorted ascending by date and capped at 180 entries.
    """
    by_date: dict[str, dict[str, Any]] = {}
    for entry in existing_history:
        d = entry.get("date")
        if isinstance(d, str) and d:
            # Defensive copy — never mutate caller-provided dicts.
            by_date[d] = dict(entry)

    for date_str, row in new_rows.items():
        target = by_date.get(date_str)
        if target is None:
            target = {"date": date_str}
            by_date[date_str] = target
        # Backfill writes win for plan_keys it computed; live coordinator
        # keys (Amber overlay, etc) that aren't in row are preserved.
        for plan_key, aud in row.items():
            target[plan_key] = aud

    merged = sorted(by_date.values(), key=lambda r: r.get("date", ""))
    if len(merged) > _HISTORY_CAP:
        merged = merged[-_HISTORY_CAP:]
    return merged


async def backfill_daily_cost_history(
    hass: HomeAssistant,
    grid_sensor_entity: str,
    plans: dict[str, dict[str, Any]],
    *,
    days_back: int = 30,
    entry_options: dict[str, Any] | None = None,
    existing_history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """End-to-end backfill across N plans. Returns merged history.

    Args:
        hass: HA instance (used for recorder executor + dt_util).
        grid_sensor_entity: Entity id of the user's net-grid power
            sensor (e.g. ``sensor.grid_power``). Returns
            ``existing_history`` unchanged if empty.
        plans: ``{plan_key: cdr_plan_data}`` — current plan + top-K
            alternatives + (Phase 3.4) named comparator. ``plan_key``
            is the column written into ``daily_cost_history`` rows
            (so ``alt_<planid>`` for alternatives, the current
            provider id for the current plan, ``"named"`` for the
            named comparator).
        days_back: How many days back to attempt. HA recorder
            retention (typically 10) caps the effective coverage —
            days with no recorder rows just get skipped.
        entry_options: Phase 2.12.1 opt-in fields passed through to
            the evaluator (OVO interest balance, VPP batteries).
        existing_history: Current ``daily_cost_history`` list; new
            backfilled rows are merged on top.

    Returns:
        Merged ``daily_cost_history``, max 180 entries, sorted by date.
        Returns ``existing_history`` unchanged when ``grid_sensor_entity``
        is empty/unset (no signal to backfill from).
    """
    existing = list(existing_history) if existing_history else []
    if not grid_sensor_entity:
        _LOGGER.info("backfill: no grid sensor configured, returning existing history")
        return existing
    if not plans:
        _LOGGER.info("backfill: no plans to replay, returning existing history")
        return existing

    # Lazy import dt_util so the module imports cleanly under conftest.
    from homeassistant.util import dt as dt_util  # noqa: PLC0415

    now_local = dt_util.now()
    today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    days_back = max(1, min(days_back, 365))

    new_rows: dict[str, dict[str, float]] = {}
    days_with_data = 0

    # Day-by-day so peak memory is one day of slots × N plans rather
    # than 30 days × N plans of CostBreakdown objects + traces.
    # See module docstring for the "Why day-by-day" rationale (memory
    # bound + status-sensor progress) — DO NOT parallelise.
    for i in range(1, days_back + 1):
        day_start = today_local - timedelta(days=i)
        day_end = today_local - timedelta(days=i - 1)
        # Widen back by one slot so the first slot of the day has a
        # prior reading to compute its delta against (zero-order-hold
        # convention from the streaming engine).
        wstart, wend = widen_window_for_slot_alignment(day_start, day_end)

        states = await _fetch_states_for_window(
            hass, grid_sensor_entity, wstart, wend,
        )
        if not states:
            continue
        tuples = _states_to_tuples(states)
        if not tuples:
            continue

        per_date_slots = daily_slot_iterator(tuples)
        # Drop slots not actually in this day's local-date bucket
        # (the one-slot pre-padding may have produced a slot from
        # the previous local date — let the date that *does* own it
        # handle it on its own pass).
        target_date = _local_date_string(day_start)
        slots_for_day = per_date_slots.get(target_date)
        if not slots_for_day:
            continue

        for date_str, row in fan_out_replay(
            {target_date: slots_for_day}, plans, entry_options=entry_options,
        ):
            if row:
                new_rows[date_str] = row
                days_with_data += 1

    _LOGGER.info(
        "backfill: replayed %d/%d days across %d plan(s)",
        days_with_data, days_back, len(plans),
    )
    return _merge_into_history(new_rows, existing)
