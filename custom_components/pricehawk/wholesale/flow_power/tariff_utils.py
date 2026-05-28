"""Utility wrapper around the aemo_to_tariff library for Flow Power v2 tariff integration.

Provides functions to look up network tariff rates, compute daily averages,
and discover available tariff codes — all while suppressing the library's
internal print() statements.

Vendored from upstream commit ``3c2a9bb`` with two FORK(#186) modifications
(see ``NOTICES.md``): :func:`compute_avg_daily_tariff` uses the DNSP's IANA
timezone instead of hard-coded AEST, and :func:`get_tariff_codes_for_network`
falls back to ``get_tariffs()`` / versioned-schedule lookups when newer
``aemo_to_tariff`` releases stop exposing the top-level ``tariffs`` dict.
"""
from __future__ import annotations

import importlib
import io
import logging
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)


@contextmanager
def _suppress_stdout():
    """Silence print() statements in the aemo_to_tariff library."""
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        yield
    finally:
        sys.stdout = old_stdout


def get_network_tariff_rate(
    dt: datetime,
    network: str,
    tariff_code: str,
) -> float | None:
    """Return the network tariff component in c/kWh for a given time.

    Calls spot_to_tariff with rrp=0 so the result is *only* the network
    charge (no wholesale component).  Loss factors are set to 1.0 because
    the Flow Power formula applies its own GST multiplier.

    Args:
        dt: The timestamp to look up (timezone-aware preferred).
        network: aemo_to_tariff network parameter (e.g. "sapn", "victoria").
        tariff_code: Tariff code (e.g. "RESELE", "6900").

    Returns:
        Network tariff rate in c/kWh, or None on error.
    """
    try:
        from aemo_to_tariff import spot_to_tariff

        with _suppress_stdout():
            rate = spot_to_tariff(
                interval_time=dt,
                network=network,
                tariff=tariff_code,
                rrp=0,
                dlf=1.0,
                mlf=1.0,
                market=1.0,
            )
        return float(rate)
    except Exception as err:
        _LOGGER.warning(
            "Failed to get network tariff rate for %s/%s at %s: %s",
            network, tariff_code, dt, err,
        )
        return None


def _network_timezone(network: str) -> ZoneInfo:
    """Return the IANA timezone for an aemo_to_tariff network parameter.

    FORK(#186): replaces upstream's hard-coded UTC+10. Falls back to
    Australia/Brisbane (no DST) if the network isn't in NETWORK_TIMEZONE —
    that's the safe default since QLD doesn't observe daylight saving and
    matches upstream's original AEST behaviour for unknown networks.
    """
    from .const import NETWORK_TIMEZONE

    tz_name = NETWORK_TIMEZONE.get(network, "Australia/Brisbane")
    return ZoneInfo(tz_name)


def compute_avg_daily_tariff(
    network: str,
    tariff_code: str,
) -> float | None:
    """Compute the 24-hour average of the network tariff rate.

    Samples all 48 half-hour slots (anchored at the DNSP's local midnight)
    and averages them. This value is subtracted in the v2 PEA formula so
    that the network tariff component nets to zero over a full day.

    FORK(#186): the 48-slot sweep is anchored in the DNSP's local timezone
    rather than fixed AEST. Without this fork, SA (+9:30 / +10:30 DST),
    NSW/VIC/TAS (DST), and the 1 July tariff transition all sample the
    wrong half-hour windows and bias the average.

    Args:
        network: aemo_to_tariff network parameter.
        tariff_code: Tariff code.

    Returns:
        Average daily tariff in c/kWh, or None on error.
    """
    try:
        from aemo_to_tariff import spot_to_tariff

        tz = _network_timezone(network)  # FORK(#186): was timezone(timedelta(hours=10))
        now = datetime.now(tz=tz)
        base_date = now.replace(hour=0, minute=0, second=0, microsecond=0)

        total = 0.0
        count = 0
        for slot in range(48):
            slot_time = base_date + timedelta(minutes=slot * 30)
            with _suppress_stdout():
                rate = spot_to_tariff(
                    interval_time=slot_time,
                    network=network,
                    tariff=tariff_code,
                    rrp=0,
                    dlf=1.0,
                    mlf=1.0,
                    market=1.0,
                )
            total += float(rate)
            count += 1

        if count == 0:
            return None

        avg = round(total / count, 4)
        _LOGGER.debug(
            "Average daily tariff for %s/%s: %.4f c/kWh (%d slots)",
            network, tariff_code, avg, count,
        )
        return avg
    except Exception as err:
        _LOGGER.warning(
            "Failed to compute avg daily tariff for %s/%s: %s",
            network, tariff_code, err,
        )
        return None


def _discover_tariff_codes(mod: Any) -> list[str]:
    """Pull tariff codes out of an aemo_to_tariff DNSP module.

    FORK(#186): upstream reads only ``mod.tariffs`` which newer releases
    no longer populate at top level (schedules moved to ``get_tariffs()``
    or year-versioned dicts like ``tariffs_2025_26``). Try each in order.
    """
    tariffs = getattr(mod, "tariffs", None)
    if isinstance(tariffs, dict) and tariffs:
        return [str(code) for code in tariffs]

    get_tariffs = getattr(mod, "get_tariffs", None)
    if callable(get_tariffs):
        try:
            result = get_tariffs()
            if isinstance(result, dict) and result:
                return [str(code) for code in result]
        except Exception:  # noqa: BLE001 — best-effort fallback per FORK(#186) rationale
            pass

    for attr in sorted(dir(mod), reverse=True):
        if attr.startswith("tariffs_"):
            candidate = getattr(mod, attr, None)
            if isinstance(candidate, dict) and candidate:
                return [str(code) for code in candidate]

    return []


def get_tariff_codes_for_network(network_display: str) -> list[str]:
    """Return available tariff codes for a DNSP display name.

    Imports the appropriate aemo_to_tariff module and discovers tariff codes
    via :func:`_discover_tariff_codes` (FORK(#186): tolerates the multiple
    schedule-export shapes recent ``aemo_to_tariff`` releases use).

    Args:
        network_display: Display name (e.g. "SAPN", "Energex").

    Returns:
        List of tariff code strings, or empty list on error.
    """
    from .const import NETWORK_MODULE_NAME

    module_name = NETWORK_MODULE_NAME.get(network_display)
    if not module_name:
        _LOGGER.warning("No module mapping for network: %s", network_display)
        return []

    try:
        mod = importlib.import_module(f"aemo_to_tariff.{module_name}")
        return _discover_tariff_codes(mod)
    except Exception as err:
        _LOGGER.warning(
            "Failed to load tariff codes for %s (module=%s): %s",
            network_display, module_name, err,
        )
        return []


def get_networks_for_region(region: str) -> list[str]:
    """Return DNSP display names available in a NEM region.

    Args:
        region: NEM region code (e.g. "SA1", "NSW1").

    Returns:
        List of display name strings.
    """
    from .const import REGION_NETWORKS

    return REGION_NETWORKS.get(region, [])
