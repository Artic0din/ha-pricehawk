"""Phase 3.2 commit 1 — pure-logic tests for ``cdr.history_replay``.

Pattern mirrors ``tests/test_coordinator_ranking.py``: one TestClass
per public function, stdlib only (no ``pytest-asyncio``), no HA mocks
(the module under test has no HA imports).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from custom_components.pricehawk.cdr.evaluator import CostBreakdown
from custom_components.pricehawk.cdr.history_replay import (
    daily_slot_iterator,
    fan_out_replay,
    group_slots_by_local_date,
    replay_day_through_plan,
    states_to_half_hour_slots,
    widen_window_for_slot_alignment,
)

AEST = timezone(timedelta(hours=10))


def _flat_plan(*, plan_id: str = "FLAT", unit_price: str = "0.30") -> dict:
    """Minimal single-rate plan body the evaluator accepts. $0.30/kWh
    flat import, $1/day supply (ex-GST)."""
    return {
        "planId": plan_id,
        "electricityContract": {
            "pricingModel": "SINGLE_RATE",
            "tariffPeriod": [
                {
                    "rateBlockUType": "singleRate",
                    "singleRate": {
                        "rates": [{"unitPrice": unit_price}],
                    },
                    "dailySupplyCharge": "1.00",
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# states_to_half_hour_slots
# ---------------------------------------------------------------------------


class TestStatesToHalfHourSlots:
    def test_states_aligned_to_30min_boundary(self):
        """An 11:17 reading must land in the 11:00–11:30 slot."""
        base = datetime(2026, 5, 17, 11, 17, 0, tzinfo=AEST)
        states = [
            (base, 2000.0, "W"),
            (base + timedelta(minutes=1), 2000.0, "W"),
        ]
        slots = states_to_half_hour_slots(states)
        assert len(slots) == 1
        assert slots[0]["ts_local"].startswith("2026-05-17T11:00:00")

    def test_kw_unit_multiplied_by_1000(self):
        """Same energy whether supplied as W or kW units."""
        start = datetime(2026, 5, 17, 12, 0, 0, tzinfo=AEST)
        # 2 kW for 6 minutes (capped delta) = 0.2 kWh
        kw_slots = states_to_half_hour_slots(
            [
                (start, 2.0, "kW"),
                (start + timedelta(minutes=10), 2.0, "kW"),
            ]
        )
        w_slots = states_to_half_hour_slots(
            [
                (start, 2000.0, "W"),
                (start + timedelta(minutes=10), 2000.0, "W"),
            ]
        )
        assert len(kw_slots) == 1
        assert len(w_slots) == 1
        assert abs(kw_slots[0]["grid_import_kwh"] - w_slots[0]["grid_import_kwh"]) < 1e-9

    def test_gap_protection_caps_long_delta(self):
        """A 1-hour gap clamps to 0.1h (6 min) — prevents runaway from recorder gaps."""
        start = datetime(2026, 5, 17, 0, 0, 0, tzinfo=AEST)
        # 1 kW * 0.1h = 0.1 kWh (NOT 1 kWh).
        slots = states_to_half_hour_slots(
            [
                (start, 1000.0, "W"),
                (start + timedelta(hours=1), 1000.0, "W"),
            ]
        )
        assert len(slots) == 1
        assert abs(slots[0]["grid_import_kwh"] - 0.1) < 1e-9

    def test_negative_power_lands_in_export(self):
        """``power_w = -2000`` fills ``grid_export_kwh`` (and zero import)."""
        start = datetime(2026, 5, 17, 12, 0, 0, tzinfo=AEST)
        slots = states_to_half_hour_slots(
            [
                (start, -2000.0, "W"),
                (start + timedelta(minutes=10), -2000.0, "W"),
            ]
        )
        assert len(slots) == 1
        assert slots[0]["grid_import_kwh"] == 0.0
        assert slots[0]["grid_export_kwh"] > 0

    def test_zero_power_skipped(self):
        """All-zero readings produce no slots (avoid empty dicts)."""
        start = datetime(2026, 5, 17, 12, 0, 0, tzinfo=AEST)
        slots = states_to_half_hour_slots(
            [
                (start, 0.0, "W"),
                (start + timedelta(minutes=10), 0.0, "W"),
            ]
        )
        assert slots == []

    def test_states_handles_string_power_values(self):
        """HA's recorder serialises some numeric states as ``"2000"``."""
        start = datetime(2026, 5, 17, 12, 0, 0, tzinfo=AEST)
        slots = states_to_half_hour_slots(
            [
                (start, "2000", "W"),
                (start + timedelta(minutes=10), "2000", "W"),
            ]
        )
        assert len(slots) == 1
        assert slots[0]["grid_import_kwh"] > 0

    def test_states_skips_nonparseable_power(self):
        """``"unavailable"`` or other non-numeric states are filtered out."""
        start = datetime(2026, 5, 17, 12, 0, 0, tzinfo=AEST)
        slots = states_to_half_hour_slots(
            [
                (start, "unavailable", "W"),
                (start + timedelta(minutes=10), "2000", "W"),
                (start + timedelta(minutes=20), "2000", "W"),
            ]
        )
        # First reading dropped, two remaining produce one slot.
        assert len(slots) == 1

    def test_states_returns_empty_for_single_reading(self):
        """Need >=2 readings to compute a delta — return empty otherwise."""
        start = datetime(2026, 5, 17, 12, 0, 0, tzinfo=AEST)
        assert states_to_half_hour_slots([(start, 2000.0, "W")]) == []
        assert states_to_half_hour_slots([]) == []

    def test_states_sorts_unordered_input(self):
        """Out-of-order timestamps still produce chronologically-sorted slots."""
        t1 = datetime(2026, 5, 17, 12, 0, 0, tzinfo=AEST)
        t2 = datetime(2026, 5, 17, 13, 0, 0, tzinfo=AEST)
        slots = states_to_half_hour_slots(
            [
                (t2, 1000.0, "W"),
                (t1, 1000.0, "W"),
                (t1 + timedelta(minutes=10), 1000.0, "W"),
            ]
        )
        timestamps = [s["ts_local"] for s in slots]
        assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# replay_day_through_plan
# ---------------------------------------------------------------------------


class TestReplayDayThroughPlan:
    def _slots(self) -> list[dict]:
        """One half-hour slot, 1 kWh import."""
        return [
            {
                "ts_local": "2026-05-17T12:00:00+10:00",
                "grid_import_kwh": 1.0,
                "grid_export_kwh": 0.0,
            }
        ]

    def test_replay_returns_breakdown_on_happy_path(self):
        bd = replay_day_through_plan(self._slots(), _flat_plan())
        assert bd is not None
        # 1 kWh × $0.30 + $1 supply = $1.30 ex-GST; ×1.10 = $1.43 inc-GST.
        assert abs(float(bd.total_aud_inc_gst) - 1.43) < 0.01

    def test_replay_returns_none_on_evaluator_exception(self):
        """An evaluator that raises returns None (not propagated)."""
        with patch(
            "custom_components.pricehawk.cdr.history_replay.evaluate",
            side_effect=RuntimeError("boom"),
        ):
            assert replay_day_through_plan(self._slots(), _flat_plan()) is None

    def test_replay_returns_none_on_zero_slot_count(self):
        """Empty slot list short-circuits to None before calling evaluate."""
        assert replay_day_through_plan([], _flat_plan()) is None

    def test_replay_returns_none_when_evaluator_yields_zero_slots(self):
        """Evaluator succeeded but produced no scored slots → None."""
        empty_bd = CostBreakdown(slot_count=0)
        with patch(
            "custom_components.pricehawk.cdr.history_replay.evaluate",
            return_value=empty_bd,
        ):
            assert replay_day_through_plan(self._slots(), _flat_plan()) is None

    def test_replay_passes_entry_options_through(self):
        """Opt-in fields reach the evaluator."""
        captured: dict = {}

        def fake_evaluate(_plan, _consumption, **kwargs):
            captured.update(kwargs)
            return CostBreakdown(slot_count=1)

        with patch(
            "custom_components.pricehawk.cdr.history_replay.evaluate",
            side_effect=fake_evaluate,
        ):
            replay_day_through_plan(
                self._slots(),
                _flat_plan(),
                entry_options={"ovo_interest_balance_aud": 100.0},
            )
        assert captured.get("entry_options") == {"ovo_interest_balance_aud": 100.0}


# ---------------------------------------------------------------------------
# fan_out_replay
# ---------------------------------------------------------------------------


class TestFanOutReplay:
    def _three_days(self) -> dict[str, list[dict]]:
        return {
            "2026-05-15": [
                {
                    "ts_local": "2026-05-15T12:00:00+10:00",
                    "grid_import_kwh": 1.0,
                    "grid_export_kwh": 0.0,
                }
            ],
            "2026-05-16": [
                {
                    "ts_local": "2026-05-16T12:00:00+10:00",
                    "grid_import_kwh": 2.0,
                    "grid_export_kwh": 0.0,
                }
            ],
            "2026-05-17": [
                {
                    "ts_local": "2026-05-17T12:00:00+10:00",
                    "grid_import_kwh": 0.5,
                    "grid_export_kwh": 0.0,
                }
            ],
        }

    def test_fan_out_yields_one_tuple_per_day(self):
        out = list(fan_out_replay(self._three_days(), {"flat": _flat_plan()}))
        assert len(out) == 3
        # All days produced a cost for the flat plan.
        for _, row in out:
            assert "flat" in row

    def test_fan_out_excludes_failed_plans_from_day_dict(self):
        """A plan whose evaluator throws is absent from that day's dict."""

        def faulty_eval(plan, *_a, **_kw):
            # Use plan_id to know which plan is being evaluated.
            pd = plan.get("data") or plan
            if pd.get("planId") == "BAD":
                raise RuntimeError("malformed")
            return CostBreakdown(slot_count=1)

        plans = {
            "flat": _flat_plan(plan_id="FLAT"),
            "alt_BAD": _flat_plan(plan_id="BAD"),
        }
        with patch(
            "custom_components.pricehawk.cdr.history_replay.evaluate",
            side_effect=faulty_eval,
        ):
            out = list(fan_out_replay(self._three_days(), plans))
        # 3 days, each row has flat but not alt_BAD.
        for _, row in out:
            assert "flat" in row
            assert "alt_BAD" not in row

    def test_fan_out_empty_plans_dict_yields_empty_dicts(self):
        """``fan_out({date: slots}, {})`` yields one tuple per date with ``{}``."""
        out = list(fan_out_replay(self._three_days(), {}))
        assert len(out) == 3
        assert all(row == {} for _, row in out)

    def test_fan_out_empty_daily_slots_yields_nothing(self):
        assert list(fan_out_replay({}, {"flat": _flat_plan()})) == []

    def test_fan_out_preserves_date_ordering(self):
        """Iterates ``daily_slots`` in lexicographic (== chronological) order."""
        out = list(fan_out_replay(self._three_days(), {"flat": _flat_plan()}))
        dates = [d for d, _ in out]
        assert dates == sorted(dates)

    def test_fan_out_returns_rounded_aud(self):
        """Cost values are rounded to 2 decimal places to match
        the existing daily_cost_history rounding convention."""
        out = list(fan_out_replay(self._three_days(), {"flat": _flat_plan()}))
        for _, row in out:
            for v in row.values():
                # round(x, 2) result has at most 2 decimals once cast to float.
                assert v == round(v, 2)


# ---------------------------------------------------------------------------
# group_slots_by_local_date
# ---------------------------------------------------------------------------


class TestGroupSlotsByLocalDate:
    def test_groups_by_date_prefix(self):
        slots = [
            {"ts_local": "2026-05-15T23:30:00+10:00", "grid_import_kwh": 1.0},
            {"ts_local": "2026-05-16T00:00:00+10:00", "grid_import_kwh": 2.0},
            {"ts_local": "2026-05-16T12:00:00+10:00", "grid_import_kwh": 3.0},
        ]
        grouped = group_slots_by_local_date(slots)
        assert set(grouped.keys()) == {"2026-05-15", "2026-05-16"}
        assert len(grouped["2026-05-16"]) == 2

    def test_skips_slots_without_ts_local(self):
        slots = [
            {"ts_local": "2026-05-15T12:00:00+10:00", "grid_import_kwh": 1.0},
            {"grid_import_kwh": 99.0},  # missing ts_local
            {"ts_local": "", "grid_import_kwh": 5.0},  # empty
        ]
        grouped = group_slots_by_local_date(slots)
        assert list(grouped.keys()) == ["2026-05-15"]
        assert len(grouped["2026-05-15"]) == 1


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


class TestDailySlotIterator:
    def test_end_to_end_states_to_per_date_map(self):
        """Compose states_to_half_hour_slots + group_slots_by_local_date."""
        t = datetime(2026, 5, 17, 12, 0, 0, tzinfo=AEST)
        states = [
            (t, 1000.0, "W"),
            (t + timedelta(minutes=10), 1000.0, "W"),
        ]
        grouped = daily_slot_iterator(states)
        assert "2026-05-17" in grouped
        assert len(grouped["2026-05-17"]) == 1


class TestWidenWindowForSlotAlignment:
    def test_pads_start_back_by_one_slot(self):
        start = datetime(2026, 5, 17, 0, 0, 0, tzinfo=AEST)
        end = datetime(2026, 5, 18, 0, 0, 0, tzinfo=AEST)
        wstart, wend = widen_window_for_slot_alignment(start, end)
        assert wend == end
        assert (start - wstart) == timedelta(minutes=30)

    def test_accepts_custom_padding(self):
        start = datetime(2026, 5, 17, 0, 0, 0, tzinfo=AEST)
        end = datetime(2026, 5, 18, 0, 0, 0, tzinfo=AEST)
        wstart, _ = widen_window_for_slot_alignment(
            start,
            end,
            pre_padding=timedelta(minutes=5),
        )
        assert (start - wstart) == timedelta(minutes=5)
