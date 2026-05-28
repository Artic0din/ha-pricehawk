"""Pull 7-day half-hourly consumption fixture from HA recorder.

Window per PHASE_0_GROUND_TRUTH.md §5: 2026-05-07 00:00 AEST -> 2026-05-14 00:00 AEST.

Strategy:
  - Pull state history for 3 cumulative `total_increasing` sensors.
  - For each half-hour slot, diff state at slot boundaries -> slot kWh.
  - Save to tests/fixtures/phase0/consumption_7d.json.

Sensors:
  - sensor.power_sync_lifetime_grid_import  (kWh imported from grid, cumulative)
  - sensor.power_sync_lifetime_grid_export  (kWh exported to grid, cumulative)
  - sensor.power_sync_lifetime_solar_energy (kWh solar produced, cumulative)

HA token read from $HA_TOKEN. Token NEVER written to disk.
Output fixture contains kWh values only — no auth material.

Run: python3 scripts/ha_pull_consumption.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

OUT = Path(__file__).parent.parent / "tests" / "fixtures" / "phase0" / "consumption_7d.json"
AEST = ZoneInfo("Australia/Sydney")  # AEDT/AEST aware; May = AEST
UTC = ZoneInfo("UTC")

WINDOW_START = datetime(2026, 5, 7, 0, 0, 0, tzinfo=AEST)
WINDOW_END = datetime(2026, 5, 14, 0, 0, 0, tzinfo=AEST)
SLOT_MINUTES = 30

SENSORS = {
    "grid_import_kwh": "sensor.power_sync_lifetime_grid_import",
    "grid_export_kwh": "sensor.power_sync_lifetime_grid_export",
    "solar_kwh": "sensor.power_sync_lifetime_solar_energy",
}


def _ha_get(path: str) -> object:
    base = os.environ["HA_BASE_URL"]
    token = os.environ["HA_TOKEN"]
    url = f"{base}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def fetch_history(entity_id: str, start_utc: datetime, end_utc: datetime) -> list[dict]:
    start = start_utc.strftime("%Y-%m-%dT%H:%M:%S%z")
    end = end_utc.strftime("%Y-%m-%dT%H:%M:%S%z")
    params = urllib.parse.urlencode(
        {
            "filter_entity_id": entity_id,
            "end_time": end,
            "minimal_response": "true",
            "no_attributes": "true",
        }
    )
    path = f"/api/history/period/{start}?{params}"
    raw = _ha_get(path)
    # API returns [[state1, state2, ...]] — outer list is one entry per entity
    if not raw:
        return []
    inner = raw[0] if isinstance(raw, list) else []
    if not isinstance(inner, list):
        return []
    parsed = []
    for s in inner:
        try:
            v = s.get("state")
            t = s.get("last_changed") or s.get("last_updated")
            if v in (None, "unknown", "unavailable"):
                continue
            kwh = float(v)
            ts = datetime.fromisoformat(t.replace("Z", "+00:00"))
            parsed.append({"ts_utc": ts.astimezone(UTC), "kwh": kwh})
        except (ValueError, TypeError, AttributeError):
            continue
    parsed.sort(key=lambda r: r["ts_utc"])
    return parsed


def value_at(history: list[dict], target_utc: datetime) -> float | None:
    """Linear interpolation. Returns None if target outside history range."""
    if not history:
        return None
    if target_utc < history[0]["ts_utc"]:
        return None
    if target_utc > history[-1]["ts_utc"]:
        return history[-1]["kwh"]
    # Binary search would be faster but 7d × 3 sensors × ~1k records is fine linear.
    for i in range(len(history) - 1):
        a, b = history[i], history[i + 1]
        if a["ts_utc"] <= target_utc <= b["ts_utc"]:
            span = (b["ts_utc"] - a["ts_utc"]).total_seconds()
            if span == 0:
                return a["kwh"]
            t = (target_utc - a["ts_utc"]).total_seconds() / span
            return a["kwh"] + t * (b["kwh"] - a["kwh"])
    return history[-1]["kwh"]


def main() -> int:
    if "HA_TOKEN" not in os.environ or "HA_BASE_URL" not in os.environ:
        print("missing HA_TOKEN or HA_BASE_URL in env", file=sys.stderr)
        return 2

    start_utc = WINDOW_START.astimezone(UTC)
    end_utc = WINDOW_END.astimezone(UTC)
    print(f"window: {WINDOW_START} -> {WINDOW_END}", file=sys.stderr)
    print(f"        ({start_utc} -> {end_utc} UTC)", file=sys.stderr)

    histories: dict[str, list[dict]] = {}
    for label, entity_id in SENSORS.items():
        print(f"fetching {entity_id}...", file=sys.stderr, end=" ")
        hist = fetch_history(entity_id, start_utc, end_utc)
        histories[label] = hist
        if hist:
            print(
                f"{len(hist)} states, range "
                f"{hist[0]['ts_utc'].strftime('%Y-%m-%d %H:%M')} -> "
                f"{hist[-1]['ts_utc'].strftime('%Y-%m-%d %H:%M')}, "
                f"kwh {hist[0]['kwh']:.3f} -> {hist[-1]['kwh']:.3f}",
                file=sys.stderr,
            )
        else:
            print("EMPTY", file=sys.stderr)

    if not all(histories.values()):
        print(
            "ERROR: at least one sensor returned empty history. "
            "HA recorder may not retain data this far back (default 10d retention).",
            file=sys.stderr,
        )
        print("Try a more recent 7d window or extend HA recorder.purge_keep_days.", file=sys.stderr)
        return 1

    # Build half-hour slots
    slots = []
    slot_start = start_utc
    while slot_start < end_utc:
        slot_end = slot_start + timedelta(minutes=SLOT_MINUTES)
        grid_in_start = value_at(histories["grid_import_kwh"], slot_start)
        grid_in_end = value_at(histories["grid_import_kwh"], slot_end)
        grid_out_start = value_at(histories["grid_export_kwh"], slot_start)
        grid_out_end = value_at(histories["grid_export_kwh"], slot_end)
        solar_start = value_at(histories["solar_kwh"], slot_start)
        solar_end = value_at(histories["solar_kwh"], slot_end)
        if None in (
            grid_in_start,
            grid_in_end,
            grid_out_start,
            grid_out_end,
            solar_start,
            solar_end,
        ):
            grid_kwh = 0.0
            export_kwh = 0.0
            solar_kwh_slot = 0.0
        else:
            grid_kwh = max(0.0, grid_in_end - grid_in_start)
            export_kwh = max(0.0, grid_out_end - grid_out_start)
            solar_kwh_slot = max(0.0, solar_end - solar_start)
        local_slot = slot_start.astimezone(AEST)
        slots.append(
            {
                "ts_utc": slot_start.isoformat(timespec="seconds"),
                "ts_local": local_slot.isoformat(timespec="seconds"),
                "local_clock": local_slot.strftime("%H:%M"),
                "grid_import_kwh": round(grid_kwh, 4),
                "grid_export_kwh": round(export_kwh, 4),
                "solar_kwh": round(solar_kwh_slot, 4),
            }
        )
        slot_start = slot_end

    total_import = sum(s["grid_import_kwh"] for s in slots)
    total_export = sum(s["grid_export_kwh"] for s in slots)
    total_solar = sum(s["solar_kwh"] for s in slots)

    out = {
        "_phase0_meta": {
            "label": "Plans A/B/C1/C2 7-day shared consumption",
            "window_local": f"{WINDOW_START.isoformat()} -> {WINDOW_END.isoformat()}",
            "window_tz": "Australia/Sydney (AEST)",
            "slot_minutes": SLOT_MINUTES,
            "slots_count": len(slots),
            "total_grid_import_kwh": round(total_import, 3),
            "total_grid_export_kwh": round(total_export, 3),
            "total_solar_kwh": round(total_solar, 3),
            "source_entity_grid_import": SENSORS["grid_import_kwh"],
            "source_entity_grid_export": SENSORS["grid_export_kwh"],
            "source_entity_solar": SENSORS["solar_kwh"],
            "source_method": "HA recorder /api/history/period, linear interpolation between recorded state changes, slot kWh = state_end - state_start",
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
        "slots": slots,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {OUT}")
    print(f"  slots: {len(slots)} (expected 336 for 7d × 48 slots/day)")
    print(
        f"  totals: import={total_import:.2f} kWh, export={total_export:.2f} kWh, solar={total_solar:.2f} kWh"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
