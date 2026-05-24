"""AEMO wholesale RRP (regional reference price) client.

Pulls the most-recent 5-minute dispatch RRP for a NEM region directly
from NEMWeb (the public AEMO data portal). No API key required.

The dispatch report is a ZIP of NEM-format CSVs published every 5
minutes at:

    https://nemweb.com.au/Reports/Current/DispatchIS_Reports/

Each ZIP contains a CSV with ``D,DISPATCH,REGIONSUM,3,...`` rows whose
RRP column carries the regional reference price in $/MWh. We convert
that to c/kWh (divide by 10) before returning.

Used as the wholesale price source for Flow Power (and any future
wholesale-pass-through provider) so we don't have to rely on Amber's
``spotPerKwh`` field which bundles network charges.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import zipfile
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

NEMWEB_DISPATCH_URL = (
    "https://nemweb.com.au/Reports/Current/DispatchIS_Reports/"
)

# Filenames look like PUBLIC_DISPATCHIS_YYYYMMDDHHMM_NNNNNNNNNNNNN.zip,
# with an optional historical `_LEGACY` suffix that AEMO retired in May 2026
# (live UAT 2026-05-23 — directory now serves files without the suffix).
# Match either shape so we don't drop dispatch data after the rename.
#
# Live UAT 2026-05-24: the directory listing returns the filenames
# **with the full server path prefix**, e.g.
#   ``HREF="/Reports/CURRENT/DispatchIS_Reports/PUBLIC_DISPATCHIS_..._.zip"``
# The prior pattern required PUBLIC_DISPATCHIS to sit immediately after
# the opening quote, so it silently matched **zero** files for two days
# even after PR #107 made the suffix optional. ``[^"]*?`` (non-greedy)
# accepts an arbitrary path prefix between the quote and the filename
# while still capturing only the filename in group 1, so the rest of
# ``_pick_latest_dispatch_file`` (lexical sort on the YYYYMMDDHHMM
# prefix) keeps working unchanged.
_FILE_RE = re.compile(
    r'href="[^"]*?(PUBLIC_DISPATCHIS_\d{12}_\d+(?:_LEGACY)?\.zip)"',
    re.IGNORECASE,
)

VALID_REGIONS = ("NSW1", "QLD1", "VIC1", "SA1", "TAS1")

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2  # seconds


class AEMOAPIError(Exception):
    """Raised when the AEMO/NEMWeb endpoint returns an unrecoverable error."""


async def fetch_current_rrp(
    session: aiohttp.ClientSession, region: str
) -> tuple[float, str] | None:
    """Fetch the most-recent dispatch RRP for ``region`` in c/kWh.

    Returns a tuple of ``(rrp_c_kwh, settlement_date)`` on success or
    ``None`` if the endpoint is unavailable or no row was found.
    """
    if region not in VALID_REGIONS:
        raise ValueError(f"Invalid NEM region: {region}")

    listing = await _fetch_directory_listing(session)
    if not listing:
        return None

    latest_file = _pick_latest_dispatch_file(listing)
    if latest_file is None:
        _LOGGER.warning("AEMO directory listing contained no dispatch files")
        return None

    payload = await _fetch_zip(session, NEMWEB_DISPATCH_URL + latest_file)
    if payload is None:
        return None

    return _parse_dispatch_zip(payload, region)


# -- internal helpers --------------------------------------------------------


async def _fetch_directory_listing(
    session: aiohttp.ClientSession,
) -> str | None:
    for attempt in range(_MAX_RETRIES):
        try:
            async with session.get(
                NEMWEB_DISPATCH_URL,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "PriceHawk/1.4"},
            ) as resp:
                if resp.status == 200:
                    return await resp.text()
                if resp.status >= 500:
                    delay = _RETRY_BASE_DELAY * (2**attempt)
                    await asyncio.sleep(delay)
                    continue
                _LOGGER.warning(
                    "AEMO listing returned status %s", resp.status
                )
                return None
        except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError) as err:
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
                continue
            _LOGGER.warning("AEMO listing fetch failed: %s", err)
    return None


def _pick_latest_dispatch_file(html: str) -> str | None:
    matches = _FILE_RE.findall(html)
    if not matches:
        return None
    # Filenames are PUBLIC_DISPATCHIS_YYYYMMDDHHMM_NNN[_LEGACY].zip.
    # The `_LEGACY` suffix was dropped from the NEMWeb directory listing in May 2026;
    # the regex accepts both shapes. Lexical sort still puts the most recent timestamp
    # last because the YYYYMMDDHHMM prefix sits at a fixed position regardless of shape.
    return sorted(matches)[-1]


async def _fetch_zip(
    session: aiohttp.ClientSession, url: str
) -> bytes | None:
    for attempt in range(_MAX_RETRIES):
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=20),
                headers={"User-Agent": "PriceHawk/1.4"},
            ) as resp:
                if resp.status == 200:
                    return await resp.read()
                if resp.status >= 500:
                    await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
                    continue
                _LOGGER.warning(
                    "AEMO ZIP fetch returned status %s for %s",
                    resp.status,
                    url,
                )
                return None
        except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError) as err:
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
                continue
            _LOGGER.warning("AEMO ZIP fetch failed: %s", err)
    return None


def _parse_dispatch_zip(
    payload: bytes, region: str
) -> tuple[float, str] | None:
    """Extract the RRP for ``region`` from a NEMWeb dispatch ZIP.

    The CSV uses the AEMO C/I/D row format: schema rows start with
    ``I,DISPATCH,REGIONSUM,3,...`` and data rows with ``D,DISPATCH,
    REGIONSUM,3,...``. Columns we care about (after the 4-element header)
    are SETTLEMENTDATE, RUNNO, REGIONID, DISPATCHINTERVAL, INTERVENTION,
    RRP — so RRP is at zero-based index 9.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            csv_name = next(
                (n for n in z.namelist() if n.upper().endswith(".CSV")),
                None,
            )
            if csv_name is None:
                return None
            text = z.read(csv_name).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError) as err:
        _LOGGER.warning("AEMO ZIP parse failed: %s", err)
        return None

    settlement_date = ""
    rrp: float | None = None
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 10:
            continue
        if row[0] != "D":
            continue
        if row[1] != "DISPATCH" or row[2] != "REGIONSUM":
            continue
        # Region IDs are sometimes wrapped in quotes — strip them.
        row_region = row[6].strip().strip('"')
        if row_region != region:
            continue
        try:
            rrp = float(row[9])
        except (ValueError, IndexError):
            continue
        settlement_date = row[4].strip().strip('"')

    if rrp is None:
        return None
    # AEMO RRP is $/MWh; PriceHawk uses c/kWh. 1 $/MWh = 0.1 c/kWh.
    return rrp / 10.0, settlement_date


# -- Synchronous helpers exposed for tests -----------------------------------


def parse_dispatch_zip_for_test(
    payload: bytes, region: str
) -> tuple[float, str] | None:
    """Public re-export of the internal CSV parser, for unit tests."""
    return _parse_dispatch_zip(payload, region)


def pick_latest_dispatch_file_for_test(html: str) -> str | None:
    """Public re-export of the listing parser, for unit tests."""
    return _pick_latest_dispatch_file(html)


def build_test_dispatch_zip(rows: list[dict[str, Any]]) -> bytes:
    """Build a synthetic NEMWeb-style dispatch ZIP from the given rows.

    Each row dict needs ``region`` and ``rrp_dollars_per_mwh``. Used by
    test_aemo_api.py to drive _parse_dispatch_zip without hitting the
    network.
    """
    lines = [
        "C,NEMP.WORLD,DISPATCHIS,AEMO,PUBLIC,2026/05/01,test,test,test,1",
        "I,DISPATCH,REGIONSUM,3,SETTLEMENTDATE,RUNNO,REGIONID,"
        "DISPATCHINTERVAL,INTERVENTION,RRP,EEP,ROP",
    ]
    for r in rows:
        lines.append(
            "D,DISPATCH,REGIONSUM,3,"
            f'"2026/05/01 12:00:00",1,"{r["region"]}",159000,0,'
            f'{r["rrp_dollars_per_mwh"]},0,{r["rrp_dollars_per_mwh"]}'
        )
    csv_text = "\n".join(lines).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("PUBLIC_DISPATCHIS_TEST.CSV", csv_text)
    return buf.getvalue()
