"""Dynamic Wholesale Tariff provider — Phase 7 / PR-2b.

ONE Provider class behind TWO config-flow entries:
- ``PROVIDER_DWT_OE``  → ``OpenElectricityPriceSource`` (API key required)
- ``PROVIDER_DWT_AEMO`` → ``NEMWebPriceSource`` (no key, NEM-only)

Self-priced: ``set_current_rates`` is a no-op. The coordinator owns the async
refresh loop and pushes ``WholesalePrice`` results in via ``set_live_price``.
``update()`` stays sync (matches Amber / Flow Power Protocol contract).

AEGIS invariants honoured:
- ``from_dict`` validates ``STATE_VERSION`` and requires an explicit HA-tz
  ``today`` — no ``date.today()`` fallback (07-02b-AUDIT.md M5/M6).
- Negative wholesale prices honour sign discipline matching AmberProvider
  (positive ``export_earnings`` = user receives money).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Final

from .openelectricity import OpenElectricityPriceSource, WholesalePrice
from .nemweb import NEMWebPriceSource

_LOGGER = logging.getLogger(__name__)

STATE_VERSION: Final[int] = 1


class DynamicWholesaleTariffProvider:
    """Provider that prices grid kWh against the latest 5-min wholesale dispatch.

    Backed by either ``OpenElectricityPriceSource`` (DWT-OE) or
    ``NEMWebPriceSource`` (DWT-AEMO). The coordinator selects which at entry
    setup; this class only knows it has a ``WholesalePriceSource``-shaped
    object with ``fetch_current_price(region)`` and ``last_good(region)``.
    """

    def __init__(
        self,
        *,
        price_source: OpenElectricityPriceSource | NEMWebPriceSource,
        region: str,
        daily_supply_c: float,
        provider_id: str,
        name: str,
    ) -> None:
        self.id = provider_id
        self.name = name
        self._price_source = price_source
        self._region = region
        self._daily_supply_c = float(daily_supply_c)

        # Latest price pushed via set_live_price. None until first refresh.
        self._last_price: WholesalePrice | None = None

        # Accumulators (reset_daily zeros these).
        self._import_kwh_today: float = 0.0
        self._export_kwh_today: float = 0.0
        self._import_cost_today_c: float = 0.0
        self._export_earnings_today_c: float = 0.0

        # Tick bookkeeping.
        self._last_tick: datetime | None = None

        # Log the "no price yet" WARNING once per UTC day, not every tick.
        self._no_price_warned_for_utc_date: date | None = None

    # ---- Provider Protocol ------------------------------------------------

    def update(self, grid_power_w: float, now_local: datetime) -> None:
        """Accumulate kWh and cost from this tick.

        Sync — matches Amber/Flow Power. The async fetch happens in a
        coordinator-driven coroutine that calls ``set_live_price`` before
        this method runs.
        """
        last_tick = self._last_tick
        self._last_tick = now_local

        if last_tick is None:
            return  # Need a previous tick to compute dt.

        if now_local.date() != last_tick.date():
            self.reset_daily()

        if self._last_price is None:
            self._warn_no_price_once(now_local)
            return

        dt_seconds = (now_local - last_tick).total_seconds()
        if dt_seconds <= 0:
            return

        wholesale_c_per_kwh = self._last_price.price_aud_per_mwh / 10.0
        kwh = abs(grid_power_w) / 1000.0 * (dt_seconds / 3600.0)
        delta_c = kwh * wholesale_c_per_kwh

        if grid_power_w >= 0:
            # Import: cost flows from user. Negative wholesale price means
            # the importer earns; delta_c will already be negative.
            self._import_kwh_today += kwh
            self._import_cost_today_c += delta_c
        else:
            # Export: earnings flow to user. Negative wholesale = exporter
            # PAYS; delta_c is negative → export_earnings_today_c decreases.
            self._export_kwh_today += kwh
            self._export_earnings_today_c += delta_c

    def set_current_rates(self, import_c_kwh: float | None, export_c_kwh: float | None) -> None:
        """No-op — self-priced provider. Rates come from set_live_price."""
        del import_c_kwh, export_c_kwh
        return

    def reset_daily(self) -> None:
        self._import_kwh_today = 0.0
        self._export_kwh_today = 0.0
        self._import_cost_today_c = 0.0
        self._export_earnings_today_c = 0.0
        # Keep _last_price + _last_tick; price survives midnight.
        self._no_price_warned_for_utc_date = None

    @property
    def current_import_rate_c_kwh(self) -> float:
        if self._last_price is None:
            return 0.0
        return self._last_price.price_aud_per_mwh / 10.0

    @property
    def current_export_rate_c_kwh(self) -> float:
        if self._last_price is None:
            return 0.0
        return self._last_price.price_aud_per_mwh / 10.0

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
            self._import_cost_today_c / 100.0
            - self._export_earnings_today_c / 100.0
            + self.daily_fixed_charges_aud
        )

    @property
    def extras(self) -> dict[str, Any]:
        if self._last_price is None:
            return {
                "attribution": None,
                "region": self._region,
                "wholesale_price_aud_per_mwh": None,
                "wholesale_price_interval_end_utc": None,
                "wholesale_price_age_seconds": None,
                "daily_supply_aud": self.daily_fixed_charges_aud,
            }
        age = max(
            0,
            int(
                (datetime.now(tz=timezone.utc) - self._last_price.interval_end_utc).total_seconds()
            ),
        )
        return {
            "attribution": self._last_price.attribution,
            "region": self._region,
            "wholesale_price_aud_per_mwh": self._last_price.price_aud_per_mwh,
            "wholesale_price_interval_end_utc": (self._last_price.interval_end_utc.isoformat()),
            "wholesale_price_age_seconds": age,
            "daily_supply_aud": self.daily_fixed_charges_aud,
        }

    def to_dict(self) -> dict[str, Any]:
        # Codex P1-5 (2026-05-23) — persist the date the daily counters
        # apply to so ``from_dict`` can detect a cross-midnight restart
        # and zero the accumulators instead of restoring yesterday's
        # numbers as today's. ``_last_tick`` is set to ``now_local`` on
        # every coordinator tick, so its date is the HA-tz date the
        # daily counters belong to. ``None`` when no tick has run yet
        # (fresh provider) — ``from_dict`` treats missing/None as a
        # cross-midnight restart (safe default = reset).
        state_date = self._last_tick.date().isoformat() if self._last_tick else None
        return {
            "version": STATE_VERSION,
            "provider_id": self.id,
            "region": self._region,
            "daily_supply_c": self._daily_supply_c,
            "state_date": state_date,
            "import_kwh_today": self._import_kwh_today,
            "export_kwh_today": self._export_kwh_today,
            "import_cost_today_c": self._import_cost_today_c,
            "export_earnings_today_c": self._export_earnings_today_c,
            "last_tick_iso": (self._last_tick.isoformat() if self._last_tick else None),
            "last_price": (
                {
                    "price_aud_per_mwh": self._last_price.price_aud_per_mwh,
                    "interval_end_utc": (self._last_price.interval_end_utc.isoformat()),
                    "region": self._last_price.region,
                    "attribution": self._last_price.attribution,
                }
                if self._last_price
                else None
            ),
        }

    def from_dict(self, data: dict[str, Any], today: date | None = None) -> None:
        """Restore daily accumulators from a stored state dict.

        ``today`` MUST be a ``datetime.date`` in the HA-configured timezone
        (AEGIS rule — no ``date.today()`` fallback). ``data["version"]`` is
        validated against ``STATE_VERSION``.
        """
        if today is None:
            raise TypeError(
                "from_dict(today=) is required and must be a datetime.date "
                "in the HA-configured timezone (no date.today() fallback)."
            )
        stored_version = data.get("version")
        if stored_version != STATE_VERSION:
            raise ValueError(
                f"DWT state version {stored_version!r} not supported; "
                f"current is {STATE_VERSION}. Manual migration required."
            )

        # Codex P1-5 (2026-05-23) — compare the stored state date with
        # the supplied HA-tz date. A restart that crosses midnight would
        # otherwise resurrect yesterday's daily counters as today's, so
        # the chosen-plan cost sensor and the external statistics rows
        # would carry yesterday's totals plus today's accruals. Missing
        # / malformed state_date is treated as a cross-midnight restart
        # (safe default = reset counters to zero). The last_price +
        # last_tick fields are still restored — they're not daily
        # accumulators and survive midnight cleanly.
        stored_date_iso = data.get("state_date")
        stored_date: date | None
        if stored_date_iso:
            try:
                stored_date = date.fromisoformat(str(stored_date_iso))
            except (TypeError, ValueError):
                _LOGGER.warning(
                    "DWT restore: malformed state_date %r — treating as "
                    "cross-midnight restart; daily counters will reset.",
                    stored_date_iso,
                )
                stored_date = None
        else:
            stored_date = None

        if stored_date == today:
            self._import_kwh_today = float(data.get("import_kwh_today", 0.0))
            self._export_kwh_today = float(data.get("export_kwh_today", 0.0))
            self._import_cost_today_c = float(data.get("import_cost_today_c", 0.0))
            self._export_earnings_today_c = float(data.get("export_earnings_today_c", 0.0))
        else:
            _LOGGER.info(
                "DWT restore: state_date=%s != today=%s; resetting daily "
                "counters (kept last_price for continuity).",
                stored_date,
                today,
            )
            self._import_kwh_today = 0.0
            self._export_kwh_today = 0.0
            self._import_cost_today_c = 0.0
            self._export_earnings_today_c = 0.0

        last_tick_iso = data.get("last_tick_iso")
        if last_tick_iso:
            try:
                self._last_tick = datetime.fromisoformat(last_tick_iso)
            except ValueError:
                self._last_tick = None
        else:
            self._last_tick = None

        last_price = data.get("last_price")
        if last_price:
            try:
                self._last_price = WholesalePrice(
                    price_aud_per_mwh=float(last_price["price_aud_per_mwh"]),
                    interval_end_utc=datetime.fromisoformat(last_price["interval_end_utc"]),
                    region=str(last_price["region"]),
                    attribution=str(last_price["attribution"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                _LOGGER.warning("DWT restore: discarding malformed last_price (%s)", exc)
                self._last_price = None

    # ---- Public API used by the coordinator -------------------------------

    @property
    def price_source(self) -> OpenElectricityPriceSource | NEMWebPriceSource:
        return self._price_source

    @property
    def region(self) -> str:
        return self._region

    @property
    def last_price(self) -> WholesalePrice | None:
        return self._last_price

    def set_live_price(self, price: WholesalePrice) -> None:
        """Push the latest wholesale price into the provider.

        Idempotent on the same ``(region, interval_end_utc)`` tuple — setting
        the same price twice is a no-op (no log spam).
        """
        if (
            self._last_price is not None
            and self._last_price.region == price.region
            and self._last_price.interval_end_utc == price.interval_end_utc
        ):
            return
        self._last_price = price

    # ---- Helpers ----------------------------------------------------------

    def _warn_no_price_once(self, now_local: datetime) -> None:
        utc_today = (
            now_local.astimezone(timezone.utc).date()
            if now_local.tzinfo is not None
            else datetime.now(tz=timezone.utc).date()
        )
        if self._no_price_warned_for_utc_date == utc_today:
            return
        self._no_price_warned_for_utc_date = utc_today
        _LOGGER.warning(
            "DWT provider %s has no wholesale price yet for region %s; "
            "skipping cost accumulation until first refresh succeeds.",
            self.id,
            self._region,
        )
