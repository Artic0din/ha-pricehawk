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
        net_kw = ((float(slot.get("grid_import_kwh", 0))
                   - float(slot.get("grid_export_kwh", 0))) / SLOT_HOURS)
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
    assert diff < 0.10, f"streaming ${stream_total:.4f} vs batch ${batch_total:.4f} (diff ${diff:.4f})"


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
    plan = _load("plan_globird_GLO731031MR@VEC.json")
    engine = CdrStreamingEngine(plan)
    engine.update(1000.0, datetime(2026, 5, 10, 12, 0, 0))
    engine.update(1000.0, datetime(2026, 5, 10, 12, 6, 0))
    state = engine.to_dict()
    today = date(2026, 5, 10)
    restored = CdrStreamingEngine.from_dict(plan, state, today)
    assert pytest.approx(restored.import_kwh_today, abs=0.001) == engine.import_kwh_today


def test_cdr_globird_provider_satisfies_protocol() -> None:
    """CdrGloBirdProvider should be importable + match Provider Protocol shape."""
    from custom_components.pricehawk.providers.base import Provider
    from custom_components.pricehawk.providers.globird_cdr import CdrGloBirdProvider

    plan = _load("plan_globird_GLO731031MR@VEC.json")
    p = CdrGloBirdProvider(plan)
    assert isinstance(p, Provider), "CdrGloBirdProvider must satisfy Provider Protocol"
    assert p.id == "globird"
    assert "CDR" in p.name


def test_cdr_globird_provider_daily_fixed_charges_inc_gst() -> None:
    """Daily supply $1.05/day ex-GST × 1.10 = $1.155/day inc-GST."""
    from custom_components.pricehawk.providers.globird_cdr import CdrGloBirdProvider

    plan = _load("plan_globird_GLO731031MR@VEC.json")
    p = CdrGloBirdProvider(plan)
    # Plan C2 fixture: dailySupplyCharge = 1.05 ex-GST
    assert pytest.approx(p.daily_fixed_charges_aud, abs=0.001) == 1.155
