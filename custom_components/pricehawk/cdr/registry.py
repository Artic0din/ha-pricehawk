"""AU energy retailer registry (CDR data-holder endpoints).

Source of truth for "which retailers does PriceHawk know about, and where
do we send CDR list / detail requests for each one".

Strategy (per design doc §H.10):

1. The package ships a baked-in copy of the jxeeno community registry at
   `cdr/data/cdr_endpoints.json`. This guarantees the wizard works
   offline at install time.
2. At first use, the wizard attempts a live fetch from
   `https://raw.githubusercontent.com/jxeeno/energy-cdr-prd-endpoints/main/docs/energy-prd-endpoints.json`.
3. If the live fetch succeeds, those entries replace the baked-in
   set in memory for the lifetime of the wizard session. If it fails
   (network down, 404, malformed body), the baked-in copy is used
   silently — wizard never blocks on registry availability.
4. A quarterly CI cron PR refreshes the baked-in copy from upstream
   (added to the workflow set in Phase 2.5).

This module deliberately does NOT persist refreshed copies to HA Store —
that lives in the coordinator's nightly job (post-v1.5.0) where there is
a stable `hass` reference. The wizard treats each session as ephemeral.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from .cdr_client import (
    USER_AGENT,
    CdrUnavailable,
)

_LOGGER = logging.getLogger(__name__)

_BAKED_IN_PATH = Path(__file__).parent / "data" / "cdr_endpoints.json"
LIVE_REGISTRY_URL = (
    "https://raw.githubusercontent.com/"
    "jxeeno/energy-cdr-prd-endpoints/main/docs/energy-prd-endpoints.json"
)
_FETCH_TIMEOUT_SEC = 15


@dataclass(frozen=True)
class RetailerEndpoint:
    """A single AU retailer's CDR data-holder configuration."""

    brand_id: str
    brand_name: str
    base_uri: str
    logo_uri: str | None = None
    abn: str | None = None
    last_updated: str | None = None

    @property
    def slug(self) -> str:
        """Lowercase brand name, spaces -> underscores. Used as a stable
        config-entry key when ``brand_id`` would be too cryptic for logs."""
        return self.brand_name.lower().replace(" ", "_").replace("-", "_")


def _parse_entries(raw: Any) -> list[RetailerEndpoint]:
    """Convert a raw jxeeno JSON envelope into RetailerEndpoint records.

    Filters to entries that have a usable productReferenceDataBaseUri.
    Industry filter is "energy" (all entries in the jxeeno registry are
    energy retailers; CDR sector overlap with banking is not represented
    in this file).
    """
    if not isinstance(raw, dict):
        raise ValueError("registry root is not a dict")
    entries = raw.get("data")
    if not isinstance(entries, list):
        raise ValueError("registry data field is not a list")

    out: list[RetailerEndpoint] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        base = e.get("productReferenceDataBaseUri")
        brand = e.get("brandName")
        bid = e.get("dataHolderBrandId") or e.get("interimId")
        if not (base and brand and bid):
            continue
        out.append(
            RetailerEndpoint(
                brand_id=str(bid),
                brand_name=str(brand),
                base_uri=str(base).rstrip("/"),
                logo_uri=e.get("logoUri"),
                abn=e.get("abn"),
                last_updated=e.get("lastUpdated"),
            )
        )
    return out


def load_baked_in() -> list[RetailerEndpoint]:
    """Load the JSON shipped inside the package."""
    raw = json.loads(_BAKED_IN_PATH.read_text())
    return _parse_entries(raw)


async def fetch_live(session: aiohttp.ClientSession) -> list[RetailerEndpoint]:
    """Pull the live jxeeno registry. Raises ``CdrUnavailable`` on any
    failure (HTTP non-200, network error, malformed body) so callers can
    decide whether to fall back to baked-in.

    Unlike `cdr_client._get_json` (which is fine-grained about 4xx vs 5xx
    semantics), the registry endpoint is a single static GitHub raw URL
    with one happy path. Any failure → unavailable.
    """
    try:
        async with session.get(
            LIVE_REGISTRY_URL,
            timeout=aiohttp.ClientTimeout(total=_FETCH_TIMEOUT_SEC),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        ) as resp:
            if resp.status != 200:
                raise CdrUnavailable(
                    f"registry HTTP {resp.status} from {LIVE_REGISTRY_URL}"
                )
            raw = await resp.json(content_type=None)
    except CdrUnavailable:
        raise
    except Exception as err:  # noqa: BLE001 — single-URL endpoint, any failure is unavailable
        _LOGGER.info("registry live fetch failed: %s", err)
        raise CdrUnavailable(str(err)) from err

    return _parse_entries(raw)


async def get_registry(
    session: aiohttp.ClientSession,
    *,
    prefer_live: bool = True,
) -> tuple[list[RetailerEndpoint], str]:
    """Return ``(endpoints, source)`` where source is ``"live"`` or
    ``"baked-in"``. Live fetch falls back to baked-in on any error.

    The boolean ``prefer_live`` lets callers (tests, offline-mode) skip the
    network attempt entirely.
    """
    if prefer_live:
        try:
            return (await fetch_live(session), "live")
        except CdrUnavailable as err:
            _LOGGER.info(
                "registry live fetch unavailable (%s); using baked-in copy", err
            )
    return (load_baked_in(), "baked-in")


def find_by_brand(
    endpoints: list[RetailerEndpoint], needle: str
) -> RetailerEndpoint | None:
    """Case-insensitive substring match on ``brand_name``."""
    needle_u = needle.upper()
    for e in endpoints:
        if needle_u in e.brand_name.upper():
            return e
    return None


# ---------------------------------------------------------------------------
# Pure-Python helpers exposed for unit tests.
# ---------------------------------------------------------------------------


def parse_entries_for_test(raw: dict[str, Any]) -> list[RetailerEndpoint]:
    """Public re-export of the internal jxeeno-envelope parser."""
    return _parse_entries(raw)


def baked_in_path_for_test() -> Path:
    """Resolved filesystem path of the baked-in JSON, for sanity tests."""
    return _BAKED_IN_PATH
