"""Tests for cdr.streaming.CdrStreamingEngine — Phase 1.2 streaming adapter.

The streaming engine ingests power readings + half-hourly accumulates them
into slots, then calls cdr.evaluate on demand. These tests drive it over
the same 7d consumption fixture used by `phase_1_parity.py` (converted to
power readings via 6-min sub-sampling) and verify it produces the same
total cost as a direct batch `cdr.evaluate` call.

Also pins TariffEngine-compatible properties so CdrGloBirdProvider drop-in
replacement works.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, date
from pathlib import Path

import pytest

from custom_components.pricehawk.cdr import evaluate
from custom_components.pricehawk.cdr.streaming import CdrStreamingEngine

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "phase0"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


def _drive_engine_with_slots(engine: CdrStreamingEngine, slots: list[dict]) -> None:
    """Feed slots into streaming engine via 6-min sub-sampling.

    Matches the convention from `scripts/phase_1_parity.py` (each 30-min slot
    fed as 5 x 6-min readings at constant mean kW). Engine auto-rolls daily
    state on date change.
    """
    SLOT_HOURS = 0.5
    SUBSTEPS = 5
    SUBSTEP_MIN = 6
    last_date = None
    for slot in slots:
        local_dt = datetime.fromisoformat(slot["ts_local"]).replace(tzinfo=None)
        if last_date is not None and local_dt.date() != last_date:
            # End-of-day rollover happens via engine's auto-reset on update()
            pass
        last_date = local_dt.date()
        net_kw = (
            float(slot.get("grid_import_kwh", 0)) - float(slot.get("grid_export_kwh", 0))
        ) / SLOT_HOURS
        net_w = net_kw * 1000.0
        for i in range(SUBSTEPS):
            engine.update(net_w, local_dt + timedelta(minutes=SUBSTEP_MIN * i))


def test_streaming_engine_starts_empty() -> None:
    plan = _load("plan_globird_GLO731031MR@VEC.json")
    engine = CdrStreamingEngine(plan)
    assert engine.import_kwh_today == 0
    assert engine.export_kwh_today == 0
    assert engine.net_daily_cost_aud == 0


def test_streaming_single_day_matches_batch_evaluate() -> None:
    """Drive engine over May 10 slots; total should match cdr.evaluate on
    those same slots (with tolerance for slot-boundary fencepost diffs)."""
    plan = _load("plan_globird_GLO731031MR@VEC.json")
    cons = _load("consumption_7d.json")
    day_slots = [s for s in cons["slots"] if s["ts_local"].startswith("2026-05-10")]

    # Batch path
    bd_batch = evaluate(plan, {"slots": day_slots})
    batch_total = float(bd_batch.total_aud_inc_gst)

    # Streaming path
    engine = CdrStreamingEngine(plan)
    _drive_engine_with_slots(engine, day_slots)
    stream_total = engine.net_daily_cost_aud

    # Tolerance: streaming sub-samples to 6-min readings which gives ±cents
    # of accumulator drift vs batch (which uses slot totals directly).
    diff = abs(batch_total - stream_total)
    assert diff < 0.10, (
        f"streaming ${stream_total:.4f} vs batch ${batch_total:.4f} (diff ${diff:.4f})"
    )


def test_streaming_import_kwh_accumulates() -> None:
    plan = _load("plan_globird_GLO731031MR@VEC.json")
    engine = CdrStreamingEngine(plan)
    # First update primes _last_update; no energy accumulates yet
    engine.update(1000.0, datetime(2026, 5, 10, 12, 0, 0))
    assert engine.import_kwh_today == 0
    # Second update 30 min later should accumulate ~0.5 kWh (1 kW × 0.5h)
    # but GAP_PROTECTION caps delta at 0.1h => 0.1 kWh
    engine.update(1000.0, datetime(2026, 5, 10, 12, 30, 0))
    assert 0.09 < engine.import_kwh_today < 0.11


def test_streaming_gap_protection_caps_delta() -> None:
    """A 1-hour gap should only accumulate GAP_PROTECTION_MAX_DELTA_H = 0.1h."""
    plan = _load("plan_globird_GLO731031MR@VEC.json")
    engine = CdrStreamingEngine(plan)
    engine.update(2000.0, datetime(2026, 5, 10, 12, 0, 0))
    engine.update(2000.0, datetime(2026, 5, 10, 13, 0, 0))
    # 2 kW × 0.1h = 0.2 kWh (not 2 kW × 1h = 2 kWh)
    assert 0.19 < engine.import_kwh_today < 0.21


def test_streaming_export_routes_negative_power() -> None:
    plan = _load("plan_globird_GLO731031MR@VEC.json")
    engine = CdrStreamingEngine(plan)
    engine.update(-1500.0, datetime(2026, 5, 10, 13, 0, 0))
    engine.update(-1500.0, datetime(2026, 5, 10, 13, 6, 0))  # 6 min later
    # 1.5 kW export × 0.1h = 0.15 kWh
    assert 0.14 < engine.export_kwh_today < 0.16
    assert engine.import_kwh_today == 0


def test_streaming_reset_daily_clears_state() -> None:
    plan = _load("plan_globird_GLO731031MR@VEC.json")
    engine = CdrStreamingEngine(plan)
    engine.update(1000.0, datetime(2026, 5, 10, 12, 0, 0))
    engine.update(1000.0, datetime(2026, 5, 10, 12, 6, 0))
    assert engine.import_kwh_today > 0
    engine.reset_daily()
    assert engine.import_kwh_today == 0
    assert engine.export_kwh_today == 0


def test_streaming_current_import_rate_matches_tou() -> None:
    """At 5pm on a weekday the GloBird PEAK rate (0.36/kWh ex-GST × 1.10
    = 39.6 c/kWh inc-GST) should be returned."""
    plan = _load("plan_globird_GLO731031MR@VEC.json")
    engine = CdrStreamingEngine(plan)
    engine.update(0.0, datetime(2026, 5, 12, 17, 0, 0))  # Tuesday 17:00
    rate = engine.current_import_rate_c_kwh
    assert 39.0 < rate < 40.0, f"expected ~39.6 c/kWh inc-GST, got {rate}"


def test_streaming_current_import_rate_offpeak_free_window() -> None:
    """11am-2pm is the free window: 0.000001/kWh ex-GST × 1.10 × 100 ≈ 0 c/kWh."""
    plan = _load("plan_globird_GLO731031MR@VEC.json")
    engine = CdrStreamingEngine(plan)
    engine.update(0.0, datetime(2026, 5, 12, 12, 0, 0))
    rate = engine.current_import_rate_c_kwh
    assert rate < 0.01


def test_streaming_to_from_dict_roundtrip() -> None:
    from decimal import Decimal

    plan = _load("plan_globird_GLO731031MR@VEC.json")
    engine = CdrStreamingEngine(plan)
    engine._state_context_day_start = {
        "test_key": "test_value",
        "tiered_fit_period_credited": Decimal("12.5"),
    }
    engine._last_finalized_date = date(2026, 5, 9)
    engine.update(1000.0, datetime(2026, 5, 10, 12, 0, 0))
    engine.update(1000.0, datetime(2026, 5, 10, 12, 6, 0))
    state = engine.to_dict()

    assert "state_context_day_start" in state
    assert state["state_context_day_start"] == {
        "test_key": "test_value",
        "tiered_fit_period_credited": 12.5,
    }
    assert state["last_finalized_date"] == "2026-05-09"

    today = date(2026, 5, 10)
    restored = CdrStreamingEngine.from_dict(plan, state, today)
    assert pytest.approx(restored.import_kwh_today, abs=0.001) == engine.import_kwh_today
    assert restored._state_context_day_start == {
        "test_key": "test_value",
        "tiered_fit_period_credited": Decimal("12.5"),
    }
    assert restored._last_finalized_date == date(2026, 5, 9)


def test_streaming_month_rollover() -> None:
    plan = _load("plan_globird_GLO731031MR@VEC.json")
    engine = CdrStreamingEngine(plan)
    engine._state_context_day_start = {"test_key": "test_value"}
    engine._last_finalized_date = date(2026, 5, 31)
    engine._last_reset_date = date(2026, 5, 31)

    # Trigger reset daily with a new month
    engine.reset_daily(next_date=date(2026, 6, 1))

    # State context should be cleared due to month rollover
    assert engine._state_context_day_start == {}
    assert engine._last_reset_date == date(2026, 6, 1)


def test_streaming_stepped_singlerate_tariff() -> None:
    # Build a stepped singleRate plan to test select stepped rate logic
    plan = {
        "planId": "stepped-test",
        "displayName": "Stepped Test Plan",
        "brand": "generic",
        "electricityContract": {
            "pricingModel": "SINGLE_RATE",
            "tariffPeriod": [
                {
                    "rateBlockUType": "singleRate",
                    "singleRate": {
                        "rates": [{"unitPrice": 0.20, "volume": 10.0}, {"unitPrice": 0.30}]
                    },
                    "dailySupplyCharge": 1.0,
                }
            ],
        },
    }
    engine = CdrStreamingEngine(plan)
    # At 0 import_kwh_today, rate should be first step price
    engine.update(1000.0, datetime(2026, 5, 10, 12, 0, 0))
    # Select rate should check against current import_kwh_today.
    # Ex-GST rate should be 0.20. (c/kWh = 0.20 * 1.1 * 100 = 22.0)
    assert engine.current_import_rate_c_kwh == pytest.approx(22.0)

    # Add enough energy to cross step threshold (> 10 kWh)
    # Since GAP_PROTECTION caps delta, let's fake self._slots_today to have 15 kWh
    engine._slots_today = [
        {
            "ts_local": "2026-05-10T11:00:00",
            "grid_import_kwh": 15.0,
            "grid_export_kwh": 0.0,
            "solar_kwh": 0.0,
        }
    ]
    # Now rate should be second step price 0.30 (c/kWh = 0.30 * 1.1 * 100 = 33.0)
    assert engine.import_kwh_today > 10.0
    assert engine.current_import_rate_c_kwh == pytest.approx(33.0)


def test_cdr_plan_provider_satisfies_protocol() -> None:
    """CdrPlanProvider should be importable + match Provider Protocol shape.

    Phase 3.0 rename: id is now derived from plan brand + planId; name
    from plan.displayName. Generic across all retailers.
    """
    from custom_components.pricehawk.providers.base import Provider
    from custom_components.pricehawk.providers.cdr_plan import CdrPlanProvider

    plan = _load("plan_globird_GLO731031MR@VEC.json")
    p = CdrPlanProvider(plan)
    assert isinstance(p, Provider), "CdrPlanProvider must satisfy Provider Protocol"
    # Identity reflects the plan envelope, not a hardcoded "globird".
    assert p.id.startswith("globird")
    assert "GLO731031MR@VEC" in p.id
    # Name comes from plan.displayName when available.
    assert "GloBird" in p.name


def test_cdr_plan_provider_daily_fixed_charges_inc_gst() -> None:
    """Daily supply $1.05/day ex-GST × 1.10 = $1.155/day inc-GST."""
    from custom_components.pricehawk.providers.cdr_plan import CdrPlanProvider

    plan = _load("plan_globird_GLO731031MR@VEC.json")
    p = CdrPlanProvider(plan)
    # Plan C2 fixture: dailySupplyCharge = 1.05 ex-GST
    assert pytest.approx(p.daily_fixed_charges_aud, abs=0.001) == 1.155


def test_cdr_streaming_reset_daily_behavior() -> None:
    plan = {
        "brand": "Origin",
        "planId": "solar-max-1",
        "displayName": "Origin Solar Max",
        "electricityContract": {
            "tariffPeriod": [
                {"dailySupplyCharge": "1.00", "singleTariff": {"rates": [{"usageRate": "25.00"}]}}
            ],
            "incentives": [
                {
                    "displayName": "Tiered Solar",
                    "eligibility": "12 cents per kWh until a daily export limit of 8 kWh is reached. The daily export limit is averaged across your billing period",
                    "description": "",
                }
            ],
        },
    }
    engine = CdrStreamingEngine(plan)

    # Ingest some exports
    engine.update(-5000.0, datetime(2026, 6, 1, 12, 0, 0))  # 5 kW export
    engine.update(-5000.0, datetime(2026, 6, 1, 12, 30, 0))  # 5 kW export

    # Ensure exports accumulated
    assert engine.export_kwh_today > 0.0

    # 1. Manual reset (no next_date) -> should clear accumulators but NOT finalize state
    engine.reset_daily()
    assert engine.export_kwh_today == 0.0
    assert engine._state_context_day_start.get("tiered_fit_period_credited", 0.0) == 0.0

    # 2. Rollover reset (with next_date) -> should finalize state into tiered_fit_period_credited
    # Ingest some exports again
    engine.update(-5000.0, datetime(2026, 6, 1, 13, 0, 0))
    engine.update(-5000.0, datetime(2026, 6, 1, 13, 30, 0))
    exported = engine.export_kwh_today
    assert exported > 0.0

    engine.reset_daily(next_date=date(2026, 6, 2))
    assert engine.export_kwh_today == 0.0
    # Because it finalized, tiered_fit_period_credited should have recorded the credited exports
    assert float(engine._state_context_day_start.get("tiered_fit_period_credited", 0.0)) > 0.0
