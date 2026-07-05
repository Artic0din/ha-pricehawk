"""Flow Power provider — wholesale pass-through + Happy Hour FiT + PEA.

Wholesale-side pricing logic (calculate_pea, calculate_export_price) is
adapted from bolagnaise/Flow-Power-HA (MIT licensed, copyright 2025
bolagnaise) — see https://github.com/bolagnaise/Flow-Power-HA.

Flow Power is a wholesale-pass-through retailer with two distinguishing
features:

1. Happy Hour FiT: solar exports during 5:30-7:30pm local time earn an
   elevated rate (45c/kWh in NSW/QLD/SA, 35c/kWh in VIC, 0c in TAS).

2. PEA (Price Efficiency Adjustment): a c/kWh adjustment applied to the
   import rate based on how the customer's *load-weighted* average price
   compares to the time-weighted average price. Negative PEA = customer
   used energy when it was cheap → discount.

Wholesale spot price is sourced from the Amber API's ``spotPerKwh`` field
on each interval (so Flow Power requires an Amber API key to be useful as
a comparator — documented in the config flow).
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from ..helpers import compute_delta_h, should_reset_daily, split_grid_power

# -- Constants vendored from Flow-Power-HA's const.py -------------------------

FLOW_POWER_MARKET_AVG_C = 8.0  # Default TWAP fallback when insufficient data
FLOW_POWER_BENCHMARK_C = 1.7  # BPEA — benchmark customer performance
FLOW_POWER_DEFAULT_BASE_RATE_C = 34.0  # Flow Power base rate (c/kWh, GST inc.)

# Happy Hour FiT rates per NEM region (c/kWh)
FLOW_POWER_EXPORT_RATES_C = {
    "NSW1": 45.0,
    "QLD1": 45.0,
    "SA1": 45.0,
    "VIC1": 35.0,
    "TAS1": 0.0,
}

HAPPY_HOUR_START = time(17, 30)
HAPPY_HOUR_END = time(19, 30)


def calculate_pea(wholesale_c_kwh: float, twap_c_kwh: float | None = None) -> float:
    """Flow Power Price Efficiency Adjustment (legacy formula).

    PEA = wholesale - TWAP - BPEA

    Where TWAP is the time-weighted average price (defaults to the
    FLOW_POWER_MARKET_AVG_C fallback if not provided) and BPEA is the
    benchmark adjustment. A negative PEA represents a discount.
    """
    market_avg = twap_c_kwh if twap_c_kwh is not None else FLOW_POWER_MARKET_AVG_C
    return wholesale_c_kwh - market_avg - FLOW_POWER_BENCHMARK_C


def is_happy_hour(now_local: datetime) -> bool:
    """Return True if local time falls within the Happy Hour window."""
    t = now_local.time()
    return HAPPY_HOUR_START <= t < HAPPY_HOUR_END


def happy_hour_rate_for_region(region: str) -> float:
    """Look up the Happy Hour FiT rate (c/kWh) for the given NEM region."""
    return FLOW_POWER_EXPORT_RATES_C.get(region, 0.0)


# -- Provider implementation --------------------------------------------------


class FlowPowerProvider:
    """Wholesale-pass-through provider with Happy Hour FiT and PEA."""

    id = "flow_power"
    name = "Flow Power"

    def __init__(self, options: dict[str, Any]) -> None:
        self._region: str = options.get("flow_power_region", "NSW1")
        self._base_rate_c: float = float(
            options.get("flow_power_base_rate", FLOW_POWER_DEFAULT_BASE_RATE_C)
        )
        self._daily_supply_c: float = float(options.get("flow_power_daily_supply", 100.0))
        self._pea_enabled: bool = bool(options.get("flow_power_pea_enabled", True))
        self._pea_override_c: float | None = options.get("flow_power_pea_override")

        # Externally-sourced inputs
        self._wholesale_c: float | None = None
        self._twap_c: float | None = None

        # Daily accumulators
        self._import_kwh_today: float = 0.0
        self._export_kwh_today: float = 0.0
        self._import_cost_today_c: float = 0.0
        self._export_earnings_today_c: float = 0.0
        self._happy_hour_export_kwh: float = 0.0

        # Timestamps
        self._last_update: datetime | None = None
        self._last_reset_date: date | None = None

    # -- Provider interface --------------------------------------------------

    def set_current_rates(self, import_c_kwh: float | None, export_c_kwh: float | None) -> None:
        # Flow Power uses set_wholesale_rate instead.
        return

    def set_wholesale_rate(self, spot_c_kwh: float | None, twap_c_kwh: float | None = None) -> None:
        """Push the latest NEM spot wholesale price (c/kWh, GST-exclusive).

        TWAP is optional — if the coordinator can compute a rolling TWAP
        from price history, supply it here for a more accurate PEA.
        """
        self._wholesale_c = spot_c_kwh
        if twap_c_kwh is not None:
            self._twap_c = twap_c_kwh

    def update(self, grid_power_w: float, now_local: datetime) -> None:
        if self._wholesale_c is None:
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

        import_rate_c = self.current_import_rate_c_kwh
        export_rate_c = self.current_export_rate_c_kwh

        self._import_cost_today_c += import_kwh * import_rate_c
        self._export_earnings_today_c += export_kwh * export_rate_c

        if export_kwh > 0 and is_happy_hour(now_local):
            self._happy_hour_export_kwh += export_kwh

    def reset_daily(self, next_date: date | None = None) -> None:
        self._import_kwh_today = 0.0
        self._export_kwh_today = 0.0
        self._import_cost_today_c = 0.0
        self._export_earnings_today_c = 0.0
        self._happy_hour_export_kwh = 0.0

    @property
    def current_import_rate_c_kwh(self) -> float:
        if self._wholesale_c is None:
            return 0.0
        if not self._pea_enabled:
            pea_c = 0.0
        elif self._pea_override_c is not None:
            pea_c = float(self._pea_override_c)
        else:
            pea_c = calculate_pea(self._wholesale_c, self._twap_c)
        return self._base_rate_c + pea_c

    @property
    def current_export_rate_c_kwh(self) -> float:
        if self._last_update is not None and is_happy_hour(self._last_update):
            return happy_hour_rate_for_region(self._region)
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
            "region": self._region,
            "base_rate_c_kwh": self._base_rate_c,
            "wholesale_c_kwh": self._wholesale_c,
            "happy_hour_active": (
                is_happy_hour(self._last_update) if self._last_update is not None else False
            ),
            "happy_hour_export_kwh": self._happy_hour_export_kwh,
            "happy_hour_rate_c_kwh": happy_hour_rate_for_region(self._region),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "import_kwh_today": self._import_kwh_today,
            "export_kwh_today": self._export_kwh_today,
            "import_cost_today_c": self._import_cost_today_c,
            "export_earnings_today_c": self._export_earnings_today_c,
            "happy_hour_export_kwh": self._happy_hour_export_kwh,
            "wholesale_c": self._wholesale_c,
            "twap_c": self._twap_c,
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
                self._happy_hour_export_kwh = data.get("happy_hour_export_kwh", 0.0)

        self._wholesale_c = data.get("wholesale_c")
        self._twap_c = data.get("twap_c")
