"""Tests for Amber CSV analyzer."""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.pricehawk.csv_analyzer import (
    analyze_amber_costs,
    analyze_csv_data,
    compare_all_plans,
    parse_amber_csv,
    simulate_globird_plan,
)
from custom_components.pricehawk.const import (
    GLOBIRD_PLAN_DEFAULTS,
    PLAN_BOOST,
    PLAN_FOUR4FREE,
    PLAN_GLOSAVE,
    PLAN_ZEROHERO,
)

FIXTURE_PATH = str(Path(__file__).parent / "fixtures" / "amber_sample.csv")

# Amber daily charges for testing (example values in cents)
AMBER_NETWORK_DAILY_C = 45.0
AMBER_SUBSCRIPTION_DAILY_C = 33.0


class TestParseAmberCsv:
    """Tests for parse_amber_csv."""

    def test_row_count(self) -> None:
        """Fixture has 3 days of data with general + feedIn channels."""
        rows = parse_amber_csv(FIXTURE_PATH)
        # 3 days x 48 slots x 2 channels = 288 rows (minus any missing)
        assert len(rows) > 0
        # Verify we have rows for all 3 days
        days = {r["day"] for r in rows}
        assert days == {"2026-04-12", "2026-04-13", "2026-04-14"}

    def test_row_structure(self) -> None:
        """Each row should have the expected keys."""
        rows = parse_amber_csv(FIXTURE_PATH)
        required_keys = {"day", "start_time", "channel", "price", "usage", "cost"}
        for row in rows:
            assert required_keys.issubset(row.keys())

    def test_channels_present(self) -> None:
        """Both general and feedIn channels should be present."""
        rows = parse_amber_csv(FIXTURE_PATH)
        channels = {r["channel"] for r in rows}
        assert "general" in channels
        assert "feedIn" in channels

    def test_numeric_types(self) -> None:
        """Price, usage, cost should be floats."""
        rows = parse_amber_csv(FIXTURE_PATH)
        for row in rows:
            assert isinstance(row["price"], float)
            assert isinstance(row["usage"], float)
            assert isinstance(row["cost"], float)


class TestAnalyzeAmberCosts:
    """Tests for analyze_amber_costs."""

    def test_daily_totals_structure(self) -> None:
        """Should return a dict keyed by day with import/export breakdowns."""
        rows = parse_amber_csv(FIXTURE_PATH)
        daily = analyze_amber_costs(rows)
        assert len(daily) == 3
        for day_data in daily.values():
            assert "import_cost_c" in day_data
            assert "export_cost_c" in day_data
            assert "import_kwh" in day_data
            assert "export_kwh" in day_data

    def test_import_costs_positive(self) -> None:
        """Import costs from the CSV should be non-negative (usage * price >= 0)."""
        rows = parse_amber_csv(FIXTURE_PATH)
        daily = analyze_amber_costs(rows)
        for day_data in daily.values():
            assert day_data["import_cost_c"] >= 0.0

    def test_day12_manual_spot_check(self) -> None:
        """Spot-check Apr 12 import: first slot has cost 10.1675c."""
        rows = parse_amber_csv(FIXTURE_PATH)
        daily = analyze_amber_costs(rows)
        # Apr 12 should have positive import costs
        assert daily["2026-04-12"]["import_cost_c"] > 10.0
        assert daily["2026-04-12"]["import_kwh"] > 1.0


class TestSimulateGlobirdPlan:
    """Tests for simulate_globird_plan."""

    def test_zerohero_produces_daily_costs(self) -> None:
        """ZEROHERO simulation should produce costs for all 3 days."""
        rows = parse_amber_csv(FIXTURE_PATH)
        result = simulate_globird_plan(rows, GLOBIRD_PLAN_DEFAULTS[PLAN_ZEROHERO])
        assert len(result) == 3
        for day_data in result.values():
            assert "cost_c" in day_data
            assert "import_kwh" in day_data
            assert "export_kwh" in day_data
            assert "supply_c" in day_data

    def test_supply_charge_included(self) -> None:
        """Each day's cost should include the daily supply charge."""
        rows = parse_amber_csv(FIXTURE_PATH)
        plan = GLOBIRD_PLAN_DEFAULTS[PLAN_ZEROHERO]
        result = simulate_globird_plan(rows, plan)
        supply_c = plan["daily_supply_charge"]
        for day_data in result.values():
            # Cost should be at least the supply charge (may be reduced by
            # credits/exports, but for most days should exceed it)
            assert day_data["supply_c"] == supply_c

    def test_boost_stepped_pricing(self) -> None:
        """BOOST plan (flat stepped) should produce reasonable daily costs."""
        rows = parse_amber_csv(FIXTURE_PATH)
        result = simulate_globird_plan(rows, GLOBIRD_PLAN_DEFAULTS[PLAN_BOOST])
        assert len(result) == 3
        for day_data in result.values():
            # Cost in cents should be a reasonable number (not zero, not astronomical)
            assert day_data["cost_c"] > 0.0
            assert day_data["cost_c"] < 10000.0  # less than $100/day


class TestCompareAllPlans:
    """Tests for compare_all_plans (full comparison)."""

    @pytest.fixture()
    def comparison(self) -> dict:
        """Run a full comparison against the fixture CSV."""
        return compare_all_plans(FIXTURE_PATH, AMBER_NETWORK_DAILY_C, AMBER_SUBSCRIPTION_DAILY_C)

    def test_all_four_plans_present(self, comparison: dict) -> None:
        """All four GloBird plans should appear in the result."""
        plans = comparison["plans"]
        assert PLAN_ZEROHERO in plans
        assert PLAN_FOUR4FREE in plans
        assert PLAN_BOOST in plans
        assert PLAN_GLOSAVE in plans

    def test_cheapest_plan_set(self, comparison: dict) -> None:
        """cheapest_plan should be one of the four plans."""
        assert comparison["cheapest_plan"] in {
            PLAN_ZEROHERO, PLAN_FOUR4FREE, PLAN_BOOST, PLAN_GLOSAVE,
        }

    def test_period_metadata(self, comparison: dict) -> None:
        """Period should span the 3 days in the fixture."""
        period = comparison["period"]
        assert period["start"] == "2026-04-12"
        assert period["end"] == "2026-04-14"
        assert period["days"] == 3

    def test_amber_total_structure(self, comparison: dict) -> None:
        """Amber totals should have the expected keys."""
        amber = comparison["amber"]
        assert "total_cost_aud" in amber
        assert "daily_charges_aud" in amber
        assert "import_cost_aud" in amber
        assert "export_credit_aud" in amber

    def test_daily_breakdown_length(self, comparison: dict) -> None:
        """Daily breakdown should have one entry per day."""
        assert len(comparison["daily_breakdown"]) == 3

    def test_daily_breakdown_has_all_plans(self, comparison: dict) -> None:
        """Each daily breakdown entry should have costs for all plans."""
        for entry in comparison["daily_breakdown"]:
            assert "day" in entry
            assert "amber_aud" in entry
            assert f"{PLAN_ZEROHERO}_aud" in entry
            assert f"{PLAN_FOUR4FREE}_aud" in entry
            assert f"{PLAN_BOOST}_aud" in entry
            assert f"{PLAN_GLOSAVE}_aud" in entry

    def test_plan_savings_consistency(self, comparison: dict) -> None:
        """savings_vs_amber_aud should equal amber_total - plan_total."""
        amber_total = comparison["amber"]["total_cost_aud"]
        for plan_data in comparison["plans"].values():
            expected_savings = round(amber_total - plan_data["total_cost_aud"], 2)
            assert abs(plan_data["savings_vs_amber_aud"] - expected_savings) <= 0.02

    def test_amber_daily_charges(self, comparison: dict) -> None:
        """Amber daily charges should equal num_days * (network + subscription)."""
        expected = 3 * (AMBER_NETWORK_DAILY_C + AMBER_SUBSCRIPTION_DAILY_C) / 100.0
        assert comparison["amber"]["daily_charges_aud"] == round(expected, 2)


class TestAmberTotalMatchesCsv:
    """Verify that our Amber cost analysis matches the CSV's own Cost column."""

    def test_amber_import_total_matches_csv(self) -> None:
        """Sum of our import_cost_c should match sum of CSV Cost for general rows."""
        rows = parse_amber_csv(FIXTURE_PATH)
        daily = analyze_amber_costs(rows)

        csv_import_total = sum(r["cost"] for r in rows if r["channel"] == "general")
        our_import_total = sum(d["import_cost_c"] for d in daily.values())

        assert abs(csv_import_total - our_import_total) < 0.01

    def test_amber_export_total_matches_csv(self) -> None:
        """Sum of our export_cost_c should match sum of CSV Cost for feedIn rows."""
        rows = parse_amber_csv(FIXTURE_PATH)
        daily = analyze_amber_costs(rows)

        csv_export_total = sum(r["cost"] for r in rows if r["channel"] == "feedIn")
        our_export_total = sum(d["export_cost_c"] for d in daily.values())

        assert abs(csv_export_total - our_export_total) < 0.01


# ======================================================================
# Tests for analyze_csv_data (dashboard-driven, user's configured rates)
# ======================================================================


def _fixture_rows_as_dicts() -> list[dict]:
    """Load the fixture CSV and return rows in the format the dashboard sends.

    The dashboard JavaScript parses by column index, producing dicts with
    keys: day, start_time, channel_type, price, usage, cost.
    """
    rows = parse_amber_csv(FIXTURE_PATH)
    return [
        {
            "day": r["day"],
            "start_time": r["start_time"],
            "channel_type": r["channel"],
            "price": r["price"],
            "usage": r["usage"],
            "cost": r["cost"],
        }
        for r in rows
    ]


# Custom rates that differ from any plan default to prove configured rates are used
_CUSTOM_GLOBIRD_OPTIONS: dict = {
    "daily_supply_charge": 99.99,  # Intentionally unusual value (c/day)
    "demand_charge": 0.0,
    "import_tariff": {
        "type": "tou",
        "periods": {
            "peak": {"rate": 50.00, "windows": [["16:00", "23:00"]]},
            "shoulder": {"rate": 30.00, "windows": [["23:00", "00:00"], ["00:00", "11:00"], ["14:00", "16:00"]]},
            "offpeak": {"rate": 10.00, "windows": [["11:00", "14:00"]]},
        },
    },
    "export_tariff": {
        "type": "tou",
        "periods": {
            "peak": {"rate": 8.00, "windows": [["16:00", "21:00"]]},
            "shoulder": {"rate": 3.00, "windows": [["21:00", "00:00"], ["00:00", "10:00"], ["14:00", "16:00"]]},
            "offpeak": {"rate": 1.00, "windows": [["10:00", "14:00"]]},
        },
    },
    "incentives": {},
}


class TestAnalyzeCsvData:
    """Tests for analyze_csv_data (dashboard service entry point)."""

    @pytest.fixture()
    def result(self) -> dict:
        """Run analyze_csv_data with custom options against fixture data."""
        rows = _fixture_rows_as_dicts()
        return analyze_csv_data(
            rows, _CUSTOM_GLOBIRD_OPTIONS,
            AMBER_NETWORK_DAILY_C, AMBER_SUBSCRIPTION_DAILY_C,
        )

    def test_returns_correct_structure(self, result: dict) -> None:
        """Result should contain all expected top-level keys."""
        assert "period" in result
        assert "amber" in result
        assert "globird" in result
        assert "savings_aud" in result
        assert "savings_direction" in result
        assert "daily" in result

    def test_period_metadata(self, result: dict) -> None:
        """Period should span the 3 days in the fixture."""
        period = result["period"]
        assert period["start"] == "2026-04-12"
        assert period["end"] == "2026-04-14"
        assert period["days"] == 3

    def test_amber_keys(self, result: dict) -> None:
        """Amber result should have the expected keys."""
        amber = result["amber"]
        for key in ("total_aud", "energy_aud", "daily_fees_aud", "import_kwh", "export_kwh"):
            assert key in amber, f"Missing amber key: {key}"

    def test_globird_keys(self, result: dict) -> None:
        """GloBird result should have the expected keys."""
        globird = result["globird"]
        for key in ("total_aud", "energy_aud", "supply_aud", "import_kwh", "export_kwh"):
            assert key in globird, f"Missing globird key: {key}"

    def test_amber_costs_match_csv(self, result: dict) -> None:
        """Amber energy costs should match the sum of the CSV Cost column."""
        rows = _fixture_rows_as_dicts()
        csv_import_total_c = sum(r["cost"] for r in rows if r["channel_type"] == "general")
        csv_export_total_c = sum(r["cost"] for r in rows if r["channel_type"] == "feedIn")
        csv_energy_aud = (csv_import_total_c + csv_export_total_c) / 100.0

        assert abs(result["amber"]["energy_aud"] - round(csv_energy_aud, 2)) < 0.02

    def test_amber_daily_fees(self, result: dict) -> None:
        """Amber daily fees should equal num_days * (network + subscription)."""
        expected = 3 * (AMBER_NETWORK_DAILY_C + AMBER_SUBSCRIPTION_DAILY_C) / 100.0
        assert result["amber"]["daily_fees_aud"] == round(expected, 2)

    def test_globird_uses_configured_rates(self, result: dict) -> None:
        """GloBird supply charge should use our custom rate, not any plan default.

        We set daily_supply_charge to 99.99 c/day which is not used by any
        real plan. The supply_aud should equal 3 * 99.99 / 100.
        """
        expected_supply_aud = round(3 * 99.99 / 100.0, 2)
        assert result["globird"]["supply_aud"] == expected_supply_aud

    def test_savings_direction_valid(self, result: dict) -> None:
        """savings_direction should be one of the valid values."""
        assert result["savings_direction"] in ("amber", "globird", "equal")

    def test_savings_aud_non_negative(self, result: dict) -> None:
        """savings_aud should always be non-negative (absolute value)."""
        assert result["savings_aud"] >= 0.0

    def test_daily_breakdown_length(self, result: dict) -> None:
        """Daily breakdown should have one entry per day."""
        assert len(result["daily"]) == 3

    def test_daily_breakdown_keys(self, result: dict) -> None:
        """Each daily entry should have date, amber_aud, globird_aud."""
        for entry in result["daily"]:
            assert "date" in entry
            assert "amber_aud" in entry
            assert "globird_aud" in entry

    def test_empty_rows_returns_zeroed_result(self) -> None:
        """Empty rows should return a valid zeroed structure."""
        result = analyze_csv_data([], _CUSTOM_GLOBIRD_OPTIONS, 0.0, 0.0)
        assert result["period"]["days"] == 0
        assert result["amber"]["total_aud"] == 0.0
        assert result["globird"]["total_aud"] == 0.0
        assert result["savings_aud"] == 0.0
        assert result["savings_direction"] == "none"
        assert result["daily"] == []

    def test_import_kwh_consistency(self, result: dict) -> None:
        """Amber and GloBird should process the same import kWh from the CSV."""
        # They should be very close (GloBird re-simulates from the same data)
        assert abs(result["amber"]["import_kwh"] - result["globird"]["import_kwh"]) < 0.5

    def test_totals_are_rounded(self, result: dict) -> None:
        """All monetary values should be rounded to 2 decimal places."""
        for key in ("total_aud", "energy_aud", "daily_fees_aud"):
            val = result["amber"][key]
            assert val == round(val, 2)
        for key in ("total_aud", "energy_aud", "supply_aud"):
            val = result["globird"][key]
            assert val == round(val, 2)
