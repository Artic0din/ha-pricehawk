"""PriceHawk coordinator — orchestrates Amber API polling and cost calculation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .amber_calculator import AmberCalculator
from .const import (
    AMBER_API_BASE_URL,
    AMBER_API_POLL_INTERVAL,
    CONF_AMBER_NETWORK_DAILY_CHARGE,
    CONF_AMBER_SUBSCRIPTION_FEE,
    CONF_API_KEY,
    CONF_CURRENT_PROVIDER,
    CONF_GRID_POWER_SENSOR,
    CONF_SITE_ID,
    COORDINATOR_SCAN_INTERVAL,
    DOMAIN,
    PERSIST_INTERVAL,
    PROVIDER_AMBER,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .tariff_engine import TariffEngine

_LOGGER = logging.getLogger(__name__)

# Amber API retry config (inspired by PowerSync)
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2  # seconds, doubles each attempt


class PriceHawkCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate Amber API polling, grid sensor reads, and cost calculation."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=COORDINATOR_SCAN_INTERVAL),
        )

        # Engines
        self._globird_engine = TariffEngine(entry.options)
        self._amber_calc = AmberCalculator(
            amber_network_daily_c=entry.options.get(CONF_AMBER_NETWORK_DAILY_CHARGE, 0.0),
            amber_subscription_daily_c=entry.options.get(CONF_AMBER_SUBSCRIPTION_FEE, 0.0),
        )

        # Config
        self._grid_power_entity: str = entry.options.get(CONF_GRID_POWER_SENSOR, "")
        self._api_key: str = entry.data.get(CONF_API_KEY, "")
        self._site_id: str = entry.data.get(CONF_SITE_ID, "")

        # Amber price cache (polled every 5 min, used every 30s)
        self._amber_import_c: float | None = None
        self._amber_export_c: float | None = None
        self._last_amber_poll: float = 0.0  # monotonic timestamp

        # Price history buffer (last 4032 points = 7 days at 30s intervals)
        self._price_history: list[dict] = []

        # Monthly saving accumulator
        now = dt_util.now()
        self._saving_month_aud: float = 0.0
        self._last_month: int = now.month
        self._last_date: int = now.day

        # Daily win tracking (who had the lowest total cost each day)
        self._daily_wins: dict = {"amber": 0, "globird": 0}

        # Daily cost history (last 30 days for historical comparison chart)
        self._daily_cost_history: list[dict] = []

        # Today's full price schedule (separate from live price_history)
        self._today_schedule: list[dict] = []

        # State persistence
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._persist_unsub: CALLBACK_TYPE | None = None

    # ------------------------------------------------------------------
    # Amber REST API polling
    # ------------------------------------------------------------------

    async def _poll_amber_prices(self) -> None:
        """Fetch current prices from Amber REST API.

        GET /v1/sites/{site_id}/prices/current
        Returns a list of price intervals; we want the current GENERAL
        (import) and FEED_IN (export) channels.

        Amber API perKwh is in c/kWh (cents per kWh, incl GST) — use directly.
        """
        data = await self._fetch_amber_with_retry()
        if data is None:
            return

        # Parse response — list of intervals with channelType and perKwh
        import_price = None
        export_price = None

        for interval in data:
            channel = interval.get("channelType", "")
            per_kwh = interval.get("perKwh")  # c/kWh (cents, incl GST)
            if per_kwh is None:
                continue
            if channel == "general" and import_price is None:
                import_price = float(per_kwh)
            elif channel == "feedIn" and export_price is None:
                export_price = abs(float(per_kwh))

        if import_price is not None:
            self._amber_import_c = import_price
        if export_price is not None:
            self._amber_export_c = export_price

        _LOGGER.debug(
            "Amber prices polled: import=%.2fc/kWh, export=%.2fc/kWh",
            self._amber_import_c or 0,
            self._amber_export_c or 0,
        )

    async def _fetch_amber_with_retry(self) -> list | None:
        """Fetch Amber API with exponential backoff retry on 429/5xx.

        Pattern follows PowerSync: retry on rate-limit and server errors,
        respect Retry-After header, fail fast on other 4xx.
        """
        url = f"{AMBER_API_BASE_URL}/sites/{self._site_id}/prices/current"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }

        session = async_get_clientsession(self.hass)

        for attempt in range(_MAX_RETRIES):
            try:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()

                    if resp.status == 429 or resp.status >= 500:
                        # Retryable — respect Retry-After or backoff
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after:
                            try:
                                delay = min(max(int(retry_after), 1), 30)
                            except ValueError:
                                # Retry-After can be an HTTP-date; fall back to backoff
                                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                        else:
                            delay = _RETRY_BASE_DELAY * (2 ** attempt)
                        _LOGGER.warning(
                            "Amber API returned %s (attempt %d/%d), retrying in %ds",
                            resp.status, attempt + 1, _MAX_RETRIES, delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                    # Non-retryable client error
                    _LOGGER.warning(
                        "Amber API returned status %s", resp.status
                    )
                    return None

            except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError) as err:
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    _LOGGER.warning(
                        "Amber API request failed (attempt %d/%d): %s, retrying in %ds",
                        attempt + 1, _MAX_RETRIES, err, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    _LOGGER.warning("Amber API request failed after %d attempts: %s", _MAX_RETRIES, err)
                    return None

        _LOGGER.warning("Amber API request failed after %d attempts", _MAX_RETRIES)
        return None

    async def _fetch_today_price_schedule(self) -> None:
        """Fetch today's full price schedule from Amber API.

        Populates price_history with all 48 half-hour intervals so the rate
        chart shows the full 24 hours from startup, not just from first poll.
        Also pairs each interval with the GloBird rate at that time.
        """
        today_str = dt_util.now().strftime("%Y-%m-%d")
        url = f"{AMBER_API_BASE_URL}/sites/{self._site_id}/prices"
        params = f"?startDate={today_str}&endDate={today_str}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }

        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                url + params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning(
                        "Failed to fetch today's price schedule: %s", resp.status
                    )
                    return
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("Error fetching price schedule: %s", err)
            return

        if not data:
            return

        # Build price points from the schedule
        from .tariff_engine import get_current_tou_period

        import_tariff = self.config_entry.options.get("import_tariff", {})
        export_tariff = self.config_entry.options.get("export_tariff", {})

        schedule_points: list[dict] = []
        for interval in data:
            channel = interval.get("channelType", "")
            start_time = interval.get("startTime") or interval.get("nemTime", "")
            per_kwh = interval.get("perKwh")

            if channel != "general" or per_kwh is None or not start_time:
                continue

            # Parse the timestamp
            try:
                ts = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue

            amber_import = float(per_kwh)

            # Find matching feedIn price for this interval
            amber_export = 0.0
            for fi in data:
                fi_start = fi.get("startTime") or fi.get("nemTime", "")
                if fi.get("channelType") == "feedIn" and fi_start == start_time:
                    amber_export = abs(float(fi.get("perKwh", 0)))
                    break

            # GloBird rates from config
            globird_import = 0.0
            globird_export = 0.0
            if import_tariff.get("type") == "tou":
                _, globird_import = get_current_tou_period(
                    import_tariff["periods"], ts
                )
            if export_tariff.get("type") == "tou":
                _, globird_export = get_current_tou_period(
                    export_tariff["periods"], ts
                )

            schedule_points.append({
                "t": ts.isoformat(),
                "ai": amber_import,
                "ae": amber_export,
                "gi": globird_import,
                "ge": globird_export,
            })

        if schedule_points:
            self._today_schedule = sorted(
                schedule_points,
                key=lambda p: p["t"],
            )
            _LOGGER.info(
                "Loaded %d price schedule points for today", len(schedule_points)
            )

    async def _maybe_poll_amber(self) -> None:
        """Poll Amber API if enough time has elapsed since last poll."""
        now_mono = self.hass.loop.time()
        if now_mono - self._last_amber_poll >= AMBER_API_POLL_INTERVAL:
            await self._poll_amber_prices()
            self._last_amber_poll = now_mono

    # ------------------------------------------------------------------
    # DataUpdateCoordinator._async_update_data (called every 30s)
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Read sensors, poll Amber, update both engines, return data dict."""
        # 0. On first run, fetch today's full price schedule for the rate chart
        if self._last_amber_poll == 0.0:
            await self._fetch_today_price_schedule()

        # 1. Poll Amber API (rate-limited to every 5 min)
        await self._maybe_poll_amber()

        # 2. Read grid power sensor
        grid_power_w = self._read_grid_power()
        now_local = dt_util.now()

        # 3. Monthly reset check (BEFORE daily check)
        if now_local.month != self._last_month:
            _LOGGER.info(
                "Monthly reset: accumulated saving $%.2f for month %d",
                self._saving_month_aud, self._last_month,
            )
            self._saving_month_aud = 0.0
            self._daily_wins = {"amber": 0, "globird": 0}
            # daily_cost_history NOT reset — keeps 6 months for historical chart
            self._last_month = now_local.month
            self._last_date = now_local.day

        # 4. Daily rollover — capture previous day's saving and winner
        if now_local.day != self._last_date:
            amber_cost = self._amber_calc.net_daily_cost_aud
            globird_cost = self._globird_engine.net_daily_cost_aud
            daily_saving = self._compute_saving(amber_cost, globird_cost)
            self._saving_month_aud += daily_saving

            # Track daily winner
            if amber_cost <= globird_cost:
                self._daily_wins["amber"] = self._daily_wins.get("amber", 0) + 1
            else:
                self._daily_wins["globird"] = self._daily_wins.get("globird", 0) + 1

            # Record daily cost history (capped at 30 days)
            yesterday = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")
            self._daily_cost_history.append({
                "date": yesterday,
                "amber": round(amber_cost, 2),
                "globird": round(globird_cost, 2),
            })
            if len(self._daily_cost_history) > 180:
                self._daily_cost_history = self._daily_cost_history[-180:]

            _LOGGER.info(
                "Daily rollover: saving=$%.2f, month=$%.2f, wins: amber=%d globird=%d",
                daily_saving, self._saving_month_aud,
                self._daily_wins["amber"], self._daily_wins["globird"],
            )
            self._last_date = now_local.day

            # Persist immediately after rollover to avoid data loss on crash
            await self.async_persist_state()

        # 5. Update GloBird engine (always, even without Amber prices)
        if grid_power_w is not None:
            self._globird_engine.update(grid_power_w, now_local)

        # 6. Update Amber calculator (only if we have prices AND grid power)
        if (
            grid_power_w is not None
            and self._amber_import_c is not None
            and self._amber_export_c is not None
        ):
            self._amber_calc.update(
                grid_power_w,
                self._amber_import_c,
                self._amber_export_c,
                now_local,
            )

        # 7. Return data dict for sensor entities
        return self._build_data_dict()

    def _compute_saving(self, amber_cost: float, globird_cost: float) -> float:
        """Compute directional saving based on current provider.

        Positive = you'd save by switching to the other provider.
        """
        current_provider = self.config_entry.data.get(CONF_CURRENT_PROVIDER, PROVIDER_AMBER)
        if current_provider == PROVIDER_AMBER:
            return amber_cost - globird_cost
        return globird_cost - amber_cost

    def _read_grid_power(self) -> float | None:
        """Read the grid power sensor state. Returns watts or None."""
        if not self._grid_power_entity:
            return None

        state = self.hass.states.get(self._grid_power_entity)
        if state is None or state.state in ("unavailable", "unknown", ""):
            _LOGGER.debug("Grid power sensor %s not available", self._grid_power_entity)
            return None

        try:
            value = float(state.state)
            # Convert kW to W if the sensor reports in kW
            unit = state.attributes.get("unit_of_measurement", "").lower()
            if unit == "kw":
                value *= 1000.0
            return value
        except (ValueError, TypeError):
            _LOGGER.debug(
                "Grid power sensor %s has non-numeric state: %s",
                self._grid_power_entity,
                state.state,
            )
            return None

    def _build_data_dict(self) -> dict[str, Any]:
        """Build the data dict consumed by sensor entities."""
        # Derive globird_peak_rate from config options
        globird_peak_rate: float | None = None
        import_tariff = self.config_entry.options.get("import_tariff", {})
        if import_tariff.get("type") == "tou":
            periods = import_tariff.get("periods", {})
            peak = periods.get("peak")
            if peak is not None:
                globird_peak_rate = peak.get("rate")
        elif import_tariff.get("type") == "flat_stepped":
            globird_peak_rate = import_tariff.get("step1_rate")

        # Derive metrics_won: how many of 3 metrics Amber beats GloBird
        amber_import = self._amber_import_c
        amber_export = self._amber_export_c
        globird_import = self._globird_engine.current_import_rate_c_kwh
        globird_export = self._globird_engine.current_export_rate_c_kwh
        amber_daily = self._amber_calc.net_daily_cost_aud
        globird_daily = self._globird_engine.net_daily_cost_aud

        if amber_import is not None and amber_export is not None:
            metrics = [
                amber_import < globird_import,   # lower import rate
                amber_export > globird_export,   # higher export earning
                amber_daily < globird_daily,     # cheaper today
            ]
            metrics_won = f"{sum(metrics)}/{len(metrics)}"
        else:
            metrics_won = "0/3"

        # Check if ZEROHERO incentive is enabled
        incentives = self.config_entry.options.get("incentives", {})
        has_zerohero = incentives.get("zerohero_credit", False) if isinstance(incentives, dict) else "zerohero_credit" in incentives

        # GloBird daily supply charge (full day value, not prorated)
        globird_supply_aud = self.config_entry.options.get("daily_supply_charge", 0.0) / 100.0

        data = {
            "globird_import_rate": globird_import,
            "globird_export_rate": globird_export,
            "globird_daily_cost": globird_daily,
            "globird_daily_supply_aud": globird_supply_aud,
            "globird_import_cost_aud": self._globird_engine.import_cost_today_c / 100.0,
            "globird_export_credit_aud": self._globird_engine.export_earnings_today_c / 100.0,
            "globird_import_kwh": self._globird_engine.import_kwh_today,
            "globird_export_kwh": self._globird_engine.export_kwh_today,
            "globird_zerohero_status": self._globird_engine.zerohero_status if has_zerohero else None,
            "globird_super_export_kwh": self._globird_engine.super_export_kwh if has_zerohero else None,
            "amber_import_rate": amber_import,
            "amber_export_rate": amber_export,
            "amber_daily_cost": amber_daily,
            "amber_daily_fixed_charges": self._amber_calc.daily_fixed_charges_aud,
            "amber_import_cost_aud": self._amber_calc.import_cost_today_c / 100.0,
            "amber_export_credit_aud": self._amber_calc.export_earnings_today_c / 100.0,
            "amber_import_kwh": self._amber_calc.import_kwh_today,
            "amber_export_kwh": self._amber_calc.export_kwh_today,
            # Directional saving
            "saving_today": self._compute_saving(amber_daily, globird_daily),
            "saving_month_aud": self._saving_month_aud,
            "globird_peak_rate": globird_peak_rate,
            "amber_peak_rate": self._amber_import_c,
            "metrics_won": metrics_won,
            "last_updated": dt_util.now(),
            "daily_wins": self._daily_wins,
            "daily_cost_history": self._daily_cost_history,
        }

        # Record price snapshot every 5 min (2016 points = 7 days)
        now_ts = dt_util.now()
        last_ph_time = self._price_history[-1]["t"] if self._price_history else ""
        # Only append if 5+ minutes since last point
        if not last_ph_time or (now_ts - datetime.fromisoformat(last_ph_time)).total_seconds() >= 290:
            self._price_history.append({
                "t": now_ts.isoformat(),
                "ai": amber_import,
                "ae": amber_export,
                "gi": globird_import,
                "ge": globird_export,
            })
            if len(self._price_history) > 2016:
                self._price_history = self._price_history[-2016:]

        data["price_history"] = list(self._price_history)
        data["today_schedule"] = list(self._today_schedule)
        _LOGGER.debug("Price history: %d points, schedule: %d points", len(self._price_history), len(self._today_schedule))
        return data

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    async def async_restore_state(self) -> None:
        """Restore engine state from Store on startup."""
        stored = await self._store.async_load()
        if not stored or not isinstance(stored, dict):
            _LOGGER.info("No stored state to restore, starting fresh")
            return

        globird_data = stored.get("globird")
        amber_data = stored.get("amber")

        today = dt_util.now().date()

        if globird_data:
            self._globird_engine = TariffEngine.from_dict(
                self.config_entry.options, globird_data, today=today
            )
            _LOGGER.debug("Restored GloBird engine state")

        if amber_data:
            self._amber_calc.from_dict(amber_data, today=today)
            _LOGGER.debug("Restored Amber calculator state")

        # Restore cached Amber prices
        if stored.get("amber_import_c") is not None:
            self._amber_import_c = stored["amber_import_c"]
        if stored.get("amber_export_c") is not None:
            self._amber_export_c = stored["amber_export_c"]

        # Restore monthly accumulator
        if stored.get("saving_month_aud") is not None:
            self._saving_month_aud = stored["saving_month_aud"]
        if stored.get("last_month") is not None:
            self._last_month = stored["last_month"]
        if stored.get("last_date") is not None:
            self._last_date = stored["last_date"]

        # Restore price history and daily wins
        if stored.get("price_history"):
            self._price_history = stored["price_history"]
        if stored.get("daily_wins"):
            self._daily_wins = stored["daily_wins"]
        if stored.get("daily_cost_history"):
            self._daily_cost_history = stored["daily_cost_history"]
        if stored.get("today_schedule"):
            self._today_schedule = stored["today_schedule"]

        _LOGGER.info(
            "Restored state: amber=%.2f/%.2fc, month_saving=$%.2f",
            self._amber_import_c or 0,
            self._amber_export_c or 0,
            self._saving_month_aud,
        )

    async def async_persist_state(self) -> None:
        """Save engine state to Store."""
        data = {
            "globird": self._globird_engine.to_dict(),
            "amber": self._amber_calc.to_dict(),
            "amber_import_c": self._amber_import_c,
            "amber_export_c": self._amber_export_c,
            "saving_month_aud": self._saving_month_aud,
            "last_month": self._last_month,
            "last_date": self._last_date,
            "price_history": self._price_history,
            "daily_wins": self._daily_wins,
            "daily_cost_history": self._daily_cost_history,
            "today_schedule": self._today_schedule,
        }
        await self._store.async_save(data)
        _LOGGER.debug("Persisted coordinator state")

    def schedule_persist(self) -> None:
        """Schedule recurring state persistence every PERSIST_INTERVAL seconds."""
        async def _persist_callback(_now: Any) -> None:
            await self.async_persist_state()
            # Reschedule
            self._persist_unsub = async_call_later(
                self.hass, PERSIST_INTERVAL, _persist_callback
            )

        self._persist_unsub = async_call_later(
            self.hass, PERSIST_INTERVAL, _persist_callback
        )

    def cancel_persist(self) -> None:
        """Cancel the scheduled persist callback."""
        if self._persist_unsub is not None:
            self._persist_unsub()
            self._persist_unsub = None

    # ------------------------------------------------------------------
    # Options update / engine rebuild
    # ------------------------------------------------------------------

    def rebuild_engine(self, new_options: dict) -> None:
        """Rebuild GloBird engine with new options (tariff changed).

        Also rebuilds Amber calculator to pick up changed fee values.
        """
        self._globird_engine = TariffEngine(new_options)
        self._amber_calc = AmberCalculator(
            amber_network_daily_c=new_options.get(CONF_AMBER_NETWORK_DAILY_CHARGE, 0.0),
            amber_subscription_daily_c=new_options.get(CONF_AMBER_SUBSCRIPTION_FEE, 0.0),
        )
        self._grid_power_entity = new_options.get(CONF_GRID_POWER_SENSOR, "")
        _LOGGER.info("Rebuilt engines with updated options")
