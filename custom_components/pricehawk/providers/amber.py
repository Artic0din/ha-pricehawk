"""Amber Electric provider — wraps the existing AmberCalculator.

Amber prices are fetched by the coordinator (REST API) and pushed in via
``set_current_rates`` before each ``update`` call.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..amber_calculator import AmberCalculator


class AmberProvider:
    """Provider adapter around AmberCalculator."""

    id = "amber"
    name = "Amber Electric"

    def __init__(
        self,
        amber_network_daily_c: float = 0.0,
        amber_subscription_daily_c: float = 0.0,
    ) -> None:
        self._calc = AmberCalculator(
            amber_network_daily_c=amber_network_daily_c,
            amber_subscription_daily_c=amber_subscription_daily_c,
        )
        self._import_c: float | None = None
        self._export_c: float | None = None

    # -- Provider interface --------------------------------------------------

    def set_current_rates(
        self, import_c_kwh: float | None, export_c_kwh: float | None
    ) -> None:
        self._import_c = import_c_kwh
        self._export_c = export_c_kwh

    def update(self, grid_power_w: float, now_local: datetime) -> None:
        if self._import_c is None or self._export_c is None:
            return
        self._calc.update(grid_power_w, self._import_c, self._export_c, now_local)

    def reset_daily(self) -> None:
        self._calc.reset_daily()

    @property
    def current_import_rate_c_kwh(self) -> float:
        return self._import_c if self._import_c is not None else 0.0

    @property
    def current_export_rate_c_kwh(self) -> float:
        return self._export_c if self._export_c is not None else 0.0

    @property
    def import_kwh_today(self) -> float:
        return self._calc.import_kwh_today

    @property
    def export_kwh_today(self) -> float:
        return self._calc.export_kwh_today

    @property
    def import_cost_today_c(self) -> float:
        return self._calc.import_cost_today_c

    @property
    def export_earnings_today_c(self) -> float:
        return self._calc.export_earnings_today_c

    @property
    def daily_fixed_charges_aud(self) -> float:
        return self._calc.daily_fixed_charges_aud

    @property
    def net_daily_cost_aud(self) -> float:
        return self._calc.net_daily_cost_aud

    @property
    def extras(self) -> dict[str, Any]:
        return {}

    def to_dict(self) -> dict[str, Any]:
        return self._calc.to_dict()

    def from_dict(self, data: dict[str, Any], today: date) -> None:
        self._calc.from_dict(data, today=today)

    # -- Pass-through for legacy access by coordinator -----------------------

    @property
    def calculator(self) -> AmberCalculator:
        """Direct access to the underlying calculator (legacy code paths)."""
        return self._calc
