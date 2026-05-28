"""Snapshot tests for the Amber-vs-GloBird comparison logic.

These lock the *entire* comparison-output dicts — every dollar figure and
every daily-breakdown row — in a single assertion each. They complement the
per-field property tests in ``test_csv_analyzer.py``: those assert invariants
(rounding, non-negativity, CSV-cost agreement), while these catch any
unintended drift in the actual comparison numbers, which range-based
assertions miss.

Inputs are deterministic (the committed ``amber_sample.csv`` fixture plus a
fixed configured-rate scenario), so the snapshots are stable. Regenerate
intentionally with ``pytest --snapshot-update`` and review the ``.ambr`` diff
before committing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from syrupy.assertion import SnapshotAssertion

from custom_components.pricehawk.const import (
    GLOBIRD_PLAN_DEFAULTS,
    PLAN_BOOST,
    PLAN_FOUR4FREE,
    PLAN_GLOSAVE,
    PLAN_ZEROHERO,
)
from custom_components.pricehawk.csv_analyzer import (
    analyze_amber_costs,
    analyze_csv_data,
    compare_all_plans,
    parse_amber_csv,
    simulate_globird_plan,
)

FIXTURE_PATH = str(Path(__file__).parent / "fixtures" / "amber_sample.csv")

# Amber daily charges (cents) — same scenario the property tests use.
AMBER_NETWORK_DAILY_C = 45.0
AMBER_SUBSCRIPTION_DAILY_C = 33.0

# Configured GloBird rates that deliberately match no plan default, exercising
# the dashboard path where the user's own rates drive the comparison.
CUSTOM_GLOBIRD_OPTIONS: dict = {
    "daily_supply_charge": 99.99,  # intentionally unusual (c/day)
    "demand_charge": 0.0,
    "import_tariff": {
        "type": "tou",
        "periods": {
            "peak": {"rate": 50.00, "windows": [["16:00", "23:00"]]},
            "shoulder": {
                "rate": 30.00,
                "windows": [["23:00", "00:00"], ["00:00", "11:00"], ["14:00", "16:00"]],
            },
            "offpeak": {"rate": 10.00, "windows": [["11:00", "14:00"]]},
        },
    },
    "export_tariff": {
        "type": "tou",
        "periods": {
            "peak": {"rate": 8.00, "windows": [["16:00", "21:00"]]},
            "shoulder": {
                "rate": 3.00,
                "windows": [["21:00", "00:00"], ["00:00", "10:00"], ["14:00", "16:00"]],
            },
            "offpeak": {"rate": 1.00, "windows": [["10:00", "14:00"]]},
        },
    },
    "incentives": {},
}


def _fixture_rows_as_dicts() -> list[dict]:
    """Load the fixture CSV in the dashboard's row format (channel_type key)."""
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


def test_analyze_csv_data_matches_snapshot(snapshot: SnapshotAssertion) -> None:
    """Full dashboard comparison output (user's configured rates) is locked."""
    result = analyze_csv_data(
        _fixture_rows_as_dicts(),
        CUSTOM_GLOBIRD_OPTIONS,
        AMBER_NETWORK_DAILY_C,
        AMBER_SUBSCRIPTION_DAILY_C,
    )
    assert result == snapshot


def test_analyze_csv_data_empty_matches_snapshot(snapshot: SnapshotAssertion) -> None:
    """The zeroed empty-input result shape is locked."""
    assert analyze_csv_data([], CUSTOM_GLOBIRD_OPTIONS, 0.0, 0.0) == snapshot


def test_compare_all_plans_matches_snapshot(snapshot: SnapshotAssertion) -> None:
    """Full all-plans comparison (plan defaults) is locked end to end."""
    result = compare_all_plans(
        FIXTURE_PATH, AMBER_NETWORK_DAILY_C, AMBER_SUBSCRIPTION_DAILY_C
    )
    assert result == snapshot


def test_analyze_amber_costs_matches_snapshot(snapshot: SnapshotAssertion) -> None:
    """Per-day Amber import/export breakdown derived from the CSV is locked."""
    assert analyze_amber_costs(parse_amber_csv(FIXTURE_PATH)) == snapshot


@pytest.mark.parametrize(
    "plan",
    [PLAN_ZEROHERO, PLAN_FOUR4FREE, PLAN_BOOST, PLAN_GLOSAVE],
)
def test_simulate_globird_plan_matches_snapshot(
    plan: str, snapshot: SnapshotAssertion
) -> None:
    """Each GloBird plan's per-day simulation (defaults) is locked."""
    rows = parse_amber_csv(FIXTURE_PATH)
    assert simulate_globird_plan(rows, GLOBIRD_PLAN_DEFAULTS[plan]) == snapshot
