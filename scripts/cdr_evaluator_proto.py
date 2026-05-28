"""Phase 0 CDR evaluator prototype.

Loads a CDR PlanDetailV2 fixture + a half-hourly consumption fixture,
returns CostBreakdown for the period. GST-inclusive output (× 1.10).
Time zone: Australia/Sydney via zoneinfo (handles DST).

NOT integration code. Bare Python, no pydantic, no aiohttp. Phase 1
will refactor into custom_components/pricehawk/cdr/evaluator.py with
pydantic models per locked decision §I.2.

Supports:
  - pricingModel: SINGLE_RATE | TIME_OF_USE | FLEXIBLE
  - rateBlockUType: singleRate | timeOfUseRates
  - Stepped rates (volume thresholds per period; daily reset)
  - TOU windows incl. midnight-spanning
  - FIT: singleTariff (flat or with timeVariations) + timeVaryingTariffs
  - Minimal GloBird incentive parser: ZEROHERO ($1/day) + Super Export (15c/kWh first 10kWh exports 6-8pm)
  - DST transitions via zoneinfo on UTC timestamps

Out of Phase 0 scope (deferred to Phase 1+):
  - demandCharges block
  - controlledLoad
  - SEASONAL / TOU Seasonal variants
  - Critical Peak events (no event schedule available)
  - Other retailers' incentive parsers (OVO Free 3, AGL Three for Free)

Run:
    python3 scripts/cdr_evaluator_proto.py <plan_fixture> <consumption_fixture>

Example:
    python3 scripts/cdr_evaluator_proto.py \\
        tests/fixtures/phase0/plan_agl_AGL907738MRE6@EME.json \\
        tests/fixtures/phase0/consumption_7d.json
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

GST_FACTOR = Decimal("1.10")
SYDNEY = ZoneInfo("Australia/Sydney")
UTC = ZoneInfo("UTC")

DAY_NAMES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


@dataclass
class CostBreakdown:
    total_aud_ex_gst: Decimal = Decimal("0")
    daily_supply_aud_ex_gst: Decimal = Decimal("0")
    import_aud_ex_gst: Decimal = Decimal("0")
    export_aud_ex_gst: Decimal = Decimal("0")  # negative (credit)
    # Incentive credits are EXPRESSED IN INC-GST DOLLARS (e.g. "$1/Day"
    # ZEROHERO credit is $1.00 inc-GST not $1.10). Stored separately from
    # ex-GST quantities and added AFTER the GST conversion of the rest.
    incentive_aud_inc_gst: Decimal = Decimal("0")
    period_days: int = 0
    slot_count: int = 0
    plan_id: str = ""
    notes: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)

    @property
    def total_aud_inc_gst(self) -> Decimal:
        # GST applied to rate-based costs (import / export / supply).
        # Incentive credits already inc-GST (PDF dollar amounts are inc-GST).
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


def _decimal(v) -> Decimal:
    if v is None:
        return Decimal("0")
    return Decimal(str(v))


def _hhmm_to_minutes(hhmm: str) -> int:
    """Convert '14:00' / '23:59' to minutes since 00:00 local."""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _slot_in_window(local_dt: datetime, days: list[str], start: str, end: str) -> bool:
    """Check whether local_dt falls within a TOU window.

    Semantics (matches CDR AER convention + legacy engine):
      - startTime INCLUSIVE, endTime EXCLUSIVE.
      - endTime "00:00" with startTime > 0 means "midnight = 24:00 = end of day".
      - For "HH:59" endings (Red Taronga style), exclusive at HH+1:00 (e.g.
        endTime "13:59" excludes minute 13:59 itself; slot at 13:30 still
        matches since 13:30 < 13:59).
      - For "HH:00" endings (GloBird style), exclusive at HH:00 (consecutive
        windows can share boundary; first-match-wins rules at the boundary).
    Slot start time is the test point — 30-min slot assignment.
    """
    day_name = DAY_NAMES[local_dt.weekday()]
    if day_name not in days:
        return False
    minutes = local_dt.hour * 60 + local_dt.minute
    start_m = _hhmm_to_minutes(start)
    end_m = _hhmm_to_minutes(end)
    # "00:00" as end with non-zero start means end-of-day (24:00 = 1440).
    if end_m == 0 and start_m > 0:
        end_m = 1440
    if end_m < start_m:
        # Wraps midnight (rare with proper end-of-day handling above)
        return minutes >= start_m or minutes < end_m
    return start_m <= minutes < end_m


def _resolve_tou_rate(local_dt: datetime, tou_rates: list[dict]) -> dict | None:
    """Return the matching tou_rate entry for the slot's local clock time.

    CDR convention: at most one entry should match per slot. If multiple, the
    first match wins (caller's responsibility to order rates correctly).
    Returns None if no match (treated as zero rate — caller may warn).
    """
    for rate in tou_rates:
        for window in rate.get("timeOfUse", []) or []:
            days = window.get("days", []) or []
            start = window.get("startTime") or "00:00"
            end = window.get("endTime") or "23:59"
            if _slot_in_window(local_dt, days, start, end):
                return rate
    return None


def _select_stepped_rate(rates: list[dict], cumulative_kwh_day: Decimal) -> Decimal:
    """Return unitPrice for the current cumulative_kwh_day.

    CDR stepped rate semantics: rates is a list, each entry may have a `volume`
    threshold. The first entry where cumulative < volume applies; final entry
    without volume catches the remainder.
    """
    for r in rates:
        vol = r.get("volume")
        if vol is None:
            return _decimal(r.get("unitPrice"))
        if cumulative_kwh_day < _decimal(vol):
            return _decimal(r.get("unitPrice"))
    # Fallback to last rate
    return _decimal(rates[-1].get("unitPrice")) if rates else Decimal("0")


def _eval_import(
    slots: list[dict],
    tariff_period: dict,
    breakdown: CostBreakdown,
) -> None:
    """Walk slots, classify each by TOU window, multiply consumption × rate."""
    rate_block_utype = tariff_period.get("rateBlockUType")
    daily_kwh_running: dict[str, Decimal] = {}  # for stepped rates: per local-day

    if rate_block_utype == "singleRate":
        single = tariff_period.get("singleRate", {}) or {}
        rates = single.get("rates", []) or []
        # SINGLE_RATE: same rate all hours. Stepped possible (volume on first
        # entry). Reset daily threshold per local date.
        for slot in slots:
            local_dt = datetime.fromisoformat(slot["ts_local"])
            kwh = _decimal(slot.get("grid_import_kwh", 0))
            day_key = local_dt.date().isoformat()
            cumul = daily_kwh_running.get(day_key, Decimal("0"))
            rate = _select_stepped_rate(rates, cumul)
            cost = kwh * rate
            breakdown.import_aud_ex_gst += cost
            daily_kwh_running[day_key] = cumul + kwh
            breakdown.trace.append(
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
            day_key = local_dt.date().isoformat()
            rate_entry = _resolve_tou_rate(local_dt, tou_rates)
            if rate_entry is None:
                breakdown.notes.append(
                    f"WARN: no TOU window matched slot {slot['ts_local']}; treated as zero"
                )
                breakdown.trace.append(
                    {
                        "ts_local": slot["ts_local"],
                        "rate_type": "UNMATCHED",
                        "kwh": float(kwh),
                        "rate_ex_gst": 0.0,
                        "cost_ex_gst": 0.0,
                    }
                )
                continue
            cumul_key = f"{day_key}|{rate_entry.get('type')}"
            cumul = daily_kwh_running.get(cumul_key, Decimal("0"))
            rate = _select_stepped_rate(rate_entry.get("rates", []) or [], cumul)
            cost = kwh * rate
            breakdown.import_aud_ex_gst += cost
            daily_kwh_running[cumul_key] = cumul + kwh
            breakdown.trace.append(
                {
                    "ts_local": slot["ts_local"],
                    "rate_type": rate_entry.get("type"),
                    "kwh": float(kwh),
                    "rate_ex_gst": float(rate),
                    "cost_ex_gst": float(cost),
                }
            )
        return

    breakdown.notes.append(f"WARN: unhandled rateBlockUType {rate_block_utype!r}; import set to 0")


def _eval_fit(
    plan: dict,
    slots: list[dict],
    breakdown: CostBreakdown,
) -> None:
    """Walk slots, classify each export-kWh against FIT structures.

    Sums FIT credits as NEGATIVE cost in export_aud_ex_gst.
    Handles: singleTariff (flat or with timeVariations); timeVaryingTariffs.
    Multiple FIT entries are summed (e.g., RETAILER FIT + GOVERNMENT FIT).
    """
    elec = plan.get("data", {}).get("electricityContract", {}) or plan.get(
        "electricityContract", {}
    )
    fits = elec.get("solarFeedInTariff", []) or []
    if not fits:
        return
    for slot in slots:
        local_dt = datetime.fromisoformat(slot["ts_local"])
        export_kwh = _decimal(slot.get("grid_export_kwh", 0) or slot.get("solar_export_kwh", 0))
        if export_kwh <= 0:
            continue
        total_credit_for_slot = Decimal("0")
        for fit in fits:
            tariff_utype = fit.get("tariffUType")
            if tariff_utype == "singleTariff":
                st = fit.get("singleTariff") or {}
                # If timeVariations present, slot must match a window; else flat.
                tvs = st.get("timeVariations") or []
                if tvs:
                    matched = False
                    for tv in tvs:
                        if _slot_in_window(
                            local_dt,
                            tv.get("days", DAY_NAMES),
                            tv.get("startTime", "00:00"),
                            tv.get("endTime", "23:59"),
                        ):
                            matched = True
                            break
                    if not matched:
                        continue
                rates = st.get("rates", []) or []
                rate = _decimal(rates[0].get("unitPrice")) if rates else Decimal("0")
                total_credit_for_slot += export_kwh * rate
            elif tariff_utype == "timeVaryingTariffs":
                for tvt in fit.get("timeVaryingTariffs") or []:
                    matched = False
                    for tv in tvt.get("timeVariations") or []:
                        if _slot_in_window(
                            local_dt,
                            tv.get("days", DAY_NAMES),
                            tv.get("startTime", "00:00"),
                            tv.get("endTime", "23:59"),
                        ):
                            matched = True
                            break
                    if not matched:
                        continue
                    rates = tvt.get("rates", []) or []
                    rate = _decimal(rates[0].get("unitPrice")) if rates else Decimal("0")
                    total_credit_for_slot += export_kwh * rate
        # FIT credits reduce cost -> negative export_aud_ex_gst
        breakdown.export_aud_ex_gst -= total_credit_for_slot


def _eval_supply(
    slots: list[dict],
    tariff_period: dict,
    breakdown: CostBreakdown,
) -> None:
    """Daily supply × number of period days (count of unique local-date keys)."""
    dsc = _decimal(tariff_period.get("dailySupplyCharge"))
    # CDR daily supply = dollars/day ex-GST.
    days = {datetime.fromisoformat(s["ts_local"]).date() for s in slots}
    breakdown.period_days = len(days)
    breakdown.daily_supply_aud_ex_gst = dsc * Decimal(len(days))


# -----------------------------
# GloBird incentive parsers
# -----------------------------

ZEROHERO_RE = re.compile(
    r"\$(?P<aud>[\d.]+)\s*/?\s*Day\s+when\s+imports\s+are\s+(?P<thresh>[\d.]+)\s+kWh/hour\s+or\s+less[,]?\s+between\s+(?P<start>\d{1,2}(?:am|pm))-(?P<end>\d{1,2}(?:am|pm))",
    re.I,
)
SUPER_EXPORT_RE = re.compile(
    r"(?P<cents>[\d.]+)\s*cents/kWh\s+applies\s+to\s+the\s+first\s+(?P<kwh>[\d.]+)\s+kWh\s+of\s+exports\s+between\s+(?P<start>\d{1,2}(?:am|pm))-(?P<end>\d{1,2}(?:am|pm))",
    re.I,
)


def _hh_token_to_minutes(tok: str) -> int:
    """Convert '6pm' -> 18*60, '10am' -> 600."""
    m = re.match(r"(\d{1,2})(am|pm)", tok.strip(), re.I)
    if not m:
        raise ValueError(f"can't parse time token {tok!r}")
    h = int(m.group(1)) % 12
    if m.group(2).lower() == "pm":
        h += 12
    return h * 60


def _parse_globird_incentives(plan: dict) -> dict:
    """Extract structured rules from incentive descriptions.

    Returns dict with detected rules. Caller applies them per slot.
    """
    elec = plan.get("data", {}).get("electricityContract", {}) or plan.get(
        "electricityContract", {}
    )
    rules: dict = {}
    for inc in elec.get("incentives", []) or []:
        desc = inc.get("description") or ""
        name = inc.get("displayName") or ""
        # ZEROHERO Credit: $1/Day when imports ≤ threshold, between window
        m = ZEROHERO_RE.search(desc)
        if m and "ZEROHERO" in name.upper():
            rules["zerohero"] = {
                "credit_aud_per_day": Decimal(m.group("aud")),
                "max_kwh_per_hour": Decimal(m.group("thresh")),
                "start_min": _hh_token_to_minutes(m.group("start")),
                "end_min": _hh_token_to_minutes(m.group("end")),
                "source_displayName": name,
            }
        # Super Export Credit: N cents/kWh applies to first M kWh exports in window
        m = SUPER_EXPORT_RE.search(desc)
        if m and "SUPER" in name.upper():
            rules["super_export"] = {
                "cents_per_kwh": Decimal(m.group("cents")),
                "first_kwh_per_day": Decimal(m.group("kwh")),
                "start_min": _hh_token_to_minutes(m.group("start")),
                "end_min": _hh_token_to_minutes(m.group("end")),
                "source_displayName": name,
            }
    return rules


def _apply_globird_incentives(
    plan: dict,
    slots: list[dict],
    breakdown: CostBreakdown,
) -> None:
    elec = plan.get("data", {}).get("electricityContract", {}) or plan.get(
        "electricityContract", {}
    )
    if "globird" not in (elec.get("brand", "") or "").lower():
        brand = plan.get("data", {}).get("brand", "") or plan.get("brand", "")
        if "globird" not in brand.lower():
            return
    rules = _parse_globird_incentives(plan)
    if not rules:
        return
    breakdown.notes.append(f"globird parser hits: {list(rules.keys())}")

    # ZEROHERO: per-day check
    if "zerohero" in rules:
        rule = rules["zerohero"]
        # Group slots by local date
        by_day: dict[str, list[dict]] = {}
        for slot in slots:
            day = slot["ts_local"][:10]
            by_day.setdefault(day, []).append(slot)
        for day, day_slots in by_day.items():
            window_kwh = Decimal("0")
            window_hours = Decimal("0")
            for slot in day_slots:
                local_dt = datetime.fromisoformat(slot["ts_local"])
                minutes = local_dt.hour * 60 + local_dt.minute
                if rule["start_min"] <= minutes < rule["end_min"]:
                    window_kwh += _decimal(slot.get("grid_import_kwh", 0))
                    window_hours += Decimal("0.5")  # half-hour slot
            if window_hours == 0:
                continue
            avg_kwh_per_hour = window_kwh / window_hours
            if avg_kwh_per_hour <= rule["max_kwh_per_hour"]:
                breakdown.incentive_aud_inc_gst -= rule["credit_aud_per_day"]
                breakdown.trace.append(
                    {
                        "incentive": "zerohero",
                        "day": day,
                        "window_kwh": float(window_kwh),
                        "window_hours": float(window_hours),
                        "avg_kwh_h": float(avg_kwh_per_hour),
                        "credited_aud_ex_gst": float(rule["credit_aud_per_day"]),
                    }
                )

    # Super Export: per-day, first N kWh exports in window
    if "super_export" in rules:
        rule = rules["super_export"]
        rate_per_kwh = rule["cents_per_kwh"] / Decimal("100")
        by_day: dict[str, list[dict]] = {}
        for slot in slots:
            day = slot["ts_local"][:10]
            by_day.setdefault(day, []).append(slot)
        for _day, day_slots in by_day.items():
            day_credited_kwh = Decimal("0")
            for slot in day_slots:
                local_dt = datetime.fromisoformat(slot["ts_local"])
                minutes = local_dt.hour * 60 + local_dt.minute
                if not (rule["start_min"] <= minutes < rule["end_min"]):
                    continue
                exp = _decimal(slot.get("grid_export_kwh", 0) or slot.get("solar_export_kwh", 0))
                if exp <= 0:
                    continue
                remaining = rule["first_kwh_per_day"] - day_credited_kwh
                if remaining <= 0:
                    break
                credit_kwh = min(exp, remaining)
                # Super Export rate from PDF is c/kWh INC-GST (15 c/kWh inc-GST)
                breakdown.incentive_aud_inc_gst -= credit_kwh * rate_per_kwh
                day_credited_kwh += credit_kwh


# -----------------------------
# Top-level evaluate()
# -----------------------------


def evaluate(plan: dict, consumption: dict, run_incentives: bool = True) -> CostBreakdown:
    bd = CostBreakdown()
    plan_data = plan.get("data", {}) or plan
    bd.plan_id = plan_data.get("planId", "?")
    elec = plan_data.get("electricityContract", {}) or {}
    pricing_model = elec.get("pricingModel", "?")
    bd.notes.append(f"pricingModel={pricing_model}")

    tps = elec.get("tariffPeriod", []) or []
    if not tps:
        bd.notes.append("ERROR: no tariffPeriod found")
        return bd
    tp = tps[0]  # Phase 0: assume single tariff period (no seasonal splits)
    if len(tps) > 1:
        bd.notes.append(f"WARN: {len(tps)} tariff periods present; using first only")

    slots = consumption.get("slots", []) or []
    bd.slot_count = len(slots)

    _eval_supply(slots, tp, bd)
    _eval_import(slots, tp, bd)
    _eval_fit(plan, slots, bd)
    if run_incentives:
        _apply_globird_incentives(plan, slots, bd)

    bd.total_aud_ex_gst = bd.daily_supply_aud_ex_gst + bd.import_aud_ex_gst + bd.export_aud_ex_gst
    return bd


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    plan_path = Path(argv[1])
    cons_path = Path(argv[2])
    plan = json.loads(plan_path.read_text())
    cons = json.loads(cons_path.read_text())

    bd = evaluate(plan, cons)
    summary = bd.summary()
    print(json.dumps(summary, indent=2))
    print(f"\nTRACE: {len(bd.trace)} rows (use --dump-trace to see all)")
    if "--dump-trace" in argv:
        print(json.dumps(bd.trace[:20], indent=2, default=str))
        if len(bd.trace) > 20:
            print(f"... and {len(bd.trace) - 20} more rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
