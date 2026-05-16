"""Phase 3.1 — Multi-plan ranking engine.

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

from decimal import Decimal, InvalidOperation
from typing import Any

# Heuristic weights: peak rate dominates (drives 80% of bills for most
# households), daily supply is a meaningful but smaller fraction.
_PEAK_WEIGHT = Decimal("0.7")
_SUPPLY_WEIGHT = Decimal("0.3")

# Top-K default. Tuneable per coordinator option in Phase 3.4.
DEFAULT_TOP_K = 20


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

    Strategy: the FIRST tariffPeriod's most-expensive TOU rate. For
    SINGLE_RATE plans (flat tariffs) this is just the only rate. For
    TOU plans this picks the peak window without needing to parse
    timeOfUse schedules.

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

    tou_rates = first.get("timeOfUseRates") or []
    best: Decimal | None = None
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
    """
    scored: list[tuple[Decimal, dict[str, Any]]] = []
    for p in plans:
        score = cheap_rank_score(p)
        if score is None:
            continue
        scored.append((score, p))
    scored.sort(key=lambda pair: pair[0])
    return [p for _, p in scored[:top_k]]
