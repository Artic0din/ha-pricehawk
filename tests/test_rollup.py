"""Phase 3.3 commit 1 — pure-logic tests for ``cdr.rollup``.

Pattern mirrors ``tests/test_history_replay.py``: one ``TestClass`` per
public function, stdlib only (no ``pytest-asyncio``), no HA mocks (the
module under test has no HA imports).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.pricehawk.cdr.rollup import (
    WINDOW_DAYS,
    best_alternative_for_window,
    filter_window,
    savings,
    sum_window,
)

# Fixed AEST timestamp used as the rolling-window endpoint across the
# tests. Pinning ``now`` avoids the AEST date-rollover gotcha that
# ``datetime.now()`` would introduce when the test suite runs near
# midnight local time.
AEST = timezone(timedelta(hours=10))
FIXED_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=AEST)


def _row(day_offset: int, **plan_costs: float) -> dict:
    """Build a daily_cost_history row dated ``FIXED_NOW + day_offset``.

    Negative offset = older days. Plan keys are passed as kwargs so the
    test signature stays readable (``_row(-1, current=8.50, alt_AGL=7.10)``).
    """
    day = (FIXED_NOW + timedelta(days=day_offset)).date()
    return {"date": day.isoformat(), **plan_costs}


# ---------------------------------------------------------------------------
# WINDOW_DAYS constant smoke
# ---------------------------------------------------------------------------


class TestWindowDaysConstants:
    def test_window_days_constants_present(self):
        """All five named windows are exposed with the expected day counts."""
        assert WINDOW_DAYS == {
            "today": 1,
            "week": 7,
            "month": 30,
            "3month": 90,
            "year": 365,
        }


# ---------------------------------------------------------------------------
# filter_window
# ---------------------------------------------------------------------------


class TestFilterWindow:
    def _thirty_day_history(self) -> list[dict]:
        """Return 30 consecutive days ending on FIXED_NOW, each with a
        single ``current`` key (value = day-offset so tests can assert
        which rows survived)."""
        return [_row(offset, current=float(offset)) for offset in range(-29, 1)]

    def test_filter_window_today_returns_only_today(self):
        rows = filter_window(self._thirty_day_history(), "today", now=FIXED_NOW)
        assert len(rows) == 1
        assert rows[0]["date"] == FIXED_NOW.date().isoformat()

    def test_filter_window_week_returns_7_most_recent(self):
        rows = filter_window(self._thirty_day_history(), "week", now=FIXED_NOW)
        assert len(rows) == 7
        # Most recent row is today; oldest is 6 days ago.
        dates = sorted(r["date"] for r in rows)
        assert dates[0] == (FIXED_NOW - timedelta(days=6)).date().isoformat()
        assert dates[-1] == FIXED_NOW.date().isoformat()

    def test_filter_window_year_returns_all_when_history_shorter(self):
        history = self._thirty_day_history()
        rows = filter_window(history, "year", now=FIXED_NOW)
        assert len(rows) == 30

    def test_filter_window_excludes_future_dated_rows(self):
        """Defensive: rows dated past ``now`` are dropped silently."""
        history = self._thirty_day_history() + [_row(1, current=99.0)]
        rows = filter_window(history, "year", now=FIXED_NOW)
        assert len(rows) == 30
        assert all(r["date"] <= FIXED_NOW.date().isoformat() for r in rows)

    def test_filter_window_handles_empty_history(self):
        assert filter_window([], "month", now=FIXED_NOW) == []

    def test_filter_window_handles_malformed_dates_silently(self):
        """Bad date strings are skipped, never raised."""
        history = [
            _row(0, current=10.0),
            {"date": "not-a-date", "current": 99.0},
            {"date": None, "current": 88.0},
            {"current": 77.0},  # missing date entirely
        ]
        rows = filter_window(history, "week", now=FIXED_NOW)
        assert len(rows) == 1
        assert rows[0]["current"] == 10.0

    def test_filter_window_unknown_window_returns_empty(self):
        """Defensive: an unrecognised window name returns ``[]`` (no
        accidental "all-history" if a caller typos the name)."""
        rows = filter_window(self._thirty_day_history(), "decade", now=FIXED_NOW)  # type: ignore[arg-type]
        assert rows == []


# ---------------------------------------------------------------------------
# sum_window
# ---------------------------------------------------------------------------


class TestSumWindow:
    def test_sum_window_sums_matching_keys(self):
        rows = [
            _row(0, current=5.0),
            _row(-1, current=3.0),
            _row(-2, current=2.5),
        ]
        total, count = sum_window(rows, "current")
        assert total == 10.5
        assert count == 3

    def test_sum_window_skips_rows_missing_plan_key(self):
        """Sparse alt presence: only rows containing the key contribute."""
        rows = [
            _row(0, current=5.0, alt_AGL=4.0),
            _row(-1, current=3.0),  # no alt_AGL today
            _row(-2, current=2.5, alt_AGL=2.0),
        ]
        total, count = sum_window(rows, "alt_AGL")
        assert total == 6.0
        assert count == 2

    def test_sum_window_returns_none_when_no_rows_have_key(self):
        rows = [
            _row(0, current=5.0),
            _row(-1, current=3.0),
        ]
        total, count = sum_window(rows, "alt_GHOST")
        assert total is None
        assert count == 0

    def test_sum_window_handles_string_values_defensively(self):
        """HA recorder restores can deliver numerics as strings."""
        rows = [
            {"date": "2026-05-17", "current": "5.25"},
            {"date": "2026-05-16", "current": "1.75"},
        ]
        total, count = sum_window(rows, "current")
        assert total == 7.0
        assert count == 2

    def test_sum_window_skips_nonnumeric_values(self):
        """``"unavailable"`` or ``None`` are filtered, not raised."""
        rows = [
            {"date": "2026-05-17", "current": "unavailable"},
            {"date": "2026-05-16", "current": None},
            {"date": "2026-05-15", "current": 4.0},
        ]
        total, count = sum_window(rows, "current")
        assert total == 4.0
        assert count == 1

    def test_sum_window_counts_only_rows_actually_summed(self):
        """``day_count`` accurately reflects contributing rows."""
        rows = [
            _row(0, current=5.0),
            _row(-1, current=3.0),
            _row(-2),  # no current key
            _row(-3, current=2.0),
        ]
        total, count = sum_window(rows, "current")
        assert total == 10.0
        assert count == 3

    def test_sum_window_treats_explicit_zero_as_legitimate(self):
        """A 0.0 cost row IS counted (100% solar self-consumption is real)."""
        rows = [
            _row(0, current=0.0),
            _row(-1, current=0.0),
        ]
        total, count = sum_window(rows, "current")
        assert total == 0.0
        assert count == 2

    def test_sum_window_handles_empty_rows(self):
        total, count = sum_window([], "current")
        assert total is None
        assert count == 0

    def test_sum_window_handles_negative_values(self):
        """Negative costs (credits) sum correctly with positives."""
        rows = [
            _row(0, current=5.0),
            _row(-1, current=-2.0),
            _row(-2, current=3.0),
        ]
        total, count = sum_window(rows, "current")
        assert total == 6.0
        assert count == 3


# ---------------------------------------------------------------------------
# best_alternative_for_window
# ---------------------------------------------------------------------------


class TestBestAlternativeForWindow:
    def test_best_alt_picks_lowest_sum_across_alts(self):
        rows = [
            _row(0, alt_AGL=4.0, alt_ORIG=5.0, alt_RED=6.0),
            _row(-1, alt_AGL=3.0, alt_ORIG=4.5, alt_RED=5.5),
        ]
        plan_id, total, count = best_alternative_for_window(rows)
        assert plan_id == "AGL"
        assert total == 7.0
        assert count == 2

    def test_best_alt_tie_broken_lexicographically(self):
        """Two alts with identical sums → lexicographically-smallest wins."""
        rows = [
            _row(0, alt_AGL=4.0, alt_BGE=4.0),
            _row(-1, alt_AGL=3.0, alt_BGE=3.0),
        ]
        plan_id, total, _ = best_alternative_for_window(rows)
        assert plan_id == "AGL"
        assert total == 7.0

    def test_best_alt_handles_no_alt_keys(self):
        """Rows present, but none carry an ``alt_*`` key."""
        rows = [
            _row(0, current=5.0),
            _row(-1, current=3.0),
        ]
        plan_id, total, count = best_alternative_for_window(rows)
        assert plan_id is None
        assert total is None
        assert count == 0

    def test_best_alt_ignores_non_alt_prefix_keys(self):
        """``current``, ``named``, ``amber`` keys are not candidates."""
        rows = [
            _row(0, current=5.0, named=4.0, amber=3.0, alt_X=2.0),
        ]
        plan_id, total, _ = best_alternative_for_window(rows)
        assert plan_id == "X"
        assert total == 2.0

    def test_best_alt_skips_alts_with_no_numeric_rows(self):
        """An alt key present only on rows with bad values is excluded."""
        rows = [
            {"date": "2026-05-17", "alt_BAD": "unavailable", "alt_GOOD": 4.0},
            {"date": "2026-05-16", "alt_BAD": None, "alt_GOOD": 3.0},
        ]
        plan_id, total, _ = best_alternative_for_window(rows)
        assert plan_id == "GOOD"
        assert total == 7.0

    def test_best_alt_returns_none_for_empty_rows(self):
        plan_id, total, count = best_alternative_for_window([])
        assert plan_id is None
        assert total is None
        assert count == 0

    def test_best_alt_accepts_custom_prefix(self):
        """Allow callers to scan alternate prefixes (e.g. for migration)."""
        rows = [
            _row(0, candidate_FOO=4.0, candidate_BAR=2.0),
        ]
        plan_id, total, _ = best_alternative_for_window(
            rows, alt_key_prefix="candidate_",
        )
        assert plan_id == "BAR"
        assert total == 2.0


# ---------------------------------------------------------------------------
# savings
# ---------------------------------------------------------------------------


class TestSavings:
    def test_savings_positive_when_alt_cheaper(self):
        assert savings(10.0, 8.0) == 2.0

    def test_savings_negative_when_current_cheaper(self):
        """Current plan beats every alt → negative savings (surface honestly)."""
        assert savings(8.0, 10.0) == -2.0

    def test_savings_zero_when_equal(self):
        assert savings(7.5, 7.5) == 0.0

    def test_savings_none_when_either_side_none(self):
        assert savings(None, 5.0) is None
        assert savings(5.0, None) is None
        assert savings(None, None) is None
