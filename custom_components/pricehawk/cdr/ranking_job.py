"""Phase 3.1 — Coordinator-facing daily ranking job orchestration.

Lives outside ``coordinator.py`` so the pure logic is unit-testable
without the HA app context that ``PriceHawkCoordinator`` requires
(``DataUpdateCoordinator[T]`` parameterised bases don't survive the
mock-based conftest).

Each function takes the inputs it needs explicitly — config-entry
options, the registry, a HTTP session. The coordinator-side wrapper
methods (in ``coordinator.py``) own the side effects (scheduling
callbacks, persisting results, swallowing exceptions across the daily
boundary).
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from .ranking import DEFAULT_TOP_K, rank_alternatives
from .registry import RetailerEndpoint, find_by_brand, get_registry

if TYPE_CHECKING:
    import aiohttp

_LOGGER = logging.getLogger(__name__)

# Big-4 nationally-active retailers scanned on every daily run.
# EME refdata2 doesn't carry per-retailer geography, so we always
# attempt these four; ``rank_alternatives`` then filters their plans
# by the user's postcode/distributor anyway.
DEFAULT_COMPETITOR_BRAND_FRAGMENTS: tuple[str, ...] = (
    "agl",
    "origin",
    "energyaustralia",
    "red energy",
)


# Live UAT 2026-05-24 — fallback map for users who configured only DWT
# (Dynamic Wholesale Tariff) but never ran the CDR wizard. The DWT region
# encodes the state implicitly; using it as a fallback means the ranking
# pipeline filters to the user's state even when ``cdr_postcode`` is unset.
# Without this, the top-K was populated with nationally-listed plans
# from other states that aren't actually purchasable by the user.
_AEMO_REGION_TO_STATE: dict[str, str] = {
    "NSW1": "NSW",
    "QLD1": "QLD",
    "VIC1": "VIC",
    "SA1": "SA",
    "TAS1": "TAS",
}


def _state_from_dwt_region(options: dict[str, Any]) -> str | None:
    """Derive AU state code from the user's DWT region option (fallback).

    DWT (Dynamic Wholesale Tariff) users configure an AEMO region like
    ``VIC1`` to scope the wholesale-price fetch. Re-using that region as
    a state filter for ranking means a VIC-DWT user only sees ranked
    alternatives that are actually available in VIC — even if they never
    completed the CDR wizard's postcode step.
    """
    region = options.get("dwt_region")
    if not isinstance(region, str):
        return None
    return _AEMO_REGION_TO_STATE.get(region.upper())


def get_user_geography(
    options: dict[str, Any],
) -> tuple[str | None, str | None, str | None]:
    """Pull ``(state, postcode, distributor)`` from a config_entry's options.

    - ``postcode``: ``cdr_postcode`` option (set by the wizard).
    - ``distributor``: first entry in ``cdr_plan.data.geography.distributors``.
      The user already accepted this plan so its distributor IS theirs.
    - ``state``: derived from the DWT region (``dwt_region`` → AEMO region
      → 2-letter AU state code) as a fallback for users who configured
      DWT but never ran the CDR wizard. Live UAT 2026-05-24: without this
      fallback, a VIC DWT-only user's top-K included AGL/Origin plans
      flagged for other states because ``matches_geography(state=None)``
      treats the state filter as a wildcard.
    """
    postcode = options.get("cdr_postcode") or None
    # CR-fix: every level guarded with isinstance — malformed payloads
    # can ship ``cdr_plan`` as a string, ``data`` as a list, ``geography``
    # as None, etc. Without guards, ``.get()`` / ``.strip()`` raise
    # AttributeError and abort the whole ranking run.
    plan_data = _safe_plan_data(options)
    geo = plan_data.get("geography") or {}
    if not isinstance(geo, dict):
        return _state_from_dwt_region(options), postcode, None
    distributors = geo.get("distributors")
    distributor = (
        distributors[0]
        if isinstance(distributors, list) and distributors and isinstance(distributors[0], str)
        else None
    )
    return _state_from_dwt_region(options), postcode, distributor


def _safe_plan_data(options: dict[str, Any]) -> dict[str, Any]:
    """Pull ``cdr_plan.data`` safely. Returns ``{}`` on any malformed shape.

    Tolerated malformations: ``cdr_plan`` missing / non-dict, ``data``
    missing / non-dict. Used by both ``get_user_geography`` and
    ``get_competitor_retailers``.
    """
    cdr_plan = options.get("cdr_plan")
    if not isinstance(cdr_plan, dict):
        return {}
    plan_data = cdr_plan.get("data")
    return plan_data if isinstance(plan_data, dict) else {}


async def get_competitor_retailers(
    session: aiohttp.ClientSession,
    options: dict[str, Any],
    *,
    competitor_fragments: tuple[str, ...] = DEFAULT_COMPETITOR_BRAND_FRAGMENTS,
) -> list[RetailerEndpoint]:
    """Build the retailer list scanned during the daily ranking job.

    Composition (in priority order, dedup by ``brand_id``):
      1. User's CURRENT retailer (from ``cdr_plan.data.brand``).
      2. The hardcoded big-4 competitors.

    Falls back to baked-in registry via ``get_registry``'s own fallback
    when live fetch fails. Returns ``[]`` if registry is empty (edge
    case; baked-in always has 100+ entries).
    """
    endpoints, source = await get_registry(session)
    _LOGGER.debug("ranking: registry source=%s, %d retailers", source, len(endpoints))

    out: list[RetailerEndpoint] = []
    seen_brand_ids: set[str] = set()

    plan_data = _safe_plan_data(options)
    raw_brand = plan_data.get("brand")
    # ``brand`` is sometimes shipped as None or non-string by retailers;
    # only accept str to keep ``.strip()`` and ``find_by_brand`` safe.
    current_brand = raw_brand.strip() if isinstance(raw_brand, str) else ""
    if current_brand:
        current = find_by_brand(endpoints, current_brand)
        if current is not None:
            out.append(current)
            seen_brand_ids.add(current.brand_id)

    for fragment in competitor_fragments:
        match = find_by_brand(endpoints, fragment)
        if match is None or match.brand_id in seen_brand_ids:
            continue
        out.append(match)
        seen_brand_ids.add(match.brand_id)

    return out


async def run_ranking_job(
    session: aiohttp.ClientSession,
    options: dict[str, Any],
    *,
    top_k: int = DEFAULT_TOP_K,
    plan_cache: dict[str, dict[str, Any]] | None = None,
    competitor_fragments: tuple[str, ...] = DEFAULT_COMPETITOR_BRAND_FRAGMENTS,
) -> list[dict[str, Any]]:
    """Run the cheap-rank pipeline. Returns the top-K plans.

    Cheap-rank only for now. Deep-rank (consumption replay) joins in
    Phase 3.2 when the universal HA-history backfill ships and we
    have real per-slot consumption to rank against.

    Caller (coordinator) is responsible for:
      - Scheduling (``async_track_time_change``).
      - Persisting the returned list onto coordinator state.
      - Catching exceptions across the daily boundary (this function
        only catches its own — ``rank_alternatives``'s exception
        isolation per retailer).

    Returns ``[]`` if no retailers resolved (e.g. registry empty).
    """
    retailers = await get_competitor_retailers(
        session, options, competitor_fragments=competitor_fragments
    )
    if not retailers:
        _LOGGER.info("ranking: no competitor retailers resolved; skipping")
        return []

    _state, postcode, distributor = get_user_geography(options)

    return await rank_alternatives(
        session,
        retailers,
        state=_state,
        postcode=postcode,
        distributor=distributor,
        top_k=top_k,
        cache=plan_cache,
    )
