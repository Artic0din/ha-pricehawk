"""Phase 11 PR-18 — Hypothesis fuzzing of tariff_engine pure functions.

Five invariants per v2 research § 7.3:

1. **Monotonic stepped cost**: ``calc_stepped_cost(t, k)`` is
   non-decreasing in ``k`` for fixed tariff.
2. **Threshold equality**: at ``k == threshold``,
   ``calc_stepped_cost`` equals ``threshold * step1_rate`` exactly.
3. **Step composition**: for ``k > threshold``,
   ``calc_stepped_cost = step1_cost + (k - threshold) * step2_rate``.
4. **Stepped rate dichotomy**: ``get_stepped_import_rate`` returns
   exactly one of ``step1_rate`` or ``step2_rate``.
5. **TOU period closure**: ``get_current_tou_period`` returns a
   period name that's either in the supplied dict OR ``"unknown"``
   (never something else); and the rate matches the period's rate
   when found.
"""

from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import assume, given, settings, strategies as st

from custom_components.pricehawk.tariff_engine import (
    calc_stepped_cost,
    get_current_tou_period,
    get_stepped_import_rate,
)


# Bounded strategies — real-world tariff rates are c/kWh in 0..200 range,
# consumption 0..200 kWh/day (a heavy household), thresholds 0.01..50.
_kwh = st.floats(
    min_value=0.0,
    max_value=200.0,
    allow_nan=False,
    allow_infinity=False,
)
_rate = st.floats(
    min_value=0.0,
    max_value=200.0,
    allow_nan=False,
    allow_infinity=False,
)
# Threshold floor: 0.01 (zero threshold is a degenerate edge case
# covered by an explicit test elsewhere; the production code guards
# against it but Hypothesis doesn't need to re-explore it).
_threshold = st.floats(
    min_value=0.01,
    max_value=50.0,
    allow_nan=False,
    allow_infinity=False,
)


def _tariff(threshold: float, step1_rate: float, step2_rate: float) -> dict:
    return {
        "step1_threshold_kwh": threshold,
        "step1_rate": step1_rate,
        "step2_rate": step2_rate,
    }


# ----------------------------------------------------------------------
# Invariant 1: Monotonic stepped cost
# ----------------------------------------------------------------------


class TestStepCostMonotonic:
    @given(_threshold, _rate, _rate, _kwh, _kwh)
    @settings(max_examples=200, deadline=None)
    def test_cost_monotonic_in_kwh(
        self,
        threshold,
        step1,
        step2,
        k1,
        k2,
    ):
        """Sort k1 <= k2; cost(t, k1) <= cost(t, k2)."""
        lo, hi = (k1, k2) if k1 <= k2 else (k2, k1)
        tariff = _tariff(threshold, step1, step2)
        cost_lo = calc_stepped_cost(tariff, lo)
        cost_hi = calc_stepped_cost(tariff, hi)
        assert cost_lo <= cost_hi + 1e-9, (
            f"Non-monotonic: cost({lo})={cost_lo} > cost({hi})={cost_hi}"
        )


# ----------------------------------------------------------------------
# Invariant 2: Threshold equality
# ----------------------------------------------------------------------


class TestStepCostAtThreshold:
    @given(_threshold, _rate, _rate)
    @settings(max_examples=100, deadline=None)
    def test_cost_at_threshold_uses_only_step1(
        self,
        threshold,
        step1,
        step2,
    ):
        tariff = _tariff(threshold, step1, step2)
        result = calc_stepped_cost(tariff, threshold)
        expected = threshold * step1
        # Allow for float rounding tolerance.
        assert abs(result - expected) < 1e-9, (
            f"At threshold {threshold} with step1={step1}, expected {expected}, got {result}"
        )


# ----------------------------------------------------------------------
# Invariant 3: Step composition above threshold
# ----------------------------------------------------------------------


class TestStepCostAboveThreshold:
    @given(_threshold, _rate, _rate, _kwh)
    @settings(max_examples=200, deadline=None)
    def test_above_threshold_step_composition(
        self,
        threshold,
        step1,
        step2,
        k,
    ):
        # Retro-review of #102 (claude, 2026-05-23): use ``assume()`` instead
        # of bare ``return`` so Hypothesis discards out-of-range examples
        # and generates replacements toward the configured ``max_examples``
        # budget. The previous ``return`` marked invalid draws as PASSED,
        # so with k uniform in [0, 200] and threshold uniform in [0.01, 50]
        # roughly 75% of draws were k ≤ threshold and exited early — the
        # effective constrained-region coverage was ~50 examples, not 200.
        assume(k > threshold)
        tariff = _tariff(threshold, step1, step2)
        result = calc_stepped_cost(tariff, k)
        expected = threshold * step1 + (k - threshold) * step2
        # Tolerance aligned with invariant 2 (1e-9). Single multiplication
        # of bounded floats has IEEE-754 round-trip error ≤ a few ULPs
        # (≤ ~4e-12) so 1e-9 is safely tight at this scale.
        assert abs(result - expected) < 1e-9, (
            f"At kwh={k}, threshold={threshold}, step1={step1}, step2={step2}: "
            f"expected {expected}, got {result}"
        )


# ----------------------------------------------------------------------
# Invariant 4: Stepped rate dichotomy
# ----------------------------------------------------------------------


class TestSteppedRateDichotomy:
    @given(_threshold, _rate, _rate, _kwh)
    @settings(max_examples=200, deadline=None)
    def test_returned_rate_is_one_of_steps(
        self,
        threshold,
        step1,
        step2,
        k,
    ):
        tariff = _tariff(threshold, step1, step2)
        rate = get_stepped_import_rate(tariff, k)
        assert rate in (step1, step2), (
            f"get_stepped_import_rate({k}, threshold={threshold}) returned "
            f"{rate} — must be step1={step1} or step2={step2}"
        )

    @given(_threshold, _rate, _rate, _kwh)
    @settings(max_examples=200, deadline=None)
    def test_below_threshold_returns_step1(
        self,
        threshold,
        step1,
        step2,
        k,
    ):
        # See note on assume() in TestStepCostAboveThreshold.
        assume(k < threshold)
        tariff = _tariff(threshold, step1, step2)
        assert get_stepped_import_rate(tariff, k) == step1

    @given(_threshold, _rate, _rate, _kwh)
    @settings(max_examples=200, deadline=None)
    def test_at_or_above_threshold_returns_step2(
        self,
        threshold,
        step1,
        step2,
        k,
    ):
        # See note on assume() in TestStepCostAboveThreshold. Boundary
        # semantics: get_stepped_import_rate uses ``<`` (so AT threshold
        # → step2_rate) while calc_stepped_cost uses ``<=`` (so AT
        # threshold → step1 cost only). Intentional — the marginal rate
        # sensor reports "you've crossed", while the cost calculation
        # treats the threshold itself as fully in the step1 band.
        # See tariff_engine.py:75 (rate) vs :83 (cost).
        assume(k >= threshold)
        tariff = _tariff(threshold, step1, step2)
        assert get_stepped_import_rate(tariff, k) == step2


# ----------------------------------------------------------------------
# Invariant 5: TOU period closure
# ----------------------------------------------------------------------


def _basic_tou_periods() -> dict:
    """Three-window day covering 24h with no gaps for fuzz tests."""
    return {
        "peak": {
            "rate": 39.6,
            "windows": [["16:00", "21:00"]],
        },
        "shoulder": {
            "rate": 27.5,
            "windows": [["07:00", "16:00"], ["21:00", "23:00"]],
        },
        "offpeak": {
            "rate": 11.0,
            "windows": [["23:00", "07:00"]],  # midnight-crossing
        },
    }


class TestTOUPeriodClosure:
    @given(
        hour=st.integers(min_value=0, max_value=23),
        minute=st.integers(min_value=0, max_value=59),
    )
    @settings(max_examples=200, deadline=None)
    def test_returns_known_period_or_unknown(self, hour, minute):
        periods = _basic_tou_periods()
        now = datetime(2026, 5, 22, hour, minute, tzinfo=timezone.utc)
        name, rate = get_current_tou_period(periods, now)
        assert name in periods or name == "unknown", (
            f"period name {name!r} not in {list(periods)} and not 'unknown'"
        )

    @given(
        hour=st.integers(min_value=0, max_value=23),
        minute=st.integers(min_value=0, max_value=59),
    )
    @settings(max_examples=200, deadline=None)
    def test_returned_rate_matches_period(self, hour, minute):
        periods = _basic_tou_periods()
        now = datetime(2026, 5, 22, hour, minute, tzinfo=timezone.utc)
        name, rate = get_current_tou_period(periods, now)
        if name in periods:
            assert rate == periods[name]["rate"]
        else:
            assert rate == 0.0

    @given(
        hour=st.integers(min_value=0, max_value=23),
        minute=st.integers(min_value=0, max_value=59),
    )
    @settings(max_examples=200, deadline=None)
    def test_full_day_coverage_no_unknown(self, hour, minute):
        """With the basic 24h-covering periods, no minute returns 'unknown'."""
        periods = _basic_tou_periods()
        now = datetime(2026, 5, 22, hour, minute, tzinfo=timezone.utc)
        name, _ = get_current_tou_period(periods, now)
        assert name != "unknown", f"hour={hour:02d}:{minute:02d} fell through period coverage"
