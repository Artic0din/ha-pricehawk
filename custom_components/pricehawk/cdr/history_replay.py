"""Phase 3.2 — pure-logic HA history replay across N plans.

No Home Assistant imports. Coordinator-side adapter (``backfill.py``)
pulls recorder history, calls ``states_to_half_hour_slots`` to convert
raw (timestamp, power, unit) tuples into evaluator-shaped slot dicts,
groups slots by local date, then hands the per-day map to
``fan_out_replay`` for the cost-per-plan-per-day computation.

The fan-out is a generator (not list-return) so the coordinator can
write each day's row to ``daily_cost_history`` without holding
``all_days × all_plans`` ``CostBreakdown`` objects in memory at once.
For 365 days × 25 plans, the full materialised list is ~50 MB of
breakdown objects + traces. Streaming keeps peak RAM at
``one day × 25 plans`` = ~1 MB.

Per-plan failure isolation matches ``cdr/ranking.py:deep_rank``: one
malformed plan can't sink the whole batch — its column in the day-dict
is simply absent. The same applies to rollup-time reads, which already
treat missing per-day plan keys as "data not available".

Slot shape returned by ``states_to_half_hour_slots`` matches the
evaluator's expected ``ConsumptionSlot`` dict (see
``cdr/models.py``): ``ts_local`` (ISO local timestamp at the slot's
30-min boundary), ``grid_import_kwh``, ``grid_export_kwh``.

NOTE on "top-K can change between ranking runs": backfill replays
yesterday's history through *today's* alternatives, so a plan that
was ranked #1 last week and dropped to #25 this week disappears from
recent backfill rows. This is acceptable — rollups (Phase 3.3) read
keys present in each row independently.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta
from typing import Any

from .evaluator import CostBreakdown, evaluate

_LOGGER = logging.getLogger(__name__)

# Maximum delta between successive readings before clamping. Matches
# ``cdr/streaming.py:GAP_PROTECTION_MAX_DELTA_H`` (= 6 minutes) so
# replayed-history and live-streaming cost numbers stay comparable.
GAP_PROTECTION_MAX_DELTA_H_DEFAULT = 0.1

# Half-hour slot size — must match the evaluator's TOU window math.
_SLOT_MINUTES = 30


def _slot_start(ts: datetime) -> datetime:
    """Round a timestamp DOWN to the nearest 30-min boundary."""
    return ts.replace(
        minute=(ts.minute // _SLOT_MINUTES) * _SLOT_MINUTES,
        second=0,
        microsecond=0,
    )


def states_to_half_hour_slots(
    states: Iterable[tuple[datetime, float, str]],
    *,
    gap_protection_h: float = GAP_PROTECTION_MAX_DELTA_H_DEFAULT,
) -> list[dict[str, Any]]:
    """Convert ``(ts, power_w, unit)`` readings to evaluator slot dicts.

    Each slot: ``{"ts_local": iso, "grid_import_kwh": float,
    "grid_export_kwh": float}``. Slots align to 30-min boundaries.
    Partial trailing slot is included (evaluator handles short slots
    gracefully).

    Args:
        states: Iterable of ``(datetime, power_float_or_str, unit)``
            tuples. ``unit`` is ``"W"`` or ``"kW"`` (case-insensitive);
            kW values are multiplied by 1000. Power may be passed as a
            string — HA's recorder serialises some sensor states as
            ``"2000"`` rather than ``2000.0``.
        gap_protection_h: Max delta (hours) between successive
            readings before clamping. Prevents runaway energy
            accumulation when the recorder has gaps (e.g. HA restart).

    Returns:
        List of slot dicts sorted by ``ts_local`` (ascending). Returns
        an empty list when fewer than 2 valid readings are present
        (need 2 to compute a delta).

    Pure function — no logger, no IO.
    """
    # Pre-pass: parse + filter the iterable into clean tuples.
    # Sorting by ts_local is required because the evaluator's incentive
    # parsers (GloBird ZEROHERO behaviour tracker, AGL Three for Free)
    # assume chronological order within a day.
    readings: list[tuple[datetime, float]] = []
    for ts, raw_power, unit in states:
        try:
            power_w = float(raw_power)
        except (TypeError, ValueError):
            continue
        if isinstance(unit, str) and unit.lower() == "kw":
            power_w *= 1000.0
        readings.append((ts, power_w))

    if len(readings) < 2:
        return []

    readings.sort(key=lambda r: r[0])

    # Accumulate per-slot energy using zero-order hold (previous reading's
    # power held over the interval until the next reading).
    slot_acc: dict[datetime, dict[str, float]] = {}
    for i in range(1, len(readings)):
        prev_ts, prev_power_w = readings[i - 1]
        curr_ts, _ = readings[i]
        delta_s = (curr_ts - prev_ts).total_seconds()
        if delta_s <= 0:
            continue
        delta_h = min(delta_s / 3600.0, gap_protection_h)
        grid_kw = prev_power_w / 1000.0
        import_kwh = max(0.0, grid_kw) * delta_h
        export_kwh = max(0.0, -grid_kw) * delta_h
        # Skip zero-energy intervals (sensor reporting 0 W at idle) —
        # they would otherwise create empty slot dicts that the
        # evaluator dutifully iterates over.
        if import_kwh == 0.0 and export_kwh == 0.0:
            continue
        slot_key = _slot_start(prev_ts)
        bucket = slot_acc.setdefault(
            slot_key,
            {"grid_import_kwh": 0.0, "grid_export_kwh": 0.0},
        )
        bucket["grid_import_kwh"] += import_kwh
        bucket["grid_export_kwh"] += export_kwh

    out: list[dict[str, Any]] = []
    for slot_key in sorted(slot_acc.keys()):
        b = slot_acc[slot_key]
        out.append({
            "ts_local": slot_key.isoformat(),
            "grid_import_kwh": b["grid_import_kwh"],
            "grid_export_kwh": b["grid_export_kwh"],
        })
    return out


def replay_day_through_plan(
    slots: list[dict[str, Any]],
    plan: dict[str, Any],
    *,
    entry_options: dict[str, Any] | None = None,
) -> CostBreakdown | None:
    """Single-day replay of slots through one plan. Returns ``None`` on failure.

    Wraps ``evaluate()`` with the standard exception-swallow pattern
    (mirrors ``deep_rank`` at ``cdr/ranking.py:391-400``). Returns
    ``None`` when:
      - ``slots`` is empty (consistent with ``deep_rank`` filtering
        zero-slot breakdowns).
      - ``evaluate()`` raises any exception (malformed plan body,
        missing tariffPeriod, etc).
      - ``slot_count`` on the returned breakdown is zero (evaluator
        succeeded but found no usable slots — same "no signal"
        condition as above).
    """
    if not slots:
        return None
    try:
        bd = evaluate(
            plan,
            {"slots": slots},
            entry_options=entry_options,
        )
    except Exception:  # noqa: BLE001 — one bad plan must not sink the batch
        # ``_LOGGER.exception`` captures the traceback so a malformed
        # CDR plan body that crashes the evaluator is debuggable
        # without re-running the backfill in verbose mode.
        _LOGGER.exception(
            "history_replay: plan %s evaluator raised; skipping day",
            (plan.get("data") or {}).get("planId") or plan.get("planId", "?"),
        )
        return None
    if bd.slot_count == 0:
        return None
    return bd


def fan_out_replay(
    daily_slots: dict[str, list[dict[str, Any]]],
    plans: dict[str, dict[str, Any]],
    *,
    entry_options: dict[str, Any] | None = None,
) -> Iterator[tuple[str, dict[str, float]]]:
    """Yield ``(date_str, {plan_key: aud_inc_gst, ...})`` per day.

    Args:
        daily_slots: ``{"YYYY-MM-DD": [slot, slot, ...]}``  one day's
            worth of evaluator slots per key. Iteration order follows
            the natural string sort (lexicographic == chronological
            for ISO ``YYYY-MM-DD``).
        plans: ``{"plan_key": plan_body}`` where ``plan_body`` is a
            CDR PlanDetailV2-shaped dict (or envelope with ``data``).
        entry_options: Phase 2.12.1 opt-in fields (OVO interest
            balance, VPP batteries enrolled) flowed through to
            ``evaluate``.

    Generator (not list-return) so the caller (coordinator) can write
    each day's row to ``daily_cost_history`` without holding
    ``all_days × all_plans`` breakdowns in memory simultaneously.

    Plans whose replay returns ``None`` (evaluator raised, or no
    slots) are simply absent from that day's dict — never raised,
    never logged at WARN per the "rare bad plan, often-empty-day"
    expected case.
    """
    for date_str in sorted(daily_slots.keys()):
        slots = daily_slots[date_str]
        row: dict[str, float] = {}
        for plan_key, plan_body in plans.items():
            bd = replay_day_through_plan(
                slots, plan_body, entry_options=entry_options,
            )
            if bd is None:
                continue
            row[plan_key] = round(float(bd.total_aud_inc_gst), 2)
        yield date_str, row


def group_slots_by_local_date(
    slots: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Bucket evaluator slots into per-local-date lists.

    Reads the YYYY-MM-DD prefix of ``ts_local`` — slots are already
    AEST-localised by ``states_to_half_hour_slots`` (which preserves
    the timezone of the input ``datetime``). Skips slots without a
    parseable ``ts_local`` rather than raising.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for slot in slots:
        ts = slot.get("ts_local")
        if not isinstance(ts, str) or len(ts) < 10:
            continue
        date_str = ts[:10]
        out.setdefault(date_str, []).append(slot)
    return out


def daily_slot_iterator(
    states: Iterable[tuple[datetime, float, str]],
    *,
    gap_protection_h: float = GAP_PROTECTION_MAX_DELTA_H_DEFAULT,
) -> dict[str, list[dict[str, Any]]]:
    """End-to-end helper: states → half-hour slots → per-date map.

    Convenience wrapper composing ``states_to_half_hour_slots`` and
    ``group_slots_by_local_date``. Exists so the coordinator-side
    adapter (``backfill.py``) doesn't repeat the two-call sequence
    on every day's recorder pull.
    """
    slots = states_to_half_hour_slots(states, gap_protection_h=gap_protection_h)
    return group_slots_by_local_date(slots)


def widen_window_for_slot_alignment(
    day_start: datetime,
    day_end: datetime,
    *,
    pre_padding: timedelta = timedelta(minutes=_SLOT_MINUTES),
) -> tuple[datetime, datetime]:
    """Return a ``(start, end)`` widened so partial-slot reads cover
    the full day. The zero-order-hold convention means we need at
    least one reading from the previous slot to attribute energy to
    the first slot of ``day_start``. The day-by-day caller in
    ``backfill.py`` uses this to extend the recorder query window
    backwards by one slot.
    """
    return day_start - pre_padding, day_end
