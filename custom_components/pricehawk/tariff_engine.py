"""GloBird tariff calculation engine.

Pure Python — no Home Assistant imports. Consumes the config dict structure
from Phase 1 (const.py / config_flow.py) and computes real-time and
accumulated costs.
"""

from __future__ import annotations

from datetime import date, datetime, time


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ZEROHERO_WINDOW_START = time(18, 0)
ZEROHERO_WINDOW_END = time(20, 0)
ZEROHERO_THRESHOLD_KWH = 0.06  # 0.03 kWh/hr × 2 hrs

SUPER_EXPORT_WINDOW_START = time(18, 0)
SUPER_EXPORT_WINDOW_END = time(20, 0)
SUPER_EXPORT_CAP_KWH = 10.0
SUPER_EXPORT_RATE_C = 15.0  # replacement rate, NOT additive

GAP_PROTECTION_MAX_DELTA_H = 0.1  # 6 minutes


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _time_to_minutes(t: str) -> int:
    """Convert "HH:MM" string to minutes since midnight."""
    parts = t.strip().split(":")
    return int(parts[0]) * 60 + int(parts[1])


def get_current_tou_period(
    periods: dict, now_local: datetime
) -> tuple[str, float]:
    """Return (period_name, rate_c_kwh) for the current local time.

    periods format: {"peak": {"rate": 38.5, "windows": [["16:00","23:00"]]}, ...}
    """
    now_minutes = now_local.hour * 60 + now_local.minute

    for period_name, period_data in periods.items():
        for window in period_data["windows"]:
            start = _time_to_minutes(window[0])
            end = _time_to_minutes(window[1])
            if end == 0:
                end = 1440  # "00:00" as end means midnight

            if start <= end:
                if start <= now_minutes < end:
                    return period_name, period_data["rate"]
            else:
                # Midnight-crossing window
                if now_minutes >= start or now_minutes < end:
                    return period_name, period_data["rate"]

    return "unknown", 0.0


def get_stepped_import_rate(tariff: dict, daily_kwh: float) -> float:
    """Return marginal import rate (c/kWh) based on daily consumption so far."""
    threshold = tariff["step1_threshold_kwh"]
    if daily_kwh < threshold:
        return tariff["step1_rate"]
    return tariff["step2_rate"]


def calc_stepped_cost(tariff: dict, total_kwh: float) -> float:
    """Total cost in cents for total_kwh imported today (stepped plan)."""
    threshold = tariff["step1_threshold_kwh"]
    if total_kwh <= threshold:
        return total_kwh * tariff["step1_rate"]
    return (
        threshold * tariff["step1_rate"]
        + (total_kwh - threshold) * tariff["step2_rate"]
    )


# ---------------------------------------------------------------------------
# ZeroHeroTracker
# ---------------------------------------------------------------------------

class ZeroHeroTracker:
    """Track grid imports during 6-8pm to determine ZEROHERO credit eligibility."""

    def __init__(self) -> None:
        self.window_import_kwh: float = 0.0
        self._credit_earned: bool = False
        self._window_closed: bool = False
        self._threshold_exceeded: bool = False

    def update(self, grid_kw: float, delta_h: float, now_local: datetime) -> None:
        t = now_local.time()
        if ZEROHERO_WINDOW_START <= t < ZEROHERO_WINDOW_END:
            import_kwh = max(0.0, grid_kw) * delta_h
            self.window_import_kwh += import_kwh
            if self.window_import_kwh > ZEROHERO_THRESHOLD_KWH:
                self._threshold_exceeded = True
        elif t >= ZEROHERO_WINDOW_END and not self._window_closed:
            self._window_closed = True
            if not self._threshold_exceeded and self.window_import_kwh <= ZEROHERO_THRESHOLD_KWH:
                self._credit_earned = True

    @property
    def status(self) -> str:
        if self._threshold_exceeded:
            return "lost"
        if self._window_closed:
            return "earned" if self._credit_earned else "lost"
        return "pending"

    def daily_credit_aud(self) -> float:
        return 1.0 if self._credit_earned else 0.0

    def reset(self) -> None:
        self.window_import_kwh = 0.0
        self._credit_earned = False
        self._window_closed = False
        self._threshold_exceeded = False

    def to_dict(self) -> dict:
        return {
            "window_import_kwh": self.window_import_kwh,
            "credit_earned": self._credit_earned,
            "window_closed": self._window_closed,
            "threshold_exceeded": self._threshold_exceeded,
        }

    def from_dict(self, data: dict) -> None:
        self.window_import_kwh = data.get("window_import_kwh", 0.0)
        self._credit_earned = data.get("credit_earned", False)
        self._window_closed = data.get("window_closed", False)
        self._threshold_exceeded = data.get("threshold_exceeded", False)


# ---------------------------------------------------------------------------
# SuperExportTracker
# ---------------------------------------------------------------------------

class SuperExportTracker:
    """Track exports during 6-8pm for the Super Export 15c/kWh replacement rate."""

    def __init__(self) -> None:
        self.window_export_kwh: float = 0.0

    def get_export_rate(self, now_local: datetime) -> float | None:
        """Return 15.0 c/kWh if in window and under cap, else None."""
        t = now_local.time()
        if SUPER_EXPORT_WINDOW_START <= t < SUPER_EXPORT_WINDOW_END:
            if self.window_export_kwh < SUPER_EXPORT_CAP_KWH:
                return SUPER_EXPORT_RATE_C
        return None

    def record_export(self, export_kwh: float, now_local: datetime) -> None:
        t = now_local.time()
        if SUPER_EXPORT_WINDOW_START <= t < SUPER_EXPORT_WINDOW_END:
            self.window_export_kwh = min(
                self.window_export_kwh + export_kwh,
                SUPER_EXPORT_CAP_KWH,
            )

    def reset(self) -> None:
        self.window_export_kwh = 0.0

    def to_dict(self) -> dict:
        return {"window_export_kwh": self.window_export_kwh}

    def from_dict(self, data: dict) -> None:
        self.window_export_kwh = data.get("window_export_kwh", 0.0)


# ---------------------------------------------------------------------------
# DemandTracker
# ---------------------------------------------------------------------------

class DemandTracker:
    """Track peak import kW over the billing period (NOT reset at midnight)."""

    def __init__(self) -> None:
        self.peak_kw_billing: float = 0.0

    def update(self, grid_kw: float) -> None:
        if grid_kw > self.peak_kw_billing:
            self.peak_kw_billing = grid_kw

    def daily_demand_charge_cents(self, rate_c_per_kw_per_day: float) -> float:
        return self.peak_kw_billing * rate_c_per_kw_per_day

    def reset_billing(self) -> None:
        self.peak_kw_billing = 0.0

    def to_dict(self) -> dict:
        return {"peak_kw_billing": self.peak_kw_billing}

    def from_dict(self, data: dict) -> None:
        self.peak_kw_billing = data.get("peak_kw_billing", 0.0)


# ---------------------------------------------------------------------------
# TariffEngine
# ---------------------------------------------------------------------------

class TariffEngine:
    """Stateful cost calculator for a GloBird plan."""

    def __init__(self, options: dict) -> None:
        self._options = options
        self._import_tariff: dict = options.get("import_tariff", {})
        self._export_tariff: dict = options.get("export_tariff", {})
        self._incentives: dict | list = options.get("incentives", {})
        self._daily_supply_charge_c: float = options.get("daily_supply_charge", 0.0)
        self._demand_charge_rate: float = options.get("demand_charge", 0.0)

        # Daily accumulators
        self._import_kwh_today: float = 0.0
        self._export_kwh_today: float = 0.0
        self._import_cost_today_c: float = 0.0
        self._export_earnings_today_c: float = 0.0

        # Timestamps
        self._last_update: datetime | None = None
        self._last_reset_date: date | None = None

        # Trackers
        self._zerohero = ZeroHeroTracker()
        self._super_export = SuperExportTracker()
        self._demand = DemandTracker()

    def _has_incentive(self, name: str) -> bool:
        """Check if an incentive is enabled."""
        inc = self._incentives
        if isinstance(inc, dict):
            return bool(inc.get(name, False))
        if isinstance(inc, list):
            return name in inc
        return False

    def update(self, grid_power_w: float, now_local: datetime) -> None:
        """Ingest a new power reading and advance accumulations."""
        # Midnight reset
        if self._last_reset_date is not None and now_local.date() != self._last_reset_date:
            self.reset_daily()
        if self._last_reset_date is None:
            self._last_reset_date = now_local.date()

        # Delta calculation with gap protection
        if self._last_update is not None:
            delta_s = (now_local - self._last_update).total_seconds()
            delta_h = delta_s / 3600.0
        else:
            self._last_update = now_local
            return

        self._last_update = now_local

        if delta_h <= 0 or delta_h > GAP_PROTECTION_MAX_DELTA_H:
            return

        grid_kw = grid_power_w / 1000.0

        # Energy split: positive = import, negative = export
        import_kwh = max(0.0, grid_kw) * delta_h
        export_kwh = max(0.0, -grid_kw) * delta_h

        # Accumulate energy
        self._import_kwh_today += import_kwh
        self._export_kwh_today += export_kwh

        # Import cost
        if import_kwh > 0:
            if self._import_tariff.get("type") == "tou":
                _, rate = get_current_tou_period(
                    self._import_tariff["periods"], now_local
                )
            elif self._import_tariff.get("type") == "flat_stepped":
                rate = get_stepped_import_rate(
                    self._import_tariff, self._import_kwh_today
                )
            else:
                rate = 0.0
            self._import_cost_today_c += import_kwh * rate

        # Export earnings
        if export_kwh > 0:
            export_rate: float = 0.0
            # Check super export override first
            if self._has_incentive("super_export"):
                override = self._super_export.get_export_rate(now_local)
                if override is not None:
                    export_rate = override
                else:
                    _, export_rate = get_current_tou_period(
                        self._export_tariff["periods"], now_local
                    )
            elif self._export_tariff.get("type") == "tou":
                _, export_rate = get_current_tou_period(
                    self._export_tariff["periods"], now_local
                )

            self._export_earnings_today_c += export_kwh * export_rate

            # Record for super export cap tracking
            if self._has_incentive("super_export"):
                self._super_export.record_export(export_kwh, now_local)

        # Update trackers
        if self._has_incentive("zerohero_credit"):
            self._zerohero.update(grid_kw, delta_h, now_local)

        # Demand tracker (always active, uses import kW only)
        import_kw = max(0.0, grid_kw)
        self._demand.update(import_kw)

    def reset_daily(self) -> None:
        """Zero daily accumulators and reset daily trackers. Does NOT reset demand."""
        self._import_kwh_today = 0.0
        self._export_kwh_today = 0.0
        self._import_cost_today_c = 0.0
        self._export_earnings_today_c = 0.0
        self._zerohero.reset()
        self._super_export.reset()
        self._last_reset_date = None  # Will be set on next update

    @property
    def current_import_rate_c_kwh(self) -> float:
        """Marginal import rate right now (c/kWh)."""
        if self._import_tariff.get("type") == "tou":
            if self._last_update is not None:
                _, rate = get_current_tou_period(
                    self._import_tariff["periods"], self._last_update
                )
                return rate
        elif self._import_tariff.get("type") == "flat_stepped":
            return get_stepped_import_rate(
                self._import_tariff, self._import_kwh_today
            )
        return 0.0

    @property
    def current_export_rate_c_kwh(self) -> float:
        """Effective export rate right now (c/kWh)."""
        if self._last_update is not None and self._has_incentive("super_export"):
            override = self._super_export.get_export_rate(self._last_update)
            if override is not None:
                return override
        if self._export_tariff.get("type") == "tou" and self._last_update is not None:
            _, rate = get_current_tou_period(
                self._export_tariff["periods"], self._last_update
            )
            return rate
        return 0.0

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
    def net_daily_cost_aud(self) -> float:
        """Full daily supply + import cost - export earnings - credits, in AUD.

        Uses the FULL day's supply charge (not prorated) because it's a fixed
        daily cost that will be charged regardless of time elapsed.
        """
        demand_c = self._demand.daily_demand_charge_cents(self._demand_charge_rate)
        total_c = (
            self._daily_supply_charge_c  # Full day's supply, not prorated
            + self._import_cost_today_c
            + demand_c
            - self._export_earnings_today_c
            - self._zerohero.daily_credit_aud() * 100
        )
        return total_c / 100.0

    @property
    def zerohero_status(self) -> str:
        return self._zerohero.status

    @property
    def super_export_kwh(self) -> float:
        return self._super_export.window_export_kwh

    def to_dict(self) -> dict:
        """Serializable snapshot for state persistence."""
        return {
            "import_kwh_today": self._import_kwh_today,
            "export_kwh_today": self._export_kwh_today,
            "import_cost_today_c": self._import_cost_today_c,
            "export_earnings_today_c": self._export_earnings_today_c,
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "last_reset_date": self._last_reset_date.isoformat() if self._last_reset_date else None,
            "zerohero": self._zerohero.to_dict(),
            "super_export": self._super_export.to_dict(),
            "demand": self._demand.to_dict(),
        }

    @classmethod
    def from_dict(cls, options: dict, data: dict, today: date | None = None) -> "TariffEngine":
        """Restore engine state from a persisted dict.

        If the stored date differs from today, daily accumulators are NOT
        restored (stale) but the demand tracker IS restored (billing period).

        Args:
            today: The current date in HA's configured timezone. Caller should
                   pass dt_util.now().date() to avoid system-timezone bugs.
        """
        engine = cls(options)
        stored_date_str = data.get("last_reset_date")
        if today is None:
            today = date.today()

        # Always restore demand tracker (billing period, not daily)
        if "demand" in data:
            engine._demand.from_dict(data["demand"])

        # Restore last_update
        if data.get("last_update"):
            engine._last_update = datetime.fromisoformat(data["last_update"])

        # Only restore daily accumulators if same day
        if stored_date_str:
            stored_date = date.fromisoformat(stored_date_str)
            if stored_date == today:
                engine._last_reset_date = stored_date
                engine._import_kwh_today = data.get("import_kwh_today", 0.0)
                engine._export_kwh_today = data.get("export_kwh_today", 0.0)
                engine._import_cost_today_c = data.get("import_cost_today_c", 0.0)
                engine._export_earnings_today_c = data.get("export_earnings_today_c", 0.0)
                if "zerohero" in data:
                    engine._zerohero.from_dict(data["zerohero"])
                if "super_export" in data:
                    engine._super_export.from_dict(data["super_export"])

        return engine
