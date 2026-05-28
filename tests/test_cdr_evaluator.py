"""Smoke tests for cdr/evaluator.py — Phase 1.1 port verification.

Uses the Phase 0 fixtures committed in `tests/fixtures/phase0/` plus the
golden numbers verified by `scripts/phase_0_verify.py` (0.0000% cross-
check) and `scripts/phase_1_parity.py` (0.46% legacy parity).

These tests pin the evaluator's output. If you change evaluator
behaviour and these golden numbers change, update the docstring +
verify with `phase_0_verify.py --markdown` and `phase_1_parity.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.pricehawk.cdr import CostBreakdown, evaluate
from custom_components.pricehawk.cdr.models import (
    ConsumptionWindow,
    PlanDetailEnvelope,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "phase0"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


# --- Golden totals (verified by phase_0_verify.py 2026-05-14) ---
GOLDEN = {
    # plan_fixture, consumption_fixture: expected total_aud_inc_gst (to 2 d.p.)
    ("plan_agl_AGL907738MRE6@EME.json", "consumption_7d.json"): 89.40,
    ("plan_red-energy_RED552831MRE15@EME.json", "consumption_7d.json"): 86.67,
    ("plan_c1_flexible_synthetic.json", "consumption_7d.json"): 88.71,
    ("plan_globird_GLO731031MR@VEC.json", "consumption_7d.json"): 65.42,
    ("plan_red-energy_RED552831MRE15@EME.json", "consumption_dst_april_2026-04-05.json"): 6.86,
    ("plan_red-energy_RED552831MRE15@EME.json", "consumption_dst_october_2026-10-04.json"): 6.48,
}


@pytest.mark.parametrize(
    "plan_f,cons_f,expected_inc_gst", [(p, c, total) for (p, c), total in GOLDEN.items()]
)
def test_phase_0_golden_totals(plan_f: str, cons_f: str, expected_inc_gst: float) -> None:
    plan = _load(plan_f)
    cons = _load(cons_f)
    bd = evaluate(plan, cons)
    assert isinstance(bd, CostBreakdown)
    actual = float(bd.total_aud_inc_gst.quantize(__import__("decimal").Decimal("0.01")))
    assert actual == pytest.approx(expected_inc_gst, abs=0.01), (
        f"{plan_f}/{cons_f}: expected ${expected_inc_gst:.2f}, got ${actual:.2f}"
    )


def test_evaluate_accepts_pydantic_envelope() -> None:
    raw = _load("plan_agl_AGL907738MRE6@EME.json")
    env = PlanDetailEnvelope.model_validate(raw)
    cons_raw = _load("consumption_7d.json")
    cons = ConsumptionWindow.model_validate(cons_raw)
    bd = evaluate(env, cons)
    assert bd.total_aud_inc_gst > 0
    # pydantic-validated input should match raw-dict input within rounding
    bd_raw = evaluate(raw, cons_raw)
    assert bd.total_aud_inc_gst == bd_raw.total_aud_inc_gst


def test_evaluate_globird_parser_credits_zerohero() -> None:
    """Plan C2 must show globird parser hits in notes + incentive credit."""
    plan = _load("plan_globird_GLO731031MR@VEC.json")
    cons = _load("consumption_7d.json")
    bd = evaluate(plan, cons)
    assert any("globird parser hits" in n for n in bd.notes), bd.notes
    assert bd.incentive_aud_inc_gst < 0, "expected at least one credit applied"


def test_evaluate_runs_without_incentives_when_flag_off() -> None:
    plan = _load("plan_globird_GLO731031MR@VEC.json")
    cons = _load("consumption_7d.json")
    bd_off = evaluate(plan, cons, run_incentives=False)
    bd_on = evaluate(plan, cons, run_incentives=True)
    # Off path: no incentive credit
    assert bd_off.incentive_aud_inc_gst == 0
    # On path: at least some credit
    assert bd_on.incentive_aud_inc_gst < 0
    # Without incentives, total must be higher (no credit subtracted)
    assert bd_off.total_aud_inc_gst > bd_on.total_aud_inc_gst


def test_dst_april_50_slot_count() -> None:
    plan = _load("plan_red-energy_RED552831MRE15@EME.json")
    cons = _load("consumption_dst_april_2026-04-05.json")
    bd = evaluate(plan, cons)
    assert bd.slot_count == 50, "Apr 5 DST-backward day should be 50 half-hour slots (25h)"
    assert bd.period_days == 1


def test_dst_october_46_slot_count() -> None:
    plan = _load("plan_red-energy_RED552831MRE15@EME.json")
    cons = _load("consumption_dst_october_2026-10-04.json")
    bd = evaluate(plan, cons)
    assert bd.slot_count == 46, "Oct 4 DST-forward day should be 46 half-hour slots (23h)"
    assert bd.period_days == 1


def test_summary_returns_inc_gst_floats() -> None:
    plan = _load("plan_agl_AGL907738MRE6@EME.json")
    cons = _load("consumption_7d.json")
    bd = evaluate(plan, cons)
    s = bd.summary()
    assert "total_aud_inc_gst" in s
    assert s["period_days"] == 7
    assert s["slot_count"] == 336
    assert isinstance(s["total_aud_inc_gst"], float)
