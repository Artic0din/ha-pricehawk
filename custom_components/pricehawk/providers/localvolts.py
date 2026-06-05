"""LocalVolts provider — wholesale pass-through with peer-to-peer matching.

LocalVolts is a wholesale-pass-through retailer that operates a
peer-to-peer matching engine on top of the AEMO NEM spot market.
Customers set their own bid (max buy) and offer (min sell) prices and
the engine matches transactions with other LocalVolts customers,
falling back to NEM spot when no peer match exists.

The API publishes 5-minute intervals (matching post-Oct-2021 NEM 5MS
settlement). For PriceHawk's apples-to-apples cost comparison the rates
are aggregated to 30-minute volume-weighted averages by the coordinator
before being pushed in via ``set_current_rates``.

This is a fresh implementation written for PriceHawk — does NOT include
code from gurrier/localvolts (GPL-3.0), only references the public API
shape documented at https://localvolts.com/localvolts-api/.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..helpers import compute_delta_h, should_reset_daily, split_grid_power


class LocalVoltsProvider:
    """LocalVolts wholesale + P2P matching provider."""

    id = "localvolts"
    name = "LocalVolts"

    def __init__(self, options: dict[str, Any]) -> None:
        # ~$1.10/day default per LocalVolts standing offer (varies slightly
        # by network). Stored in cents.
        self._daily_supply_c: float = float(options.get("localvolts_daily_supply", 110.0))
        self._buy_ceiling_c: float | None = options.get("localvolts_buy_ceiling")
        self._sell_floor_c: float | None = options.get("localvolts_sell_floor")

        self._import_c: float | None = None
        self._export_c: float | None = None

        # Daily accumulators
        self._import_kwh_today: float = 0.0
        self._export_kwh_today: float = 0.0
        self._import_cost_today_c: float = 0.0
        self._export_earnings_today_c: float = 0.0
        self._negative_export_kwh: float = 0.0
        self._negative_export_cost_c: float = 0.0

        # Timestamps
        self._last_update: datetime | None = None
        self._last_reset_date: date | None = None

    # -- Provider interface --------------------------------------------------

    def set_current_rates(self, import_c_kwh: float | None, export_c_kwh: float | None) -> None:
        # Apply the buy ceiling: if peer/spot exceeds ceiling, customer
        # avoids importing at that price (modelled as ceiling cap rather
        # than zero import — real LocalVolts behaviour is more complex).
        if import_c_kwh is not None and self._buy_ceiling_c is not None:
            import_c_kwh = min(import_c_kwh, self._buy_ceiling_c)
        # Sell floor: if export rate is below floor, customer would rather
        # not export. Model as effective rate = max(rate, 0) when below
        # floor (i.e. don't pay to export, but don't earn either).
        if (
            export_c_kwh is not None
            and self._sell_floor_c is not None
            and export_c_kwh < self._sell_floor_c
        ):
            export_c_kwh = max(export_c_kwh, 0.0)
        self._import_c = import_c_kwh
        self._export_c = export_c_kwh

    def update(self, grid_power_w: float, now_local: datetime) -> None:
        if self._import_c is None or self._export_c is None:
            return

        if should_reset_daily(now_local.date(), self._last_reset_date):
            self.reset_daily()
            self._last_reset_date = now_local.date()

        delta_h = compute_delta_h(now_local, self._last_update)
        self._last_update = now_local
        if delta_h is None:
            return

        import_kw, export_kw = split_grid_power(grid_power_w)
        import_kwh = import_kw * delta_h
        export_kwh = export_kw * delta_h

        self._import_kwh_today += import_kwh
        self._export_kwh_today += export_kwh

        # Imports always charged (rate may have been capped by buy_ceiling)
        self._import_cost_today_c += import_kwh * self._import_c

        # Exports earn (or cost, if spot is negative — common during midday
        # solar peak when LocalVolts pays nothing or charges to export).
        export_earnings_c = export_kwh * self._export_c
        self._export_earnings_today_c += export_earnings_c
        if export_earnings_c < 0:
            self._negative_export_kwh += export_kwh
            self._negative_export_cost_c += -export_earnings_c

    def reset_daily(self, next_date: date | None = None) -> None:
        self._import_kwh_today = 0.0
        self._export_kwh_today = 0.0
        self._import_cost_today_c = 0.0
        self._export_earnings_today_c = 0.0
        self._negative_export_kwh = 0.0
        self._negative_export_cost_c = 0.0

    @property
    def current_import_rate_c_kwh(self) -> float:
        return self._import_c if self._import_c is not None else 0.0

    @property
    def current_export_rate_c_kwh(self) -> float:
        return self._export_c if self._export_c is not None else 0.0

    @property
    def import_kwh_today(self) -> float:
        return self._import_kwh_today

    @property
    def export_kwh_today(self) -> float:
        return self._export_kwh_today

    @property
    def import_cost_today_c(self) -> float:
        return self._import_cost_today_c

    @property
    def export_earnings_today_c(self) -> float:
        return self._export_earnings_today_c

    @property
    def daily_fixed_charges_aud(self) -> float:
        return self._daily_supply_c / 100.0

    @property
    def net_daily_cost_aud(self) -> float:
        return (
            self._daily_supply_c + self._import_cost_today_c - self._export_earnings_today_c
        ) / 100.0

    @property
    def extras(self) -> dict[str, Any]:
        return {
            "buy_ceiling_c_kwh": self._buy_ceiling_c,
            "sell_floor_c_kwh": self._sell_floor_c,
            "negative_export_kwh": self._negative_export_kwh,
            "negative_export_cost_aud": self._negative_export_cost_c / 100.0,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "import_kwh_today": self._import_kwh_today,
            "export_kwh_today": self._export_kwh_today,
            "import_cost_today_c": self._import_cost_today_c,
            "export_earnings_today_c": self._export_earnings_today_c,
            "negative_export_kwh": self._negative_export_kwh,
            "negative_export_cost_c": self._negative_export_cost_c,
            "import_c": self._import_c,
            "export_c": self._export_c,
            "last_update": (self._last_update.isoformat() if self._last_update else None),
            "last_reset_date": (
                self._last_reset_date.isoformat() if self._last_reset_date else None
            ),
        }

    def from_dict(self, data: dict[str, Any], today: date) -> None:
        last_update_str = data.get("last_update")
        last_reset_str = data.get("last_reset_date")

        if last_update_str:
            self._last_update = datetime.fromisoformat(last_update_str)
        if last_reset_str:
            stored_date = date.fromisoformat(last_reset_str)
            self._last_reset_date = stored_date
            if stored_date == today:
                self._import_kwh_today = data.get("import_kwh_today", 0.0)
                self._export_kwh_today = data.get("export_kwh_today", 0.0)
                self._import_cost_today_c = data.get("import_cost_today_c", 0.0)
                self._export_earnings_today_c = data.get("export_earnings_today_c", 0.0)
                self._negative_export_kwh = data.get("negative_export_kwh", 0.0)
                self._negative_export_cost_c = data.get("negative_export_cost_c", 0.0)

        self._import_c = data.get("import_c")
        self._export_c = data.get("export_c")
