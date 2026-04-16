"""Stateful cost accumulator for Amber Electric.

Pure Python — no Home Assistant imports. Receives rate values and grid power
as arguments and accumulates daily costs using power x time integration.
"""

from __future__ import annotations

from datetime import date, datetime

from .helpers import compute_delta_h, should_reset_daily, split_grid_power


class AmberCalculator:
    """Stateful cost accumulator for Amber Electric."""

    def __init__(
        self,
        amber_network_daily_c: float = 0.0,
        amber_subscription_daily_c: float = 0.0,
    ) -> None:
        self._import_kwh_today: float = 0.0
        self._export_kwh_today: float = 0.0
        self._import_cost_today_c: float = 0.0
        self._export_earnings_today_c: float = 0.0
        self._last_update: datetime | None = None
        self._last_reset_date: date | None = None
        self._current_import_rate_c: float = 0.0
        self._current_export_rate_c: float = 0.0
        self._network_daily_c: float = amber_network_daily_c
        self._subscription_daily_c: float = amber_subscription_daily_c

    def update(
        self,
        grid_power_w: float,
        import_rate_c_kwh: float,
        export_rate_c_kwh: float,
        now_local: datetime,
    ) -> None:
        """Ingest a new power reading with current Amber rates."""
        # Store current rates
        self._current_import_rate_c = import_rate_c_kwh
        self._current_export_rate_c = export_rate_c_kwh

        # Midnight reset
        if should_reset_daily(now_local.date(), self._last_reset_date):
            self.reset_daily()
            self._last_reset_date = now_local.date()

        # Compute time delta with gap protection
        delta_h = compute_delta_h(now_local, self._last_update)
        self._last_update = now_local

        if delta_h is None:
            return

        # Split grid power into import/export components
        import_kw, export_kw = split_grid_power(grid_power_w)

        # Accumulate energy
        import_kwh = import_kw * delta_h
        export_kwh = export_kw * delta_h
        self._import_kwh_today += import_kwh
        self._export_kwh_today += export_kwh

        # Accumulate costs
        self._import_cost_today_c += import_kwh * import_rate_c_kwh
        self._export_earnings_today_c += export_kwh * abs(export_rate_c_kwh)

    def reset_daily(self) -> None:
        """Zero all daily accumulators."""
        self._import_kwh_today = 0.0
        self._export_kwh_today = 0.0
        self._import_cost_today_c = 0.0
        self._export_earnings_today_c = 0.0

    # --- Properties ---

    @property
    def current_import_rate_c_kwh(self) -> float:
        """Latest Amber import rate in c/kWh."""
        return self._current_import_rate_c

    @property
    def current_export_rate_c_kwh(self) -> float:
        """Latest Amber export rate in c/kWh."""
        return self._current_export_rate_c

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
        """Combined network + subscription daily charges in AUD."""
        return (self._network_daily_c + self._subscription_daily_c) / 100

    @property
    def net_daily_cost_aud(self) -> float:
        """Net daily cost in AUD. Negative means net earnings."""
        energy_cost_c = self._import_cost_today_c - self._export_earnings_today_c
        fixed_c = self._network_daily_c + self._subscription_daily_c
        return (energy_cost_c + fixed_c) / 100

    # --- Serialization ---

    def to_dict(self) -> dict:
        """Serialize state for persistence."""
        return {
            "import_kwh_today": self._import_kwh_today,
            "export_kwh_today": self._export_kwh_today,
            "import_cost_today_c": self._import_cost_today_c,
            "export_earnings_today_c": self._export_earnings_today_c,
            "current_import_rate_c": self._current_import_rate_c,
            "current_export_rate_c": self._current_export_rate_c,
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "last_reset_date": self._last_reset_date.isoformat() if self._last_reset_date else None,
        }

    def from_dict(self, data: dict, today: date) -> None:
        """Restore state from dict. Only restores daily accumulators if same day.

        Args:
            today: The current date in HA's configured timezone. Caller MUST
                   pass dt_util.now().date() — no fallback to avoid TZ bugs.
        """
        # Parse dates
        last_update_str = data.get("last_update")
        last_reset_str = data.get("last_reset_date")

        if last_update_str:
            self._last_update = datetime.fromisoformat(last_update_str)
        if last_reset_str:
            stored_date = date.fromisoformat(last_reset_str)
            self._last_reset_date = stored_date

            # Only restore daily accumulators if stored date is today
            if stored_date == today:
                self._import_kwh_today = data.get("import_kwh_today", 0.0)
                self._export_kwh_today = data.get("export_kwh_today", 0.0)
                self._import_cost_today_c = data.get("import_cost_today_c", 0.0)
                self._export_earnings_today_c = data.get("export_earnings_today_c", 0.0)

        # Always restore rates
        self._current_import_rate_c = data.get("current_import_rate_c", 0.0)
        self._current_export_rate_c = data.get("current_export_rate_c", 0.0)
