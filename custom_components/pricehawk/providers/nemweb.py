"""NEMWeb DISPATCH wholesale-price source for PriceHawk (Phase 7 / PR-3).

Public AEMO endpoint — no API key required. NEM-only (NSW1, QLD1, SA1, TAS1,
VIC1). WEM is rejected; use OpenElectricity for WA.

Wraps ``custom_components.pricehawk.aemo_api.fetch_current_rrp`` so we inherit
its retry + HTTPS + case-sensitive-URL discipline. AEMO is decommissioning HTTP
on 2026-04-07 per their NEMWeb page; the underlying ``aemo_api`` already uses
HTTPS so no migration cost here.

Not wired into the coordinator or config flow in this PR — that's 07-02b.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Final
from zoneinfo import ZoneInfo

import aiohttp

from ..aemo_api import VALID_REGIONS, fetch_current_rrp
from .openelectricity import WholesalePrice

_LOGGER = logging.getLogger(__name__)

_ATTRIBUTION: Final[str] = "Wholesale price data: AEMO NEMWeb DISPATCH (public)"

# AEMO publishes NEM dispatch timestamps in "NEM time" = AEST year-round
# (no DST). Anchor to Australia/Brisbane (QLD, no DST observed) rather than
# Australia/Sydney (NSW, DST applied) — Sydney-anchored parsing produces a
# 1-hour error every dispatch row during AEDT (Oct–Apr in NSW/VIC/TAS).
_NEM_TIMEZONE: Final[ZoneInfo] = ZoneInfo("Australia/Brisbane")

# Bounded fetch — directory listing + ZIP fetch + parse, plus aemo_api's
# 3-attempt retry budget (15s + 20s per attempt). 45s is the outer ceiling.
_FETCH_TIMEOUT_SECONDS: Final[float] = 45.0


class NEMWebPriceSource:
    """Anonymous async client for AEMO NEMWeb DISPATCH wholesale prices.

    Same contract as OpenElectricityPriceSource minus the API-key surface.
    No __repr__ redaction needed (no secret). No ConfigEntryAuthFailed path
    (anonymous endpoint).
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._last_good_by_region: dict[str, WholesalePrice] = {}

    @staticmethod
    def _validate_region(region: str) -> None:
        if region == "WEM":
            raise ValueError(
                "WEM is not supported by NEMWeb — NEMWeb DISPATCH is "
                "NEM-only. Use OpenElectricity (provides WEM coverage) "
                "for Western Australia."
            )
        if region not in VALID_REGIONS:
            raise ValueError(f"Unknown NEM region {region!r}; expected one of {VALID_REGIONS}")

    async def fetch_current_price(self, region: str) -> WholesalePrice | None:
        """Fetch the latest 5-minute dispatch RRP for the NEM region.

        Returns None on transient errors; caller can fall back to last_good.
        Raises ValueError on unknown/WEM region before any network call.
        """
        self._validate_region(region)

        try:
            async with asyncio.timeout(_FETCH_TIMEOUT_SECONDS):
                raw = await fetch_current_rrp(self._session, region)
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "NEMWeb fetch timed out after %.0fs for region %s",
                _FETCH_TIMEOUT_SECONDS,
                region,
            )
            return None

        if raw is None:
            # aemo_api logged the underlying reason at WARNING already.
            return None

        rrp_c_kwh, settlement_date = raw
        try:
            ts_utc = _parse_settlement_date(settlement_date)
        except ValueError as exc:
            _LOGGER.warning(
                "NEMWeb settlement-date parse failed for %s (%r): %s",
                region,
                settlement_date,
                exc,
            )
            return None

        # aemo_api emits RRP as c/kWh (already divided by 10).
        # WholesalePrice contract is $/MWh — invert: c/kWh * 10 = $/MWh.
        price = WholesalePrice(
            price_aud_per_mwh=rrp_c_kwh * 10.0,
            interval_end_utc=ts_utc,
            region=region,
            attribution=_ATTRIBUTION,
        )
        self._last_good_by_region[region] = price
        return price

    def last_good(self, region: str) -> WholesalePrice | None:
        return self._last_good_by_region.get(region)


def _parse_settlement_date(s: str) -> datetime:
    """Parse AEMO settlement date "YYYY/MM/DD HH:MM:SS" as NEM time → UTC.

    AEMO publishes settlement dates in NEM time (AEST year-round, no DST)
    with no timezone marker. We anchor to Australia/Brisbane (QLD, no DST)
    and convert to UTC.
    """
    s = s.strip().strip('"')
    if not s:
        raise ValueError("empty settlement date string")
    dt_local = datetime.strptime(s, "%Y/%m/%d %H:%M:%S")
    dt_aware = dt_local.replace(tzinfo=_NEM_TIMEZONE)
    return dt_aware.astimezone(timezone.utc)
