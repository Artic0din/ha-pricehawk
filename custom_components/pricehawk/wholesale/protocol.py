"""WholesaleProvider Protocol.

Contract that every wholesale spot-price provider (Amber, Flow Power, …)
must satisfy. Mirrors the shape that ``coordinator.py`` already drives:
power-and-rate readings in via :meth:`update`, accumulators and current
rates out via properties, persistence via :meth:`to_dict` / :meth:`from_dict`.

Rates are injected by the caller rather than fetched by the provider.
Moving rate-fetching ownership onto the provider is deferred to PR 4 so
this PR is zero behaviour change.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class WholesaleProvider(Protocol):
    """Stateful daily cost accumulator for a wholesale-priced retailer."""

    def update(
        self,
        grid_power_w: float,
        import_rate_c_kwh: float,
        export_rate_c_kwh: float,
        now_local: datetime,
    ) -> None:
        """Ingest a power reading at the provider's currently-known rates."""

    def reset_daily(self) -> None:
        """Zero all daily accumulators (called at midnight)."""

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
    def net_daily_cost_aud(self) -> float:
        """Net daily cost in AUD. Negative means the household earned more
        in export credits than it paid in import + fixed charges."""

    def to_dict(self) -> dict:
        """Serialise state for persistence across HA restarts."""

    def from_dict(self, data: dict, today: date) -> None:
        """Restore state. ``today`` MUST be the HA-timezone date; no fallback.

        Daily accumulators are only restored if ``data['last_reset_date']``
        equals ``today``. Otherwise they stay at construction defaults
        (zeros), and the next :meth:`update` call will trigger a midnight
        reset.
        """
