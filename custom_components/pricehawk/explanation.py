"""Per-day "Why X won" explanation engine.

Deterministic — no LLM. Given the previous day's per-provider cost
snapshot and a few accumulated context values (e.g. average wholesale
spot price), produces a structured list of bullets explaining why the
cheapest provider beat the rest.

Adapted from VoltCompare's ``buildExplanation`` (CC-0 / no licence
declared, considered fair derivative — the underlying logic is
deterministic threshold checks, not creative expression).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, TypedDict

Sentiment = Literal["good", "bad", "neu"]


class ProviderSnapshot(TypedDict):
    """Per-provider snapshot consumed by :func:`build_explanation`.

    Mirrors the shape produced by
    ``coordinator._build_providers_block``. Declared here (next to the
    consumer) so that schema drift between producer and consumer
    surfaces as a type-check failure rather than a silent ``KeyError``
    at runtime. ``extras`` is provider-specific and intentionally
    loosely typed — each ``_<provider>_won_bullets`` helper validates
    the keys it cares about.
    """

    name: str
    import_rate_c_kwh: float
    export_rate_c_kwh: float
    import_kwh_today: float
    export_kwh_today: float
    import_cost_today_aud: float
    export_credit_today_aud: float
    daily_fixed_charges_aud: float
    net_daily_cost_aud: float
    extras: dict[str, Any]


ProviderBlock = dict[str, ProviderSnapshot]


@dataclass(frozen=True)
class Bullet:
    """A single rendered bullet with a sentiment for UI colouring."""

    sentiment: Sentiment
    text: str


@dataclass
class Explanation:
    """Structured explanation of which provider won and why."""

    winner_id: str
    winner_name: str
    section_label: str
    margin_aud: float  # cents-cheaper-than-next-best, in dollars
    bullets: list[Bullet] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "winner_id": self.winner_id,
            "winner_name": self.winner_name,
            "section_label": self.section_label,
            "margin_aud": round(self.margin_aud, 2),
            "bullets": [asdict(b) for b in self.bullets],
        }


def _money(amount_c: float) -> str:
    """Format cents as a dollar string with 2 dp."""
    return f"${amount_c / 100:.2f}"


def _kwh(kwh: float) -> str:
    return f"{kwh:.2f} kWh"


def _rate(c_per_kwh: float) -> str:
    return f"{c_per_kwh:.1f}c/kWh"


def build_explanation(
    providers: ProviderBlock,
    *,
    avg_amber_spot_c_kwh: float | None = None,
    free_window_import_kwh: float = 0.0,
    free_window_saving_aud: float = 0.0,
    peak_import_kwh_6_9pm: float = 0.0,
) -> Explanation:
    """Produce a structured explanation of the cheapest provider's win.

    Args:
        providers: Per-provider snapshot dict from
            ``coordinator._build_providers_block``. Each value contains the
            standard cost/kwh/extras keys.
        avg_amber_spot_c_kwh: Volume-weighted avg Amber spot rate today.
            When provided, enables comparisons like "Amber spot avg X was
            above ZH's flat Y".
        free_window_import_kwh: kWh imported during GloBird's 11am-2pm
            free window (used in ZH win bullets).
        free_window_saving_aud: $ saved from the free window vs. peak rate.
        peak_import_kwh_6_9pm: kWh imported during the 6-9pm window
            (relevant for the ZH $1/day credit).

    Returns:
        An Explanation with bullets describing why the winner won.
    """
    if not providers:
        return Explanation(
            winner_id="",
            winner_name="(no providers)",
            section_label="No data",
            margin_aud=0.0,
        )

    # Find the cheapest provider by net daily cost
    by_cost = sorted(
        providers.items(),
        key=lambda kv: kv[1]["net_daily_cost_aud"],
    )
    winner_id, winner = by_cost[0]
    winner_cost = winner["net_daily_cost_aud"]
    runner_up_cost = by_cost[1][1]["net_daily_cost_aud"] if len(by_cost) > 1 else winner_cost
    margin = runner_up_cost - winner_cost

    section_label = f"Why {winner['name']} won"
    bullets: list[Bullet] = []

    # Provider-specific bullet builders
    if winner_id == "globird":
        bullets.extend(
            _globird_won_bullets(
                providers,
                avg_amber_spot_c_kwh=avg_amber_spot_c_kwh,
                free_window_import_kwh=free_window_import_kwh,
                free_window_saving_aud=free_window_saving_aud,
                peak_import_kwh_6_9pm=peak_import_kwh_6_9pm,
            )
        )
    elif winner_id == "flow_power":
        bullets.extend(_flow_power_won_bullets(providers))
    elif winner_id == "localvolts":
        bullets.extend(_localvolts_won_bullets(providers))
    elif winner_id == "amber":
        bullets.extend(
            _amber_won_bullets(providers, avg_amber_spot_c_kwh)
        )
    elif winner_id.startswith("dwt_"):
        # Dynamic Wholesale Tariff family (dwt_aemo_direct, dwt_openelectricity)
        # — wholesale-pass-through providers added in Phase 7. Without a
        # dedicated builder the bullets list stayed empty for every DWT win,
        # surfaced in live UAT 2026-05-24: winner_explanation.bullets = [].
        bullets.extend(_dwt_won_bullets(providers, winner_id))

    # Always include the margin
    if margin > 0.005:
        next_name = by_cost[1][1]["name"]
        bullets.append(
            Bullet(
                sentiment="neu",
                text=(
                    f"Beat next-best ({next_name}) by "
                    f"${margin:.2f} today."
                ),
            )
        )

    return Explanation(
        winner_id=winner_id,
        winner_name=winner["name"],
        section_label=section_label,
        margin_aud=margin,
        bullets=bullets,
    )


# -- Per-provider bullet builders --------------------------------------------


def _globird_won_bullets(
    providers: Mapping[str, ProviderSnapshot],
    *,
    avg_amber_spot_c_kwh: float | None,
    free_window_import_kwh: float,
    free_window_saving_aud: float,
    peak_import_kwh_6_9pm: float,
) -> list[Bullet]:
    bullets: list[Bullet] = []
    gb = providers["globird"]
    extras = gb.get("extras", {})

    # ZeroHero $1/day credit
    if extras.get("zerohero_status") == "earned":
        bullets.append(
            Bullet(
                "good",
                "$1/day credit earned — grid import 6–9pm stayed under 0.09 kWh.",
            )
        )
    elif (
        extras.get("zerohero_status") == "lost"
        and peak_import_kwh_6_9pm > 0.09
    ):
        bullets.append(
            Bullet(
                "bad",
                f"$1 credit not earned — {_kwh(peak_import_kwh_6_9pm)} "
                "imported during 6–9pm (limit 0.09 kWh).",
            )
        )

    # Super export
    sx_kwh = extras.get("super_export_kwh", 0.0) or 0.0
    if sx_kwh > 0.05:
        bullets.append(
            Bullet(
                "good",
                f"Super export: {_kwh(sx_kwh)} at 15c/kWh during 6–9pm "
                f"— earned ${sx_kwh * 15.0 / 100:.2f}.",
            )
        )

    # Free window
    if free_window_saving_aud > 0.05:
        bullets.append(
            Bullet(
                "good",
                f"Free 11–2pm: {_kwh(free_window_import_kwh)} imported at $0 "
                f"— saved ${free_window_saving_aud:.2f}.",
            )
        )

    # Wholesale comparison
    flat_rate = gb["import_rate_c_kwh"]
    amber_snap = providers.get("amber")
    if (
        avg_amber_spot_c_kwh is not None
        and amber_snap is not None
        and amber_snap.get("import_kwh_today", 0) > 0.1
    ):
        if avg_amber_spot_c_kwh > flat_rate:
            bullets.append(
                Bullet(
                    "good",
                    f"Amber spot avg {_rate(avg_amber_spot_c_kwh)} was above "
                    f"GloBird's flat {_rate(flat_rate)}.",
                )
            )
        else:
            bullets.append(
                Bullet(
                    "neu",
                    f"Amber spot avg {_rate(avg_amber_spot_c_kwh)} was below "
                    f"GloBird's {_rate(flat_rate)} — credits made the difference.",
                )
            )
    return bullets


def _dwt_won_bullets(
    providers: Mapping[str, ProviderSnapshot],
    winner_id: str,
) -> list[Bullet]:
    """Bullets for Dynamic Wholesale Tariff family winners.

    DWT providers (``dwt_aemo_direct``, ``dwt_openelectricity``) pass the
    wholesale spot price through to the user with a daily supply charge.
    The bullets quantify today's spot exposure, total import, and how the
    supply charge stacks against the spot-driven cost.

    Live UAT 2026-05-24: previously these providers fell through every
    winner-type check in build_explanation and produced an empty bullets
    list. This builder fills that gap with a generic but data-driven
    explanation that mirrors the shape of the Flow Power / LocalVolts
    builders.
    """
    bullets: list[Bullet] = []
    dwt = providers.get(winner_id)
    if dwt is None:
        # Winner id absent from the providers block — nothing to explain.
        return bullets
    extras = dwt.get("extras") or {}

    wholesale = extras.get("wholesale_price_aud_per_mwh")
    if wholesale is not None:
        # Convert $/MWh to c/kWh: divide by 10. Matches DWT provider's
        # internal ``current_import_rate_c_kwh`` formula.
        wholesale_c_kwh = wholesale / 10.0
        bullets.append(
            Bullet(
                "neu",
                f"Wholesale spot {_rate(wholesale_c_kwh)} (region "
                f"{extras.get('region', '?')}) — passed through with no "
                "retailer margin.",
            )
        )

    import_kwh = dwt.get("import_kwh_today", 0.0) or 0.0
    import_cost = dwt.get("import_cost_today_aud", 0.0) or 0.0
    if import_kwh > 0.05:
        bullets.append(
            Bullet(
                "neu",
                f"Imported {_kwh(import_kwh)} at the spot rate — "
                f"{_money(import_cost * 100)} of variable cost today.",
            )
        )

    daily_supply = dwt.get("daily_fixed_charges_aud", 0.0) or 0.0
    if daily_supply > 0:
        bullets.append(
            Bullet(
                "neu",
                f"Daily supply charge {_money(daily_supply * 100)} — the "
                "only fixed cost; everything else tracks the wholesale spot.",
            )
        )

    age = extras.get("wholesale_price_age_seconds")
    if age is not None and age > 600:
        mins = age // 60
        bullets.append(
            Bullet(
                "bad",
                f"Latest wholesale price is {mins} min old — provider "
                "data source may be lagging.",
            )
        )

    return bullets


def _flow_power_won_bullets(
    providers: Mapping[str, ProviderSnapshot],
) -> list[Bullet]:
    bullets: list[Bullet] = []
    fp = providers["flow_power"]
    extras = fp.get("extras", {})

    hh_kwh = extras.get("happy_hour_export_kwh", 0.0) or 0.0
    hh_rate = extras.get("happy_hour_rate_c_kwh", 0.0) or 0.0
    if hh_kwh > 0.05 and hh_rate > 0:
        bullets.append(
            Bullet(
                "good",
                f"Happy Hour FiT: {_kwh(hh_kwh)} exported 5:30–7:30pm at "
                f"{_rate(hh_rate)} — earned ${hh_kwh * hh_rate / 100:.2f}.",
            )
        )
    elif fp.get("export_kwh_today", 0) > 0.05:
        bullets.append(
            Bullet(
                "bad",
                f"{_kwh(fp['export_kwh_today'])} exported but none during the "
                "5:30–7:30pm Happy Hour — earned 0c FiT.",
            )
        )

    if fp.get("import_kwh_today", 0) > 0.1:
        wholesale = extras.get("wholesale_c_kwh")
        if wholesale is not None:
            bullets.append(
                Bullet(
                    "neu",
                    f"Wholesale spot averaged {_rate(wholesale)} — Flow Power "
                    "passes that through plus PEA + base rate.",
                )
            )
    return bullets


def _localvolts_won_bullets(
    providers: Mapping[str, ProviderSnapshot],
) -> list[Bullet]:
    bullets: list[Bullet] = []
    lv = providers["localvolts"]
    extras = lv.get("extras", {})

    if lv.get("export_kwh_today", 0) > 0.05:
        export_credit = lv.get("export_credit_today_aud", 0) or 0
        if export_credit > 0:
            bullets.append(
                Bullet(
                    "good",
                    f"Export earnings ${export_credit:.2f} from "
                    f"{_kwh(lv['export_kwh_today'])} (peer-matched + spot).",
                )
            )

    neg_kwh = extras.get("negative_export_kwh", 0.0) or 0.0
    if neg_kwh > 0.05:
        neg_cost = extras.get("negative_export_cost_aud", 0.0) or 0.0
        bullets.append(
            Bullet(
                "bad",
                f"Negative spot pricing: {_kwh(neg_kwh)} exported at a loss "
                f"of ${neg_cost:.2f} (set a sell floor to avoid this).",
            )
        )

    sell_floor = extras.get("sell_floor_c_kwh")
    if sell_floor is not None:
        bullets.append(
            Bullet(
                "neu",
                f"Sell floor {_rate(sell_floor)} active — exports below this "
                "earn nothing rather than incurring a charge.",
            )
        )
    return bullets


def _amber_won_bullets(
    providers: Mapping[str, ProviderSnapshot],
    avg_amber_spot_c_kwh: float | None,
) -> list[Bullet]:
    bullets: list[Bullet] = []
    amber = providers["amber"]
    export_credit = amber.get("export_credit_today_aud", 0) or 0
    if export_credit > 0.50:
        bullets.append(
            Bullet(
                "good",
                f"Strong feed-in income: ${export_credit:.2f} at variable "
                "spot rates.",
            )
        )

    # vs each other registered provider
    for pid, p in providers.items():
        if pid == "amber":
            continue
        if avg_amber_spot_c_kwh is None:
            continue
        if amber.get("import_kwh_today", 0) < 0.1:
            continue
        their_rate = p["import_rate_c_kwh"]
        if avg_amber_spot_c_kwh < their_rate:
            bullets.append(
                Bullet(
                    "good",
                    f"vs {p['name']}: spot avg {_rate(avg_amber_spot_c_kwh)} "
                    f"was below their {_rate(their_rate)}.",
                )
            )
    return bullets
