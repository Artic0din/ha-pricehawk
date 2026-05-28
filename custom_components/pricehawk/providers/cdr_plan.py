"""Generic CDR-plan provider — wraps the streaming evaluator for any
AU retailer's CDR PlanDetailV2 envelope.

Phase 3.0 (rename from CdrGloBirdProvider): the same class powers the
user's CURRENT plan and any alternative plan we're ranking. Identity
(`id`, `name`) is derived from the plan's `brand` / `brandName` /
`displayName` instead of hardcoded GloBird-specific values.

Config entry shape:
- `entry.options["cdr_plan"]` is the CDR PlanDetailV2 JSON envelope for
  the user's CURRENT plan (the truth source).
- Phase 3.1 will introduce alongside-running instances for top-K
  ranked alternatives.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..cdr.streaming import CdrStreamingEngine


class CdrPlanProvider:
    """Provider adapter around `cdr.streaming.CdrStreamingEngine`.

    Generic across all CDR retailers. `id` and `name` are derived from
    the plan envelope, so the dashboard reads the user-meaningful
    retailer + plan name automatically.
    """

    def __init__(
        self,
        cdr_plan: dict[str, Any],
        entry_options: dict[str, Any] | None = None,
    ) -> None:
        self._plan = cdr_plan
        # Phase 2.12.1: user-side opt-in fields plumbed to engine.
        self._entry_options = entry_options or {}
        self._engine = CdrStreamingEngine(cdr_plan, entry_options=entry_options)
        # Resolve daily supply charge once at init (CDR is ex-GST $/day)
        plan_data = cdr_plan.get("data", cdr_plan)
        elec = plan_data.get("electricityContract", {}) or {}
        tps = elec.get("tariffPeriod", []) or []
        # CR-fix: guard against malformed dailySupplyCharge values in
        # the CDR payload (rare but observed — some retailers publish
        # empty strings during republish windows). Bad value → $0/day
        # supply rather than crashing coordinator/provider setup.
        raw_dsc = (tps[0] if tps else {}).get("dailySupplyCharge", 0)
        try:
            dsc_ex_gst = float(raw_dsc or 0)
        except (TypeError, ValueError):
            dsc_ex_gst = 0.0
        self._daily_supply_aud = dsc_ex_gst * 1.10
        # Identity derived from plan envelope (Phase 3.0).
        self._brand = (plan_data.get("brand") or "unknown").lower()
        self._plan_id = plan_data.get("planId") or "unknown"
        self._display_name = (
            plan_data.get("displayName") or plan_data.get("brandName") or self._brand.title()
        )

    @property
    def id(self) -> str:
        """Provider identity for sensor naming. Brand slug + plan id."""
        return f"{self._brand}_{self._plan_id}"

    @property
    def name(self) -> str:
        """Human-readable provider name for dashboards + winner-explanation."""
        return self._display_name

    # -- Provider interface -----------------------------------------------

    def set_current_rates(self, import_c_kwh: float | None, export_c_kwh: float | None) -> None:
        """Self-priced. Rates come from CDR tariffPeriod."""
        return

    def update(self, grid_power_w: float, now_local: datetime) -> None:
        self._engine.update(grid_power_w, now_local)

    def reset_daily(self) -> None:
        self._engine.reset_daily()

    @property
    def current_import_rate_c_kwh(self) -> float:
        return self._engine.current_import_rate_c_kwh

    @property
    def current_export_rate_c_kwh(self) -> float:
        return self._engine.current_export_rate_c_kwh

    @property
    def import_kwh_today(self) -> float:
        return self._engine.import_kwh_today

    @property
    def export_kwh_today(self) -> float:
        return self._engine.export_kwh_today

    @property
    def import_cost_today_c(self) -> float:
        return self._engine.import_cost_today_c

    @property
    def export_earnings_today_c(self) -> float:
        return self._engine.export_earnings_today_c

    @property
    def daily_fixed_charges_aud(self) -> float:
        return self._daily_supply_aud

    @property
    def net_daily_cost_aud(self) -> float:
        return self._engine.net_daily_cost_aud

    @property
    def extras(self) -> dict[str, Any]:
        return {
            "zerohero_status": self._engine.zerohero_status,
            "super_export_kwh": self._engine.super_export_kwh,
        }

    def to_dict(self) -> dict[str, Any]:
        return self._engine.to_dict()

    def from_dict(self, data: dict[str, Any], today: date) -> None:
        self._engine = CdrStreamingEngine.from_dict(
            self._plan,
            data,
            today=today,
            entry_options=self._entry_options,
        )
