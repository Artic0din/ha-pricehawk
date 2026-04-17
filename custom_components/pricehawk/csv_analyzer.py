"""Amber CSV analyzer -- runs historical usage through tariff engines for comparison.

Pure Python -- no Home Assistant imports. Reads an Amber Electric usage CSV
and simulates each GloBird plan's tariff engine against the same consumption
data to produce a cost comparison.

Two entry points:
  - compare_all_plans(): file-based, compares all four default plans (legacy)
  - analyze_csv_data(): row-based, uses user's CONFIGURED rates from dashboard
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from .const import (
    GLOBIRD_PLAN_DEFAULTS,
    PLAN_BOOST,
    PLAN_FOUR4FREE,
    PLAN_GLOSAVE,
    PLAN_ZEROHERO,
)
from .tariff_engine import TariffEngine

# All four GloBird plans to compare against
_ALL_PLANS = [PLAN_ZEROHERO, PLAN_FOUR4FREE, PLAN_BOOST, PLAN_GLOSAVE]

# Number of synthetic readings per 30-minute CSV slot
_READINGS_PER_SLOT = 60  # one reading every 30 seconds


def parse_amber_csv(file_path: str) -> list[dict[str, Any]]:
    """Read an Amber usage CSV and return a list of row dicts.

    Each returned dict contains:
        day (str): Date string "YYYY-MM-DD"
        start_time (str): Slot start "YYYY-MM-DD HH:MM:SS"
        channel (str): "general" (import) or "feedIn" (export)
        price (float): Amber price in c/kWh
        usage (float): Energy in kWh
        cost (float): Pre-computed cost in cents
    """
    rows: list[dict[str, Any]] = []
    with open(file_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append({
                "day": row["Day"].strip(),
                "start_time": row["Start Time"].strip(),
                "channel": row["Channel Type"].strip(),
                "price": float(row["Price"]),
                "usage": float(row["Usage"]),
                "cost": float(row["Cost"]),
            })
    return rows


def analyze_amber_costs(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Group rows by day and sum import/export costs and energy.

    Returns:
        {day: {import_cost_c, export_cost_c, import_kwh, export_kwh}}
    """
    daily: dict[str, dict[str, float]] = defaultdict(
        lambda: {"import_cost_c": 0.0, "export_cost_c": 0.0, "import_kwh": 0.0, "export_kwh": 0.0}
    )
    for row in rows:
        day = row["day"]
        if row["channel"] == "general":
            daily[day]["import_cost_c"] += row["cost"]
            daily[day]["import_kwh"] += row["usage"]
        elif row["channel"] == "feedIn":
            daily[day]["export_cost_c"] += row["cost"]
            daily[day]["export_kwh"] += row["usage"]
    return dict(daily)


def simulate_globird_plan(
    rows: list[dict[str, Any]], plan_options: dict[str, Any]
) -> dict[str, dict[str, float]]:
    """Simulate a GloBird plan against Amber CSV data.

    For each day:
    1. Merge import+export rows per 30-min slot
    2. Compute net_power_w = (import_kwh - export_kwh) / 0.5 * 1000
    3. Feed 60 x 30-second readings per slot through TariffEngine
    4. Capture net_daily_cost_aud at end of day

    Returns:
        {day: {cost_c, import_kwh, export_kwh, supply_c}}
    """
    # Group rows by (day, start_time)
    slots_by_day: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: {"import_kwh": 0.0, "export_kwh": 0.0})
    )
    for row in rows:
        day = row["day"]
        st = row["start_time"]
        if row["channel"] == "general":
            slots_by_day[day][st]["import_kwh"] += row["usage"]
        elif row["channel"] == "feedIn":
            slots_by_day[day][st]["export_kwh"] += row["usage"]

    results: dict[str, dict[str, float]] = {}

    for day in sorted(slots_by_day.keys()):
        engine = TariffEngine(plan_options)
        day_slots = slots_by_day[day]

        for start_time_str in sorted(day_slots.keys()):
            slot = day_slots[start_time_str]
            import_kwh = slot["import_kwh"]
            export_kwh = slot["export_kwh"]

            # net power in watts: positive = import, negative = export
            # slot is 0.5 hours, so kW = kwh / 0.5, W = kW * 1000
            net_power_w = (import_kwh - export_kwh) / 0.5 * 1000.0

            # Parse start time for the slot
            slot_start = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")

            # Feed 60 readings at 30-second intervals through the engine
            for i in range(_READINGS_PER_SLOT):
                reading_time = slot_start + timedelta(seconds=i * 30)
                engine.update(net_power_w, reading_time)

        results[day] = {
            "cost_c": engine.net_daily_cost_aud * 100.0,
            "import_kwh": engine.import_kwh_today,
            "export_kwh": engine.export_kwh_today,
            "supply_c": plan_options.get("daily_supply_charge", 0.0),
        }

    return results


def compare_all_plans(
    file_path: str,
    amber_network_daily_c: float,
    amber_subscription_daily_c: float,
) -> dict[str, Any]:
    """Main entry point: parse CSV, compute Amber costs, simulate all GloBird plans.

    Args:
        file_path: Path to Amber usage CSV.
        amber_network_daily_c: Amber daily network charge in cents.
        amber_subscription_daily_c: Amber daily subscription fee in cents.

    Returns:
        Comparison dict with period, amber totals, plan costs, cheapest plan,
        and daily breakdown.
    """
    rows = parse_amber_csv(file_path)
    if not rows:
        return {
            "period": {"start": "", "end": "", "days": 0},
            "amber": {"total_cost_aud": 0.0, "daily_charges_aud": 0.0,
                       "import_cost_aud": 0.0, "export_credit_aud": 0.0},
            "plans": {},
            "cheapest_plan": "",
            "daily_breakdown": [],
        }

    # Amber daily costs from CSV
    amber_daily = analyze_amber_costs(rows)
    days_sorted = sorted(amber_daily.keys())
    num_days = len(days_sorted)

    # Amber totals
    amber_import_cost_c = sum(d["import_cost_c"] for d in amber_daily.values())
    amber_export_cost_c = sum(d["export_cost_c"] for d in amber_daily.values())
    amber_daily_charges_c = num_days * (amber_network_daily_c + amber_subscription_daily_c)
    amber_total_c = amber_import_cost_c + amber_export_cost_c + amber_daily_charges_c

    # Simulate each GloBird plan
    plan_results: dict[str, dict[str, Any]] = {}
    plan_daily_data: dict[str, dict[str, dict[str, float]]] = {}

    for plan_name in _ALL_PLANS:
        plan_opts = GLOBIRD_PLAN_DEFAULTS[plan_name]
        daily_data = simulate_globird_plan(rows, plan_opts)
        plan_daily_data[plan_name] = daily_data

        total_cost_c = sum(d["cost_c"] for d in daily_data.values())
        total_cost_aud = total_cost_c / 100.0
        daily_avg_aud = total_cost_aud / num_days if num_days > 0 else 0.0
        savings_vs_amber_aud = (amber_total_c / 100.0) - total_cost_aud

        plan_results[plan_name] = {
            "total_cost_aud": round(total_cost_aud, 2),
            "daily_avg_aud": round(daily_avg_aud, 2),
            "savings_vs_amber_aud": round(savings_vs_amber_aud, 2),
        }

    # Find cheapest plan
    cheapest_plan = min(plan_results, key=lambda p: plan_results[p]["total_cost_aud"])

    # Build daily breakdown
    daily_breakdown: list[dict[str, Any]] = []
    for day in days_sorted:
        entry: dict[str, Any] = {
            "day": day,
            "amber_aud": round(
                (amber_daily[day]["import_cost_c"]
                 + amber_daily[day]["export_cost_c"]
                 + amber_network_daily_c
                 + amber_subscription_daily_c) / 100.0,
                2,
            ),
        }
        for plan_name in _ALL_PLANS:
            if day in plan_daily_data[plan_name]:
                entry[f"{plan_name}_aud"] = round(
                    plan_daily_data[plan_name][day]["cost_c"] / 100.0, 2
                )
            else:
                entry[f"{plan_name}_aud"] = 0.0
        daily_breakdown.append(entry)

    return {
        "period": {
            "start": days_sorted[0],
            "end": days_sorted[-1],
            "days": num_days,
        },
        "amber": {
            "total_cost_aud": round(amber_total_c / 100.0, 2),
            "daily_charges_aud": round(amber_daily_charges_c / 100.0, 2),
            "import_cost_aud": round(amber_import_cost_c / 100.0, 2),
            "export_credit_aud": round(amber_export_cost_c / 100.0, 2),
        },
        "plans": plan_results,
        "cheapest_plan": cheapest_plan,
        "daily_breakdown": daily_breakdown,
    }


# ---------------------------------------------------------------------------
# Dashboard-driven analysis (uses user's CONFIGURED rates, not plan defaults)
# ---------------------------------------------------------------------------


def _simulate_globird_from_rows(
    rows: list[dict[str, Any]], globird_options: dict[str, Any]
) -> dict[str, dict[str, float]]:
    """Simulate GloBird costs using the user's configured tariff options.

    Same algorithm as simulate_globird_plan but accepts pre-parsed row dicts
    (from dashboard JavaScript) instead of reading from a file.

    Args:
        rows: List of dicts with keys: day, start_time, channel_type, price,
              usage, cost. ``channel_type`` uses Amber CSV column names
              ("general" for import, "feedIn" for export).
        globird_options: The user's config_entry.options dict containing
              import_tariff, export_tariff, daily_supply_charge, incentives, etc.

    Returns:
        {day: {cost_c, import_kwh, export_kwh, supply_c}}
    """
    slots_by_day: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: {"import_kwh": 0.0, "export_kwh": 0.0})
    )
    for row in rows:
        day = row["day"]
        st = row["start_time"]
        channel = row.get("channel_type", row.get("channel", ""))
        usage = float(row.get("usage", 0.0))
        if channel == "general":
            slots_by_day[day][st]["import_kwh"] += usage
        elif channel == "feedIn":
            slots_by_day[day][st]["export_kwh"] += usage

    results: dict[str, dict[str, float]] = {}

    for day in sorted(slots_by_day.keys()):
        engine = TariffEngine(globird_options)
        day_slots = slots_by_day[day]

        for start_time_str in sorted(day_slots.keys()):
            slot = day_slots[start_time_str]
            import_kwh = slot["import_kwh"]
            export_kwh = slot["export_kwh"]

            # net power in watts: positive = import, negative = export
            # slot is 0.5 hours, so kW = kwh / 0.5, W = kW * 1000
            net_power_w = (import_kwh - export_kwh) / 0.5 * 1000.0

            # Parse start time for the slot
            slot_start = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")

            # Feed 60 readings at 30-second intervals through the engine
            for i in range(_READINGS_PER_SLOT):
                reading_time = slot_start + timedelta(seconds=i * 30)
                engine.update(net_power_w, reading_time)

        results[day] = {
            "cost_c": engine.net_daily_cost_aud * 100.0,
            "import_kwh": engine.import_kwh_today,
            "export_kwh": engine.export_kwh_today,
            "supply_c": globird_options.get("daily_supply_charge", 0.0),
        }

    return results


def analyze_csv_data(
    rows: list[dict[str, Any]],
    globird_options: dict[str, Any],
    amber_network_daily_c: float,
    amber_subscription_daily_c: float,
) -> dict[str, Any]:
    """Compare Amber CSV data against the user's configured GloBird rates.

    Called from the dashboard via HA service call. Uses the user's own
    config_entry.options for GloBird rates -- NOT hardcoded plan defaults.

    Args:
        rows: Pre-parsed CSV rows from dashboard JavaScript. Each dict has
              keys: day, start_time, channel_type, price, usage, cost.
        globird_options: The user's config_entry.options dict (import_tariff,
              export_tariff, daily_supply_charge, demand_charge, incentives).
        amber_network_daily_c: Amber daily network charge in cents.
        amber_subscription_daily_c: Amber daily subscription fee in cents.

    Returns:
        Comparison dict with period, amber totals, globird totals using
        user's configured rates, savings info, and daily breakdown.
    """
    if not rows:
        return {
            "period": {"start": "", "end": "", "days": 0},
            "amber": {
                "total_aud": 0.0,
                "energy_aud": 0.0,
                "daily_fees_aud": 0.0,
                "import_kwh": 0.0,
                "export_kwh": 0.0,
            },
            "globird": {
                "total_aud": 0.0,
                "energy_aud": 0.0,
                "supply_aud": 0.0,
                "import_kwh": 0.0,
                "export_kwh": 0.0,
            },
            "savings_aud": 0.0,
            "savings_direction": "none",
            "daily": [],
        }

    # --- Amber costs from CSV data ---
    daily_amber: dict[str, dict[str, float]] = defaultdict(
        lambda: {"import_cost_c": 0.0, "export_cost_c": 0.0, "import_kwh": 0.0, "export_kwh": 0.0}
    )
    for row in rows:
        day = row["day"]
        channel = row.get("channel_type", row.get("channel", ""))
        cost = float(row.get("cost", 0.0))
        usage = float(row.get("usage", 0.0))
        if channel == "general":
            daily_amber[day]["import_cost_c"] += cost
            daily_amber[day]["import_kwh"] += usage
        elif channel == "feedIn":
            daily_amber[day]["export_cost_c"] += cost
            daily_amber[day]["export_kwh"] += usage

    days_sorted = sorted(daily_amber.keys())
    num_days = len(days_sorted)

    # Amber totals
    amber_import_cost_c = sum(d["import_cost_c"] for d in daily_amber.values())
    amber_export_cost_c = sum(d["export_cost_c"] for d in daily_amber.values())
    amber_daily_fees_c = num_days * (amber_network_daily_c + amber_subscription_daily_c)
    amber_energy_c = amber_import_cost_c + amber_export_cost_c
    amber_total_c = amber_energy_c + amber_daily_fees_c

    amber_import_kwh = sum(d["import_kwh"] for d in daily_amber.values())
    amber_export_kwh = sum(d["export_kwh"] for d in daily_amber.values())

    # --- GloBird costs using user's configured rates ---
    globird_daily = _simulate_globird_from_rows(rows, globird_options)
    globird_total_c = sum(d["cost_c"] for d in globird_daily.values())
    globird_import_kwh = sum(d["import_kwh"] for d in globird_daily.values())
    globird_export_kwh = sum(d["export_kwh"] for d in globird_daily.values())

    # GloBird supply charge (already included in net_daily_cost_aud via TariffEngine)
    supply_per_day_c = globird_options.get("daily_supply_charge", 0.0)
    globird_supply_c = supply_per_day_c * num_days
    globird_energy_c = globird_total_c - globird_supply_c

    amber_total_aud = amber_total_c / 100.0
    globird_total_aud = globird_total_c / 100.0
    savings_aud = amber_total_aud - globird_total_aud

    if savings_aud > 0:
        savings_direction = "globird"
    elif savings_aud < 0:
        savings_direction = "amber"
    else:
        savings_direction = "equal"

    # --- Daily breakdown ---
    daily_breakdown: list[dict[str, Any]] = []
    for day in days_sorted:
        day_amber_c = (
            daily_amber[day]["import_cost_c"]
            + daily_amber[day]["export_cost_c"]
            + amber_network_daily_c
            + amber_subscription_daily_c
        )
        day_globird_c = globird_daily.get(day, {}).get("cost_c", 0.0)
        daily_breakdown.append({
            "date": day,
            "amber_aud": round(day_amber_c / 100.0, 2),
            "globird_aud": round(day_globird_c / 100.0, 2),
        })

    return {
        "period": {
            "start": days_sorted[0],
            "end": days_sorted[-1],
            "days": num_days,
        },
        "amber": {
            "total_aud": round(amber_total_aud, 2),
            "energy_aud": round(amber_energy_c / 100.0, 2),
            "daily_fees_aud": round(amber_daily_fees_c / 100.0, 2),
            "import_kwh": round(amber_import_kwh, 1),
            "export_kwh": round(amber_export_kwh, 1),
        },
        "globird": {
            "total_aud": round(globird_total_aud, 2),
            "energy_aud": round(globird_energy_c / 100.0, 2),
            "supply_aud": round(globird_supply_c / 100.0, 2),
            "import_kwh": round(globird_import_kwh, 1),
            "export_kwh": round(globird_export_kwh, 1),
        },
        "savings_aud": round(abs(savings_aud), 2),
        "savings_direction": savings_direction,
        "daily": daily_breakdown,
    }
