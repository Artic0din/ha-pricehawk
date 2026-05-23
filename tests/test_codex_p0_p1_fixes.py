"""Regression tests for codex full-repo review findings (2026-05-23).

Covers the subset addressed in PR #109:
- P0-2: daily rollover MUST call ``reset_daily()`` on every registered
  provider, not only Amber. Without this, DWT/CdrPlan/FlowPower/LocalVolts
  providers accumulate ``today_cost`` across days, corrupting Energy
  Dashboard cost sensors and external statistics.
- P1-6: setup background tasks (ranking + backfill) MUST register
  ``entry.async_on_unload(task.cancel)`` so HA cancels them cleanly on
  reload/unload instead of leaving them to race an unloaded coordinator.

Source-level + behaviour assertions, mirroring the existing test_reauth
+ test_reconfigure conventions (the EnergyCompareConfigFlow can't be
instantiated under conftest HA stubs — see 07-02b D-1 deviation).
"""

from __future__ import annotations

from pathlib import Path


def _coordinator_source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "pricehawk"
        / "coordinator.py"
    ).read_text()


def _init_source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "pricehawk"
        / "__init__.py"
    ).read_text()


# ----------------------------------------------------------------------
# P0-2 — Daily rollover resets every provider, not just Amber
# ----------------------------------------------------------------------


class TestDailyRolloverResetsAllProviders:
    """Codex P0-2: daily rollover persists yesterday's history but the
    original implementation only reset the Amber provider's daily
    accumulators (and only inside the monthly-reset branch lower down).
    DWT, CdrPlan, FlowPower, and LocalVolts providers carried today's
    counters across midnight, corrupting Energy Dashboard cost sensors
    and external statistics.
    """

    def test_rollover_iterates_every_registered_provider(self):
        src = _coordinator_source()
        # Find the daily-rollover block — opens on `if now_local.day != self._last_date:`,
        # closes on the next async def.
        start = src.index("if now_local.day != self._last_date:")
        end = src.index("# 5. Push current rates into providers", start)
        block = src[start:end]
        # The fix iterates self._providers (the registered set) and
        # calls reset_daily on each. Both must appear in the block.
        assert "for provider in self._providers.values():" in block, (
            "Daily rollover must iterate every registered provider to "
            "reset their daily accumulators."
        )
        assert "provider.reset_daily()" in block, (
            "Daily rollover must call reset_daily() on each provider."
        )

    def test_rollover_guards_each_reset_with_try_except(self):
        """A single provider raising must not skip the rest. The rollover
        is shared infrastructure — one buggy provider can't take out the
        whole day's reset.
        """
        src = _coordinator_source()
        start = src.index("if now_local.day != self._last_date:")
        end = src.index("# 5. Push current rates into providers", start)
        block = src[start:end]
        # The for-loop wraps each call in a try/except.
        for_idx = block.index("for provider in self._providers.values():")
        # Walk forward to find the corresponding try.
        post_for = block[for_idx:]
        assert "try:" in post_for[: post_for.index("provider.reset_daily()") + 50], (
            "Each provider.reset_daily() call must be wrapped in try/"
            "except so one buggy provider can't break the day's reset."
        )
        assert "reset_daily() raised for provider" in block, (
            "On reset_daily failure the coordinator must log which "
            "provider raised so operators can triage."
        )

    def test_rollover_reset_runs_after_history_capture(self):
        """The history-append loop snapshots the PREVIOUS day's
        net_daily_cost_aud per provider. Resetting BEFORE that snapshot
        would zero history. The fix must run AFTER history capture +
        BEFORE the new day's tick.
        """
        src = _coordinator_source()
        start = src.index("if now_local.day != self._last_date:")
        end = src.index("# 5. Push current rates into providers", start)
        block = src[start:end]
        history_idx = block.index("history_entry[pid]")
        reset_idx = block.index("provider.reset_daily()")
        assert reset_idx > history_idx, (
            "reset_daily() must run AFTER history capture — otherwise "
            "the recorded daily_cost_history rows are always zero."
        )


# ----------------------------------------------------------------------
# P1-6 — Setup background tasks register cancellation on unload
# ----------------------------------------------------------------------


class TestBackgroundTaskCancellationOnUnload:
    """Codex P1-6: PR #107 switched the initial ranking + backfill
    schedulers to ``hass.async_create_background_task`` to fix the
    bootstrap-blocking bug, but didn't retain the task handles. On
    reload/unload the tasks survive against an unloaded coordinator —
    visible as pytest "coroutine was never awaited" warnings and as
    real data races in production.
    """

    def test_ranking_task_handle_captured_and_registered_for_cancel(self):
        src = _init_source()
        assert "ranking_task = hass.async_create_background_task(" in src, (
            "The initial ranking job task must be retained in a "
            "variable so we can hand its cancel to async_on_unload."
        )
        assert "entry.async_on_unload(ranking_task.cancel)" in src, (
            "Ranking task must register ``task.cancel`` with "
            "entry.async_on_unload so HA cancels it on reload/unload."
        )

    def test_backfill_task_handle_captured_and_registered_for_cancel(self):
        src = _init_source()
        assert "backfill_task = hass.async_create_background_task(" in src
        assert "entry.async_on_unload(backfill_task.cancel)" in src, (
            "Backfill task must register cancel-on-unload."
        )

    def test_no_bare_async_create_task_for_ranking_or_backfill(self):
        """Regression guard against accidentally reverting the
        bootstrap-blocking fix (PR #107) while patching task lifecycle.
        Bare ``hass.async_create_task(coordinator.async_run_ranking_job())``
        was the original bug — HA's bootstrap wait collected it and
        logged "Something is blocking startup". The fix uses
        ``async_create_background_task`` exclusively.
        """
        src = _init_source()
        assert "hass.async_create_task(coordinator.async_run_ranking_job" not in src, (
            "Ranking job must NOT be scheduled with async_create_task — "
            "use async_create_background_task to keep it off HA's "
            "bootstrap-wait list."
        )
        assert "hass.async_create_task(_backfill_after_ranking" not in src, (
            "Backfill task must NOT use async_create_task."
        )
