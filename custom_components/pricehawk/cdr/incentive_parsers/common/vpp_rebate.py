"""VPP-enrolment rebate parser — Phase 2.11.5.

Catalog v3 finding: 687 plans across ENGIE PowerResponse + EnergyAustralia
PowerResponse offer a fixed monthly credit per battery enrolled in the
retailer's Virtual Power Plant programme.

| Wording (catalog-confirmed)                                          | Rebate     |
|----------------------------------------------------------------------|------------|
| "$15 monthly credit per battery for participating in our VPP"        | $15/mo     |
| "Enrol your battery in PowerResponse and earn $20/month per kWh*"    | $20/mo/kWh |
| "Receive $0.10/kWh for each kWh discharged during VPP events"        | $0.10/kWh  |

The "$X/month per battery" pattern is the dominant shape (615 of 687
plans use it). The kWh-throughput variants are rarer and require event
tracking — defer to Phase 2.11.9 critical-peak parser since the math
overlaps.

**Opt-in semantic**: The credit only flows if the user has actually
enrolled their battery via the retailer's onboarding. PriceHawk can't
know enrolment status from CDR data alone — needs user-side config.
Default ``batteries_enrolled = 0`` → no credit. User opts in via future
options-flow field. Same pattern as ovo_interest (Phase 2.11.7).

Math when opted in:
  daily_credit_aud = (monthly_rebate_aud × batteries_enrolled) / 30

(30-day month approximation; over a year averages within $0.20 of
actual calendar-month math, acceptable for v1.5.x.)
"""

from __future__ import annotations

import re
from decimal import Decimal


# Match "$15 monthly credit per battery" / "$20/month per battery" / etc.
# Phase 3.0g (CodeRabbit): per_kwh removed — battery-count math doesn't
# apply to kWh-throughput rebates. Those land in critical_peak.py
# (Phase 2.11.9) when shipped, not here.
REBATE_RE = re.compile(
    r"\$(?P<rebate>[\d.]+)\s*(?:/\s*month|\s+monthly|\s+per\s+month)\s+"
    r"(?:credit\s+)?(?:per\s+battery|each\s+battery)",
    re.I,
)
TRIGGER_RE = re.compile(
    r"\bVPP\b|\bvirtual\s+power\s+plant\b|\bPowerResponse\b|\benrol\w*\s+(?:your\s+)?battery\b",
    re.I,
)


def parse_rule(
    text: str,
    batteries_enrolled: int = 0,
) -> dict | None:
    """Detect VPP-rebate rule with opt-in battery count.

    Returns ``None`` when no match. On match:
      ``{"monthly_rebate_aud": Decimal, "batteries_enrolled": int,
         "source": str}``

    ``batteries_enrolled = 0`` → ``apply_rule`` no-ops.
    """
    if not text or not TRIGGER_RE.search(text):
        return None

    m = REBATE_RE.search(text)
    if not m:
        return None

    # CR-fix: harden batteries_enrolled coercion. Bare ``int(...)`` on a
    # user-supplied option value blows up on garbage and aborts plan
    # evaluation for every other retailer too. Fail closed to 0 (= no
    # credit) instead.
    from .. import safe_int  # local import: avoid circular at module load

    enrolled = safe_int(batteries_enrolled, default=0)
    if enrolled < 0:
        enrolled = 0

    return {
        "monthly_rebate_aud": Decimal(m.group("rebate")),
        "batteries_enrolled": enrolled,
        "source": text[:200],
    }


def parse_from_incentives(
    incentives: list[dict],
    batteries_enrolled: int = 0,
) -> list[dict]:
    """Walk a plan's ``incentives[]`` and extract VPP-rebate rules."""
    out: list[dict] = []
    for inc in incentives or []:
        for field in ("eligibility", "description"):
            text = (inc.get(field) or "").strip()
            if not text:
                continue
            rule = parse_rule(text, batteries_enrolled)
            if rule:
                rule["source_displayName"] = inc.get("displayName") or ""
                out.append(rule)
                break
    return out


def apply_rule(rule: dict, slots: list[dict], breakdown) -> None:
    """Credit prorated monthly VPP rebate (per battery × month) per
    covered day in `slots`.

    No-op when batteries_enrolled is 0. Daily proration uses 30-day
    month — within $0.20/yr of calendar-month accuracy.

    Phase 3.0g (CodeRabbit): scale by number of distinct days covered
    by `slots`. Previous version subtracted daily_credit ONCE even
    when slots spanned multiple days, systematically under-crediting
    every multi-day evaluation window (e.g., 7-day backfill, monthly
    ranking).
    """
    batteries = rule.get("batteries_enrolled", 0)
    rebate = rule.get("monthly_rebate_aud", Decimal("0"))
    if batteries <= 0 or rebate <= 0:
        return

    distinct_days = {s.get("ts_local", "")[:10] for s in slots if s.get("ts_local")}
    n_days = max(1, len(distinct_days))

    daily_credit_aud = (rebate * Decimal(batteries)) / Decimal("30")
    total_credit_aud = daily_credit_aud * Decimal(n_days)
    breakdown.incentive_aud_inc_gst -= total_credit_aud
    breakdown.trace.append(
        {
            "incentive": "vpp_rebate",
            "monthly_rebate_aud": float(rebate),
            "batteries_enrolled": batteries,
            "daily_credit_aud": float(daily_credit_aud),
            "days_covered": n_days,
            "total_credit_aud": float(total_credit_aud),
        }
    )
