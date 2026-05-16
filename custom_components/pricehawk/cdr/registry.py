"""AU energy retailer registry (CDR data-holder endpoints).

Source of truth for "which retailers does PriceHawk know about, and where
do we send CDR list / detail requests for each one".

Strategy (Phase 3.1 prep — EME refdata2):

1. The package ships a baked-in copy of the
   ``https://api.energymadeeasy.gov.au/refdata2`` ``organisations`` map at
   ``cdr/data/eme_refdata.json``. EME covers 117 orgs and carries the
   metadata PriceHawk needs to disambiguate shared base URIs
   (``cdrCode`` → URL path segment, ``cdrBrand`` → ``?brand=`` query
   param matching ``PlanDetail.brand``).
2. At first use, the wizard tries a live fetch from EME. On any failure
   it loads the baked-in EME snapshot. The wizard never blocks on
   registry availability.
3. A quarterly CI cron PR refreshes the baked-in EME copy from upstream.

Sources NOT used and why:
- jxeeno community registry has 2 known base-URI bugs (ARCLINE,
  iO Energy) and drifts from AER PDF. Two unreliable sources are not
  better than one good source + offline cache.
- ACCC Register API is broken for energy PRD (SM#561, unresolved since
  Dec 2022).
- AER PDF is authoritative but human-curated monthly — not suitable
  for a live source.

This module deliberately does NOT persist refreshed copies to HA Store —
that lives in the coordinator's nightly job (post-v1.5.0) where there is
a stable ``hass`` reference. The wizard treats each session as ephemeral.
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

_BAKED_IN_PATH = Path(__file__).parent / "data" / "eme_refdata.json"

LIVE_REGISTRY_URL = (
    "https://api.energymadeeasy.gov.au/refdata2?keys=organisations,thirdParties"
)

_FETCH_TIMEOUT_SEC = 15
_EME_BASE_URI_TEMPLATE = "https://cdr.energymadeeasy.gov.au/{cdr_code}"
_EME_LOGO_PREFIX = "https://energymadeeasy.gov.au"


@dataclass(frozen=True)
class RetailerEndpoint:
    """A single AU retailer's CDR data-holder configuration.

    ``brand_id`` is the EME ``orgId`` — opaque, do not parse.

    ``cdr_brand`` is the ``brand`` discriminator in CDR PlanDetailV2.
    When multiple retailers share a base URI (e.g. seven brands hosted
    on ``cdr.energymadeeasy.gov.au/energy-locals/``), ``cdr_brand``
    distinguishes them. Pass it through ``?brand=<cdr_brand>`` on plan
    list / detail requests.
    """

    brand_id: str
    brand_name: str
    base_uri: str
    logo_uri: str | None = None
    abn: str | None = None
    last_updated: str | None = None
    cdr_brand: str | None = None

    @property
    def slug(self) -> str:
        """Lowercase brand name, spaces -> underscores. Used as a stable
        config-entry key when ``brand_id`` would be too cryptic for logs."""
        return self.brand_name.lower().replace(" ", "_").replace("-", "_")


def _parse_eme_entries(raw: Any) -> list[RetailerEndpoint]:
    """Convert an EME ``refdata2`` envelope into RetailerEndpoint records.

    EME structure: ``{"data": {"organisations": {"<orgId>": {...}, ...}}}``.
    We only keep orgs that have a ``cdrCode`` (the URL path segment) —
    a handful of broker-only entries lack one. ``cdrBrand`` may differ
    from ``cdrCode`` for shared-base-URI brands; it is preserved so
    callers can disambiguate plans via ``?brand=``.
    """
    if not isinstance(raw, dict):
        raise ValueError("EME registry root is not a dict")
    data = raw.get("data")
    if not isinstance(data, dict):
        raise ValueError("EME registry data field is not a dict")
    orgs = data.get("organisations")
    if not isinstance(orgs, dict):
        raise ValueError("EME registry organisations field is not a dict")

    out: list[RetailerEndpoint] = []
    for org_id, o in orgs.items():
        if not isinstance(o, dict):
            continue
        # CR-fix: every upstream string is coerced via _safe_str (handles
        # None, int, bool, missing keys) before .strip() — avoids
        # AttributeError when EME ships a non-string in cdrCode/cdrBrand.
        cdr_code = _safe_str(o.get("cdrCode"))
        # Upstream has trailing-space bugs in some cdrBrand values
        # ("aurora ", "brighte ", "amber " etc); _safe_str strips
        # defensively.
        cdr_brand = _safe_str(o.get("cdrBrand")) or None
        # CR-fix: trim display names too — several EME orgs ship
        # trailing whitespace in tradingName/orgName which would leak
        # into UI labels.
        display = _safe_str(o.get("tradingName")) or _safe_str(o.get("orgName"))
        if not (cdr_code and display):
            continue
        logo_path = o.get("logo")
        logo_uri = (
            f"{_EME_LOGO_PREFIX}{logo_path}"
            if isinstance(logo_path, str) and logo_path.startswith("/")
            else logo_path
        )
        out.append(
            RetailerEndpoint(
                brand_id=str(org_id),
                brand_name=display,
                base_uri=_EME_BASE_URI_TEMPLATE.format(cdr_code=cdr_code),
                logo_uri=logo_uri,
                abn=str(o.get("abn")) if o.get("abn") else None,
                last_updated=None,  # EME envelope has no per-row mtime
                cdr_brand=cdr_brand,
            )
        )
    return out


def _safe_str(value: Any) -> str:
    """Defensive string coercion for upstream registry payloads.

    Returns ``""`` for None / non-string types. Strips whitespace.
    Used wherever we need to call ``.strip()`` on a value that EME
    might ship as something other than a string (rare but observed).
    """
    if not isinstance(value, str):
        return ""
    return value.strip()


def load_baked_in() -> list[RetailerEndpoint]:
    """Load the EME snapshot shipped inside the package."""
    raw = json.loads(_BAKED_IN_PATH.read_text())
    return _parse_eme_entries(raw)


async def fetch_live(session: aiohttp.ClientSession) -> list[RetailerEndpoint]:
    """Pull the live EME refdata2 registry. Raises ``CdrUnavailable`` on
    any failure (HTTP non-200, network error, malformed body) so callers
    can decide whether to fall back to baked-in.
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
    except Exception as err:  # noqa: BLE001 — single-URL endpoint
        _LOGGER.info("registry live fetch failed: %s", err)
        raise CdrUnavailable(str(err)) from err
    try:
        return _parse_eme_entries(raw)
    except (ValueError, TypeError, KeyError, AttributeError) as err:
        # Malformed payload from EME (schema drift) — treat as
        # unavailable so callers fall back to baked-in.
        _LOGGER.info("registry parse failed: %s", err)
        raise CdrUnavailable(f"parse failed: {err}") from err


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


def parse_eme_for_test(raw: dict[str, Any]) -> list[RetailerEndpoint]:
    """Public re-export of the EME refdata2 envelope parser."""
    return _parse_eme_entries(raw)


def baked_in_path_for_test() -> Path:
    """Resolved filesystem path of the EME baked-in JSON, for sanity tests."""
    return _BAKED_IN_PATH
