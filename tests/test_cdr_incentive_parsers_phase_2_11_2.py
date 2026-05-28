"""Tests for Phase 2.11.2 — origin.py / alinta.py / energyaustralia.py
wiring of the shared common/tiered_fit helper.

Each test feeds a minimal plan_data dict + slot fixture through the
brand dispatch (apply_retailer_incentives) and verifies the credit
lands on CostBreakdown.incentive_aud_inc_gst with the expected
inc-GST math.

Catalog reference: scripts/CDR_INCENTIVE_CATALOG.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from custom_components.pricehawk.cdr.incentive_parsers import (
    RETAILER_PARSERS,
    apply_retailer_incentives,
)


@dataclass
class _StubBreakdown:
    incentive_aud_inc_gst: Decimal = Decimal("0")
    notes: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)


def _stub_slot_in_window(*_args, **_kwargs):
    """Per-retailer files don't use the window matcher — argument exists
    only to satisfy the dispatch signature."""
    return False


def _slots_30day(daily_export_kwh: float) -> list[dict]:
    """Build 30 days of single-slot exports."""
    return [
        {"ts_local": f"2026-05-{day:02d}T12:00:00", "grid_export_kwh": daily_export_kwh}
        for day in range(1, 31)
    ]


# ---------------------------------------------------------------------------
# Registry — confirms every Phase 2.11.2 retailer is dispatched
# ---------------------------------------------------------------------------


class TestRegistryDispatch:
    def test_origin_registered(self):
        assert "origin" in RETAILER_PARSERS

    def test_alinta_registered(self):
        assert "alinta" in RETAILER_PARSERS

    def test_energyaustralia_registered(self):
        assert "energyaustralia" in RETAILER_PARSERS

    def test_unknown_brand_no_op(self):
        # Plans from retailers not in the registry must not crash dispatch.
        plan = {"brand": "tesla", "electricityContract": {"incentives": []}}
        b = _StubBreakdown()
        apply_retailer_incentives(plan, [], b, slot_in_window=_stub_slot_in_window)
        assert b.incentive_aud_inc_gst == Decimal("0")
        assert b.notes == []


# ---------------------------------------------------------------------------
# Origin — period-averaged tiered FIT
# ---------------------------------------------------------------------------


class TestOriginEndToEnd:
    def _origin_plan(self, base_fit_aud_per_kwh: str = "0.04") -> dict:
        return {
            "brand": "origin",
            "electricityContract": {
                "solarFeedInTariff": [
                    {
                        "tariffUType": "singleTariff",
                        "singleTariff": {"rates": [{"unitPrice": base_fit_aud_per_kwh}]},
                    }
                ],
                "incentives": [
                    {
                        "displayName": "Solar feed-in tariffs",
                        "eligibility": (
                            "Origin offers 12 cents per kWh until "
                            "a daily export limit of 8 kWh is "
                            "reached. The daily export limit is "
                            "averaged across your billing period "
                            "(calculated by multiplying the number "
                            "of days in your billing period by "
                            "your daily export limit of 8)"
                        ),
                    }
                ],
            },
        }

    def test_origin_30day_within_pool(self):
        # 30 days × 5 kWh/day = 150 kWh.
        # Pool = 8 × 30 = 240 kWh; all 150 fits in tier 1.
        # Base FIT inc-GST: 0.04 × 110 = 4.4 c/kWh.
        # Tier 1 inc-GST: 12 c/kWh.
        # Delta credit = (12 - 4.4) / 100 × 150 = 11.40 AUD
        plan = self._origin_plan(base_fit_aud_per_kwh="0.04")
        slots = _slots_30day(5.0)
        b = _StubBreakdown()
        apply_retailer_incentives(plan, slots, b, slot_in_window=_stub_slot_in_window)
        assert b.incentive_aud_inc_gst == Decimal("-11.40")
        assert any("origin parser hits" in n for n in b.notes)

    def test_origin_pool_exhausted(self):
        # 30 days × 10 kWh/day = 300 kWh. Pool 240 kWh.
        # Delta = (12 - 4.4) / 100 × 240 = 18.24 AUD
        plan = self._origin_plan(base_fit_aud_per_kwh="0.04")
        slots = _slots_30day(10.0)
        b = _StubBreakdown()
        apply_retailer_incentives(plan, slots, b, slot_in_window=_stub_slot_in_window)
        assert b.incentive_aud_inc_gst == Decimal("-18.24")

    def test_origin_no_incentive_no_op(self):
        plan = {"brand": "origin", "electricityContract": {"incentives": []}}
        b = _StubBreakdown()
        apply_retailer_incentives(plan, _slots_30day(5.0), b, slot_in_window=_stub_slot_in_window)
        assert b.incentive_aud_inc_gst == Decimal("0")


# ---------------------------------------------------------------------------
# Alinta — daily-cap tiered FIT
# ---------------------------------------------------------------------------


class TestAlintaEndToEnd:
    def _alinta_plan(self, base_fit_aud_per_kwh: str = "0.0004") -> dict:
        return {
            "brand": "alinta",
            "electricityContract": {
                "solarFeedInTariff": [
                    {
                        "tariffUType": "singleTariff",
                        "singleTariff": {"rates": [{"unitPrice": base_fit_aud_per_kwh}]},
                    }
                ],
                "incentives": [
                    {
                        "displayName": "Solar Feed-in Tariff",
                        "eligibility": (
                            "This Energy Plan includes a stepped "
                            "feed-in tariff, where you will receive "
                            "a feed-in of 7c/kWh for the first "
                            "10kW exported. For any export after "
                            "that you will obtain Alinta Energy's "
                            "standard retailer feed-in tariff of "
                            "0.04c/kWh."
                        ),
                    }
                ],
            },
        }

    def test_alinta_single_day_below_cap(self):
        # 5 kWh exported in one day, cap 10 → all tier 1.
        # Base FIT inc-GST: 0.0004 × 110 = 0.044 c/kWh.
        # Tier 1 inc-GST: 7 c/kWh.
        # Delta = (7 - 0.044) / 100 × 5 = 0.3478 AUD
        plan = self._alinta_plan(base_fit_aud_per_kwh="0.0004")
        slots = [{"ts_local": "2026-05-15T12:00:00", "grid_export_kwh": 5.0}]
        b = _StubBreakdown()
        apply_retailer_incentives(plan, slots, b, slot_in_window=_stub_slot_in_window)
        assert b.incentive_aud_inc_gst == Decimal("-0.3478")

    def test_alinta_daily_reset(self):
        # Two days, 8 kWh each. Cap 10/day, both fully credited.
        # Delta = 2 × (7 - 0.044) / 100 × 8 = 1.11296
        plan = self._alinta_plan(base_fit_aud_per_kwh="0.0004")
        slots = [
            {"ts_local": "2026-05-15T12:00:00", "grid_export_kwh": 8.0},
            {"ts_local": "2026-05-16T12:00:00", "grid_export_kwh": 8.0},
        ]
        b = _StubBreakdown()
        apply_retailer_incentives(plan, slots, b, slot_in_window=_stub_slot_in_window)
        assert b.incentive_aud_inc_gst == Decimal("-1.11296")


# ---------------------------------------------------------------------------
# EnergyAustralia — Solar Max no-rate-in-elig falls through silently
# ---------------------------------------------------------------------------


class TestEnergyAustraliaEndToEnd:
    def test_solar_max_no_rate_in_elig_no_op(self):
        # EA Solar Max eligibility describes the averaging window but
        # not the rate. Parser correctly returns no rule → no credit.
        plan = {
            "brand": "energyaustralia",
            "electricityContract": {
                "solarFeedInTariff": [
                    {
                        "tariffUType": "singleTariff",
                        "singleTariff": {"rates": [{"unitPrice": "0.05"}]},
                    }
                ],
                "incentives": [
                    {
                        "displayName": "Solar Max",
                        "eligibility": (
                            "Solar Max is for electricity only and "
                            "is available to eligible residential "
                            "solar customers not receiving any "
                            "Government feed-in-tariff. The daily "
                            "export is averaged by dividing the "
                            "total solar export by the number of "
                            "days in each billing period"
                        ),
                    }
                ],
            },
        }
        slots = [{"ts_local": "2026-05-15T12:00:00", "grid_export_kwh": 5.0}]
        b = _StubBreakdown()
        apply_retailer_incentives(plan, slots, b, slot_in_window=_stub_slot_in_window)
        assert b.incentive_aud_inc_gst == Decimal("0")
        assert b.notes == []  # parser exits before logging when no rule found

    def test_ea_with_explicit_rate_in_elig(self):
        # If a different EA plan ships the rate-and-cap text directly,
        # parser handles it. Pin behaviour for future-proofing.
        plan = {
            "brand": "energyaustralia",
            "electricityContract": {
                "solarFeedInTariff": [
                    {
                        "tariffUType": "singleTariff",
                        "singleTariff": {"rates": [{"unitPrice": "0.04"}]},
                    }
                ],
                "incentives": [
                    {
                        "displayName": "Solar Max",
                        "eligibility": (
                            "EA pays 10 cents per kWh until a "
                            "daily export limit of 6 kWh is "
                            "reached. The daily export limit is "
                            "averaged across your billing period."
                        ),
                    }
                ],
            },
        }
        slots = _slots_30day(4.0)
        # Pool = 6 × 30 = 180 kWh. Total export = 30 × 4 = 120, all in tier 1.
        # Base inc-GST: 0.04 × 110 = 4.4. Tier 1 inc-GST: 10.
        # Delta = (10 - 4.4) / 100 × 120 = 6.72 AUD
        b = _StubBreakdown()
        apply_retailer_incentives(plan, slots, b, slot_in_window=_stub_slot_in_window)
        assert b.incentive_aud_inc_gst == Decimal("-6.72")
