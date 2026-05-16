"""Async CDR client for AER Product Reference Data endpoints.

Wraps the public Consumer Data Right `cds-au/v1/energy/plans` endpoints
served by individual retailer data holders (and the energymadeeasy.gov.au
AER proxy). Reusable across the config-flow wizard (Phase 2) and the
coordinator nightly refresh (Phase 1.5+).

Locked architectural notes (see design doc §I.1):

- HTTP transport is `aiohttp` via `async_get_clientsession(hass)` — caller
  passes the session in. Mirrors the convention used by `aemo_api.py`.
- List endpoint requires header `x-v: 1`; detail requires `x-v: 3`.
- Pagination follows CDR Common spec: `page` + `page-size` query params,
  `meta.totalPages` in the envelope.
- 25-29 detail requests/sec is the documented budget for the energymadeeasy
  proxy; we do not parallelise from this client. Callers that need batching
  must serialise + insert sleeps themselves.

Exceptions:
- `CdrPlanNotFound` — 404 on a detail fetch (planId no longer published)
- `CdrUnavailable` — network failure or 5xx after retries (caller may
   retry interactively or fall through to manual wizard)
- `CdrAPIError` — every other unexpected 4xx response
"""

from __future__ import annotations

import asyncio
import logging
import urllib.parse
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

USER_AGENT = "PriceHawk/1.5 (+https://github.com/Artic0din/pricehawk)"
_TIMEOUT_SEC = 20
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2  # seconds; exponential backoff
_LIST_PAGE_SIZE = 1000


class CdrUnavailable(Exception):
    """Network / 5xx failure after retries; caller may retry or fall through."""


class CdrPlanNotFound(Exception):
    """404 on plan detail fetch — planId stale or never published."""


class CdrAPIError(Exception):
    """Unexpected non-success response from CDR endpoint."""


async def fetch_plan_list(
    session: aiohttp.ClientSession,
    base_url: str,
    *,
    customer_type: str = "RESIDENTIAL",
    fuel_type: str = "ELECTRICITY",
    brand: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch all residential-electricity MARKET plans for ``base_url``.

    Returns the deduplicated ``plans`` array across all pages. Dedup is
    by ``planId`` (the CDR ID-Permanence rules guarantee planId stable
    across republish boundaries) — without it, retailers that republish
    a plan during pagination produce duplicate rows in the wizard.
    Filtering is done client-side because the CDR list endpoint does
    not accept ``customerType`` as a query param.

    ``brand`` is the CDR ``brand`` discriminator for shared base URIs
    (e.g. seven brands hosted on ``cdr.energymadeeasy.gov.au/energy-locals/``).
    Passed as ``?brand=<brand>`` and harmlessly ignored by single-brand
    endpoints.

    A 404 at the list endpoint indicates a bad base URL or proxy-path
    regression, not a stale plan — surfaces as ``CdrAPIError`` rather
    than ``CdrPlanNotFound`` (which is reserved for the detail
    endpoint).
    """
    page = 1
    seen_ids: set[str] = set()
    out: list[dict[str, Any]] = []
    while True:
        query: dict[str, Any] = {
            "type": "ALL",
            "fuelType": fuel_type,
            "page": page,
            "page-size": _LIST_PAGE_SIZE,
        }
        if brand:
            query["brand"] = brand
        params = urllib.parse.urlencode(query)
        url = f"{base_url.rstrip('/')}/cds-au/v1/energy/plans?{params}"
        try:
            body = await _get_json(session, url, x_v="1")
        except CdrPlanNotFound as err:
            # 404 from the list endpoint is a bad URL, not a stale plan.
            raise CdrAPIError(str(err)) from err
        chunk = body.get("data", {}).get("plans", [])
        for p in chunk:
            if (
                p.get("customerType") != customer_type
                or p.get("fuelType") != fuel_type
            ):
                continue
            pid = p.get("planId")
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)
            out.append(p)
        meta = body.get("meta", {})
        total_pages = int(meta.get("totalPages", 1))
        if page >= total_pages or not chunk:
            break
        page += 1
    return out


async def fetch_plan_detail(
    session: aiohttp.ClientSession,
    base_url: str,
    plan_id: str,
    *,
    brand: str | None = None,
) -> dict[str, Any]:
    """Fetch PlanDetailV2 envelope for ``plan_id``.

    Returns the full response body (envelope, ``data`` shape preserved)
    so callers can store the raw bytes as a config-entry fixture without
    losing audit fields. Raises ``CdrPlanNotFound`` on 404 — that
    actually does mean a stale planId at this endpoint.

    ``brand`` is the CDR brand discriminator for shared base URIs — see
    ``fetch_plan_list`` docstring. Appended as ``?brand=<brand>`` when set.
    """
    url = f"{base_url.rstrip('/')}/cds-au/v1/energy/plans/{plan_id}"
    if brand:
        url = f"{url}?{urllib.parse.urlencode({'brand': brand})}"
    return await _get_json(session, url, x_v="3")


async def _get_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    x_v: str,
) -> dict[str, Any]:
    """Internal helper: GET + JSON parse with retry-on-5xx + timeout backoff."""
    headers = {
        "x-v": x_v,
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    for attempt in range(_MAX_RETRIES):
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SEC),
                headers=headers,
            ) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                if resp.status == 404:
                    raise CdrPlanNotFound(f"404 from {url}")
                if resp.status >= 500 or resp.status == 429:
                    if attempt < _MAX_RETRIES - 1:
                        await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
                        continue
                    raise CdrUnavailable(
                        f"HTTP {resp.status} from {url} after {_MAX_RETRIES} attempts"
                    )
                raise CdrAPIError(f"HTTP {resp.status} from {url}")
        except (CdrPlanNotFound, CdrUnavailable, CdrAPIError):
            raise
        except Exception as err:  # noqa: BLE001 — narrowed below
            # Transient network failures (aiohttp.ClientError / built-in
            # TimeoutError) trigger retry. Anything else re-raises.
            if not isinstance(err, (aiohttp.ClientError, TimeoutError)):
                raise
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
                continue
            _LOGGER.warning("CDR fetch failed for %s: %s", url, err)
            raise CdrUnavailable(str(err)) from err
    raise CdrUnavailable(f"exhausted retries for {url}")


# ---------------------------------------------------------------------------
# Pure-Python helpers exposed for unit tests (matches aemo_api.py pattern).
# ---------------------------------------------------------------------------


def build_list_envelope_for_test(plans: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap ``plans`` in a CDR-shaped list-response envelope."""
    return {
        "data": {"plans": plans},
        "links": {"self": "https://test/cds-au/v1/energy/plans"},
        "meta": {"totalRecords": len(plans), "totalPages": 1},
    }


def build_detail_envelope_for_test(plan_detail: dict[str, Any]) -> dict[str, Any]:
    """Wrap ``plan_detail`` in a CDR-shaped detail-response envelope."""
    return {
        "data": plan_detail,
        "links": {"self": "https://test/cds-au/v1/energy/plans/X"},
        "meta": {},
    }


def filter_residential_electricity_for_test(
    plans: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pure-Python re-export of the boundary filter applied in ``fetch_plan_list``."""
    return [
        p
        for p in plans
        if p.get("customerType") == "RESIDENTIAL"
        and p.get("fuelType") == "ELECTRICITY"
    ]
