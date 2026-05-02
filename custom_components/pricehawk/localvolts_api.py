"""LocalVolts REST API client.

Public API at https://api.localvolts.com/v1/. Auth via two headers
(``Authorization: apikey <key>`` and ``partner: <partner_id>``) plus the
NMI as a query string parameter.

This client only implements the read endpoints PriceHawk needs:
``GET /customer/interval`` for the most-recent finalised 5-minute
interval. The 5-min intervals are aggregated into a 30-min volume-
weighted-average price for parity with Amber's resolution.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

LOCALVOLTS_API_BASE = "https://api.localvolts.com/v1"

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2  # seconds


class LocalVoltsAPIError(Exception):
    """Raised when the LocalVolts API returns an unrecoverable error."""


async def fetch_recent_intervals(
    session: aiohttp.ClientSession,
    api_key: str,
    partner_id: str,
    nmi: str,
    minutes_back: int = 30,
) -> list[dict[str, Any]]:
    """Fetch finalised 5-minute intervals for the last ``minutes_back`` mins.

    Returns intervals where ``quality == "exp"`` (final/exposed). Earlier
    intervals with non-final quality are skipped.

    Raises LocalVoltsAPIError on auth failure or persistent server errors.
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=minutes_back + 5)
    params = {
        "NMI": nmi,
        "from": start.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "to": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    headers = {
        "Authorization": f"apikey {api_key}",
        "partner": partner_id,
        "Accept": "application/json",
    }
    url = f"{LOCALVOLTS_API_BASE}/customer/interval"

    for attempt in range(_MAX_RETRIES):
        try:
            async with session.get(
                url,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if not isinstance(data, list):
                        return []
                    return [
                        iv for iv in data if iv.get("quality") == "exp"
                    ]

                if resp.status in (401, 403):
                    raise LocalVoltsAPIError(
                        f"LocalVolts auth failed ({resp.status})"
                    )

                if resp.status == 429 or resp.status >= 500:
                    delay = _RETRY_BASE_DELAY * (2**attempt)
                    _LOGGER.warning(
                        "LocalVolts API %s, retry in %ds (attempt %d/%d)",
                        resp.status, delay, attempt + 1, _MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    continue

                _LOGGER.warning(
                    "LocalVolts API returned status %s", resp.status
                )
                return []

        except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError) as err:
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_BASE_DELAY * (2**attempt)
                _LOGGER.warning(
                    "LocalVolts request failed (%d/%d): %s, retry in %ds",
                    attempt + 1, _MAX_RETRIES, err, delay,
                )
                await asyncio.sleep(delay)
            else:
                _LOGGER.warning("LocalVolts request failed: %s", err)
                return []

    return []


def aggregate_to_half_hour(
    intervals: list[dict[str, Any]],
) -> tuple[float | None, float | None]:
    """Volume-weighted average of (import_c_kwh, export_c_kwh) over the
    most-recent 30-minute window.

    LocalVolts publishes 5-min intervals with ``costsAllVarRate`` (import
    c/kWh) and ``earningsAllVarRate`` (export c/kWh) plus per-interval
    ``loadKwh``. When ``loadKwh`` is missing we fall back to a simple
    arithmetic mean.
    """
    if not intervals:
        return None, None

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    recent = []
    for iv in intervals:
        end_str = iv.get("intervalEnd") or iv.get("endTime")
        if not end_str:
            continue
        try:
            end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if end >= cutoff:
            recent.append(iv)

    if not recent:
        return None, None

    total_load = sum(float(iv.get("loadKwh", 0.0) or 0.0) for iv in recent)
    if total_load > 0:
        wsum_imp = sum(
            float(iv.get("costsAllVarRate", 0.0) or 0.0)
            * float(iv.get("loadKwh", 0.0) or 0.0)
            for iv in recent
        )
        wsum_exp = sum(
            float(iv.get("earningsAllVarRate", 0.0) or 0.0)
            * float(iv.get("loadKwh", 0.0) or 0.0)
            for iv in recent
        )
        return wsum_imp / total_load, wsum_exp / total_load

    # Simple mean fallback
    n = len(recent)
    imp = sum(float(iv.get("costsAllVarRate", 0.0) or 0.0) for iv in recent) / n
    exp = sum(
        float(iv.get("earningsAllVarRate", 0.0) or 0.0) for iv in recent
    ) / n
    return imp, exp
