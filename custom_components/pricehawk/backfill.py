"""Backfill PriceHawk daily cost history from HA recorder + Amber API.

Reads historical grid power states from HA's history API, pairs each
reading with the Amber price at that time, and computes daily costs
for both Amber and GloBird using the user's configured rates.

Pure Python -- no Home Assistant imports needed for core logic.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from .tariff_engine import get_current_tou_period, get_stepped_import_rate

_LOGGER = logging.getLogger(__name__)

# Maximum delta between readings before clamping (matches live calculator)
_GAP_PROTECTION_MAX_DELTA_H = 0.1  # 6 minutes


def fetch_amber_price_history(
    api_key: str,
    site_id: str,
    start_time: datetime,
    end_time: datetime,
) -> list[dict[str, Any]]:
    """Fetch price history from Amber API.

    Amber API: GET /v1/sites/{site_id}/prices?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD
    Max 7 days per request, 90 days max history.

    Uses urllib (no external dependency on requests).

    Args:
        api_key: Amber API bearer token.
        site_id: Amber site identifier.
        start_time: Start of the period to fetch.
        end_time: End of the period to fetch.

    Returns:
        List of price interval dicts from Amber API.
    """
    prices: list[dict[str, Any]] = []
    current = start_time

    while current < end_time:
        chunk_end = min(current + timedelta(days=7), end_time)

        # AEST date formatting — never use toISOString() equivalent
        start_y = current.year
        start_m = str(current.month).zfill(2)
        start_d = str(current.day).zfill(2)
        end_y = chunk_end.year
        end_m = str(chunk_end.month).zfill(2)
        end_d = str(chunk_end.day).zfill(2)

        url = (
            f"https://api.amber.com.au/v1/sites/{site_id}/prices"
            f"?startDate={start_y}-{start_m}-{start_d}"
            f"&endDate={end_y}-{end_m}-{end_d}"
        )

        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    prices.extend(data)
                else:
                    _LOGGER.warning(
                        "Amber API returned %s for %s to %s",
                        resp.status,
                        f"{start_y}-{start_m}-{start_d}",
                        f"{end_y}-{end_m}-{end_d}",
                    )
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
            _LOGGER.warning(
                "Amber API request failed for %s to %s: %s",
                f"{start_y}-{start_m}-{start_d}",
                f"{end_y}-{end_m}-{end_d}",
                err,
            )

        current = chunk_end

    _LOGGER.info("Fetched %d Amber price intervals", len(prices))
    return prices


def _build_amber_price_index(
    amber_prices: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Build a lookup from channel type to sorted list of price intervals.

    Each interval has startTime and endTime as ISO strings and perKwh in c/kWh.

    Returns:
        {"general": [sorted intervals...], "feedIn": [sorted intervals...]}
    """
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for interval in amber_prices:
        channel = interval.get("channelType", "")
        if channel not in ("general", "feedIn"):
            continue
        start_str = interval.get("startTime", "")
        end_str = interval.get("endTime", "")
        per_kwh = interval.get("perKwh")
        if not start_str or not end_str or per_kwh is None:
            continue

        # Parse ISO timestamps (Amber returns UTC offsets like +10:00)
        try:
            start_dt = datetime.fromisoformat(start_str)
            end_dt = datetime.fromisoformat(end_str)
        except ValueError:
            continue

        index[channel].append({
            "start": start_dt,
            "end": end_dt,
            "perKwh": float(per_kwh),
        })

    # Sort by start time for binary search
    for channel in index:
        index[channel].sort(key=lambda x: x["start"])

    return dict(index)


def _find_amber_rate(
    intervals: list[dict[str, Any]],
    timestamp: datetime,
) -> float | None:
    """Find the Amber rate (c/kWh) for a given timestamp using linear scan.

    Returns None if no matching interval found.
    """
    for interval in intervals:
        if interval["start"] <= timestamp < interval["end"]:
            return interval["perKwh"]
    return None


def _parse_history_states(
    history_states: list[dict[str, Any]],
) -> list[tuple[datetime, float]]:
    """Parse HA history states into sorted (timestamp, power_w) tuples.

    Filters out unavailable/unknown states and converts kW to W.
    """
    readings: list[tuple[datetime, float]] = []

    for state in history_states:
        raw_state = state.get("state")
        if raw_state is None:
            continue

        # Handle both numeric and string states
        try:
            power_val = float(raw_state)
        except (ValueError, TypeError):
            continue

        # Convert kW to W if needed
        unit = state.get("unit", state.get("unit_of_measurement", "W"))
        if isinstance(unit, str) and unit.lower() == "kw":
            power_val *= 1000.0

        # Parse timestamp
        ts_str = state.get("last_changed", "")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
        except ValueError:
            continue

        readings.append((ts, power_val))

    readings.sort(key=lambda x: x[0])
    return readings


def _format_date(d: datetime) -> str:
    """Format a datetime to YYYY-MM-DD without TZ issues (AEST safe)."""
    y = d.year
    m = str(d.month).zfill(2)
    day = str(d.day).zfill(2)
    return f"{y}-{m}-{day}"


def backfill_from_history(
    history_states: list[dict[str, Any]],
    amber_prices: list[dict[str, Any]],
    globird_options: dict[str, Any],
    amber_network_daily_c: float,
    amber_subscription_daily_c: float,
    existing_history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compute daily costs from HA recorder history and merge with existing data.

    Args:
        history_states: List of {state, last_changed, unit} from HA history API.
            state is the grid power value (W or kW, check unit).
            last_changed is ISO timestamp.
        amber_prices: List of {channelType, perKwh, startTime, endTime} from Amber API.
            perKwh is c/kWh. channelType is "general" or "feedIn".
        globird_options: User's tariff config (import_tariff, export_tariff, etc.)
        amber_network_daily_c: Amber daily network charge in cents.
        amber_subscription_daily_c: Amber daily subscription fee in cents.
        existing_history: Current daily_cost_history list to merge with.

    Returns:
        Merged daily_cost_history list (max 180 entries), sorted by date.
        Each entry: {"date": "YYYY-MM-DD", "amber": X.XX, "globird": Y.YY}
    """
    # 1. Parse history states into time-series
    readings = _parse_history_states(history_states)
    if not readings:
        _LOGGER.warning("No valid power readings found in history data")
        return list(existing_history)

    # 2. Build Amber price lookup
    price_index = _build_amber_price_index(amber_prices)
    general_intervals = price_index.get("general", [])
    feedin_intervals = price_index.get("feedIn", [])

    # 3. Extract GloBird tariff config
    import_tariff = globird_options.get("import_tariff", {})
    export_tariff = globird_options.get("export_tariff", {})
    supply_charge_c = globird_options.get("daily_supply_charge", 0.0)

    # 4. Group readings by date and compute daily costs
    daily_amber: dict[str, float] = defaultdict(float)
    daily_globird: dict[str, float] = defaultdict(float)
    daily_globird_import_kwh: dict[str, float] = defaultdict(float)
    dates_seen: set[str] = set()

    for i in range(1, len(readings)):
        prev_ts, prev_power = readings[i - 1]
        curr_ts, curr_power = readings[i]

        # Use previous reading's power over the interval (zero-order hold)
        power_w = prev_power
        date_str = _format_date(prev_ts)
        dates_seen.add(date_str)

        # Compute delta hours with gap protection
        delta_s = (curr_ts - prev_ts).total_seconds()
        delta_h = delta_s / 3600.0
        if delta_h <= 0:
            continue
        delta_h = min(delta_h, _GAP_PROTECTION_MAX_DELTA_H)

        # Split into import/export (positive = import, negative = export)
        grid_kw = power_w / 1000.0
        import_kw = max(0.0, grid_kw)
        export_kw = max(0.0, -grid_kw)
        import_kwh = import_kw * delta_h
        export_kwh = export_kw * delta_h

        # --- Amber cost for this interval ---
        if import_kwh > 0:
            rate = _find_amber_rate(general_intervals, prev_ts)
            if rate is not None:
                daily_amber[date_str] += import_kwh * rate  # c

        if export_kwh > 0:
            rate = _find_amber_rate(feedin_intervals, prev_ts)
            if rate is not None:
                # Feed-in rates are negative (credit) in Amber API
                daily_amber[date_str] -= export_kwh * abs(rate)  # c (credit)

        # --- GloBird cost for this interval ---
        if import_kwh > 0:
            daily_globird_import_kwh[date_str] += import_kwh

            if import_tariff.get("type") == "tou":
                _, rate_c = get_current_tou_period(
                    import_tariff["periods"], prev_ts
                )
            elif import_tariff.get("type") == "flat_stepped":
                rate_c = get_stepped_import_rate(
                    import_tariff, daily_globird_import_kwh[date_str]
                )
            else:
                rate_c = 0.0

            daily_globird[date_str] += import_kwh * rate_c  # c

        if export_kwh > 0:
            if export_tariff.get("type") == "tou":
                _, rate_c = get_current_tou_period(
                    export_tariff["periods"], prev_ts
                )
            else:
                rate_c = 0.0

            daily_globird[date_str] -= export_kwh * rate_c  # c (credit)

    # 5. Add daily fixed charges and convert to AUD
    daily_costs: dict[str, dict[str, float]] = {}
    for date_str in sorted(dates_seen):
        amber_energy_c = daily_amber.get(date_str, 0.0)
        amber_total_c = amber_energy_c + amber_network_daily_c + amber_subscription_daily_c
        amber_total_aud = amber_total_c / 100.0

        globird_energy_c = daily_globird.get(date_str, 0.0)
        globird_total_c = globird_energy_c + supply_charge_c
        globird_total_aud = globird_total_c / 100.0

        daily_costs[date_str] = {
            "date": date_str,
            "amber": round(amber_total_aud, 2),
            "globird": round(globird_total_aud, 2),
        }

    # 6. Merge with existing history
    # Overwrite existing days if backfill has real energy data and existing
    # entry looks stale (amber == globird means only daily charges, no energy)
    existing_by_date: dict[str, dict[str, Any]] = {}
    for entry in existing_history:
        entry_date = entry.get("date", "")
        if entry_date:
            existing_by_date[entry_date] = entry

    # Backfill always overwrites — backfill data is computed from recorder
    # history + Amber API and is more accurate than stale coordinator data
    new_count = 0
    replaced_count = 0
    for date_str, backfill_entry in daily_costs.items():
        if date_str in existing_by_date:
            replaced_count += 1
        else:
            new_count += 1
        existing_by_date[date_str] = backfill_entry

    merged = list(existing_by_date.values())

    # Sort by date and cap at 180 entries
    merged.sort(key=lambda x: x.get("date", ""))
    if len(merged) > 180:
        merged = merged[-180:]

    _LOGGER.info(
        "Backfill: %d new days, %d replaced (stale), merged total: %d days",
        new_count,
        replaced_count,
        len(merged),
    )

    return merged
