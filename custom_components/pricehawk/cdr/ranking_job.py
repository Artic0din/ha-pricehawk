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


def get_user_geography(
    options: dict[str, Any],
) -> tuple[str | None, str | None, str | None]:
    """Pull ``(state, postcode, distributor)`` from a config_entry's options.

    - ``postcode``: ``cdr_postcode`` option (set by the wizard).
    - ``distributor``: first entry in ``cdr_plan.data.geography.distributors``.
      The user already accepted this plan so its distributor IS theirs.
    - ``state``: returned as ``None`` — derived later in the registry
      filter when needed. Postcode + distributor is more precise.
    """
    postcode = options.get("cdr_postcode") or None
    cdr_plan = options.get("cdr_plan") or {}
    plan_data = cdr_plan.get("data", {}) if isinstance(cdr_plan, dict) else {}
    geo = plan_data.get("geography", {}) or {}
    # CR-fix: malformed CDR payload could ship ``distributors`` as a
    # string or dict instead of a list. ``isinstance(..., list)``
    # gate prevents ``"United Energy"[0] == "U"`` becoming the
    # active distributor filter and silently breaking ranking.
    distributors = geo.get("distributors")
    distributor = (
        distributors[0]
        if isinstance(distributors, list) and distributors
        else None
    )
    return None, postcode, distributor


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
    _LOGGER.debug(
        "ranking: registry source=%s, %d retailers", source, len(endpoints)
    )

    out: list[RetailerEndpoint] = []
    seen_brand_ids: set[str] = set()

    cdr_plan = options.get("cdr_plan") or {}
    plan_data = cdr_plan.get("data", {}) if isinstance(cdr_plan, dict) else {}
    current_brand = (plan_data.get("brand") or "").strip()
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
