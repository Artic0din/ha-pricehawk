"""Coverage gap tests for FlowPowerProvider — 2 uncovered lines.

Line 120: set_wholesale_rate stores twap_c_kwh when provided.
Line 165: PEA override path in current_import_rate_c_kwh.

The existing test_flow_power_provider.py already reaches 98%; these
tests close the remaining 2 lines.
"""

from __future__ import annotations

import pytest

from custom_components.pricehawk.providers.flow_power import (
    FLOW_POWER_DEFAULT_BASE_RATE_C,
    FlowPowerProvider,
)


class TestSetWholesaleRateStoresTwap:
    """set_wholesale_rate stores twap_c_kwh only when not None (line 120)."""

    def test_twap_stored_when_provided(self):
        """set_wholesale_rate with a twap value persists that twap for PEA."""
        # ARRANGE
        provider = FlowPowerProvider({"flow_power_region": "NSW1"})
        spot = 20.0
        twap = 12.5

        # ACT
        provider.set_wholesale_rate(spot, twap_c_kwh=twap)

        # ASSERT: twap is used in PEA, not the default fallback
        # PEA = wholesale - twap - benchmark = 20 - 12.5 - 1.7 = 5.8
        # import_rate = base + pea = 34 + 5.8 = 39.8
        assert provider.current_import_rate_c_kwh == pytest.approx(39.8, abs=0.01)
        # Verify: without this twap the default (8.0) would give 34 + (20-8-1.7) = 44.3
        assert provider.current_import_rate_c_kwh != pytest.approx(44.3, abs=0.1)

    def test_twap_not_overwritten_when_none_passed(self):
        """set_wholesale_rate with twap_c_kwh=None preserves any previously set twap."""
        # ARRANGE
        provider = FlowPowerProvider({"flow_power_region": "NSW1"})
        provider.set_wholesale_rate(20.0, twap_c_kwh=12.5)

        # ACT — push new spot without touching twap
        provider.set_wholesale_rate(25.0, twap_c_kwh=None)

        # ASSERT: twap=12.5 still in effect, not reset to None/default
        # PEA = 25 - 12.5 - 1.7 = 10.8; rate = 34 + 10.8 = 44.8
        assert provider.current_import_rate_c_kwh == pytest.approx(44.8, abs=0.01)


class TestPeaOverridePath:
    """current_import_rate_c_kwh uses pea_override_c when supplied (line 165)."""

    def test_pea_override_replaces_calculated_pea(self):
        """Manual pea_override_c is applied instead of calculated PEA."""
        # ARRANGE — explicit override of -3.0 c/kWh
        provider = FlowPowerProvider(
            {
                "flow_power_region": "NSW1",
                "flow_power_pea_override": -3.0,
                "flow_power_pea_enabled": True,
            }
        )
        provider.set_wholesale_rate(50.0)  # high spot — calculated PEA would be large

        # ACT
        rate = provider.current_import_rate_c_kwh

        # ASSERT: base + override = 34.0 + (-3.0) = 31.0; NOT 34 + (50-8-1.7)=74.3
        assert rate == pytest.approx(FLOW_POWER_DEFAULT_BASE_RATE_C + (-3.0), abs=0.01)

    def test_pea_override_zero_is_accepted(self):
        """A pea_override_c of 0.0 sets PEA to zero, not falling back to calculated."""
        # ARRANGE
        provider = FlowPowerProvider(
            {
                "flow_power_region": "NSW1",
                "flow_power_pea_override": 0.0,
                "flow_power_pea_enabled": True,
            }
        )
        provider.set_wholesale_rate(30.0)

        # ACT
        rate = provider.current_import_rate_c_kwh

        # ASSERT: base + 0 = 34.0 exactly; NOT 34 + (30-8-1.7)=54.3
        assert rate == pytest.approx(FLOW_POWER_DEFAULT_BASE_RATE_C, abs=0.01)

    def test_pea_override_does_not_apply_when_pea_disabled(self):
        """pea_enabled=False takes priority over the override — pea_c stays 0."""
        # ARRANGE
        provider = FlowPowerProvider(
            {
                "flow_power_region": "NSW1",
                "flow_power_pea_override": 99.0,  # would add 99c if reached
                "flow_power_pea_enabled": False,
            }
        )
        provider.set_wholesale_rate(20.0)

        # ACT
        rate = provider.current_import_rate_c_kwh

        # ASSERT: PEA disabled → base rate only
        assert rate == pytest.approx(FLOW_POWER_DEFAULT_BASE_RATE_C, abs=0.01)
