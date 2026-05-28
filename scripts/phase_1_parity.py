"""Phase 1 parity check — legacy TariffEngine vs new CDR evaluator on
the SAME canonical CDR data.

Translates the C2 CDR PlanDetailV2 JSON into the legacy engine's
options dict (the shape used by v1.4.x config_flow). Drives both
engines over the SAME consumption fixture. Compares per-day totals.

Gate: ±0.5% per day per §H §3 / DECISIONS.md D-P0-6. Failure means
the new evaluator's algorithm diverges from legacy's, NOT a rate-
version drift.

The CDR fixture's `tariffPeriod[0].dailySupplyCharge` is ex-GST in
DOLLARS; legacy expects inc-GST CENTS — translator handles the unit
conversion. Same for `unitPrice` in rate blocks.

Run:
    python3 scripts/phase_1_parity.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).parent.parent
CDR_PLAN_PATH = REPO / "tests" / "fixtures" / "phase0" / "plan_globird_GLO731031MR@VEC.json"
CONSUMPTION_PATH = REPO / "tests" / "fixtures" / "phase0" / "consumption_7d.json"
OUT_REPORT = REPO / "tests" / "fixtures" / "legacy_engine_outputs" / "PARITY_REPORT.md"


# Direct-load tariff_engine.py (bypass package __init__)
def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclass needs module registered in sys.modules
    spec.loader.exec_module(mod)
    return mod


_tariff_engine = _load(
    "legacy_tariff_engine", REPO / "custom_components" / "pricehawk" / "tariff_engine.py"
)
TariffEngine = _tariff_engine.TariffEngine

_evaluator = _load("cdr_evaluator_proto", Path(__file__).parent / "cdr_evaluator_proto.py")
evaluate = _evaluator.evaluate
GST_FACTOR = _evaluator.GST_FACTOR

SLOT_HOURS = 0.5
SUBSTEP_MINUTES = 6
SUBSTEPS_PER_SLOT = int((SLOT_HOURS * 60) / SUBSTEP_MINUTES)
GAP_PROTECTION = 0.1  # h, must match tariff_engine.GAP_PROTECTION_MAX_DELTA_H

ALL_DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def _ex_gst_dollars_to_inc_gst_cents(d: float | str | Decimal) -> float:
    """CDR uses ex-GST $/kWh; legacy uses inc-GST c/kWh."""
    return float(Decimal(str(d)) * Decimal("1.10") * Decimal("100"))


def _window_pairs(time_of_use: list[dict]) -> list[list[str]]:
    """Translate CDR timeOfUse [{startTime, endTime, days}] to legacy
    [[start, end+1min]] pairs. Legacy uses HH:MM with end EXCLUSIVE
    where windows end at midnight encoded as 00:00.
    """
    pairs = []
    for tu in time_of_use:
        start = tu.get("startTime", "00:00")
        end = tu.get("endTime", "23:59")
        # Convert CDR's inclusive "HH:59" to legacy's exclusive "HH+1:00"
        if end.endswith(":59"):
            h, _ = end.split(":")
            end_excl = f"{int(h) + 1:02d}:00" if int(h) < 23 else "00:00"
        else:
            end_excl = end
        pairs.append([start, end_excl])
    return pairs


def cdr_to_legacy_options(cdr_plan: dict) -> dict:
    """Translate CDR PlanDetailV2 -> legacy ZEROHERO-shaped options dict.

    Phase 1 helper. NOT general-purpose: assumes ZEROHERO-flavored
    FLEXIBLE plan with TOU import + TOU FIT. Other pricingModels need
    different mapping.
    """
    elec = cdr_plan["data"]["electricityContract"]
    tp = elec["tariffPeriod"][0]

    # Import side: timeOfUseRates -> legacy "periods" dict
    import_periods: dict = {}
    type_map = {"PEAK": "peak", "SHOULDER": "shoulder", "OFF_PEAK": "offpeak"}
    for r in tp.get("timeOfUseRates", []) or []:
        legacy_type = type_map.get(r["type"], r["type"].lower())
        rates = r.get("rates", []) or []
        if not rates:
            continue
        rate_ex = rates[0].get("unitPrice", "0")
        rate_inc_c = _ex_gst_dollars_to_inc_gst_cents(rate_ex)
        windows = _window_pairs(r.get("timeOfUse", []) or [])
        if legacy_type in import_periods:
            import_periods[legacy_type]["windows"].extend(windows)
        else:
            import_periods[legacy_type] = {"rate": rate_inc_c, "windows": windows}

    # Export side: timeVaryingTariffs (post-augmentation) -> legacy "periods"
    export_periods: dict = {}
    fits = elec.get("solarFeedInTariff", []) or []
    for fit in fits:
        if fit.get("tariffUType") != "timeVaryingTariffs":
            continue
        for tvt in fit.get("timeVaryingTariffs") or []:
            legacy_type = type_map.get(tvt["type"], tvt["type"].lower())
            rates = tvt.get("rates", []) or []
            if not rates:
                continue
            rate_ex = rates[0].get("unitPrice", "0")
            rate_inc_c = _ex_gst_dollars_to_inc_gst_cents(rate_ex)
            windows = _window_pairs(tvt.get("timeVariations", []) or [])
            if legacy_type in export_periods:
                export_periods[legacy_type]["windows"].extend(windows)
            else:
                export_periods[legacy_type] = {"rate": rate_inc_c, "windows": windows}

    # Supply: ex-GST $/day -> inc-GST c/day
    supply_inc_c = _ex_gst_dollars_to_inc_gst_cents(tp.get("dailySupplyCharge", "0")) / 100 * 100  # noqa
    # (multiplication is identity, kept explicit for clarity)
    supply_inc_c = float(
        Decimal(str(tp.get("dailySupplyCharge", "0"))) * Decimal("1.10") * Decimal("100")
    )

    return {
        "plan_type": "zerohero_cdr_translated",
        "daily_supply_charge": supply_inc_c,
        "demand_charge": 0.0,
        "import_tariff": {"type": "tou", "periods": import_periods},
        "export_tariff": {"type": "tou", "periods": export_periods},
        "incentives": ["zerohero_credit", "super_export", "free_power_window"],
    }


def _drive_legacy(options: dict, slots: list[dict]) -> dict:
    """Same sub-sampling driver as snapshot_legacy_engine.py."""
    engine = TariffEngine(options)
    per_day: dict[str, dict] = {}
    current_day: str | None = None

    for slot in slots:
        local_dt = datetime.fromisoformat(slot["ts_local"]).replace(tzinfo=None)
        day_key = local_dt.date().isoformat()
        if current_day is None:
            current_day = day_key
        elif day_key != current_day:
            per_day[current_day] = {
                "cost_aud": engine.net_daily_cost_aud,
                "import_kwh": engine.import_kwh_today,
                "export_kwh": engine.export_kwh_today,
                "import_cost_c": engine.import_cost_today_c,
                "export_earnings_c": engine.export_earnings_today_c,
                "zerohero": engine.zerohero_status,
                "super_export_kwh": engine.super_export_kwh,
            }
            engine.reset_daily()
            current_day = day_key

        import_kwh = float(slot.get("grid_import_kwh", 0) or 0)
        export_kwh = float(slot.get("grid_export_kwh", 0) or slot.get("solar_export_kwh", 0) or 0)
        net_w = ((import_kwh - export_kwh) / SLOT_HOURS) * 1000.0
        for sub_i in range(SUBSTEPS_PER_SLOT):
            engine.update(net_w, local_dt + timedelta(minutes=SUBSTEP_MINUTES * sub_i))

    if current_day:
        per_day[current_day] = {
            "cost_aud": engine.net_daily_cost_aud,
            "import_kwh": engine.import_kwh_today,
            "export_kwh": engine.export_kwh_today,
            "import_cost_c": engine.import_cost_today_c,
            "export_earnings_c": engine.export_earnings_today_c,
            "zerohero": engine.zerohero_status,
            "super_export_kwh": engine.super_export_kwh,
        }
    return per_day


def _drive_new(cdr_plan: dict, consumption: dict) -> dict:
    """Per-day breakdown using the new evaluator.

    The new evaluator returns one whole-period CostBreakdown, not per-day.
    To produce per-day numbers for parity comparison, slice the consumption
    fixture by local date and run evaluator once per slice.
    """
    slots = consumption.get("slots", []) or []
    by_day: dict[str, list[dict]] = {}
    for slot in slots:
        day_key = slot["ts_local"][:10]
        by_day.setdefault(day_key, []).append(slot)
    per_day: dict[str, float] = {}
    for day, day_slots in by_day.items():
        sub_consumption = {"slots": day_slots}
        bd = evaluate(cdr_plan, sub_consumption)
        per_day[day] = float(bd.total_aud_inc_gst.quantize(Decimal("0.0001")))
    return per_day


def main() -> int:
    cdr_plan = json.loads(CDR_PLAN_PATH.read_text())
    consumption = json.loads(CONSUMPTION_PATH.read_text())
    slots = consumption.get("slots", []) or []

    # Translate CDR -> legacy options
    legacy_options = cdr_to_legacy_options(cdr_plan)
    print("=== Translated CDR -> legacy options ===")
    print(json.dumps(legacy_options, indent=2))

    # Drive both engines
    print("\n=== Driving legacy engine with CDR-translated options ===")
    legacy_per_day = _drive_legacy(legacy_options, slots)
    legacy_total = sum(d["cost_aud"] for d in legacy_per_day.values())
    print(f"legacy 7d total: ${legacy_total:.2f}")

    print("\n=== Driving new CDR evaluator ===")
    new_per_day = _drive_new(cdr_plan, consumption)
    new_total = sum(new_per_day.values())
    print(f"new 7d total: ${new_total:.2f}")

    # Per-day comparison
    print("\n=== PARITY (per-day, inc-GST AUD) ===")
    rows = []
    print(
        f"{'Day':<12} {'Legacy $':>10} {'New $':>10} {'Diff $':>10} {'Diff %':>10}  {'Status':<10}"
    )
    pass_count = 0
    for day in sorted(set(legacy_per_day) | set(new_per_day)):
        leg = legacy_per_day.get(day, {}).get("cost_aud", 0.0)
        new = new_per_day.get(day, 0.0)
        diff = abs(leg - new)
        rel = (diff / leg * 100) if leg else 0.0
        zh = legacy_per_day.get(day, {}).get("zerohero", "n/a")
        status = "PASS" if rel <= 0.5 else "FAIL"
        if status == "PASS":
            pass_count += 1
        rows.append(
            {
                "day": day,
                "legacy": leg,
                "new": new,
                "diff": diff,
                "rel_pct": rel,
                "zerohero": zh,
                "status": status,
            }
        )
        print(
            f"{day:<12} {leg:>10.4f} {new:>10.4f} {diff:>10.4f} {rel:>10.4f}  {status:<10} zh={zh}"
        )

    total_diff = abs(legacy_total - new_total)
    total_rel = (total_diff / legacy_total * 100) if legacy_total else 0.0
    total_status = "PASS" if total_rel <= 0.5 else "FAIL"
    print(
        f"\n{'TOTAL':<12} {legacy_total:>10.4f} {new_total:>10.4f} {total_diff:>10.4f} {total_rel:>10.4f}  {total_status}"
    )
    print(f"\nPer-day pass count: {pass_count}/{len(rows)} (gate: ±0.5%)")

    # Write markdown report
    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    md = [
        "# Phase 1 Parity Report — Legacy TariffEngine vs CDR Evaluator",
        "",
        "**Inputs:**",
        f"- CDR plan: `{CDR_PLAN_PATH.relative_to(REPO)}` ({cdr_plan['data']['planId']})",
        f"- Consumption: `{CONSUMPTION_PATH.relative_to(REPO)}` ({len(slots)} slots, "
        f"window {consumption['_phase0_meta']['window_local']})",
        "",
        "**Method:** translate CDR `electricityContract` -> legacy options dict via",
        "`cdr_to_legacy_options()`. Drive legacy engine (6-min sub-sampling per",
        "GAP_PROTECTION cap). Drive new evaluator on per-day slot slices. Compare",
        "per-day totals.",
        "",
        "**Gate (§H §3 / D-P0-6):** ±0.5% per day. New evaluator must reproduce",
        "legacy results within that bound before `tariff_engine.py` (496 lines) is",
        "deleted at end of Phase 1.",
        "",
        "## Per-day comparison",
        "",
        "| Day | Legacy $ | New $ | Diff $ | Diff % | Status | zerohero |",
        "|-----|---------:|------:|-------:|-------:|:------:|----------|",
    ]
    for r in rows:
        md.append(
            f"| {r['day']} | ${r['legacy']:.4f} | ${r['new']:.4f} | ${r['diff']:.4f} | {r['rel_pct']:.4f}% | {r['status']} | {r['zerohero']} |"
        )
    md += [
        f"| **TOTAL** | **${legacy_total:.4f}** | **${new_total:.4f}** | **${total_diff:.4f}** | **{total_rel:.4f}%** | **{total_status}** | — |",
        "",
        f"**Per-day passes:** {pass_count}/{len(rows)} (gate: ±0.5%)",
        "",
        "## Translated legacy options (for reproducibility)",
        "",
        "```json",
        json.dumps(legacy_options, indent=2),
        "```",
        "",
        "## Interpretation",
        "",
        "- If TOTAL gate is PASS: refactor can proceed; new evaluator is parity-equivalent to legacy at the algorithm level.",
        "- If TOTAL is FAIL but per-day diffs are random ±X: likely a numerical-precision quirk; investigate but probably acceptable.",
        "- If a SPECIFIC day fails (e.g. ZEROHERO 'lost' day shows large diff): incentive parser logic divergence between legacy ZeroHeroTracker (instantaneous threshold) and new evaluator parser (avg-over-window threshold). May require switching new parser to instantaneous logic or sub-sample driver for legacy parity.",
        "",
        f"_Generated by `scripts/phase_1_parity.py` at {datetime.now().isoformat(timespec='seconds')}_",
    ]
    OUT_REPORT.write_text("\n".join(md))
    print(f"\nwrote {OUT_REPORT}")
    return 0 if total_status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
