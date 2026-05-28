"""Snapshot legacy TariffEngine outputs for Phase 1 parity gate.

Drives `custom_components/pricehawk/tariff_engine.py` (the 496-line legacy
GloBird engine that will be DELETED at end of Phase 1) over a fixed
consumption fixture using both ZEROHERO and BOOST configs. Saves outputs
to tests/fixtures/legacy_engine_outputs/.

Per locked decision §H §3 (DECISIONS.md D-P0-6 follow-on): the new
cdr/evaluator.py must reproduce these snapshots within 0.5% before legacy
deletion. Snapshots are the contract.

Run THIS SCRIPT BEFORE refactoring tariff_engine.py. Once the snapshots
exist + are committed, Phase 1 evaluator work can begin without
risk of regressing battle-tested behaviour.

Streaming model: legacy engine takes (grid_power_w, now_local) per call
and caps delta_h at GAP_PROTECTION_MAX_DELTA_H = 0.1h (6 min). Our Phase 0
consumption fixture has 30-min slots. Sub-sample each slot into 5 x 6-min
sub-readings at the same mean kW so engine accumulates kWh correctly.

Each slot conversion:
    net_grid_kw = (import_kwh - export_kwh) / 0.5
    net_grid_w  = net_grid_kw * 1000
    for sub in 0..4: engine.update(net_grid_w, slot_start + sub*6min)

Run:
    python3 scripts/snapshot_legacy_engine.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).parent.parent

# tariff_engine.py is pure Python per its docstring, but
# custom_components/pricehawk/__init__.py imports HA. Bypass the package
# __init__ by loading tariff_engine.py directly via importlib.
import importlib.util  # noqa: E402

_TE_PATH = REPO / "custom_components" / "pricehawk" / "tariff_engine.py"
_spec = importlib.util.spec_from_file_location("legacy_tariff_engine", _TE_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"can't load {_TE_PATH}")
_tariff_engine = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tariff_engine)
TariffEngine = _tariff_engine.TariffEngine

OUT_DIR = REPO / "tests" / "fixtures" / "legacy_engine_outputs"
CONSUMPTION_PATH = REPO / "tests" / "fixtures" / "phase0" / "consumption_7d.json"

# Configs lifted verbatim from tests/test_tariff_engine.py
ZEROHERO_IMPORT_PERIODS = {
    "peak": {"rate": 38.50, "windows": [["16:00", "23:00"]]},
    "shoulder": {
        "rate": 26.95,
        "windows": [["23:00", "00:00"], ["00:00", "11:00"], ["14:00", "16:00"]],
    },
    "offpeak": {"rate": 0.00, "windows": [["11:00", "14:00"]]},
}
ZEROHERO_EXPORT_PERIODS = {
    "peak": {"rate": 3.00, "windows": [["16:00", "21:00"]]},
    "shoulder": {
        "rate": 0.30,
        "windows": [["21:00", "00:00"], ["00:00", "10:00"], ["14:00", "16:00"]],
    },
    "offpeak": {"rate": 0.00, "windows": [["10:00", "14:00"]]},
}
ZEROHERO_OPTIONS = {
    "plan_type": "zerohero",
    "daily_supply_charge": 113.30,
    "demand_charge": 0.0,
    "import_tariff": {"type": "tou", "periods": ZEROHERO_IMPORT_PERIODS},
    "export_tariff": {"type": "tou", "periods": ZEROHERO_EXPORT_PERIODS},
    "incentives": ["zerohero_credit", "super_export", "free_power_window"],
}

BOOST_OPTIONS = {
    "plan_type": "boost",
    "daily_supply_charge": 111.10,
    "demand_charge": 0.0,
    "import_tariff": {
        "type": "flat_stepped",
        "step1_threshold_kwh": 25.0,
        "step1_rate": 21.67,
        "step2_rate": 25.30,
    },
    "export_tariff": {
        "type": "tou",
        "periods": {
            "peak": {"rate": 3.00, "windows": [["16:00", "21:00"]]},
            "shoulder": {
                "rate": 0.10,
                "windows": [["21:00", "00:00"], ["00:00", "10:00"], ["14:00", "16:00"]],
            },
            "offpeak": {"rate": 0.00, "windows": [["10:00", "14:00"]]},
        },
    },
    "incentives": [],
}

SLOT_HOURS = 0.5
# Legacy engine caps delta_h at GAP_PROTECTION_MAX_DELTA_H = 0.1h (6 min).
# A 30-min step would discard 80% of energy. Sub-sample each slot into
# 5 x 6-min sub-readings at the same mean kW so accumulation matches.
SUBSTEP_MINUTES = 6
SUBSTEPS_PER_SLOT = int((SLOT_HOURS * 60) / SUBSTEP_MINUTES)


def _drive_engine(options: dict, slots: list[dict]) -> dict:
    """Walk slots, step engine, capture per-day rollups."""
    engine = TariffEngine(options)
    per_day_cost_aud: dict[str, float] = {}
    per_day_import_kwh: dict[str, float] = {}
    per_day_export_kwh: dict[str, float] = {}
    per_day_import_cost_c: dict[str, float] = {}
    per_day_export_earnings_c: dict[str, float] = {}
    per_day_zerohero: dict[str, str] = {}
    per_day_super_export_kwh: dict[str, float] = {}
    current_day: str | None = None

    for slot in slots:
        local_dt = datetime.fromisoformat(slot["ts_local"])
        # Strip tz so legacy engine sees naive datetime (matches test pattern)
        local_naive = local_dt.replace(tzinfo=None)
        day_key = local_naive.date().isoformat()

        if current_day is None:
            current_day = day_key
        elif day_key != current_day:
            # End-of-day rollup BEFORE engine processes next slot
            per_day_cost_aud[current_day] = engine.net_daily_cost_aud
            per_day_import_kwh[current_day] = engine.import_kwh_today
            per_day_export_kwh[current_day] = engine.export_kwh_today
            per_day_import_cost_c[current_day] = engine.import_cost_today_c
            per_day_export_earnings_c[current_day] = engine.export_earnings_today_c
            per_day_zerohero[current_day] = engine.zerohero_status
            per_day_super_export_kwh[current_day] = engine.super_export_kwh
            engine.reset_daily()
            current_day = day_key

        # Convert slot kWh to mean-power Watts (positive=import, negative=export)
        import_kwh = float(slot.get("grid_import_kwh", 0) or 0)
        export_kwh = float(slot.get("grid_export_kwh", 0) or slot.get("solar_export_kwh", 0) or 0)
        net_kw = (import_kwh - export_kwh) / SLOT_HOURS
        net_w = net_kw * 1000.0
        # Sub-sample at 6-min intervals (matches engine's GAP_PROTECTION cap)
        for sub_i in range(SUBSTEPS_PER_SLOT):
            sub_dt = local_naive + timedelta(minutes=SUBSTEP_MINUTES * sub_i)
            engine.update(net_w, sub_dt)

    # Final day rollup
    if current_day:
        per_day_cost_aud[current_day] = engine.net_daily_cost_aud
        per_day_import_kwh[current_day] = engine.import_kwh_today
        per_day_export_kwh[current_day] = engine.export_kwh_today
        per_day_import_cost_c[current_day] = engine.import_cost_today_c
        per_day_export_earnings_c[current_day] = engine.export_earnings_today_c
        per_day_zerohero[current_day] = engine.zerohero_status
        per_day_super_export_kwh[current_day] = engine.super_export_kwh

    total_aud = sum(per_day_cost_aud.values())
    return {
        "per_day_cost_aud": per_day_cost_aud,
        "per_day_import_kwh": {k: round(v, 4) for k, v in per_day_import_kwh.items()},
        "per_day_export_kwh": {k: round(v, 4) for k, v in per_day_export_kwh.items()},
        "per_day_import_cost_c": {k: round(v, 4) for k, v in per_day_import_cost_c.items()},
        "per_day_export_earnings_c": {k: round(v, 4) for k, v in per_day_export_earnings_c.items()},
        "per_day_zerohero_status": per_day_zerohero,
        "per_day_super_export_kwh": {k: round(v, 4) for k, v in per_day_super_export_kwh.items()},
        "total_aud_period": round(total_aud, 4),
        "final_engine_state": engine.to_dict(),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    consumption = json.loads(CONSUMPTION_PATH.read_text())
    slots = consumption.get("slots", []) or []
    print(f"loaded {len(slots)} slots from {CONSUMPTION_PATH.name}")

    for label, options in (("zerohero", ZEROHERO_OPTIONS), ("boost", BOOST_OPTIONS)):
        print(f"\n=== driving {label} engine ===")
        result = _drive_engine(options, slots)
        result["_meta"] = {
            "engine_module": "custom_components.pricehawk.tariff_engine.TariffEngine",
            "engine_options_label": label,
            "consumption_fixture": CONSUMPTION_PATH.name,
            "slot_count": len(slots),
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "purpose": "Phase 1 parity snapshot per design doc §H §3. New CDR evaluator must reproduce per_day_cost_aud within 0.5% before legacy tariff_engine.py is deleted.",
            "options": options,
        }
        out = OUT_DIR / f"legacy_{label}_7d.json"
        out.write_text(json.dumps(result, indent=2, default=str))
        print(f"wrote {out.name}")
        print("  per-day totals (AUD):")
        for day, cost in sorted(result["per_day_cost_aud"].items()):
            print(f"    {day}: ${cost:.2f}")
        print(f"  7-day total: ${result['total_aud_period']:.2f}")
        print(
            f"  zerohero status sample: {next(iter(result['per_day_zerohero_status'].items()), 'n/a')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
