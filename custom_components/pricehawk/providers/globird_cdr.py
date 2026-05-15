"""GloBird provider — CDR-native variant.

Drop-in replacement for `GloBirdProvider` (which wraps the legacy
`TariffEngine`). This variant wraps `cdr.streaming.CdrStreamingEngine`
and consumes a CDR `PlanDetail` envelope instead of a legacy options
dict.

Phase 1.2: parallel implementation behind a feature flag. The legacy
`GloBirdProvider` remains the default until Phase 1.3 validates this
variant against a real HA instance.

Config entry shape change:
- Legacy: `entry.options` is a flat dict of `daily_supply_charge`,
  `import_tariff`, `export_tariff`, `incentives`.
- CDR: `entry.options["cdr_plan"]` is a CDR PlanDetailV2 JSON envelope.
  Other options preserved.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..cdr.streaming import CdrStreamingEngine


class CdrGloBirdProvider:
    """Provider adapter around `cdr.streaming.CdrStreamingEngine`.

    Satisfies the same Provider Protocol as the legacy `GloBirdProvider`
    so the coordinator + sensor.py keep working unchanged.
    """

    id = "globird"
    name = "GloBird Energy (CDR)"

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
        dsc_ex_gst = float((tps[0] if tps else {}).get("dailySupplyCharge", 0) or 0)
        self._daily_supply_aud = dsc_ex_gst * 1.10

    # -- Provider interface -----------------------------------------------

    def set_current_rates(
        self, import_c_kwh: float | None, export_c_kwh: float | None
    ) -> None:
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
            self._plan, data, today=today,
            entry_options=self._entry_options,
        )
