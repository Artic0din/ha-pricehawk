"""Phase 8 PR-8 — repairs platform tests.

PriceHawkCoordinator is a MagicMock under conftest HA stubs (same root
cause as 07-02b D-1 deviation). The production `_set_repair` and
`_check_repairs` logic is therefore exercised via a small standalone
re-implementation that mirrors the production semantics + source-grep
asserts on coordinator.py for the existence + threshold contracts.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from custom_components.pricehawk.const import DOMAIN
from homeassistant.helpers import issue_registry as ir


def _coordinator_source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "pricehawk"
        / "coordinator.py"
    ).read_text()


def _reset_registry():
    ir._created.clear()
    ir._deleted.clear()


class _Stand:
    """Minimal stand-in mirroring the production _set_repair / _check_repairs."""

    def __init__(self, entry_id="test-entry"):
        self.hass = object()
        self.config_entry = type("E", (), {"entry_id": entry_id})()
        self._active_repair_ids: set[str] = set()
        self._grid_sensor_missing_ticks = 0
        self._ranking_last_run_at: datetime | None = None
        self._grid_power_entity = "sensor.grid_power"

    def _set_repair(
        self, issue_id, on, *, severity=ir.IssueSeverity.WARNING,
        translation_placeholders=None,
    ):
        scoped = f"{self.config_entry.entry_id}_{issue_id}"
        if on:
            if scoped in self._active_repair_ids:
                return
            ir.async_create_issue(
                self.hass, DOMAIN, scoped,
                is_fixable=False, severity=severity,
                translation_key=issue_id,
                translation_placeholders=translation_placeholders,
            )
            self._active_repair_ids.add(scoped)
        else:
            if scoped not in self._active_repair_ids:
                return
            ir.async_delete_issue(self.hass, DOMAIN, scoped)
            self._active_repair_ids.discard(scoped)

    def _check_repairs(self, grid_power_w, now_local):
        if grid_power_w is None:
            self._grid_sensor_missing_ticks += 1
            if self._grid_sensor_missing_ticks >= 10:
                self._set_repair(
                    "grid_sensor_unavailable", True,
                    translation_placeholders={
                        "entity_id": self._grid_power_entity or "(unset)",
                    },
                )
        else:
            self._grid_sensor_missing_ticks = 0
            self._set_repair("grid_sensor_unavailable", False)

        last_rank = self._ranking_last_run_at
        if last_rank is None:
            return
        age_hours = (now_local - last_rank).total_seconds() / 3600.0
        if age_hours > 36.0:
            self._set_repair(
                "ranking_stale", True,
                translation_placeholders={"hours": f"{age_hours:.1f}"},
            )
        else:
            self._set_repair("ranking_stale", False)


class TestGridSensorUnavailable:
    def test_raised_after_10_consecutive_none_reads(self):
        _reset_registry()
        c = _Stand()
        now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
        for _ in range(9):
            c._check_repairs(None, now)
        assert not ir._created
        c._check_repairs(None, now)
        assert any(
            "grid_sensor_unavailable" in iid
            for (_d, iid) in ir._created.keys()
        )

    def test_recovery_clears_issue(self):
        _reset_registry()
        c = _Stand()
        now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
        for _ in range(10):
            c._check_repairs(None, now)
        c._check_repairs(2000.0, now)
        assert c._grid_sensor_missing_ticks == 0
        assert any(
            "grid_sensor_unavailable" in iid for (_d, iid) in ir._deleted
        )

    def test_counter_resets_between_brief_outages(self):
        _reset_registry()
        c = _Stand()
        now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
        for _ in range(5):
            c._check_repairs(None, now)
        c._check_repairs(2000.0, now)
        for _ in range(5):
            c._check_repairs(None, now)
        assert c._grid_sensor_missing_ticks == 5


class TestRankingStale:
    def test_no_run_yet_does_not_raise(self):
        _reset_registry()
        c = _Stand()
        c._check_repairs(
            2000.0, datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
        )
        assert not ir._created

    def test_raised_after_36h(self):
        _reset_registry()
        c = _Stand()
        now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
        c._ranking_last_run_at = now - timedelta(hours=37)
        c._check_repairs(2000.0, now)
        assert any(
            "ranking_stale" in iid for (_d, iid) in ir._created.keys()
        )

    def test_cleared_after_fresh_run(self):
        _reset_registry()
        c = _Stand()
        now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
        c._ranking_last_run_at = now - timedelta(hours=37)
        c._check_repairs(2000.0, now)
        c._ranking_last_run_at = now
        c._check_repairs(2000.0, now)
        assert any(
            "ranking_stale" in iid for (_d, iid) in ir._deleted
        )

    def test_recent_run_not_flagged(self):
        _reset_registry()
        c = _Stand()
        now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
        c._ranking_last_run_at = now - timedelta(hours=20)
        c._check_repairs(2000.0, now)
        assert not any(
            "ranking_stale" in iid for (_d, iid) in ir._created.keys()
        )


class TestMultiEntryKeying:
    def test_issue_id_scoped_to_entry_id(self):
        _reset_registry()
        a = _Stand(entry_id="entry-A")
        b = _Stand(entry_id="entry-B")
        a._set_repair("grid_sensor_unavailable", True)
        b._set_repair("grid_sensor_unavailable", True)
        keys = list(ir._created.keys())
        assert (DOMAIN, "entry-A_grid_sensor_unavailable") in keys
        assert (DOMAIN, "entry-B_grid_sensor_unavailable") in keys


class TestSetRepairDedup:
    def test_no_double_create(self):
        _reset_registry()
        c = _Stand()
        c._set_repair("grid_sensor_unavailable", True)
        ir._created.clear()
        c._set_repair("grid_sensor_unavailable", True)
        assert not ir._created

    def test_no_double_delete(self):
        _reset_registry()
        c = _Stand()
        c._set_repair("grid_sensor_unavailable", False)
        assert not ir._deleted


class TestCoordinatorSourceContract:
    """Production coordinator.py must match the stand-in semantics."""

    def test_set_repair_present(self):
        assert "def _set_repair(" in _coordinator_source()

    def test_check_repairs_present(self):
        assert "def _check_repairs(" in _coordinator_source()

    def test_check_repairs_called_in_tick_loop(self):
        src = _coordinator_source()
        assert "self._check_repairs(grid_power_w, now_local)" in src

    def test_grid_sensor_threshold_matches_stand_in(self):
        src = _coordinator_source()
        # Threshold is 10 ticks (= 5 minutes at 30s coordinator interval).
        assert "self._grid_sensor_missing_ticks >= 10" in src

    def test_ranking_threshold_matches_stand_in(self):
        src = _coordinator_source()
        assert "if age_hours > 36.0:" in src

    def test_issue_id_scoped_to_entry_id_in_production(self):
        src = _coordinator_source()
        assert 'f"{self.config_entry.entry_id}_{issue_id}"' in src

    def test_active_repair_ids_dedup_set_in_init(self):
        src = _coordinator_source()
        assert "self._active_repair_ids: set[str] = set()" in src


class TestStringsHaveIssues:
    def test_issues_block_present(self):
        s = json.load(open(
            Path(__file__).resolve().parents[1]
            / "custom_components" / "pricehawk" / "strings.json"
        ))
        assert "issues" in s
        for issue_id in ("grid_sensor_unavailable", "ranking_stale"):
            assert issue_id in s["issues"]
            assert "title" in s["issues"][issue_id]
            assert "description" in s["issues"][issue_id]

    def test_translations_byte_identical(self):
        repo = Path(__file__).resolve().parents[1]
        a = (
            repo / "custom_components" / "pricehawk" / "strings.json"
        ).read_bytes()
        b = (
            repo / "custom_components" / "pricehawk" / "translations" / "en.json"
        ).read_bytes()
        assert a == b
