"""Phase 3.2 commit 2 — tests for the rewritten ``backfill.py``.

The Amber-API-specific tests from the legacy backfill have been
deleted (those tested ``fetch_amber_price_history`` /
``_build_amber_price_index`` / ``_find_amber_rate`` — none of which
exist after the rewrite). The new tests exercise the multi-plan
recorder-driven path with the recorder mocked at the import
boundary.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.pricehawk.backfill import (
    _local_date_string,
    _merge_into_history,
    _states_to_tuples,
    backfill_daily_cost_history,
)

AEST = timezone(timedelta(hours=10))


def _flat_plan(*, plan_id: str = "FLAT", unit_price: str = "0.30") -> dict:
    """Minimal single-rate plan — $0.30/kWh import, $1/day supply ex-GST."""
    return {
        "planId": plan_id,
        "electricityContract": {
            "pricingModel": "SINGLE_RATE",
            "tariffPeriod": [
                {
                    "rateBlockUType": "singleRate",
                    "singleRate": {"rates": [{"unitPrice": unit_price}]},
                    "dailySupplyCharge": "1.00",
                }
            ],
        },
    }


def _state(ts: datetime, value: float, unit: str = "W") -> SimpleNamespace:
    """Build a recorder-style ``State``-shaped object for the mock."""
    return SimpleNamespace(
        state=str(value),
        last_changed=ts,
        attributes={"unit_of_measurement": unit},
    )


def _states_for_day(local_day: datetime, *, power_w: float = 2000.0, interval_min: int = 5) -> list:
    """Generate States covering 00:00..23:55 on ``local_day`` (AEST)."""
    out = []
    t = local_day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = t + timedelta(days=1)
    while t < end:
        out.append(_state(t, power_w))
        t += timedelta(minutes=interval_min)
    return out


def _patch_recorder(
    states_by_day: dict[datetime, list],
) -> tuple[MagicMock, MagicMock]:
    """Build mocks for HA recorder used inside backfill.

    Returns a 2-tuple ``(get_instance, history_mock)`` so tests can
    assert on calls. ``get_instance`` is the patched
    ``recorder.get_instance`` factory whose return value exposes
    ``async_add_executor_job``; ``history_mock`` stands in for
    ``recorder.history.state_changes_during_period`` and returns the
    pre-canned states for whichever day's window the caller queries.

    states_by_day: keys are AEST midnight datetimes (the local day);
    values are the State objects to return when the recorder is
    queried for that day's window.
    """

    def _query(_hass, start, _end, _entity):
        # Match by local-date prefix of ``start`` (which is widened by
        # one slot back into the previous day) → look up the day that
        # falls within [start, _end).
        for day, states in states_by_day.items():
            if start <= day < _end:
                return {_entity: states}
        return {}

    # async_add_executor_job is awaited but runs synchronously here.
    async def _exec_job(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    instance = MagicMock()
    instance.async_add_executor_job = AsyncMock(side_effect=_exec_job)
    get_instance = MagicMock(return_value=instance)

    history_mock = MagicMock(side_effect=_query)
    return get_instance, history_mock


# ---------------------------------------------------------------------------
# _local_date_string
# ---------------------------------------------------------------------------


class TestLocalDateString:
    def test_formats_aest_local_date_without_utc_flip(self):
        """A 23:30 AEST datetime stays on its OWN local date."""
        d = datetime(2026, 5, 17, 23, 30, 0, tzinfo=AEST)
        assert _local_date_string(d) == "2026-05-17"

    def test_pads_month_and_day_to_two_digits(self):
        d = datetime(2026, 3, 4, 12, 0, 0, tzinfo=AEST)
        assert _local_date_string(d) == "2026-03-04"


# ---------------------------------------------------------------------------
# _states_to_tuples
# ---------------------------------------------------------------------------


class TestStatesToTuples:
    def test_converts_state_objects(self):
        t = datetime(2026, 5, 17, 12, 0, 0, tzinfo=AEST)
        tuples = _states_to_tuples([_state(t, 2000.0)])
        assert len(tuples) == 1
        ts, val, unit = tuples[0]
        assert ts == t
        assert val == "2000.0"
        assert unit == "W"

    def test_skips_unavailable_unknown_empty_states(self):
        t = datetime(2026, 5, 17, 12, 0, 0, tzinfo=AEST)
        bad = [
            SimpleNamespace(state="unavailable", last_changed=t, attributes={}),
            SimpleNamespace(state="unknown", last_changed=t, attributes={}),
            SimpleNamespace(state="", last_changed=t, attributes={}),
        ]
        assert _states_to_tuples(bad) == []

    def test_accepts_dict_shaped_states(self):
        """Legacy fixtures + simple test mocks ship dict states."""
        t = datetime(2026, 5, 17, 12, 0, 0, tzinfo=AEST)
        tuples = _states_to_tuples(
            [
                {"state": 2000.0, "last_changed": t.isoformat(), "unit": "W"},
            ]
        )
        assert len(tuples) == 1


# ---------------------------------------------------------------------------
# _merge_into_history
# ---------------------------------------------------------------------------


class TestMergeIntoHistory:
    def test_inserts_new_dates(self):
        merged = _merge_into_history(
            {"2026-05-16": {"flat": 1.0}, "2026-05-17": {"flat": 2.0}},
            [],
        )
        dates = [r["date"] for r in merged]
        assert dates == ["2026-05-16", "2026-05-17"]
        assert merged[0]["flat"] == 1.0

    def test_merges_new_keys_into_existing_rows(self):
        existing = [{"date": "2026-05-17", "amber": 8.40}]
        merged = _merge_into_history(
            {"2026-05-17": {"flat": 9.21, "alt_X": 7.5}},
            existing,
        )
        assert len(merged) == 1
        row = merged[0]
        # Amber preserved, new keys added.
        assert row["amber"] == 8.40
        assert row["flat"] == 9.21
        assert row["alt_X"] == 7.5

    def test_caps_at_180_entries(self):
        # 200 days input → 180 output, most-recent retained.
        new = {
            f"2026-{((i // 31) % 12) + 1:02d}-{(i % 28) + 1:02d}": {"flat": float(i)}
            for i in range(200)
        }
        # Use unique dates for cap test.
        new = {
            f"2026-{m:02d}-{d:02d}": {"flat": float(m * 31 + d)}
            for m in range(1, 13)
            for d in range(1, 29)
        }
        merged = _merge_into_history(new, [])
        assert len(merged) == 180
        # Sorted ascending.
        assert merged[0]["date"] < merged[-1]["date"]


# ---------------------------------------------------------------------------
# backfill_daily_cost_history — end-to-end with mocked recorder
# ---------------------------------------------------------------------------


class TestBackfillDailyCostHistory:
    def _run(self, *, states_by_day, plans, days_back=2, now_local=None, existing=None):
        """Helper to run backfill under patched recorder + dt_util."""
        if now_local is None:
            now_local = datetime(2026, 5, 17, 10, 0, 0, tzinfo=AEST)

        get_instance, history_mock = _patch_recorder(states_by_day)

        with (
            patch(
                "homeassistant.components.recorder.get_instance",
                get_instance,
            ),
            patch(
                "homeassistant.components.recorder.history.state_changes_during_period",
                history_mock,
            ),
            patch(
                "homeassistant.util.dt.now",
                MagicMock(return_value=now_local),
            ),
        ):
            return asyncio.run(
                backfill_daily_cost_history(
                    MagicMock(),
                    "sensor.grid_power",
                    plans,
                    days_back=days_back,
                    existing_history=existing,
                )
            )

    def test_returns_one_row_per_day_in_window(self):
        """2 days of data → 2 backfilled rows."""
        now = datetime(2026, 5, 17, 10, 0, 0, tzinfo=AEST)
        day1 = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        day2 = (now - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
        states_by_day = {
            day1: _states_for_day(day1),
            day2: _states_for_day(day2),
        }
        result = self._run(
            states_by_day=states_by_day,
            plans={"flat": _flat_plan()},
            days_back=2,
            now_local=now,
        )
        # Two new rows, each with a "flat" cost > 0.
        assert len(result) == 2
        for row in result:
            assert "flat" in row
            assert row["flat"] > 0

    def test_merges_with_existing_history_overwriting_same_dates(self):
        """Existing Amber overlay preserved; backfill adds plan cost."""
        now = datetime(2026, 5, 17, 10, 0, 0, tzinfo=AEST)
        day1 = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        existing = [
            {
                "date": _local_date_string(day1),
                "amber": 8.40,
                "flat": 99.99,  # stale; backfill should overwrite.
            }
        ]
        states_by_day = {day1: _states_for_day(day1)}
        result = self._run(
            states_by_day=states_by_day,
            plans={"flat": _flat_plan()},
            days_back=1,
            now_local=now,
            existing=existing,
        )
        assert len(result) == 1
        row = result[0]
        assert row["amber"] == 8.40  # Preserved.
        assert row["flat"] != 99.99  # Overwritten by real replay.
        assert row["flat"] > 0

    def test_caps_at_180_entries(self):
        """Existing 200 rows + new ones → capped at 180 (most-recent)."""
        now = datetime(2026, 5, 17, 10, 0, 0, tzinfo=AEST)
        # 200 historical rows, all on distinct dates.
        existing = [
            {"date": f"2025-{((i // 31) % 12) + 1:02d}-{(i % 28) + 1:02d}", "amber": float(i)}
            for i in range(200)
        ]
        result = self._run(
            states_by_day={},  # no new rows from recorder
            plans={"flat": _flat_plan()},
            days_back=1,
            now_local=now,
            existing=existing,
        )
        assert len(result) == 180

    def test_skips_days_with_no_history(self):
        """Days where the recorder returns empty produce no row."""
        now = datetime(2026, 5, 17, 10, 0, 0, tzinfo=AEST)
        result = self._run(
            states_by_day={},  # recorder returns nothing every day
            plans={"flat": _flat_plan()},
            days_back=3,
            now_local=now,
        )
        # No rows emitted — existing history was empty too.
        assert result == []

    def test_handles_evaluator_failure_gracefully(self):
        """A plan whose evaluator throws is absent from the day's row."""
        now = datetime(2026, 5, 17, 10, 0, 0, tzinfo=AEST)
        day1 = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        states_by_day = {day1: _states_for_day(day1)}

        # A malformed plan (no tariffPeriod) makes the evaluator return a
        # zero-slot breakdown, which the wrapper treats as None.
        bad_plan = {
            "planId": "BAD",
            "electricityContract": {"pricingModel": "SINGLE_RATE"},
        }
        result = self._run(
            states_by_day=states_by_day,
            plans={"flat": _flat_plan(), "alt_BAD": bad_plan},
            days_back=1,
            now_local=now,
        )
        assert len(result) == 1
        row = result[0]
        assert "flat" in row
        # Bad plan column should be absent — the day still has the
        # good plan's column.
        assert "alt_BAD" not in row

    def test_returns_existing_history_when_grid_sensor_unconfigured(self):
        """Empty grid_sensor_entity short-circuits — recorder never queried."""

        async def _go():
            return await backfill_daily_cost_history(
                MagicMock(),
                "",  # no sensor configured
                {"flat": _flat_plan()},
                days_back=30,
                existing_history=[{"date": "2026-05-17", "amber": 5.0}],
            )

        result = asyncio.run(_go())
        assert result == [{"date": "2026-05-17", "amber": 5.0}]

    def test_backfill_tiered_fit_state_activation_on_boundaries(self):
        """A tiered FiT plan backfill starting mid-month should seed state_by_plan
        with None for daily proration. If it starts on the 1st of the month,
        it should seed it with {} for stateful tracking.
        """
        now = datetime(2026, 6, 5, 10, 0, 0, tzinfo=AEST)
        day1 = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        day2 = (now - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
        day3 = (now - timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0)
        day4 = (now - timedelta(days=4)).replace(hour=0, minute=0, second=0, microsecond=0)
        states_by_day = {
            day1: _states_for_day(day1),
            day2: _states_for_day(day2),
            day3: _states_for_day(day3),
            day4: _states_for_day(day4),
        }

        # 1. Start mid-month: June 3rd (with 2 days back from June 5th)
        with patch("custom_components.pricehawk.backfill.fan_out_replay", return_value=[]) as mock_replay:
            self._run(
                states_by_day=states_by_day,
                plans={"flat": _flat_plan()},
                days_back=2,
                now_local=now,
            )
            assert mock_replay.called
            called_state = mock_replay.call_args[1].get("state_by_plan")
            assert called_state["flat"] is None

        # 2. Start at boundary: June 1st (with 4 days back from June 5th)
        with patch("custom_components.pricehawk.backfill.fan_out_replay", return_value=[]) as mock_replay:
            self._run(
                states_by_day=states_by_day,
                plans={"flat": _flat_plan()},
                days_back=4,
                now_local=now,
            )
            assert mock_replay.called
            called_state = mock_replay.call_args[1].get("state_by_plan")
            assert called_state["flat"] == {}
