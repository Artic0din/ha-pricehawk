"""Phase 0 gate verifier: independent kWh-bucket aggregator vs evaluator.

Different code path from cdr_evaluator_proto.py. Buckets consumption
by TOU window using simple per-rate-type aggregation, then multiplies
by per-bucket rate. Surfaces kWh-by-bucket breakdown for hand-calc
spreadsheet replication.

The two paths SHOULD agree. Where they disagree -> bug in one or both;
the human hand-calc spreadsheet is the canonical tie-breaker.

Run:
    python3 scripts/phase_0_verify.py            # all 6 plans, table output
    python3 scripts/phase_0_verify.py --markdown # writes GATE_RESULTS.md
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from cdr_evaluator_proto import evaluate, GST_FACTOR  # noqa: E402

REPO = Path(__file__).parent.parent
FIXTURE_DIR = REPO / "tests" / "fixtures" / "phase0"
RESULTS_MD = FIXTURE_DIR / "GATE_RESULTS.md"

DAY_NAMES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
SYDNEY = ZoneInfo("Australia/Sydney")

# Plans + consumption fixture pairs.
CASES = [
    ("A", "AGL Residential Smart Saver (SINGLE_RATE NSW)",
     "plan_agl_AGL907738MRE6@EME.json", "consumption_7d.json", 0.05),
    ("B", "Red Taronga Flex (TIME_OF_USE NSW Ausgrid)",
     "plan_red-energy_RED552831MRE15@EME.json", "consumption_7d.json", 0.05),
    ("C1", "Synthetic FLEXIBLE (stepped 24.6c -> 30.1c at 15 kWh/day)",
     "plan_c1_flexible_synthetic.json", "consumption_7d.json", 0.05),
    ("C2", "GloBird ZEROHERO United Energy (FLEXIBLE + parser)",
     "plan_globird_GLO731031MR@VEC.json", "consumption_7d.json", 0.05),
    ("D", "Red Taronga Flex × DST backward 2026-04-05 (25h day)",
     "plan_red-energy_RED552831MRE15@EME.json", "consumption_dst_april_2026-04-05.json", 0.05),
    ("E", "Red Taronga Flex × DST forward 2026-10-04 (23h day)",
     "plan_red-energy_RED552831MRE15@EME.json", "consumption_dst_october_2026-10-04.json", 0.05),
]


def _hhmm_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _slot_in_window(local_dt: datetime, days: list[str], start: str, end: str) -> bool:
    """Same semantics as evaluator: start-inclusive, end-exclusive. "00:00"
    end with non-zero start = end-of-day (24:00 = 1440)."""
    if DAY_NAMES[local_dt.weekday()] not in days:
        return False
    m = local_dt.hour * 60 + local_dt.minute
    sm = _hhmm_to_minutes(start)
    em = _hhmm_to_minutes(end)
    if em == 0 and sm > 0:
        em = 1440
    if em < sm:
        return m >= sm or m < em
    return sm <= m < em


def _bucketize_import(plan: dict, slots: list[dict]) -> dict:
    """Bucket consumption by TOU window or singleRate; return per-bucket kWh + cost.

    Independent path: aggregate kWh first, then multiply by rate. Stepped
    rates are handled by inserting an extra synthetic bucket per day for the
    over-threshold tail.
    """
    elec = plan.get("data", {}).get("electricityContract", {}) or plan.get("electricityContract", {})
    tps = elec.get("tariffPeriod", []) or []
    if not tps:
        return {}
    tp = tps[0]
    rblock = tp.get("rateBlockUType")

    daily_running: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    buckets: dict[str, dict] = defaultdict(lambda: {"kwh": Decimal("0"), "cost_ex_gst": Decimal("0"), "rate_label": ""})

    if rblock == "singleRate":
        single = tp.get("singleRate", {}) or {}
        rates = single.get("rates", []) or []
        for slot in slots:
            local_dt = datetime.fromisoformat(slot["ts_local"])
            day = local_dt.date().isoformat()
            kwh = Decimal(str(slot.get("grid_import_kwh", 0) or 0))
            running = daily_running[day]
            # Walk stepped rates
            for r in rates:
                vol = r.get("volume")
                price = Decimal(str(r.get("unitPrice", 0)))
                bucket_key = f"SINGLE_RATE@{price}"
                if vol is None:
                    if running < (Decimal(str(rates[0].get("volume", 1e9))) if rates and rates[0].get("volume") else Decimal("1e9")):
                        continue
                    buckets[bucket_key]["kwh"] += kwh
                    buckets[bucket_key]["cost_ex_gst"] += kwh * price
                    buckets[bucket_key]["rate_label"] = f"flat {price}/kWh"
                    daily_running[day] += kwh
                    break
                else:
                    vol_d = Decimal(str(vol))
                    if running < vol_d:
                        buckets[bucket_key]["kwh"] += kwh
                        buckets[bucket_key]["cost_ex_gst"] += kwh * price
                        buckets[bucket_key]["rate_label"] = f"first {vol_d} kWh/period @ {price}/kWh"
                        daily_running[day] += kwh
                        break
        return buckets

    if rblock == "timeOfUseRates":
        tou_rates = tp.get("timeOfUseRates", []) or []
        for slot in slots:
            local_dt = datetime.fromisoformat(slot["ts_local"])
            day = local_dt.date().isoformat()
            kwh = Decimal(str(slot.get("grid_import_kwh", 0) or 0))
            matched = None
            for rate in tou_rates:
                for window in rate.get("timeOfUse", []) or []:
                    if _slot_in_window(local_dt, window.get("days", []), window.get("startTime", "00:00"), window.get("endTime", "23:59")):
                        matched = rate
                        break
                if matched:
                    break
            if not matched:
                buckets["UNMATCHED"]["kwh"] += kwh
                continue
            rtype = matched.get("type", "?")
            running_key = f"{day}|{rtype}"
            running = daily_running[running_key]
            # Pick rate from stepped rates list
            chosen_price = None
            chosen_label = None
            for r in matched.get("rates", []) or []:
                vol = r.get("volume")
                price = Decimal(str(r.get("unitPrice", 0)))
                if vol is None:
                    chosen_price = price
                    chosen_label = f"{rtype} flat {price}/kWh"
                    break
                vol_d = Decimal(str(vol))
                if running < vol_d:
                    chosen_price = price
                    chosen_label = f"{rtype} <{vol_d} kWh/day @ {price}/kWh"
                    break
            if chosen_price is None:
                last = matched.get("rates", [{}])[-1]
                chosen_price = Decimal(str(last.get("unitPrice", 0)))
                chosen_label = f"{rtype} (fallback) {chosen_price}/kWh"
            bucket_key = f"{rtype}@{chosen_price}"
            buckets[bucket_key]["kwh"] += kwh
            buckets[bucket_key]["cost_ex_gst"] += kwh * chosen_price
            buckets[bucket_key]["rate_label"] = chosen_label
            daily_running[running_key] += kwh
        return buckets

    return {}


def _supply_cost(plan: dict, slots: list[dict]) -> tuple[Decimal, int]:
    elec = plan.get("data", {}).get("electricityContract", {}) or plan.get("electricityContract", {})
    tp = (elec.get("tariffPeriod") or [{}])[0]
    dsc = Decimal(str(tp.get("dailySupplyCharge", 0) or 0))
    days = {datetime.fromisoformat(s["ts_local"]).date() for s in slots}
    return dsc * Decimal(len(days)), len(days)


def _fit_cost(plan: dict, slots: list[dict]) -> Decimal:
    """Independent FIT cross-check: walk each slot, find matching FIT, sum credit."""
    elec = plan.get("data", {}).get("electricityContract", {}) or plan.get("electricityContract", {})
    fits = elec.get("solarFeedInTariff", []) or []
    total_credit = Decimal("0")
    for slot in slots:
        local_dt = datetime.fromisoformat(slot["ts_local"])
        exp = Decimal(str(slot.get("grid_export_kwh", 0) or slot.get("solar_export_kwh", 0) or 0))
        if exp <= 0:
            continue
        for fit in fits:
            if fit.get("tariffUType") == "singleTariff":
                st = fit.get("singleTariff") or {}
                tvs = st.get("timeVariations") or []
                if tvs and not any(_slot_in_window(local_dt, t.get("days", DAY_NAMES), t.get("startTime", "00:00"), t.get("endTime", "23:59")) for t in tvs):
                    continue
                rates = st.get("rates") or []
                if rates:
                    total_credit += exp * Decimal(str(rates[0].get("unitPrice", 0)))
            elif fit.get("tariffUType") == "timeVaryingTariffs":
                for tvt in fit.get("timeVaryingTariffs") or []:
                    if any(_slot_in_window(local_dt, t.get("days", DAY_NAMES), t.get("startTime", "00:00"), t.get("endTime", "23:59")) for t in (tvt.get("timeVariations") or [])):
                        rates = tvt.get("rates") or []
                        if rates:
                            total_credit += exp * Decimal(str(rates[0].get("unitPrice", 0)))
    return -total_credit  # credit -> negative cost contribution


def run_one(label: str, desc: str, plan_path: Path, cons_path: Path) -> dict:
    plan = json.loads(plan_path.read_text())
    cons = json.loads(cons_path.read_text())
    slots = cons.get("slots", []) or []

    # Evaluator path
    bd = evaluate(plan, cons)
    evaluator_total_inc = bd.total_aud_inc_gst

    # Independent path: bucket-aggregated import + supply + FIT (no incentives,
    # apples-to-apples with evaluator before its incentive parser fires)
    buckets = _bucketize_import(plan, slots)
    independent_import_ex = sum((b["cost_ex_gst"] for b in buckets.values()), Decimal("0"))
    supply_ex, days = _supply_cost(plan, slots)
    fit_cost_ex = _fit_cost(plan, slots)

    # Incentive credit (already inc-GST per parser convention; legacy treats
    # "$1/Day" credit as $1 inc-GST flat).
    incentive_inc = bd.incentive_aud_inc_gst

    independent_total_ex = independent_import_ex + supply_ex + fit_cost_ex
    independent_total_inc = (independent_total_ex * GST_FACTOR + incentive_inc).quantize(Decimal("0.01"))
    evaluator_total_inc_q = evaluator_total_inc.quantize(Decimal("0.01"))

    diff_abs = abs(independent_total_inc - evaluator_total_inc_q)
    diff_rel = float(diff_abs / evaluator_total_inc_q * 100) if evaluator_total_inc_q != 0 else 0.0

    return {
        "label": label,
        "desc": desc,
        "plan_id": bd.plan_id,
        "days": days,
        "slots": len(slots),
        "evaluator_total_inc": float(evaluator_total_inc_q),
        "independent_total_inc": float(independent_total_inc),
        "diff_abs": float(diff_abs),
        "diff_rel_pct": diff_rel,
        "buckets": {k: {"kwh": float(v["kwh"].quantize(Decimal("0.001"))), "cost_ex_gst": float(v["cost_ex_gst"].quantize(Decimal("0.0001"))), "label": v["rate_label"]} for k, v in buckets.items()},
        "supply_ex": float(supply_ex.quantize(Decimal("0.01"))),
        "fit_credit_ex": float(fit_cost_ex.quantize(Decimal("0.0001"))),
        "incentive_credit_inc": float(incentive_inc.quantize(Decimal("0.0001"))),
        "notes": bd.notes,
    }


def main(argv: list[str]) -> int:
    results = []
    print("=" * 80)
    for code, desc, plan_f, cons_f, _tol in CASES:
        r = run_one(code, desc, FIXTURE_DIR / plan_f, FIXTURE_DIR / cons_f)
        results.append(r)
        print(f"\nPLAN {code} | {desc}")
        print(f"  plan_id={r['plan_id']}  days={r['days']}  slots={r['slots']}")
        print(f"  evaluator_total_inc_gst:   ${r['evaluator_total_inc']:.2f}")
        print(f"  independent_total_inc_gst: ${r['independent_total_inc']:.2f}")
        print(f"  diff: ${r['diff_abs']:.4f}  ({r['diff_rel_pct']:.3f}%)")
        print(f"  supply_ex: ${r['supply_ex']:.2f}  fit_credit_ex: ${r['fit_credit_ex']:.4f}  incentive_credit_inc: ${r['incentive_credit_inc']:.4f}")
        print("  buckets (independent kWh × rate, ex-GST):")
        for k, b in sorted(r["buckets"].items()):
            print(f"    {b['label']:<48} kWh={b['kwh']:>10.3f}  cost_ex_gst=${b['cost_ex_gst']:.4f}")
        for n in r["notes"]:
            print(f"  NOTE: {n}")

    print("\n" + "=" * 80)
    print("CROSS-CHECK SUMMARY (evaluator vs independent bucket aggregator)")
    print(f"  {'Plan':<5} {'Evaluator $':>14} {'Independent $':>16} {'Diff $':>10} {'Diff %':>10}")
    for r in results:
        print(f"  {r['label']:<5} {r['evaluator_total_inc']:>14.2f} {r['independent_total_inc']:>16.2f} {r['diff_abs']:>10.4f} {r['diff_rel_pct']:>10.4f}")

    if "--markdown" in argv:
        _write_markdown(results)
        print(f"\nwrote {RESULTS_MD}")

    return 0


def _write_markdown(results: list[dict]) -> None:
    lines = [
        "# Phase 0 Gate Results — Cross-Check Report",
        "",
        "**Purpose:** Independent verification that the Phase 0 evaluator prototype",
        "(`scripts/cdr_evaluator_proto.py`) reproduces a separate bucket-aggregation",
        "pass over the same fixtures. The two code paths share no logic except input",
        "parsing. If they agree, the evaluator's structural logic is internally",
        "consistent.",
        "",
        "**This does NOT replace human hand-calc** — that remains the canonical",
        "ground-truth per locked decision D-P0-2 / design doc §F. Use this report",
        "to drive what to hand-check first: focus on buckets with the largest kWh",
        "contribution, validate the rate × kWh math against the plan PDF, sum the",
        "buckets, apply × 1.10 for GST, compare to the evaluator total.",
        "",
        "All dollar values shown GST-inclusive unless suffixed `_ex`.",
        "",
        "## Summary",
        "",
        "| Plan | Description | Days | Slots | Evaluator $ | Independent $ | Diff $ | Diff % |",
        "|------|-------------|-----:|------:|------------:|--------------:|-------:|-------:|",
    ]
    for r in results:
        lines.append(f"| {r['label']} | {r['desc']} | {r['days']} | {r['slots']} | ${r['evaluator_total_inc']:.2f} | ${r['independent_total_inc']:.2f} | ${r['diff_abs']:.4f} | {r['diff_rel_pct']:.4f}% |")

    lines += [
        "",
        "## Per-plan bucket breakdown (ex-GST)",
        "",
        "Each bucket = sum of half-hour kWh that fell into one TOU window slot × the applicable rate.",
        "Useful for hand-spreadsheet replication: each row in your spreadsheet should match a row here.",
        "",
    ]
    for r in results:
        lines += [
            f"### Plan {r['label']} — {r['desc']}",
            f"- plan_id: `{r['plan_id']}`",
            f"- supply ex-GST: ${r['supply_ex']:.4f}  ({r['days']} days × daily supply)",
            f"- FIT credit ex-GST: ${r['fit_credit_ex']:.4f}  (negative = credit toward bill)",
            f"- Incentive credit ex-GST (parser output): ${r['incentive_credit_inc']:.4f}",
            "",
            "| Bucket | kWh | Cost ex-GST |",
            "|--------|----:|------------:|",
        ]
        for k, b in sorted(r["buckets"].items()):
            lines.append(f"| {b['label']} | {b['kwh']:.3f} | ${b['cost_ex_gst']:.4f} |")
        lines.append("")

    lines += [
        "## Hand-calc gate criteria",
        "",
        "Per `scripts/PHASE_0_GROUND_TRUTH.md` §6:",
        "- Plans A / B / C1 / C2: within ±5% of hand-calc total_aud_inc_gst.",
        "- Plans D / E: within ±$0.05 absolute (24h windows).",
        "- C2 (GloBird ZEROHERO) is load-bearing — fail = Approach A fallback.",
        "",
        "## How to read this report",
        "",
        "1. For each plan, sum (Bucket cost_ex_gst) + supply_ex + fit_credit_ex + incentive_credit_inc.",
        "2. Multiply the sum by 1.10 for GST.",
        "3. The result should equal `Independent $` to 2 d.p.",
        "4. `Diff $` between Evaluator and Independent should be ~$0.00 — the two are computing the same thing two ways. Non-zero diff indicates a bug in one path.",
        "5. For the canonical Phase 0 gate, replace this report's bucket totals with your hand-calc spreadsheet values and re-check the per-plan diff.",
    ]
    RESULTS_MD.write_text("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
