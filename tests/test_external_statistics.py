"""Phase 9 PR-10 — external statistics dual-write tests.

The conftest stubs `homeassistant.components.recorder.statistics` with
- StatisticData / StatisticMetaData as plain dicts
- async_add_external_statistics as an observable recorder
so tests can verify the calls without HA's recorder running.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from homeassistant.components.recorder.statistics import _calls as _stats_calls
from custom_components.pricehawk.const import DOMAIN
from custom_components.pricehawk.statistics import (
    async_backfill_external_statistics,
    async_push_daily_cost_to_statistics,
    external_statistic_id,
)


def _reset_stats():
    _stats_calls.clear()


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ----------------------------------------------------------------------
# external_statistic_id
# ----------------------------------------------------------------------


class TestExternalStatisticId:
    def test_format_prefixes_with_domain(self):
        sid = external_statistic_id("abcdef1234567890", "amber")
        assert sid.startswith(f"{DOMAIN}:cost_")

    def test_entry_id_sliced_to_8_chars(self):
        sid = external_statistic_id("entry-id-very-long-string", "amber")
        assert "entry-id" in sid
        assert "very-long" not in sid

    def test_stable_across_calls(self):
        a = external_statistic_id("abcdefgh", "amber")
        b = external_statistic_id("abcdefgh", "amber")
        assert a == b

    def test_distinct_per_provider(self):
        amber = external_statistic_id("abcdefgh", "amber")
        globird = external_statistic_id("abcdefgh", "globird")
        assert amber != globird

    def test_distinct_per_entry(self):
        a = external_statistic_id("entry-AAA", "amber")
        b = external_statistic_id("entry-BBB", "amber")
        assert a != b

    def test_provider_id_lowercased_for_ha_recorder_contract(self):
        """Belt-and-suspenders: a future provider whose id is not all-lowercase
        (e.g. ``DWT_AEMO_Direct``) must still produce a valid recorder object_id.
        Regression guard from #107 retro-review.
        """
        sid = external_statistic_id("01KS83AKB2TN6G0BT9TAC1EMN9", "DWT_AEMO_Direct")
        _, object_id = sid.split(":", 1)
        assert object_id == object_id.lower(), (
            f"object_id {object_id!r} must be lowercase per HA recorder contract"
        )
        assert sid == "pricehawk:cost_01ks83ak_dwt_aemo_direct"

    def test_id_is_lowercase_for_ha_recorder_contract(self):
        """HA's recorder validates statistic_id as ``<domain>:<object_id>``
        where ``object_id`` must match ``[a-z0-9_]+``. HA's ULID entry_ids
        are UPPERCASE (e.g. ``01KS83AKB2TN6G0BT9TAC1EMN9``) so the raw
        slice produces an invalid id and recorder raises "Invalid
        statistic_id" — the backfill silently fails and the Energy
        Dashboard never sees historical cost data.
        Regression test for live UAT 2026-05-23.
        """
        sid = external_statistic_id("01KS83AKB2TN6G0BT9TAC1EMN9", "dwt_aemo_direct")
        # Split on the first colon (the domain separator) — everything
        # AFTER the colon is the object_id and must be lowercase.
        _, object_id = sid.split(":", 1)
        assert object_id == object_id.lower(), (
            f"object_id {object_id!r} must be lowercase per HA recorder contract"
        )
        # Spot-check the literal expected form so a future refactor
        # that drops the .lower() trips this test immediately.
        assert sid == "pricehawk:cost_01ks83ak_dwt_aemo_direct"


# ----------------------------------------------------------------------
# async_push_daily_cost_to_statistics
# ----------------------------------------------------------------------


class TestPushDailyCost:
    def test_calls_async_add_external_statistics(self):
        _reset_stats()
        _run(async_push_daily_cost_to_statistics(
            hass=None,
            entry_id="entry-abc123",
            provider_id="amber",
            day=date(2026, 5, 22),
            cost_aud=5.23,
            cumulative_sum=156.78,
        ))
        assert len(_stats_calls) == 1

    def test_metadata_includes_unit_of_measurement_aud(self):
        _reset_stats()
        _run(async_push_daily_cost_to_statistics(
            None, "entry-abc123", "amber",
            date(2026, 5, 22), 5.23, 156.78,
        ))
        metadata, _stats = _stats_calls[0]
        assert metadata["unit_of_measurement"] == "AUD"

    def test_metadata_has_sum_true(self):
        _reset_stats()
        _run(async_push_daily_cost_to_statistics(
            None, "entry-abc123", "amber",
            date(2026, 5, 22), 5.23, 156.78,
        ))
        metadata, _ = _stats_calls[0]
        assert metadata["has_sum"] is True
        assert metadata["has_mean"] is False

    def test_metadata_source_is_domain(self):
        _reset_stats()
        _run(async_push_daily_cost_to_statistics(
            None, "entry-abc123", "amber",
            date(2026, 5, 22), 5.23, 156.78,
        ))
        metadata, _ = _stats_calls[0]
        assert metadata["source"] == DOMAIN

    def test_stat_start_is_midnight_utc(self):
        _reset_stats()
        _run(async_push_daily_cost_to_statistics(
            None, "entry-abc123", "amber",
            date(2026, 5, 22), 5.23, 156.78,
        ))
        _, stats = _stats_calls[0]
        assert len(stats) == 1
        start = stats[0]["start"]
        assert isinstance(start, datetime)
        assert start.tzinfo == timezone.utc
        assert start.hour == 0
        assert start.minute == 0


# ----------------------------------------------------------------------
# async_backfill_external_statistics
# ----------------------------------------------------------------------


def _history(*rows):
    return [{"date": d, **costs} for d, costs in rows]


class TestBackfill:
    def test_empty_history_returns_zero(self):
        _reset_stats()
        result = _run(async_backfill_external_statistics(
            None, "entry-abc123", [],
        ))
        assert result == 0
        assert len(_stats_calls) == 0

    def test_walks_history_in_order(self):
        _reset_stats()
        history = _history(
            ("2026-05-20", {"amber": 5.0}),
            ("2026-05-21", {"amber": 6.0}),
            ("2026-05-22", {"amber": 7.0}),
        )
        count = _run(async_backfill_external_statistics(
            None, "entry-abc123", history,
        ))
        assert count == 3
        assert len(_stats_calls) == 1  # one batch call for amber
        _, stats = _stats_calls[0]
        assert [s["state"] for s in stats] == [5.0, 6.0, 7.0]

    def test_computes_monotonic_cumulative_sum(self):
        _reset_stats()
        history = _history(
            ("2026-05-20", {"amber": 5.0}),
            ("2026-05-21", {"amber": 6.0}),
            ("2026-05-22", {"amber": 7.0}),
        )
        _run(async_backfill_external_statistics(
            None, "entry-abc123", history,
        ))
        _, stats = _stats_calls[0]
        assert [s["sum"] for s in stats] == [5.0, 11.0, 18.0]

    def test_one_batch_per_provider(self):
        _reset_stats()
        history = _history(
            ("2026-05-20", {"amber": 5.0, "globird": 4.5}),
            ("2026-05-21", {"amber": 6.0, "globird": 5.5}),
        )
        _run(async_backfill_external_statistics(
            None, "entry-abc123", history,
        ))
        assert len(_stats_calls) == 2  # one batch per provider

    def test_negative_cost_does_not_break_cumulative(self):
        """Export-heavy day with high FiT → negative cost. Cumulative dips."""
        _reset_stats()
        history = _history(
            ("2026-05-20", {"amber": 5.0}),
            ("2026-05-21", {"amber": -2.0}),
            ("2026-05-22", {"amber": 3.0}),
        )
        _run(async_backfill_external_statistics(
            None, "entry-abc123", history,
        ))
        _, stats = _stats_calls[0]
        assert [s["sum"] for s in stats] == [5.0, 3.0, 6.0]

    def test_malformed_date_skipped(self):
        _reset_stats()
        history = [
            {"date": "2026-05-20", "amber": 5.0},
            {"date": "garbage", "amber": 6.0},  # skip
            {"date": "2026-05-22", "amber": 7.0},
        ]
        count = _run(async_backfill_external_statistics(
            None, "entry-abc123", history,
        ))
        assert count == 2

    def test_non_numeric_provider_value_skipped(self):
        """Defensive: history dict might have str values from old code paths."""
        _reset_stats()
        history = [
            {"date": "2026-05-20", "amber": 5.0, "extras": "non-numeric"},
        ]
        count = _run(async_backfill_external_statistics(
            None, "entry-abc123", history,
        ))
        assert count == 1
