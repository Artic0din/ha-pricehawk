"""CDR-native tariff cost evaluator.

Port of `scripts/cdr_evaluator_proto.py` (the Phase 0 prototype that
gate-passed 2026-05-14 + cleared Phase 1 parity 0.46% vs legacy
`tariff_engine.py`). Same semantics; HA-integration packaging shape.

Boundary types from `cdr.models`. Internal walk-the-dict logic is
intentionally untyped — CDR `electricityContract` has 30+ optional
keys and retailers populate different subsets; locking down the inner
schema with pydantic creates maintenance overhead with no benefit.

Public API:
    evaluate(plan, consumption, run_incentives=True) -> CostBreakdown

Accepts both `PlanDetailEnvelope` pydantic models and raw dicts for
the plan (envelope or unwrapped) for caller flexibility. Same for
`ConsumptionWindow` vs raw dict.

Semantics summary (locked, verified by phase_0_verify.py + phase_1_parity.py):
  - pricingModel: SINGLE_RATE / TIME_OF_USE / FLEXIBLE
  - rateBlockUType: singleRate / timeOfUseRates
  - Stepped rates with daily-reset volume thresholds
  - TOU window: start-INCLUSIVE, end-EXCLUSIVE; endTime "00:00" with
    startTime > 0 means end-of-day (24:00 = 1440 min)
  - FIT: singleTariff (flat or time-variant) + timeVaryingTariffs
  - DST handled via `zoneinfo.ZoneInfo("Australia/Sydney")` on slots'
    `ts_local` ISO timestamps
  - GST factor 1.10 applied ONCE at output via `total_aud_inc_gst`
    property; incentive credits tracked inc-GST separately (PDF
    dollar amounts already inc-GST per legacy convention)

Out-of-scope for v1.5.0 (deferred to v1.5.1 / v1.6.0):
  - demandCharges as primary rate block
  - controlledLoad accounting
  - SEASONAL / TOU Seasonal variants
  - Critical Peak event credits (no event schedule available)
  - Cross-retailer parsers beyond GloBird (OVO Free 3, AGL Three for Free)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from .incentive_parsers import apply_retailer_incentives

GST_FACTOR = Decimal("1.10")
DAY_NAMES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


@dataclass
class CostBreakdown:
    """Period cost breakdown returned by `evaluate()`.

    Internal storage:
      - `*_ex_gst`: rate-based contributions (import / export / supply)
        stored ex-GST. `total_aud_inc_gst` property applies × 1.10.
      - `incentive_aud_inc_gst`: parser credits stored inc-GST (PDF
        dollar amounts are already inc-GST). NOT multiplied.

    `trace` is the per-slot or per-event log for hand-calc spot-check.
    Phase 0 verifier (`scripts/phase_0_verify.py`) reads this to cross-
    check evaluator output against an independent bucket aggregator.
    """

    total_aud_ex_gst: Decimal = Decimal("0")
    daily_supply_aud_ex_gst: Decimal = Decimal("0")
    import_aud_ex_gst: Decimal = Decimal("0")
    export_aud_ex_gst: Decimal = Decimal("0")
    incentive_aud_inc_gst: Decimal = Decimal("0")
    period_days: int = 0
    slot_count: int = 0
    plan_id: str = ""
    notes: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)

    @property
    def total_aud_inc_gst(self) -> Decimal:
        rate_based = (
            self.import_aud_ex_gst + self.export_aud_ex_gst + self.daily_supply_aud_ex_gst
        ) * GST_FACTOR
        return rate_based + self.incentive_aud_inc_gst

    def summary(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "period_days": self.period_days,
            "slot_count": self.slot_count,
            "total_aud_inc_gst": float(self.total_aud_inc_gst.quantize(Decimal("0.01"))),
            "import_aud_inc_gst": float(
                (self.import_aud_ex_gst * GST_FACTOR).quantize(Decimal("0.01"))
            ),
            "export_aud_inc_gst": float(
                (self.export_aud_ex_gst * GST_FACTOR).quantize(Decimal("0.01"))
            ),
            "daily_supply_aud_inc_gst": float(
                (self.daily_supply_aud_ex_gst * GST_FACTOR).quantize(Decimal("0.01"))
            ),
            "incentive_aud_inc_gst": float(self.incentive_aud_inc_gst.quantize(Decimal("0.01"))),
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Helpers (private; pure functions over dicts)
# ---------------------------------------------------------------------------


def _decimal(v: Any) -> Decimal:
    if v is None:
        return Decimal("0")
    return Decimal(str(v))


def _hhmm_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def slot_in_window(local_dt: datetime, days: list[str], start: str, end: str) -> bool:
    """Whether a slot's local clock time falls inside a TOU window.

    Start-inclusive, end-exclusive. `endTime "00:00"` with non-zero start
    means end-of-day (24:00 = 1440 min). Public for cross-check use.
    """
    if DAY_NAMES[local_dt.weekday()] not in days:
        return False
    minutes = local_dt.hour * 60 + local_dt.minute
    start_m = _hhmm_to_minutes(start)
    end_m = _hhmm_to_minutes(end)
    if end_m == 0 and start_m > 0:
        end_m = 1440
    if end_m < start_m:
        return minutes >= start_m or minutes < end_m
    return start_m <= minutes < end_m


def _resolve_tou_rate(local_dt: datetime, tou_rates: list[dict]) -> dict | None:
    for rate in tou_rates:
        for window in rate.get("timeOfUse", []) or []:
            if slot_in_window(
                local_dt,
                window.get("days", []) or [],
                window.get("startTime") or "00:00",
                window.get("endTime") or "23:59",
            ):
                return rate
    return None


def _select_stepped_rate(rates: list[dict], cumulative_kwh_day: Decimal) -> Decimal:
    """Stepped CDR rate: entries with `volume` thresholds; final entry without
    `volume` catches the remainder."""
    for r in rates:
        vol = r.get("volume")
        if vol is None:
            return _decimal(r.get("unitPrice"))
        if cumulative_kwh_day < _decimal(vol):
            return _decimal(r.get("unitPrice"))
    return _decimal(rates[-1].get("unitPrice")) if rates else Decimal("0")


def _calc_stepped_cost(rates: list[dict], kwh: Decimal, cumul: Decimal) -> Decimal:
    """Proportionally split energy consumption across step limits and unit prices."""
    if not rates:
        return Decimal("0")
    if kwh <= 0:
        return Decimal("0")

    total_cost = Decimal("0")
    target_start = cumul
    target_end = cumul + kwh

    current_start = Decimal("0")
    for idx, r in enumerate(rates):
        vol = r.get("volume")
        if vol is not None and idx < len(rates) - 1:
            current_end = _decimal(vol)
        else:
            current_end = Decimal("Infinity")
        price = _decimal(r.get("unitPrice"))

        overlap_start = max(target_start, current_start)
        overlap_end = min(target_end, current_end)

        if overlap_start < overlap_end:
            total_cost += (overlap_end - overlap_start) * price

        current_start = current_end
        if current_start == Decimal("Infinity"):
            break

    return total_cost


def _eval_supply(slots: list[dict], tariff_period: dict, bd: CostBreakdown) -> None:
    dsc = _decimal(tariff_period.get("dailySupplyCharge"))
    days = {datetime.fromisoformat(s["ts_local"]).date() for s in slots}
    bd.period_days = len(days)
    bd.daily_supply_aud_ex_gst = dsc * Decimal(len(days))


def _eval_import(slots: list[dict], tariff_period: dict, bd: CostBreakdown) -> None:
    rate_block_utype = tariff_period.get("rateBlockUType")
    daily_running: dict[str, Decimal] = {}

    if rate_block_utype == "singleRate":
        rates = (tariff_period.get("singleRate") or {}).get("rates", []) or []
        for slot in slots:
            local_dt = datetime.fromisoformat(slot["ts_local"])
            kwh = _decimal(slot.get("grid_import_kwh", 0))
            day = local_dt.date().isoformat()
            cumul = daily_running.get(day, Decimal("0"))
            cost = _calc_stepped_cost(rates, kwh, cumul)
            rate = cost / kwh if kwh > 0 else _select_stepped_rate(rates, cumul)
            bd.import_aud_ex_gst += cost
            daily_running[day] = cumul + kwh
            bd.trace.append(
                {
                    "ts_local": slot["ts_local"],
                    "rate_type": "SINGLE_RATE",
                    "kwh": float(kwh),
                    "rate_ex_gst": float(rate),
                    "cost_ex_gst": float(cost),
                    "cumul_day_kwh": float(cumul + kwh),
                }
            )
        return

    if rate_block_utype == "timeOfUseRates":
        tou_rates = tariff_period.get("timeOfUseRates", []) or []
        for slot in slots:
            local_dt = datetime.fromisoformat(slot["ts_local"])
            kwh = _decimal(slot.get("grid_import_kwh", 0))
            day = local_dt.date().isoformat()
            rate_entry = _resolve_tou_rate(local_dt, tou_rates)
            if rate_entry is None:
                bd.notes.append(f"WARN: no TOU window matched slot {slot['ts_local']}; zero rate")
                bd.trace.append(
                    {
                        "ts_local": slot["ts_local"],
                        "rate_type": "UNMATCHED",
                        "kwh": float(kwh),
                        "rate_ex_gst": 0.0,
                        "cost_ex_gst": 0.0,
                    }
                )
                continue
            cumul_key = f"{day}|{rate_entry.get('type')}"
            cumul = daily_running.get(cumul_key, Decimal("0"))
            rates = rate_entry.get("rates", []) or []
            cost = _calc_stepped_cost(rates, kwh, cumul)
            rate = cost / kwh if kwh > 0 else _select_stepped_rate(rates, cumul)
            bd.import_aud_ex_gst += cost
            daily_running[cumul_key] = cumul + kwh
            bd.trace.append(
                {
                    "ts_local": slot["ts_local"],
                    "rate_type": rate_entry.get("type"),
                    "kwh": float(kwh),
                    "rate_ex_gst": float(rate),
                    "cost_ex_gst": float(cost),
                }
            )
        return

    bd.notes.append(f"WARN: unhandled rateBlockUType {rate_block_utype!r}; import set to 0")


def _eval_fit(plan_data: dict, slots: list[dict], bd: CostBreakdown) -> None:
    """Walk slots, sum FIT credits as negative export_aud_ex_gst.

    Multiple FIT entries summed (e.g., RETAILER + GOVERNMENT). Both
    `singleTariff` (with optional `timeVariations`) and `timeVaryingTariffs`
    shapes supported.
    """
    elec = plan_data.get("electricityContract", {}) or {}
    fits = elec.get("solarFeedInTariff", []) or []
    if not fits:
        return
    for slot in slots:
        local_dt = datetime.fromisoformat(slot["ts_local"])
        export_kwh = _decimal(slot.get("grid_export_kwh", 0) or slot.get("solar_export_kwh", 0))
        if export_kwh <= 0:
            continue
        total = Decimal("0")
        for fit in fits:
            utype = fit.get("tariffUType")
            if utype == "singleTariff":
                st = fit.get("singleTariff") or {}
                tvs = st.get("timeVariations") or []
                if tvs and not any(
                    slot_in_window(
                        local_dt,
                        t.get("days", DAY_NAMES),
                        t.get("startTime", "00:00"),
                        t.get("endTime", "23:59"),
                    )
                    for t in tvs
                ):
                    continue
                rates = st.get("rates", []) or []
                rate = _decimal(rates[0].get("unitPrice")) if rates else Decimal("0")
                total += export_kwh * rate
            elif utype == "timeVaryingTariffs":
                for tvt in fit.get("timeVaryingTariffs") or []:
                    if not any(
                        slot_in_window(
                            local_dt,
                            t.get("days", DAY_NAMES),
                            t.get("startTime", "00:00"),
                            t.get("endTime", "23:59"),
                        )
                        for t in (tvt.get("timeVariations") or [])
                    ):
                        continue
                    rates = tvt.get("rates", []) or []
                    rate = _decimal(rates[0].get("unitPrice")) if rates else Decimal("0")
                    total += export_kwh * rate
        bd.export_aud_ex_gst -= total


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def _unwrap_plan(plan: Any) -> dict:
    """Accept pydantic envelope, pydantic PlanDetail, or raw dict in any of
    the three shapes ({data: {...}}, {electricityContract: ...}, or full
    PlanDetail dict)."""
    if hasattr(plan, "model_dump"):
        plan = plan.model_dump()
    if isinstance(plan, dict) and "data" in plan and isinstance(plan["data"], dict):
        return plan["data"]
    return plan if isinstance(plan, dict) else {}


def _unwrap_consumption(consumption: Any) -> dict:
    if hasattr(consumption, "model_dump"):
        return consumption.model_dump()
    return consumption if isinstance(consumption, dict) else {"slots": []}


def evaluate(
    plan: Any,
    consumption: Any,
    run_incentives: bool = True,
    entry_options: dict | None = None,
    state_context: dict | None = None,
) -> CostBreakdown:
    """Evaluate plan cost over a consumption window.

    Args:
        plan: CDR PlanDetail or envelope (pydantic model or raw dict).
        consumption: ConsumptionWindow (pydantic model or raw dict with `slots`).
        run_incentives: skip retailer-specific incentive parsers (useful for
            parity testing against engines that ignore incentives).
        entry_options: Phase 2.12.1 — user-side opt-in fields the
            retailer parsers need (ovo_interest_balance_aud,
            vpp_batteries_enrolled). Pass-through to
            apply_retailer_incentives. None → empty dict → opt-in
            math no-ops.
        state_context: optional persistent dictionary across daily replays
            used to track period-averaged incentives.
    """
    bd = CostBreakdown()
    plan_data = _unwrap_plan(plan)
    bd.plan_id = plan_data.get("planId", "?")
    elec = plan_data.get("electricityContract", {}) or {}
    bd.notes.append(f"pricingModel={elec.get('pricingModel', '?')}")

    tps = elec.get("tariffPeriod", []) or []
    if not tps:
        bd.notes.append("ERROR: no tariffPeriod found")
        return bd
    tp = tps[0]
    if len(tps) > 1:
        bd.notes.append(f"WARN: {len(tps)} tariff periods present; using first only")

    cons = _unwrap_consumption(consumption)
    slots = cons.get("slots", []) or []
    # Phase 3.0g (CodeRabbit): order-sensitive math (stepped FIT,
    # capped windows, zerohero behavior tracker) needs slots in
    # chronological order. Sort by ts_local; slots without ts_local
    # sort last (defensive — should never happen).
    slots = sorted(slots, key=lambda s: s.get("ts_local") or "9999")
    bd.slot_count = len(slots)

    _eval_supply(slots, tp, bd)
    _eval_import(slots, tp, bd)
    _eval_fit(plan_data, slots, bd)
    if run_incentives:
        apply_retailer_incentives(
            plan_data,
            slots,
            bd,
            slot_in_window=slot_in_window,
            entry_options=entry_options,
            state_context=state_context,
        )

    bd.total_aud_ex_gst = bd.daily_supply_aud_ex_gst + bd.import_aud_ex_gst + bd.export_aud_ex_gst
    return bd
