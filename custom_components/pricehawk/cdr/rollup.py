"""Phase 3.3 — pure-logic period rollup over ``daily_cost_history``.

No Home Assistant imports. Sensors (in ``sensor.py``) bind a ``window``
name (``today | week | month | 3month | year``) and a metric kind
(``current | best_alt | savings``) to these pure functions; on every
coordinator tick the sensor calls ``filter_window`` + ``sum_window`` (or
``best_alternative_for_window``) over the latest history list.

Design notes locked in ``.planning/PHASE-3.2-to-3.5-PLAN.md`` section 3:

- **Rolling windows, not calendar months.** ``month`` = last 30 days,
  ``3month`` = 90, ``year`` = 365. Calendar accounting can be added
  later by changing ``WINDOW_DAYS`` and ``filter_window`` semantics.
- **Floats throughout.** History rows already store floats (rounded to 2dp
  in ``cdr/history_replay.fan_out_replay`` and in the live coordinator
  daily-rollover). Decimal precision is unnecessary for 365-day AUD ranges
  and would force conversions across the sensor boundary.
- **Missing keys are silent.** If a plan disappeared from the top-K
  between ranking runs, its column is absent on later rows. Rollups
  count only the rows that DO have the key and surface a non-zero
  ``day_count`` so the caller knows the coverage.
- **(None, 0) over (0.0, 0).** When no rows contain the key at all, we
  return ``None`` for the sum so the sensor displays ``unknown`` rather
  than ``$0.00`` — the former is honest about missing data, the latter
  would falsely imply a zero-spend day.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

WindowName = Literal["today", "week", "month", "3month", "year"]

# Window sizes in DAYS (rolling, end-inclusive at today).
# Calendar month vs rolling 30 days: locked rolling for simplicity +
# consistency across window names. REVISIT if users want calendar-month
# accounting — trivial to flip by changing both the constant table and
# ``filter_window`` to bucket on year-month.
WINDOW_DAYS: dict[str, int] = {
    "today": 1,
    "week": 7,
    "month": 30,
    "3month": 90,
    "year": 365,
}

_ALT_KEY_PREFIX_DEFAULT = "alt_"


def _today(now: datetime | None) -> date:
    """Resolve the local date for ``now``. Imported lazily because the
    coordinator-side caller passes ``dt_util.now()`` explicitly in the
    sensor, and unit tests pass a fixed datetime."""
    if now is None:
        # Local-time fallback; coordinator-side callers should pass
        # ``dt_util.now()`` to honour the HA timezone.
        return datetime.now().date()
    return now.date()


def filter_window(
    history: list[dict[str, Any]],
    window: WindowName,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return rows whose ``date`` falls inside the rolling window ending today.

    Window semantics: ``today`` = exactly today's row (if present).
    ``week`` = last 7 days inclusive of today. ``month`` = 30, ``3month``
    = 90, ``year`` = 365.

    Rows with missing, non-string, or unparseable ``date`` are silently
    skipped — the daily_cost_history schema is enforced by writers, but
    a malformed restore should not blow up sensor reads. Future-dated
    rows (date > today, e.g. clock-skew or test fixtures) are also
    excluded as a defensive measure.

    ``history`` is not mutated. ``now`` defaults to local-machine
    ``datetime.now()``; callers should pass ``dt_util.now()`` so the
    HA-configured timezone is honoured.
    """
    days = WINDOW_DAYS.get(window)
    if days is None:
        return []
    end = _today(now)
    # ``today`` window is "this date and no other" — start == end.
    start = end - timedelta(days=days - 1)
    out: list[dict[str, Any]] = []
    for row in history:
        # Defensive: ``history`` is typed list[dict] but restored state
        # from storage / 3rd-party callers may slip in scalars; calling
        # ``.get`` on a non-dict raises and would crash sensor reads.
        if not isinstance(row, dict):
            continue
        raw = row.get("date")
        if not isinstance(raw, str):
            continue
        try:
            row_date = date.fromisoformat(raw)
        except ValueError:
            continue
        if row_date < start or row_date > end:
            continue
        out.append(row)
    return out


def sum_window(
    rows: list[dict[str, Any]],
    plan_key: str,
) -> tuple[float | None, int]:
    """Sum ``rows[i][plan_key]`` across ``rows``.

    Returns ``(sum_aud, day_count)``. Rows lacking ``plan_key`` (sparse
    alt presence, named-comparator absent on pre-3.4 rows, etc) are
    skipped — ``day_count`` reflects only the rows that contributed.

    Returns ``(None, 0)`` when **no** row contains ``plan_key``. This is
    the "missing data" state — sensors display ``unknown`` rather than
    ``$0.00`` so users don't mistake it for a real zero-spend day.

    Values are coerced via ``float()``. Non-numeric strings (and ``None``)
    are skipped, not raised — defensive against malformed restores. A
    numeric ``0.0`` IS counted (it's a legitimate zero-cost day, e.g.
    100% solar self-consumption).
    """
    total = 0.0
    count = 0
    for row in rows:
        if plan_key not in row:
            continue
        raw = row[plan_key]
        if raw is None:
            continue
        try:
            total += float(raw)
        except (TypeError, ValueError):
            continue
        count += 1
    if count == 0:
        return None, 0
    return total, count


def best_alternative_for_window(
    rows: list[dict[str, Any]],
    *,
    alt_key_prefix: str = _ALT_KEY_PREFIX_DEFAULT,
) -> tuple[str | None, float | None, int]:
    """Pick the alternative plan with the LOWEST summed cost across rows.

    Returns ``(best_plan_id, sum_aud, day_count)``. ``best_plan_id`` is
    the value AFTER the prefix is stripped (``alt_AGL900`` → ``AGL900``)
    so the dashboard can render it directly. Returns ``(None, None, 0)``
    when no row contains any key matching the prefix.

    Ties broken by lexicographic ``best_plan_id`` so the choice is
    deterministic across reads — important because rollups recompute on
    every coordinator tick, and a flipping winner would churn the
    dashboard.
    """
    # Collect every distinct alt key seen across the window.
    alt_keys: set[str] = set()
    for row in rows:
        for k in row:
            if k.startswith(alt_key_prefix):
                alt_keys.add(k)
    if not alt_keys:
        return None, None, 0

    best_plan_id: str | None = None
    best_sum: float | None = None
    best_count = 0
    # Iterate in lexicographic order so ties resolve deterministically.
    for key in sorted(alt_keys):
        total, count = sum_window(rows, key)
        if total is None:
            continue
        plan_id = key[len(alt_key_prefix) :]
        if not plan_id:
            continue
        if best_sum is None or total < best_sum:
            best_plan_id = plan_id
            best_sum = total
            best_count = count
    if best_plan_id is None:
        return None, None, 0
    return best_plan_id, best_sum, best_count


def savings(
    current_sum: float | None,
    best_alt_sum: float | None,
) -> float | None:
    """``current - best_alt``. ``None`` if either side is ``None``.

    Positive = you'd save by switching to the cheapest alternative.
    Negative = your current plan is already cheaper than every
    alternative tracked in the window — a legitimate outcome that the
    dashboard surfaces honestly (the sign of the value carries the
    message, no clamping at zero).
    """
    if current_sum is None or best_alt_sum is None:
        return None
    return current_sum - best_alt_sum
