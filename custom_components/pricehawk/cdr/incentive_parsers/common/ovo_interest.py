"""OVO Interest Rewards — Phase 2.11.7.

Catalog v3 finding: 324 OVO plans publish "Interest Rewards":
  "3% interest on credit balances. Paid monthly to your OVO account."

This is a behaviour-based credit, not a kWh-rate credit. Math depends
on the user's payment pattern (prepayment / overpayment carrying a
positive balance for X days at Y average). We can't observe this from
HA energy data alone — it requires user-side config (typical credit
balance held with OVO) OR a billing-API hook (not yet shipped).

v1.5.x behaviour:
- Parser DETECTS the incentive presence in eligibility text.
- Returns a rule with ``annual_rate_pct`` and ``balance_aud`` (default
  $0 = no credit). Users opt-in via a future options-flow step that
  sets ``balance_aud`` to their typical OVO credit balance.
- apply_rule credits ``balance_aud × annual_rate_pct / 100 / 365`` per
  day to ``incentive_aud_inc_gst``.

Conservative default. A user with $100 average balance at 3% APR earns
$3/year, prorated to ~$0.008/day. A user opting in at $500 balance
earns ~$15/year. The catalog's typical "low impact" guidance (~$10-30/
yr per user) bracketed by this range.
"""
from __future__ import annotations

import re
from decimal import Decimal


# Match "3% interest" or "5% APR" variants.
INTEREST_RE = re.compile(
    r"(?P<pct>[\d.]+)\s*%\s*(?:interest|APR|annual)",
    re.I,
)
TRIGGER_RE = re.compile(
    r"\bcredit\s+balance\b|\binterest\s+(?:rewards?|on)\b|\baccount\s+balance\b",
    re.I,
)


def parse_rule(text: str, balance_aud: Decimal = Decimal("0")) -> dict | None:
    """Detect OVO-style interest-on-balance rule.

    Args:
      text: incentive eligibility/description string.
      balance_aud: user-supplied average credit balance (opt-in, default 0).

    Returns ``None`` when no match. On match returns
      ``{"annual_rate_pct": Decimal, "balance_aud": Decimal,
         "source": str}``.

    If ``balance_aud`` is 0, ``apply_rule`` will be a no-op.
    """
    if not text or not TRIGGER_RE.search(text):
        return None

    m = INTEREST_RE.search(text)
    if not m:
        return None

    return {
        "annual_rate_pct": Decimal(m.group("pct")),
        "balance_aud": Decimal(balance_aud),
        "source": text[:200],
    }


def parse_from_incentives(
    incentives: list[dict],
    balance_aud: Decimal = Decimal("0"),
) -> list[dict]:
    """Walk a plan's ``incentives[]`` for interest-on-balance rules."""
    out: list[dict] = []
    for inc in incentives or []:
        for field in ("eligibility", "description"):
            text = (inc.get(field) or "").strip()
            if not text:
                continue
            rule = parse_rule(text, balance_aud)
            if rule:
                rule["source_displayName"] = inc.get("displayName") or ""
                out.append(rule)
                break
    return out


def apply_rule(rule: dict, slots: list[dict], breakdown) -> None:
    """Credit daily interest on average credit balance per covered day.

    Per-day credit = balance × annual_rate / 100 / 365.
    No-op when balance_aud is 0 (user hasn't opted in).

    Phase 3.0g (CodeRabbit): scale by number of distinct days in
    `slots`. Previous version subtracted daily_credit ONCE for any
    multi-day window, systematically under-crediting interest on
    weekly/monthly/yearly evaluations.
    """
    balance = rule.get("balance_aud", Decimal("0"))
    rate_pct = rule.get("annual_rate_pct", Decimal("0"))
    if balance <= 0 or rate_pct <= 0:
        return

    distinct_days = {s.get("ts_local", "")[:10] for s in slots if s.get("ts_local")}
    n_days = max(1, len(distinct_days))

    daily_credit_aud = balance * rate_pct / Decimal("100") / Decimal("365")
    total_credit_aud = daily_credit_aud * Decimal(n_days)
    breakdown.incentive_aud_inc_gst -= total_credit_aud
    breakdown.trace.append({
        "incentive": "ovo_interest",
        "balance_aud": float(balance),
        "annual_rate_pct": float(rate_pct),
        "daily_credit_aud": float(daily_credit_aud),
        "days_covered": n_days,
        "total_credit_aud": float(total_credit_aud),
    })
