"""Phase 3.1 — Multi-plan ranking engine.

Orchestrator
------------

``rank_alternatives()`` is the top-level entry point. Per-retailer flow:

  fetch_plan_list  (1 cheap call per retailer; no rate per-plan)
    → fetch_plan_detail × N  (expensive; 25 req/s budget on EME proxy)
    → cache by planId so daily refresh skips unchanged plans
    → filter_eligible_plans (geography)
    → cheap_rank → top-K

Deep-rank (consumption replay) lives in evaluator/streaming; this
module exits at top-K to keep the cheap path single-pass.

Heuristic
---------



Daily job:
  CDR plan list (per retailer)
    → ``filter_eligible_plans()``  (geography match)
    → ``cheap_rank()``             (peak_rate * 0.7 + daily_supply * 0.3)
    → top-K (default 20)
    → ``deep_rank()`` via ``evaluator.replay``  (HA consumption slots)
    → ranked list persisted on coordinator

Heuristic is intentionally cheap so we can score thousands of plans
without hitting the per-plan detail endpoint twice. Deep-rank then
runs only on the top-K survivors (~20 of ~300+ residential plans
per state) and uses the full streaming evaluator (TOU, stepped,
incentive parsers, etc) against the user's actual HA consumption.

CDR ``customerType`` and ``fuelType`` filtering is already done by
``cdr_client.fetch_plan_list`` (RESIDENTIAL + ELECTRICITY).
Geography filtering happens here because CDR plans carry
``geography.includedPostcodes`` / ``excludedPostcodes`` / ``distributors``
that the list endpoint does not pre-filter by.

Decimal usage: CDR ships ``dailySupplyCharge`` in $/day and
``timeOfUseRates[].rates[].unitPrice`` in $/kWh, ex-GST per spec.
We keep the heuristic in those native units (no GST inflation) since
all plans share the same multiplier — relative ranking is preserved.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, TYPE_CHECKING

from .cdr_client import (
    CdrAPIError,
    CdrPlanNotFound,
    CdrUnavailable,
    fetch_plan_detail,
    fetch_plan_list,
)
from .evaluator import CostBreakdown, evaluate

if TYPE_CHECKING:
    import aiohttp

    from .registry import RetailerEndpoint

_LOGGER = logging.getLogger(__name__)

# Heuristic weights: peak rate dominates (drives 80% of bills for most
# households), daily supply is a meaningful but smaller fraction.
_PEAK_WEIGHT = Decimal("0.7")
_SUPPLY_WEIGHT = Decimal("0.3")

# Top-K default. Tuneable per coordinator option in Phase 3.4.
DEFAULT_TOP_K = 20

# Per-detail-fetch delay (seconds). EME proxy is documented at
# 25-29 req/s; 0.05s = 20 req/s leaves headroom. Coordinator may
# override when running off-proxy retailer endpoints.
DEFAULT_DETAIL_DELAY_SEC = 0.05


def matches_geography(
    plan: dict[str, Any],
    *,
    state: str | None = None,
    postcode: str | None = None,
    distributor: str | None = None,
) -> bool:
    """Return True when ``plan`` covers the requested geography.

    Filters are AND-ed: a postcode + distributor must both match.
    Any filter passed as ``None`` is treated as "wildcard, accept all".

    CDR semantics:
      - ``geography.includedPostcodes`` — if present, postcode MUST be in it.
      - ``geography.excludedPostcodes`` — if present, postcode MUST NOT be in it.
      - ``geography.distributors`` — if present, distributor MUST be in it
        (case-insensitive; CDR uses display names like ``"United Energy"``).
      - ``geography.state`` — optional; not always populated by retailers,
        used as fallback when postcode is unknown.
      - Plans with no ``geography`` block at all are treated as
        nationally-available (rare but valid per CDR spec).
    """
    geo = plan.get("geography") or {}

    if postcode is not None:
        included = geo.get("includedPostcodes")
        excluded = geo.get("excludedPostcodes") or []
        if included is not None and postcode not in included:
            return False
        if postcode in excluded:
            return False

    if distributor is not None:
        plan_distributors = geo.get("distributors")
        if plan_distributors:
            if not _case_insensitive_contains(plan_distributors, distributor):
                return False

    if state is not None:
        plan_state = geo.get("state")
        if plan_state and str(plan_state).upper() != state.upper():
            return False

    return True


def _case_insensitive_contains(haystack: list[str], needle: str) -> bool:
    needle_u = needle.upper()
    return any(isinstance(h, str) and h.upper() == needle_u for h in haystack)


def cheap_rank_score(plan: dict[str, Any]) -> Decimal | None:
    """Cheap-rank heuristic: ``peak_rate * 0.7 + daily_supply * 0.3``.

    Both terms in cents (peak in c/kWh, supply in c/day) so they live
    on roughly the same numeric scale (~30 c/kWh peak, ~100 c/day supply).

    Returns ``None`` when the plan cannot be scored (missing
    ``tariffPeriod``, missing rates, unparseable values). Callers
    treat ``None`` as "skip from ranking" rather than zero, so a
    malformed plan doesn't accidentally rank as cheapest.
    """
    peak_c_per_kwh = _extract_peak_rate_cents(plan)
    supply_c_per_day = _extract_daily_supply_cents(plan)
    if peak_c_per_kwh is None or supply_c_per_day is None:
        return None
    return peak_c_per_kwh * _PEAK_WEIGHT + supply_c_per_day * _SUPPLY_WEIGHT


def _extract_peak_rate_cents(plan: dict[str, Any]) -> Decimal | None:
    """Pull the headline peak rate (c/kWh) from a CDR PlanDetail body.

    Strategy: the FIRST tariffPeriod's most-expensive rate, checking
    BOTH ``timeOfUseRates`` and ``singleRate`` rate blocks. CDR's
    ``rateBlockUType`` enum is one of ``singleRate`` / ``timeOfUseRates``
    / ``demandCharges``; the rate list lives in the matching key.

    Codex P1-4 (2026-05-23): the previous implementation read only
    ``timeOfUseRates`` so any flat/single-rate plan was silently
    excluded from ranking — the ``alternatives`` sensor was biased
    against simpler plans. ``demandCharges``-primary plans remain
    excluded (TODO-5 in TODOS.md) — that's a wholly different cost
    model that needs evaluator work, not just rate extraction.

    CDR ``unitPrice`` is decimal dollars ex-GST; we multiply by 100
    to land in cents (matching the supply scale).
    """
    contract = plan.get("electricityContract") or {}
    periods = contract.get("tariffPeriod") or []
    if not periods:
        return None
    first = periods[0] if isinstance(periods[0], dict) else None
    if not first:
        return None

    best: Decimal | None = None

    # TOU plans: rates live in tariffPeriod[].timeOfUseRates[].rates[]
    tou_rates = first.get("timeOfUseRates") or []
    for tier in tou_rates:
        if not isinstance(tier, dict):
            continue
        for rate in tier.get("rates") or []:
            if not isinstance(rate, dict):
                continue
            try:
                price = Decimal(str(rate.get("unitPrice")))
            except (InvalidOperation, TypeError):
                continue
            cents = price * Decimal("100")
            if best is None or cents > best:
                best = cents

    # SINGLE_RATE plans: rates live in tariffPeriod[].singleRate.rates[]
    # (per CDR Energy API spec rateBlockUType=singleRate). The test
    # fixtures encode single-rate plans inside timeOfUseRates with a
    # type="SINGLE_RATE" marker — that shape is also handled above
    # for fixture compat, but real CDR endpoints use this key.
    single_rate = first.get("singleRate")
    if isinstance(single_rate, dict):
        for rate in single_rate.get("rates") or []:
            if not isinstance(rate, dict):
                continue
            try:
                price = Decimal(str(rate.get("unitPrice")))
            except (InvalidOperation, TypeError):
                continue
            cents = price * Decimal("100")
            if best is None or cents > best:
                best = cents

    return best


def _extract_daily_supply_cents(plan: dict[str, Any]) -> Decimal | None:
    """Pull dailySupplyCharge ($/day) from the first tariffPeriod, return cents."""
    contract = plan.get("electricityContract") or {}
    periods = contract.get("tariffPeriod") or []
    if not periods or not isinstance(periods[0], dict):
        return None
    raw = periods[0].get("dailySupplyCharge")
    try:
        return Decimal(str(raw)) * Decimal("100")
    except (InvalidOperation, TypeError):
        return None


def filter_eligible_plans(
    plans: list[dict[str, Any]],
    *,
    state: str | None = None,
    postcode: str | None = None,
    distributor: str | None = None,
) -> list[dict[str, Any]]:
    """Return only plans whose geography matches the request."""
    return [p for p in plans if matches_geography(
        p, state=state, postcode=postcode, distributor=distributor
    )]


def _economic_fingerprint(plan: dict[str, Any]) -> tuple | None:
    """Identity tuple for "economically the same plan".

    Live UAT 2026-05-24: retailers ship the same headline economics under
    multiple CDR ``planId``s for marketing-channel purposes
    (``Origin Affinity Variable - Comparable``, ``- One Click Switch``,
    ``- Electricity Wizard``, etc. — all carry identical peak rate and
    daily supply charge, only the acquisition-channel label differs).
    Without dedupe, top-K is dominated by 5+ variants of one underlying
    plan and the user sees the same offer five times instead of five
    different cheapest offers.

    Fingerprint = ``(peak_cents, supply_cents)``. Two plans with identical
    headline economics collapse to one representative. Subtle rate-shape
    differences (TOU windows, step thresholds, demand charges) are not
    in the key — they're captured downstream by ``deep_rank`` against the
    user's actual consumption, which is the right place to differentiate
    them. Returns ``None`` for unscorable plans so they're skipped.
    """
    peak = _extract_peak_rate_cents(plan)
    supply = _extract_daily_supply_cents(plan)
    if peak is None or supply is None:
        return None
    return (peak, supply)


def cheap_rank(
    plans: list[dict[str, Any]],
    *,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict[str, Any]]:
    """Sort plans by ascending cheap-rank score; return top-K.

    Plans whose score is ``None`` (malformed / missing rates) are
    dropped — they cannot be ranked, so listing them as "cheap" would
    mislead. Filed as a follow-up if a retailer ships malformed
    payloads at scale.

    Live UAT 2026-05-24: results are deduped by economic fingerprint
    (``peak_cents, supply_cents``) BEFORE the top-K cut, so the same
    plan shipped under multiple marketing-channel planIds collapses to
    a single representative. First seen at each fingerprint wins —
    deterministic given the input order from ``fetch_plans_for_retailer``.
    """
    scored: list[tuple[Decimal, dict[str, Any]]] = []
    seen_fingerprints: set[tuple] = set()
    for p in plans:
        score = cheap_rank_score(p)
        if score is None:
            continue
        fingerprint = _economic_fingerprint(p)
        if fingerprint is not None and fingerprint in seen_fingerprints:
            continue
        if fingerprint is not None:
            seen_fingerprints.add(fingerprint)
        scored.append((score, p))
    scored.sort(key=lambda pair: pair[0])
    return [p for _, p in scored[:top_k]]


# ---------------------------------------------------------------------------
# Orchestrator: fetch all plans + filter + cheap-rank.
# ---------------------------------------------------------------------------


async def fetch_plans_for_retailer(
    session: aiohttp.ClientSession,
    retailer: RetailerEndpoint,
    *,
    cache: dict[str, dict[str, Any]] | None = None,
    detail_delay_sec: float = DEFAULT_DETAIL_DELAY_SEC,
) -> list[dict[str, Any]]:
    """Pull every residential-electricity PlanDetailV2 body for ``retailer``.

    Strategy:
      1. ``fetch_plan_list`` (1 call) — get planId summaries.
      2. ``fetch_plan_detail`` × N — get the full body needed for ranking.
         Sleeps ``detail_delay_sec`` between calls to respect the 25
         req/s EME proxy budget.

    ``cache`` is an optional ``{planId: detail_body}`` dict used to skip
    detail fetches for plans we've already seen. The coordinator owns
    TTL — this function never expires entries, only reads + writes.

    Per-plan fetch failures (``CdrPlanNotFound``, ``CdrAPIError``,
    ``CdrUnavailable``) are logged and skipped so one bad planId
    doesn't sink the whole retailer. Caller gets fewer plans, not
    an exception.
    """
    try:
        summaries = await fetch_plan_list(
            session,
            retailer.base_uri,
            brand=retailer.cdr_brand,
        )
    except (CdrUnavailable, CdrAPIError) as err:
        _LOGGER.info(
            "rank: retailer %s plan list unavailable (%s); skipping",
            retailer.brand_name, err,
        )
        return []

    details: list[dict[str, Any]] = []
    for i, summary in enumerate(summaries):
        plan_id = summary.get("planId")
        if not plan_id:
            continue
        if cache is not None and plan_id in cache:
            details.append(cache[plan_id])
            continue
        if i > 0 and detail_delay_sec > 0:
            await asyncio.sleep(detail_delay_sec)
        try:
            envelope = await fetch_plan_detail(
                session,
                retailer.base_uri,
                plan_id,
                brand=retailer.cdr_brand,
            )
        except (CdrPlanNotFound, CdrAPIError, CdrUnavailable) as err:
            _LOGGER.info(
                "rank: plan %s @ %s skipped (%s)",
                plan_id, retailer.brand_name, err,
            )
            continue
        body = envelope.get("data") if isinstance(envelope, dict) else None
        if not isinstance(body, dict):
            continue
        if cache is not None:
            cache[plan_id] = body
        details.append(body)
    return details


async def rank_alternatives(
    session: aiohttp.ClientSession,
    retailers: list[RetailerEndpoint],
    *,
    state: str | None = None,
    postcode: str | None = None,
    distributor: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    cache: dict[str, dict[str, Any]] | None = None,
    detail_delay_sec: float = DEFAULT_DETAIL_DELAY_SEC,
) -> list[dict[str, Any]]:
    """End-to-end cheap-rank pipeline across ``retailers``.

    Fetches every retailer's plans, filters by geography, cheap-ranks,
    returns the top-K plan bodies (CDR PlanDetailV2 ``data`` shape).

    The caller decides which retailers to scan — this function doesn't
    pre-filter the registry by state because EME refdata2 doesn't carry
    per-retailer region info. Pragmatic v1 callers pass the user's
    current retailer plus a small handful of well-known competitors
    (AGL, Origin, EnergyAustralia, Red Energy) to keep network cost low.
    """
    all_plans: list[dict[str, Any]] = []
    for retailer in retailers:
        plans = await fetch_plans_for_retailer(
            session,
            retailer,
            cache=cache,
            detail_delay_sec=detail_delay_sec,
        )
        all_plans.extend(plans)

    eligible = filter_eligible_plans(
        all_plans,
        state=state,
        postcode=postcode,
        distributor=distributor,
    )
    return cheap_rank(eligible, top_k=top_k)


# ---------------------------------------------------------------------------
# Deep-rank: re-rank cheap-rank survivors by true projected cost.
# ---------------------------------------------------------------------------


def deep_rank(
    plans: list[dict[str, Any]],
    slots: list[dict[str, Any]],
    *,
    entry_options: dict[str, Any] | None = None,
) -> list[tuple[dict[str, Any], CostBreakdown]]:
    """Re-rank cheap-rank survivors by true projected cost.

    Runs the full streaming evaluator (TOU, stepped, controlled load,
    per-retailer incentive parsers) against the user's actual HA
    consumption slots and sorts ascending by ``total_aud_inc_gst``.

    Returns ``[(plan, breakdown), ...]`` so the caller has both the
    projected cost (for ranking display) and the plan body (for the
    "switch to X" prompt). Plans whose evaluator returns a zero-slot
    breakdown (no tariffPeriod, all consumption outside window, etc)
    are filtered out — they cannot be honestly ranked.

    ``entry_options`` flows through to ``evaluate(...)``'s
    ``entry_options`` argument so opt-in fields (OVO interest balance,
    VPP batteries enrolled) shape the per-plan credit math when the
    user has them configured.
    """
    if not slots:
        return []
    scored: list[tuple[Decimal, dict[str, Any], CostBreakdown]] = []
    for plan in plans:
        try:
            bd = evaluate(
                plan,
                {"slots": slots},
                entry_options=entry_options,
            )
        except Exception:  # noqa: BLE001 — one bad plan must not sink the batch
            # ``_LOGGER.exception`` captures the traceback so a malformed
            # CDR plan body that crashes the evaluator (rare but
            # observed during parser rollout) is actually debuggable
            # without re-running the rank job in verbose mode.
            _LOGGER.exception(
                "deep_rank: plan %s evaluator raised; skipping",
                plan.get("planId", "?"),
            )
            continue
        if bd.slot_count == 0:
            continue
        scored.append((bd.total_aud_inc_gst, plan, bd))
    scored.sort(key=lambda triple: triple[0])
    return [(plan, bd) for _, plan, bd in scored]


# ---------------------------------------------------------------------------
# Sensor summary: lean per-plan dict for HA attribute exposure.
# ---------------------------------------------------------------------------


def summarize_for_sensor(
    plan: dict[str, Any],
    *,
    score: Decimal | None = None,
) -> dict[str, Any]:
    """Compress a PlanDetailV2 body to the fields the alternatives sensor
    needs. Full CDR bodies can be 5-15 KB each; HA recorder warns on
    large attribute payloads, so we surface only the headline fields.

    Returns: ``plan_id``, ``display_name``, ``brand``, ``customer_type``,
    ``peak_c_per_kwh``, ``supply_c_per_day``, ``score`` (cheap-rank,
    ``None`` if plan unscored).

    ``score`` parameter lets callers thread a pre-computed cheap-rank
    score through to avoid redundant work + eliminate any drift risk
    between ``cheap_rank``'s ordering and the sensor summaries.
    ``cheap_rank`` computes it once; callers that already have it
    should pass it. ``None`` triggers a recompute via ``cheap_rank_score``.
    """
    peak = _extract_peak_rate_cents(plan)
    supply = _extract_daily_supply_cents(plan)
    if score is None:
        score = cheap_rank_score(plan)
    return {
        "plan_id": plan.get("planId"),
        "display_name": plan.get("displayName"),
        "brand": plan.get("brand"),
        "customer_type": plan.get("customerType"),
        "peak_c_per_kwh": float(peak) if peak is not None else None,
        "supply_c_per_day": float(supply) if supply is not None else None,
        "score": float(score) if score is not None else None,
    }
