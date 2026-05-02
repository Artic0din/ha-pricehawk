"""GloBird provider — wraps the existing TariffEngine.

Self-priced: rates derive from the TOU/stepped configuration baked into
``options``. ``set_current_rates`` is a no-op.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..tariff_engine import TariffEngine


class GloBirdProvider:
    """Provider adapter around TariffEngine."""

    id = "globird"
    name = "GloBird Energy"

    def __init__(self, options: dict[str, Any]) -> None:
        self._engine = TariffEngine(options)
        self._options = options

    # -- Provider interface --------------------------------------------------

    def set_current_rates(
        self, import_c_kwh: float | None, export_c_kwh: float | None
    ) -> None:
        # Self-priced: rates come from configured TOU/stepped tariff.
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
        return self._options.get("daily_supply_charge", 0.0) / 100.0

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
        # TariffEngine.from_dict is a classmethod that returns a new engine;
        # adapt to mutate-in-place by replacing _engine.
        self._engine = TariffEngine.from_dict(self._options, data, today=today)

    # -- Pass-through for legacy access by coordinator -----------------------

    @property
    def engine(self) -> TariffEngine:
        """Direct access to the underlying engine (legacy code paths)."""
        return self._engine
