"""Generate synthetic 24h half-hourly consumption fixtures for DST gates.

Plans D + E per PHASE_0_GROUND_TRUTH.md §5 + design doc §I.6.

Each fixture covers one DST day at NSW (Australia/Sydney):
  - Plan D: 2026-04-06 AEDT->AEST (clocks fall back 02:00 -> 01:00). 25-hour day.
  - Plan E: 2026-10-05 AEST->AEDT (clocks spring forward 02:00 -> 03:00). 23-hour day.

Consumption profile (synthetic but realistic Melbourne residential pattern):
  - Overnight 22:00-07:00: 0.4 kWh/half-hour grid import (fridge + standby)
  - Morning 07:00-09:00: 1.2 kWh/half-hour (water heating + breakfast)
  - Daytime 09:00-14:00: 0.3 kWh/half-hour grid (mostly solar covers load)
  - Solar export 09:00-15:00: 1.5 kWh/half-hour
  - Afternoon 14:00-18:00: 0.5 kWh/half-hour
  - Evening 18:00-22:00: 1.0 kWh/half-hour (cooking + heating peak)

Uses zoneinfo.ZoneInfo for DST handling. Outputs include both
UTC timestamps (canonical) and local Australia/Sydney clock times.

Run: python3 scripts/gen_dst_fixtures.py
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

OUT_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "phase0"
SYDNEY = ZoneInfo("Australia/Sydney")
UTC = ZoneInfo("UTC")


def consumption_for_local_hour(hour: int) -> tuple[float, float]:
    """Return (grid_import_kwh, solar_export_kwh) for one half-hour slot.

    Caller supplies the local-clock hour (0-23) and gets the half-hour
    profile slice. We use the same profile shape regardless of DST
    transition; the evaluator's job is to walk the timeline correctly,
    not to model behavioural differences on DST days.
    """
    if 22 <= hour or hour < 7:
        return 0.4, 0.0
    if 7 <= hour < 9:
        return 1.2, 0.0
    if 9 <= hour < 14:
        return 0.3, 1.5
    if 14 <= hour < 15:
        return 0.3, 1.5
    if 15 <= hour < 18:
        return 0.5, 0.5
    if 18 <= hour < 22:
        return 1.0, 0.0
    return 0.0, 0.0


def generate_fixture(local_date: str, label: str, transition: str) -> dict:
    """Walk wall-clock 30-min steps from 00:00 local to 24:00 local.

    On DST-forward day (October): the 02:00-03:00 hour does NOT exist.
    Stepping by 30min in Sydney tz, datetime arithmetic naturally skips
    the gap. Result: 23 hour day = 46 half-hour slots.

    On DST-backward day (April): the 02:00-03:00 hour exists TWICE
    (once as AEDT, once as AEST). Naive datetime stepping in local
    tz would loop forever or double-count. Solution: do all math in
    UTC, then label each slot with its local clock for hand-calc.
    Result: 25 hour day = 50 half-hour slots.
    """
    start_local = datetime.fromisoformat(f"{local_date}T00:00:00").replace(tzinfo=SYDNEY)
    end_local = datetime.fromisoformat(f"{local_date}T00:00:00").replace(tzinfo=SYDNEY) + timedelta(days=1)

    start_utc = start_local.astimezone(UTC)
    end_utc = end_local.astimezone(UTC)

    slots = []
    cur_utc = start_utc
    step = timedelta(minutes=30)
    while cur_utc < end_utc:
        local_clock = cur_utc.astimezone(SYDNEY)
        # For consumption profile we use the local-clock hour. This means
        # on the DST-backward day the 02:00-03:00 hour is duplicated and
        # gets the overnight profile both times (correct — clocks fall back
        # but residents are still asleep, so same load shape).
        hour = local_clock.hour
        grid_kwh, solar_kwh = consumption_for_local_hour(hour)
        offset = local_clock.utcoffset()
        offset_h = offset.total_seconds() / 3600 if offset is not None else 0.0
        slots.append({
            "ts_utc": cur_utc.isoformat(timespec="seconds"),
            "ts_local": local_clock.isoformat(timespec="seconds"),
            "local_clock": local_clock.strftime("%H:%M"),
            "local_offset": offset_h,
            "grid_import_kwh": grid_kwh,
            "solar_export_kwh": solar_kwh,
        })
        cur_utc += step

    total_grid = sum(s["grid_import_kwh"] for s in slots)
    total_solar = sum(s["solar_export_kwh"] for s in slots)
    hours_covered = (end_utc - start_utc).total_seconds() / 3600

    return {
        "_phase0_meta": {
            "label": label,
            "transition": transition,
            "local_date": local_date,
            "tz": "Australia/Sydney",
            "slots_count": len(slots),
            "wall_clock_hours": hours_covered,
            "total_grid_import_kwh": round(total_grid, 4),
            "total_solar_export_kwh": round(total_solar, 4),
            "profile_source": "synthetic residential pattern per scripts/gen_dst_fixtures.py",
            "test_assertion": "evaluator total cost matches hand-calc within $0.05",
        },
        "slots": slots,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 2026-04-05 (Sun) = DST backward in NSW (AEDT 03:00 -> AEST 02:00, gain 1h).
    # Note: design doc + checkpoint claimed Apr 6, but verified via
    # zoneinfo that the transition is the first Sunday (Apr 5). Apr 6 is the
    # day after. Correction logged in DECISIONS.md as D-P0-4.
    april = generate_fixture(
        local_date="2026-04-05",
        label="Plan D — NSW DST backward (gain 1h)",
        transition="AEDT_to_AEST",
    )
    out_april = OUT_DIR / "consumption_dst_april_2026-04-05.json"
    out_april.write_text(json.dumps(april, indent=2))
    meta = april["_phase0_meta"]
    print(f"wrote {out_april.name}: {meta['slots_count']} slots, "
          f"{meta['wall_clock_hours']:.1f}h wall-clock, "
          f"grid={meta['total_grid_import_kwh']} kWh, solar={meta['total_solar_export_kwh']} kWh")

    # 2026-10-04 (Sun) = DST forward in NSW (AEST 02:00 -> AEDT 03:00, lose 1h).
    # Design doc + checkpoint claimed Oct 5; correction logged D-P0-4.
    october = generate_fixture(
        local_date="2026-10-04",
        label="Plan E — NSW DST forward (lose 1h)",
        transition="AEST_to_AEDT",
    )
    out_october = OUT_DIR / "consumption_dst_october_2026-10-04.json"
    out_october.write_text(json.dumps(october, indent=2))
    meta = october["_phase0_meta"]
    print(f"wrote {out_october.name}: {meta['slots_count']} slots, "
          f"{meta['wall_clock_hours']:.1f}h wall-clock, "
          f"grid={meta['total_grid_import_kwh']} kWh, solar={meta['total_solar_export_kwh']} kWh")

    # Sanity assertions on slot counts
    assert len(april["slots"]) == 50, f"April should be 50 half-hour slots (25h), got {len(april['slots'])}"
    assert len(october["slots"]) == 46, f"October should be 46 half-hour slots (23h), got {len(october['slots'])}"
    print("\nslot count sanity: PASS (50 for April back, 46 for October forward)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
