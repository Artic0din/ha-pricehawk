"""Explicit unit-conversion tests for the Amber Electric pricing chain.

Amber API conventions (binding contract):
  - ``perKwh`` in API price intervals: c/kWh
  - ``cost`` field in Amber CSV rows: CENTS (÷100 = AUD)
  - HA sensor state should be in $/kWh (so perKwh ÷ 100)
  - AmberCalculator accumulates costs in CENTS; net_daily_cost_aud ÷100

These tests verify each ÷100 conversion in the AmberCalculator and ensure
the output properties are in the correct units. A missing or extra ÷100
would fail at least one assertion.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.pricehawk.amber_calculator import AmberCalculator


# ---------------------------------------------------------------------------
# AmberCalculator: rates in c/kWh; accumulators in cents; AUD property ÷100
# ---------------------------------------------------------------------------


class TestAmberCalculatorUnitConventions:
    """The calculator receives rates in c/kWh and accumulates costs in CENTS.

    All ``_today_c`` properties are in cents. net_daily_cost_aud converts
    cents → AUD via ÷100 internally.
    """

    def test_import_cost_accumulated_in_cents_not_dollars(self):
        """1 kW * 0.1h at 30 c/kWh → import_cost_today_c == 3.0c, NOT $0.03."""
        # ARRANGE
        calc = AmberCalculator()
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        t1 = t0 + timedelta(hours=1)  # gap clamped to 0.1h

        # ACT — 1 kW * 0.1h = 0.1 kWh * 30 c/kWh = 3.0c
        calc.update(1_000.0, 30.0, 8.0, t0)
        calc.update(1_000.0, 30.0, 8.0, t1)

        # ASSERT: value is 3.0 (cents), not 0.03 (which would indicate ÷100 already applied)
        assert calc.import_cost_today_c == pytest.approx(3.0, abs=0.01)
        # AUD is import_cost_today_c ÷ 100
        assert calc.net_daily_cost_aud == pytest.approx(0.03, abs=0.001)

    def test_export_earnings_accumulated_in_cents_not_dollars(self):
        """-3 kW * 0.1h at 10 c/kWh → export_earnings_today_c == 3.0c."""
        # ARRANGE
        calc = AmberCalculator()
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        t1 = t0 + timedelta(hours=1)  # clamped to 0.1h

        # ACT — 3 kW * 0.1h = 0.3 kWh exported * 10 c/kWh = 3.0c earnings
        calc.update(0.0, 20.0, 10.0, t0)
        calc.update(-3_000.0, 20.0, 10.0, t1)

        # ASSERT: value is cents
        assert calc.export_earnings_today_c == pytest.approx(3.0, abs=0.01)

    def test_daily_fixed_charges_aud_divides_by_100(self):
        """network_daily_c=200 + subscription_daily_c=50 → AUD = $2.50."""
        # ARRANGE + ACT
        calc = AmberCalculator(
            amber_network_daily_c=200.0,
            amber_subscription_daily_c=50.0,
        )

        # ASSERT: 250c ÷ 100 = $2.50
        assert calc.daily_fixed_charges_aud == pytest.approx(2.50)

    def test_net_daily_cost_aud_computation_is_cents_divided_by_100(self):
        """Net = (import_c - export_c + fixed_c) / 100.

        Example: import=100c, export=40c, network=40c, sub=20c
        → (100 - 40 + 40 + 20) / 100 = $1.20
        """
        # ARRANGE
        calc = AmberCalculator(
            amber_network_daily_c=40.0,
            amber_subscription_daily_c=20.0,
        )
        calc._import_cost_today_c = 100.0
        calc._export_earnings_today_c = 40.0

        # ACT + ASSERT: (100 - 40 + 60) / 100 = 1.20
        assert calc.net_daily_cost_aud == pytest.approx(1.20)

    def test_earning_day_net_cost_is_negative_aud(self):
        """Export credits > import + fixed → net_daily_cost_aud is negative."""
        # ARRANGE
        calc = AmberCalculator(
            amber_network_daily_c=50.0,
            amber_subscription_daily_c=10.0,
        )
        calc._import_cost_today_c = 30.0  # 30c
        calc._export_earnings_today_c = 200.0  # 200c credit

        # ACT: (30 - 200 + 60) / 100 = -110c / 100 = -$1.10
        assert calc.net_daily_cost_aud == pytest.approx(-1.10)

    def test_rate_properties_are_in_cents_per_kwh(self):
        """current_import_rate_c_kwh and current_export_rate_c_kwh are c/kWh, not $/kWh."""
        # ARRANGE + ACT
        calc = AmberCalculator()
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        calc.update(0.0, 35.5, 9.2, t0)

        # ASSERT: rates stored verbatim, not scaled
        assert calc.current_import_rate_c_kwh == pytest.approx(35.5)
        assert calc.current_export_rate_c_kwh == pytest.approx(9.2)
        # Sanity: must be in tens-of-cents range (c/kWh), not sub-one ($/kWh)
        assert calc.current_import_rate_c_kwh > 1.0

    def test_net_cost_value_sanity_is_in_dollar_range(self):
        """A typical day's net_daily_cost_aud should be in dollar range, not cents range.

        This catches an extra ÷100 that would make dollars look like fractional cents.
        """
        # ARRANGE — 5 kWh import at 30 c/kWh + 100c fixed = 250c = $2.50
        calc = AmberCalculator(
            amber_network_daily_c=100.0,
            amber_subscription_daily_c=0.0,
        )
        calc._import_cost_today_c = 150.0  # 5 kWh * 30 c/kWh

        # ASSERT: result should be close to $2.50, NOT 0.025 (extra ÷100) or 250 (missing ÷100)
        net = calc.net_daily_cost_aud
        assert 1.0 < net < 10.0, (
            f"net_daily_cost_aud={net} is outside expected dollar range "
            "(if ~0.025 a double-÷100 occurred; if ~250 ÷100 was omitted)"
        )
        assert net == pytest.approx(2.50, abs=0.01)

    def test_per_kwh_rate_used_directly_in_cost_formula(self):
        """perKwh values are used in c/kWh directly, not treated as $/kWh.

        If 30 c/kWh were treated as $30/kWh, costs would be 100× too large.
        """
        # ARRANGE — 1 kW * 0.1h = 0.1 kWh at 30 c/kWh → 3.0c cost
        calc = AmberCalculator()
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        t1 = t0 + timedelta(hours=1)  # clamped to 0.1h

        # ACT
        calc.update(1_000.0, 30.0, 0.0, t0)
        calc.update(1_000.0, 30.0, 0.0, t1)

        # ASSERT: 3.0c, NOT 3000c (which would indicate $/kWh * 1000 scaling)
        assert calc.import_cost_today_c == pytest.approx(3.0, abs=0.01)
        assert calc.import_cost_today_c < 100.0, (
            f"Cost {calc.import_cost_today_c}c looks like $/kWh was misinterpreted as c/kWh"
        )
