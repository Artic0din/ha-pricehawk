"""Provider Protocol — retailer-agnostic cost-tracking interface.

Each provider owns its own state and computes per-interval cost using
grid power readings from HA. The coordinator iterates over registered
providers each tick.

Externally-priced providers (Amber, LocalVolts) receive rates via
``set_current_rates``; self-priced providers (GloBird, Flow Power) derive
rates from their own configuration.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Provider(Protocol):
    """Common interface every retailer implementation honours."""

    id: str
    name: str

    def update(self, grid_power_w: float, now_local: datetime) -> None:
        """Ingest a new power reading and advance accumulators."""
        ...

    def set_current_rates(
        self, import_c_kwh: float | None, export_c_kwh: float | None
    ) -> None:
        """Update externally-sourced rates. No-op for self-priced providers."""
        ...

    def reset_daily(self) -> None:
        """Zero daily accumulators (called at midnight)."""
        ...

    @property
    def current_import_rate_c_kwh(self) -> float: ...

    @property
    def current_export_rate_c_kwh(self) -> float: ...

    @property
    def import_kwh_today(self) -> float: ...

    @property
    def export_kwh_today(self) -> float: ...

    @property
    def import_cost_today_c(self) -> float: ...

    @property
    def export_earnings_today_c(self) -> float: ...

    @property
    def daily_fixed_charges_aud(self) -> float: ...

    @property
    def net_daily_cost_aud(self) -> float: ...

    @property
    def extras(self) -> dict[str, Any]:
        """Provider-specific extras surfaced to the data dict & sensors."""
        ...

    def to_dict(self) -> dict[str, Any]: ...

    def from_dict(self, data: dict[str, Any], today: date) -> None: ...
