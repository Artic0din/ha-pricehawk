"""PriceHawk coordinator — orchestrates Amber API polling and cost calculation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

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
from .aemo_api import fetch_current_rrp
from .const import (
    AEMO_API_POLL_INTERVAL,
    CONF_AMBER_ENABLED,
    CONF_FLOW_POWER_ENABLED,
    CONF_FLOW_POWER_REGION,
    CONF_LOCALVOLTS_API_KEY,
    CONF_LOCALVOLTS_ENABLED,
    CONF_LOCALVOLTS_NMI,
    CONF_LOCALVOLTS_PARTNER_ID,
    LOCALVOLTS_API_POLL_INTERVAL,
)
from .explanation import build_explanation
from .localvolts_api import aggregate_to_half_hour, fetch_recent_intervals
from .providers.cdr_plan import CdrPlanProvider
from .providers import (
    AmberProvider,
    FlowPowerProvider,
    LocalVoltsProvider,
    Provider,
)

_LOGGER = logging.getLogger(__name__)

# Amber API retry config (inspired by PowerSync)
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2  # seconds, doubles each attempt


def _extract_peak_rate_c_inc_gst(cdr_plan: dict[str, Any] | None) -> float | None:
    """Phase 3.0e — pull PEAK rate from a CDR plan envelope.

    Walks the optional nested chain
    `cdr_plan.data.electricityContract.tariffPeriod[0]` → reads
    `rateBlockUType` to find the active rate block (timeOfUseRates,
    singleRate, …) → finds the period with `type == "PEAK"` → returns
    the first rate's `unitPrice` converted to inc-GST cents (× 100 × 1.10).

    Returns None on ANY missing key, malformed type, empty list, or
    non-numeric unitPrice. Caller treats None as "rate unknown" and
    leaves the sensor as `unavailable`.

    Module-level + free-standing so it's unit-testable without an HA
    runtime, and so future Phase 3.1 ranking logic can reuse the same
    derivation across N alternative plans.
    """
    if not cdr_plan:
        return None
    try:
        tp = (
            cdr_plan.get("data", {})
            .get("electricityContract", {})
            .get("tariffPeriod", [])
        )
    except (AttributeError, TypeError):
        return None
    if not tp or not isinstance(tp, list):
        return None
    period_block = tp[0]
    if not isinstance(period_block, dict):
        return None

    block_key = period_block.get("rateBlockUType") or ""
    block = period_block.get(block_key, {})
    if isinstance(block, dict):
        periods = block.get("timeOfUseRates", []) or []
    elif isinstance(block, list):
        periods = block
    else:
        return None

    for period in periods:
        if not isinstance(period, dict):
            continue
        if (period.get("type") or "").upper() != "PEAK":
            continue
        rates = period.get("rates") or []
        if not rates or not isinstance(rates[0], dict):
            continue
        try:
            ex_gst = float(rates[0].get("unitPrice", 0))
        except (TypeError, ValueError):
            return None
        # CDR unitPrice is ex-GST $/kWh. × 100 → c/kWh. × 1.10 → inc-GST.
        return ex_gst * 100.0 * 1.10
    return None


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

        # Phase 3.0c: every entry has a `cdr_plan` envelope. The legacy
        # manual-tariff path (GloBirdProvider) is dead code now and gets
        # removed in Phase 3.0d once the wizard rewrite enforces this
        # invariant for new installs. Existing entries from Phase 2.x
        # without cdr_plan are unsupported per the no-migration policy.
        cdr_plan = entry.options.get("cdr_plan")
        if not cdr_plan:
            raise ConfigEntryNotReady(
                "PriceHawk entry is missing 'cdr_plan' option. "
                "Per Phase 3 'no migration' policy: remove this integration "
                "and re-add it through the new wizard."
            )
        # Phase 2.12.1: pass entry.options for opt-in fields
        # (ovo_interest_balance_aud, vpp_batteries_enrolled). The provider
        # plumbs these to the streaming engine → evaluator →
        # per-retailer incentive parsers.
        self._current_plan_provider: Provider = CdrPlanProvider(
            cdr_plan, entry_options=dict(entry.options),
        )
        _LOGGER.info("Using CdrPlanProvider (CDR plan %s)",
                     cdr_plan.get("data", {}).get("planId", "?"))
        self._providers: dict[str, Provider] = {
            self._current_plan_provider.id: self._current_plan_provider,
        }

        # Flow Power is universally enabled by default (uses AEMO direct,
        # no credentials required); user can disable via options flow.
        self._flow_power: FlowPowerProvider | None = None
        if entry.options.get(CONF_FLOW_POWER_ENABLED, False):
            self._flow_power = FlowPowerProvider(entry.options)
            self._providers[self._flow_power.id] = self._flow_power

        # Amber only registers when the user is actually an Amber customer
        # (i.e. they provided an API key during setup or via options).
        self._amber: AmberProvider | None = None
        amber_enabled = entry.options.get(CONF_AMBER_ENABLED)
        if amber_enabled is None:
            # Back-compat: pre-existing installs always had Amber enabled.
            amber_enabled = bool(entry.data.get(CONF_API_KEY))
        if amber_enabled:
            self._amber = AmberProvider(
                amber_network_daily_c=entry.options.get(CONF_AMBER_NETWORK_DAILY_CHARGE, 0.0),
                amber_subscription_daily_c=entry.options.get(CONF_AMBER_SUBSCRIPTION_FEE, 0.0),
            )
            self._providers[self._amber.id] = self._amber

        # LocalVolts only registers when the user is actually a LocalVolts
        # customer (API key collected at setup or via options).
        self._localvolts: LocalVoltsProvider | None = None
        if entry.options.get(CONF_LOCALVOLTS_ENABLED):
            self._localvolts = LocalVoltsProvider(entry.options)
            self._providers[self._localvolts.id] = self._localvolts

        # Wholesale RRP fetched from AEMO NEMWeb dispatch reports (Flow Power
        # input). c/kWh, signed (can be negative). NOT sourced from Amber's
        # spotPerKwh which bundles network charges, and NOT requiring an
        # Amber API key.
        self._wholesale_c: float | None = None
        self._wholesale_settlement: str = ""
        self._last_aemo_poll: float = 0.0

        # Amber 24-hour forecast (computed from /prices/current?next=48)
        self._forecast_peak_c: float | None = None
        self._forecast_peak_at: str = ""
        self._forecast_dip_c: float | None = None
        self._forecast_dip_at: str = ""
        self._forecast_avg_c: float | None = None
        self._forecast_intervals: list[dict[str, Any]] = []

        # LocalVolts API state
        self._localvolts_import_c: float | None = None
        self._localvolts_export_c: float | None = None
        self._last_localvolts_poll: float = 0.0

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

        # Daily win tracking (who had the lowest total cost each day) — keys
        # are provider IDs; auto-extends as new providers are registered.
        self._daily_wins: dict[str, int] = {pid: 0 for pid in self._providers}

        # Daily cost history (last 180 days for historical comparison chart)
        self._daily_cost_history: list[dict] = []

        # Most-recent end-of-day "Why X won" explanation snapshot
        self._last_explanation: dict | None = None

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

        # Parse response — list of intervals with channelType and perKwh.
        # The "current" interval is the one with type=="CurrentInterval";
        # later entries (next=48) are forecast intervals tagged
        # "ForecastInterval".
        # We deliberately do NOT use spotPerKwh as the wholesale source for
        # Flow Power because Amber's "spot" field bundles network charges.
        # Wholesale RRP is fetched separately from AEMO NEMWeb.
        import_price = None
        export_price = None
        forecast_intervals: list[tuple[str, float]] = []

        for interval in data:
            channel = interval.get("channelType", "")
            per_kwh = interval.get("perKwh")  # c/kWh (cents, incl GST)
            if per_kwh is None:
                continue

            interval_type = interval.get("type", "")
            if interval_type == "CurrentInterval":
                if channel == "general" and import_price is None:
                    import_price = float(per_kwh)
                elif channel == "feedIn" and export_price is None:
                    export_price = abs(float(per_kwh))
            elif interval_type == "ForecastInterval" and channel == "general":
                start_time = (
                    interval.get("startTime") or interval.get("nemTime") or ""
                )
                forecast_intervals.append((start_time, float(per_kwh)))

        if import_price is not None:
            self._amber_import_c = import_price
        if export_price is not None:
            self._amber_export_c = export_price

        # Update forecast peak/dip/avg
        if forecast_intervals:
            self._update_amber_forecast(forecast_intervals)

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
        # ?next=48 returns the next 48 forecast intervals (24 h forward) so
        # we can populate forecast peak/dip/avg sensors from the same call
        # used to fetch the current price.
        url = (
            f"{AMBER_API_BASE_URL}/sites/{self._site_id}/prices/current"
            "?next=48&previous=0"
        )
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

        # Phase 3.0g (CodeRabbit): legacy `import_tariff` / `export_tariff`
        # options are dead under the cdr_plan-only invariant. Reading them
        # returned `gi=0.0 ge=0.0` for every interval, painting the
        # current plan as free all day on the comparison chart.
        # Keep Amber points only (still useful for the Amber-side chart);
        # current-plan-rates per-interval will be back in Phase 3.1
        # ranking when we evaluate the CDR plan against the schedule.
        schedule_points: list[dict] = []
        for interval in data:
            channel = interval.get("channelType", "")
            start_time = interval.get("startTime") or interval.get("nemTime", "")
            per_kwh = interval.get("perKwh")

            if channel != "general" or per_kwh is None or not start_time:
                continue

            try:
                ts = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue

            amber_import = float(per_kwh)
            amber_export = 0.0
            for fi in data:
                fi_start = fi.get("startTime") or fi.get("nemTime", "")
                if fi.get("channelType") == "feedIn" and fi_start == start_time:
                    amber_export = abs(float(fi.get("perKwh", 0)))
                    break

            schedule_points.append({
                "t": ts.isoformat(),
                "ai": amber_import,
                "ae": amber_export,
            })

        if schedule_points:
            self._today_schedule = sorted(
                schedule_points,
                key=lambda p: p["t"],
            )
            _LOGGER.info(
                "Loaded %d price schedule points for today", len(schedule_points)
            )

    def _update_amber_forecast(
        self, intervals: list[tuple[str, float]]
    ) -> None:
        """Compute peak / dip / average over the forecast intervals.

        Each tuple is (start_time_iso, c_per_kwh). Updates the cached
        peak/dip/avg state plus a ``_forecast_intervals`` list used by
        chart-style attributes.
        """
        if not intervals:
            return
        # Find peak (max) and dip (min)
        peak_idx = max(range(len(intervals)), key=lambda i: intervals[i][1])
        dip_idx = min(range(len(intervals)), key=lambda i: intervals[i][1])
        avg = sum(price for _, price in intervals) / len(intervals)

        self._forecast_peak_at, self._forecast_peak_c = intervals[peak_idx]
        self._forecast_dip_at, self._forecast_dip_c = intervals[dip_idx]
        self._forecast_avg_c = avg
        self._forecast_intervals = [
            {"start_time": t, "c_kwh": p} for t, p in intervals
        ]

    async def _maybe_poll_amber(self) -> None:
        """Poll Amber API if enough time has elapsed since last poll."""
        now_mono = self.hass.loop.time()
        if now_mono - self._last_amber_poll >= AMBER_API_POLL_INTERVAL:
            await self._poll_amber_prices()
            self._last_amber_poll = now_mono

    async def _maybe_poll_aemo(self) -> None:
        """Poll AEMO NEMWeb for the latest dispatch RRP (Flow Power input).

        Only runs when Flow Power is configured. Updates ``_wholesale_c``
        which is then pushed to FlowPowerProvider via set_wholesale_rate.
        """
        if self._flow_power is None:
            return
        now_mono = self.hass.loop.time()
        if now_mono - self._last_aemo_poll < AEMO_API_POLL_INTERVAL:
            return

        region = self.config_entry.options.get(CONF_FLOW_POWER_REGION, "NSW1")
        session = async_get_clientsession(self.hass)
        try:
            result = await fetch_current_rrp(session, region)
        except ValueError:
            _LOGGER.warning("Invalid AEMO region configured: %s", region)
            return

        if result is not None:
            self._wholesale_c, self._wholesale_settlement = result
            _LOGGER.debug(
                "AEMO RRP polled: %.2fc/kWh (%s, settlement %s)",
                self._wholesale_c,
                region,
                self._wholesale_settlement,
            )
        self._last_aemo_poll = now_mono

    async def _maybe_poll_localvolts(self) -> None:
        """Poll LocalVolts API every LOCALVOLTS_API_POLL_INTERVAL seconds."""
        if self._localvolts is None:
            return
        now_mono = self.hass.loop.time()
        if now_mono - self._last_localvolts_poll < LOCALVOLTS_API_POLL_INTERVAL:
            return

        opts = self.config_entry.options
        api_key = opts.get(CONF_LOCALVOLTS_API_KEY, "")
        partner_id = opts.get(CONF_LOCALVOLTS_PARTNER_ID, "")
        nmi = opts.get(CONF_LOCALVOLTS_NMI, "")
        if not (api_key and partner_id and nmi):
            return

        session = async_get_clientsession(self.hass)
        intervals = await fetch_recent_intervals(
            session, api_key, partner_id, nmi
        )
        imp_c, exp_c = aggregate_to_half_hour(intervals)
        if imp_c is not None:
            self._localvolts_import_c = imp_c
        if exp_c is not None:
            self._localvolts_export_c = exp_c
        self._last_localvolts_poll = now_mono

        _LOGGER.debug(
            "LocalVolts polled: import=%.2fc/kWh export=%.2fc/kWh",
            self._localvolts_import_c or 0,
            self._localvolts_export_c or 0,
        )

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

        # 1a. Poll AEMO NEMWeb for wholesale RRP (Flow Power input).
        # Independent of Amber — works for users with no Amber account.
        await self._maybe_poll_aemo()

        # 1b. Poll LocalVolts API (rate-limited)
        await self._maybe_poll_localvolts()

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
            self._daily_wins = {pid: 0 for pid in self._providers}
            # daily_cost_history NOT reset — keeps 6 months for historical chart
            self._last_month = now_local.month
            self._last_date = now_local.day

        # 4. Daily rollover — capture previous day's saving, winner, and
        # build the Why-X-won explanation snapshot.
        if now_local.day != self._last_date:
            globird_cost = self._current_plan_provider.net_daily_cost_aud
            # CR-fix: don't pollute saving_month_aud when Amber isn't
            # configured. Previously fell back to amber_cost=0 →
            # _compute_saving(0, plan) returned a real-looking saving
            # delta against a non-existent provider.
            if self._amber is not None:
                amber_cost = self._amber.net_daily_cost_aud
                daily_saving = self._compute_saving(amber_cost, globird_cost)
                self._saving_month_aud += daily_saving

            # Find winner across all registered providers
            winner_id = min(
                self._providers,
                key=lambda pid: self._providers[pid].net_daily_cost_aud,
            )
            self._daily_wins[winner_id] = self._daily_wins.get(winner_id, 0) + 1

            # Record daily cost history (capped at 180 days)
            yesterday = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")
            history_entry: dict[str, Any] = {"date": yesterday}
            for pid, p in self._providers.items():
                history_entry[pid] = round(p.net_daily_cost_aud, 2)
            self._daily_cost_history.append(history_entry)
            if len(self._daily_cost_history) > 180:
                self._daily_cost_history = self._daily_cost_history[-180:]

            # Build the explanation BEFORE resetting accumulators
            avg_spot = None
            if self._amber and self._amber.import_kwh_today > 0:
                avg_spot = (
                    self._amber.import_cost_today_c
                    / self._amber.import_kwh_today
                )
            explanation = build_explanation(
                self._build_providers_block(),
                avg_amber_spot_c_kwh=avg_spot,
            )
            self._last_explanation = explanation.to_dict()

            _LOGGER.info(
                "Daily rollover: winner=%s saving=$%.2f month=$%.2f wins=%s",
                winner_id, daily_saving, self._saving_month_aud,
                self._daily_wins,
            )
            self._last_date = now_local.day

            # Persist immediately after rollover to avoid data loss on crash
            await self.async_persist_state()

        # 5. Push current externally-sourced rates into providers that need them
        if self._amber is not None:
            self._amber.set_current_rates(
                self._amber_import_c, self._amber_export_c
            )
        if self._flow_power is not None:
            self._flow_power.set_wholesale_rate(self._wholesale_c)
        if self._localvolts is not None:
            self._localvolts.set_current_rates(
                self._localvolts_import_c, self._localvolts_export_c
            )

        # 6. Tick every registered provider (no-ops gracefully if a provider
        # is missing rates).
        if grid_power_w is not None:
            for provider in self._providers.values():
                provider.update(grid_power_w, now_local)

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

    def _build_providers_block(self) -> dict[str, dict[str, Any]]:
        """Build a generic per-provider snapshot for the sensor layer.

        Used by sensor.pricehawk_<provider>_<metric> entities. Each entry
        carries the standard rate/cost/kwh metrics plus any provider-
        specific extras.
        """
        block: dict[str, dict[str, Any]] = {}
        for pid, provider in self._providers.items():
            block[pid] = {
                "name": provider.name,
                "import_rate_c_kwh": provider.current_import_rate_c_kwh,
                "export_rate_c_kwh": provider.current_export_rate_c_kwh,
                "import_kwh_today": provider.import_kwh_today,
                "export_kwh_today": provider.export_kwh_today,
                "import_cost_today_aud": provider.import_cost_today_c / 100.0,
                "export_credit_today_aud": (
                    provider.export_earnings_today_c / 100.0
                ),
                "daily_fixed_charges_aud": provider.daily_fixed_charges_aud,
                "net_daily_cost_aud": provider.net_daily_cost_aud,
                "extras": provider.extras,
            }
        return block

    def _build_data_dict(self) -> dict[str, Any]:
        """Build the data dict consumed by sensor entities."""
        # Phase 3.0e: derive current_plan_peak_rate from the CDR plan
        # via _extract_peak_rate_c_inc_gst (module-level helper).
        cdr_plan = self.config_entry.options.get("cdr_plan") or {}
        current_plan_peak_rate = _extract_peak_rate_c_inc_gst(cdr_plan)

        # Derive metrics_won: how many of 3 metrics Amber beats current plan.
        # Phase 3.0g (CodeRabbit): only meaningful when Amber is configured.
        # Returning "0/3" with amber_daily=0.0 when Amber is absent makes
        # the dashboard pretend the current plan is losing to a phantom
        # zero-cost provider. None signals "no comparison available".
        amber_import = self._amber_import_c
        amber_export = self._amber_export_c
        current_plan_import = self._current_plan_provider.current_import_rate_c_kwh
        current_plan_export = self._current_plan_provider.current_export_rate_c_kwh
        amber_daily: float | None
        if self._amber is not None:
            amber_daily = self._amber.net_daily_cost_aud
        else:
            amber_daily = None
        current_plan_daily = self._current_plan_provider.net_daily_cost_aud

        if (
            self._amber is not None
            and amber_import is not None
            and amber_export is not None
            and amber_daily is not None
        ):
            metrics = [
                amber_import < current_plan_import,
                amber_export > current_plan_export,
                amber_daily < current_plan_daily,
            ]
            metrics_won = f"{sum(metrics)}/{len(metrics)}"
        else:
            metrics_won = None

        # Check if ZEROHERO incentive is enabled — legacy options OR CDR plan
        incentives = self.config_entry.options.get("incentives", {})
        has_zerohero = (
            incentives.get("zerohero_credit", False)
            if isinstance(incentives, dict)
            else "zerohero_credit" in incentives
        )
        if not has_zerohero:
            cdr_plan = self.config_entry.options.get("cdr_plan") or {}
            cdr_incentives = (
                cdr_plan.get("data", {})
                .get("electricityContract", {})
                .get("incentives", [])
                or []
            )
            for inc in cdr_incentives:
                name = (inc.get("displayName") or "").lower()
                if "zerohero" in name and "credit" in name:
                    has_zerohero = True
                    break

        # GloBird daily supply charge (full day value, inc-GST).
        # CDR plan: read from tariffPeriod[0].dailySupplyCharge (ex-GST AUD, ×1.10).
        # Legacy: read from options.daily_supply_charge (cents, /100).
        cdr_plan = self.config_entry.options.get("cdr_plan") or {}
        cdr_supply_aud_ex_gst = None
        if cdr_plan:
            try:
                tp = (
                    cdr_plan.get("data", {})
                    .get("electricityContract", {})
                    .get("tariffPeriod", [])
                )
                if tp:
                    cdr_supply_aud_ex_gst = float(tp[0].get("dailySupplyCharge", 0))
            except (KeyError, TypeError, ValueError):
                cdr_supply_aud_ex_gst = None
        if cdr_supply_aud_ex_gst is not None and cdr_supply_aud_ex_gst > 0:
            current_plan_supply_aud = cdr_supply_aud_ex_gst * 1.10
        else:
            current_plan_supply_aud = (
                self.config_entry.options.get("daily_supply_charge", 0.0) / 100.0
            )

        data = {
            "current_plan_import_rate": current_plan_import,
            "current_plan_export_rate": current_plan_export,
            "current_plan_daily_cost": current_plan_daily,
            "current_plan_daily_supply_aud": current_plan_supply_aud,
            "current_plan_import_cost_aud": self._current_plan_provider.import_cost_today_c / 100.0,
            "current_plan_export_credit_aud": self._current_plan_provider.export_earnings_today_c / 100.0,
            "current_plan_import_kwh": self._current_plan_provider.import_kwh_today,
            "current_plan_export_kwh": self._current_plan_provider.export_kwh_today,
            "current_plan_zerohero_status": self._current_plan_provider.extras["zerohero_status"] if has_zerohero else None,
            "current_plan_super_export_kwh": self._current_plan_provider.extras["super_export_kwh"] if has_zerohero else None,
            "amber_import_rate": amber_import,
            "amber_export_rate": amber_export,
            "amber_daily_cost": amber_daily,
            "amber_daily_fixed_charges": (
                self._amber.daily_fixed_charges_aud if self._amber else 0.0
            ),
            "amber_import_cost_aud": (
                self._amber.import_cost_today_c / 100.0 if self._amber else 0.0
            ),
            "amber_export_credit_aud": (
                self._amber.export_earnings_today_c / 100.0
                if self._amber
                else 0.0
            ),
            "amber_import_kwh": (
                self._amber.import_kwh_today if self._amber else 0.0
            ),
            "amber_export_kwh": (
                self._amber.export_kwh_today if self._amber else 0.0
            ),
            # Directional saving — None when Amber not configured
            # (can't compute saving against a phantom $0 baseline).
            "saving_today": (
                self._compute_saving(amber_daily, current_plan_daily)
                if amber_daily is not None
                else None
            ),
            "saving_month_aud": self._saving_month_aud,
            "current_plan_peak_rate": current_plan_peak_rate,
            "current_plan_name": self._current_plan_provider.name,
            "amber_peak_rate": self._amber_import_c,
            # Wholesale spot from Amber API (input to Flow Power)
            "wholesale_c_kwh": self._wholesale_c,
            # Generic per-provider data block — keyed by provider id, used by
            # the new pricehawk_<provider>_* sensors. Always present, even
            # if a provider is disabled (in which case its entry is omitted).
            "providers": self._build_providers_block(),
            # Most-recent end-of-day explanation snapshot (None until first
            # daily rollover happens).
            "last_explanation": self._last_explanation,
            # Amber 24h forecast (from /prices/current?next=48)
            "amber_forecast_peak_c_kwh": self._forecast_peak_c,
            "amber_forecast_peak_at": self._forecast_peak_at,
            "amber_forecast_dip_c_kwh": self._forecast_dip_c,
            "amber_forecast_dip_at": self._forecast_dip_at,
            "amber_forecast_avg_c_kwh": self._forecast_avg_c,
            "amber_forecast_intervals": list(self._forecast_intervals),
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
                "gi": current_plan_import,
                "ge": current_plan_export,
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
        """Restore engine state from Store on startup.

        Phase 2.11.5: after the standard persist-restore, run a
        replay-today pass for any provider that lacks restored state
        (mid-day comparator enable, fresh install, or missing field in
        the persisted store). Replay fetches today's grid power history
        + retailer rates and seeds the accumulator so the dashboard
        reflects today's true totals immediately rather than starting
        from $0 and slowly catching up.

        Phase 3.0g (CodeRabbit): validates `_storage_version` field
        in the persisted dict matches the in-code STORAGE_VERSION
        before restoring. The HA Store class auto-bumps version inside
        a manifest envelope, but Phase 1.x persisted directly without
        a version sentinel, so a future schema change would silently
        load mismatched data. Explicit validation makes drift loud.
        """
        stored = await self._store.async_load()
        today = dt_util.now().date()
        amber_was_restored = False

        if stored and isinstance(stored, dict):
            stored_version = stored.get("_storage_version")
            if stored_version is not None and stored_version != STORAGE_VERSION:
                _LOGGER.warning(
                    "Persisted state version %s != current STORAGE_VERSION %s; "
                    "discarding stored data. Today will rebuild from API replay.",
                    stored_version, STORAGE_VERSION,
                )
                stored = None
        if stored and isinstance(stored, dict):
            globird_data = stored.get("globird")
            amber_data = stored.get("amber")

            if globird_data:
                self._current_plan_provider.from_dict(globird_data, today=today)
                _LOGGER.debug("Restored GloBird provider state")

            if amber_data and self._amber is not None:
                self._amber.from_dict(amber_data, today=today)
                amber_was_restored = True
                _LOGGER.debug("Restored Amber provider state")

            # Restore optional providers if enabled and persisted
            if self._flow_power is not None and stored.get("flow_power"):
                self._flow_power.from_dict(stored["flow_power"], today=today)
            if self._localvolts is not None and stored.get("localvolts"):
                self._localvolts.from_dict(stored["localvolts"], today=today)

            # Restore cached rates
            if stored.get("amber_import_c") is not None:
                self._amber_import_c = stored["amber_import_c"]
            if stored.get("amber_export_c") is not None:
                self._amber_export_c = stored["amber_export_c"]
            if stored.get("wholesale_c") is not None:
                self._wholesale_c = stored["wholesale_c"]
            if stored.get("localvolts_import_c") is not None:
                self._localvolts_import_c = stored["localvolts_import_c"]
            if stored.get("localvolts_export_c") is not None:
                self._localvolts_export_c = stored["localvolts_export_c"]

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
            if stored.get("last_explanation"):
                self._last_explanation = stored["last_explanation"]

            _LOGGER.info(
                "Restored state: amber=%.2f/%.2fc, month_saving=$%.2f",
                self._amber_import_c or 0,
                self._amber_export_c or 0,
                self._saving_month_aud,
            )
        else:
            _LOGGER.info("No stored state to restore, starting fresh")

        # Phase 2.11.5: backfill today's totals for any unrestored
        # provider so dashboards reflect real spend immediately on a
        # fresh install or mid-day comparator enable.
        if self._amber is not None and not amber_was_restored:
            await self._replay_amber_today_from_api()

    async def _replay_amber_today_from_api(self) -> None:
        """Replay today's grid-power history through AmberProvider.

        Seeds the live accumulator (import_cost_today_c,
        export_earnings_today_c, kwh) with today's true totals computed
        from HA recorder history + Amber `/sites/{id}/prices` data.
        Idempotent: callers gate on "did persist restore this provider?"
        so we don't overwrite a freshly-restored accumulator.

        Bails silently on any setup gap (no API key, no grid sensor, no
        history rows). The next live coordinator tick takes over from
        wherever we leave the accumulator.
        """
        if not self._api_key or not self._site_id or not self._grid_power_entity:
            _LOGGER.info("Amber replay skipped: missing api_key/site_id/grid sensor")
            return
        if self._amber is None:
            return

        from datetime import timedelta as _td  # noqa: PLC0415

        try:
            from homeassistant.components.recorder import get_instance  # noqa: PLC0415
            from homeassistant.components.recorder.history import (  # noqa: PLC0415
                state_changes_during_period,
            )
        except ImportError:
            _LOGGER.warning("HA recorder not available; skipping Amber replay")
            return

        now = dt_util.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if now <= start:
            return

        try:
            history = await get_instance(self.hass).async_add_executor_job(
                state_changes_during_period,
                self.hass,
                start,
                now,
                self._grid_power_entity,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Amber replay: HA history fetch failed: %s", err)
            return

        states = history.get(self._grid_power_entity, []) if history else []
        if not states:
            _LOGGER.info(
                "Amber replay: no history rows for %s today; nothing to seed",
                self._grid_power_entity,
            )
            return

        # Fetch Amber prices for today via existing helper (urllib, sync).
        from .backfill import fetch_amber_price_history  # noqa: PLC0415

        try:
            prices = await self.hass.async_add_executor_job(
                fetch_amber_price_history,
                self._api_key,
                self._site_id,
                start,
                now + _td(days=1),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Amber replay: price-history fetch failed: %s", err)
            return

        general = sorted(
            (p for p in prices if p.get("channelType") == "general"),
            key=lambda p: p.get("startTime", ""),
        )
        feed = sorted(
            (p for p in prices if p.get("channelType") == "feedIn"),
            key=lambda p: p.get("startTime", ""),
        )

        def _rate_at(intervals: list[dict], ts_iso: str) -> float | None:
            """Find perKwh value for ts within an interval. Returns c/kWh."""
            for itv in intervals:
                if itv.get("startTime", "") <= ts_iso <= itv.get("endTime", ""):
                    try:
                        return float(itv["perKwh"])
                    except (KeyError, TypeError, ValueError):
                        return None
            return None

        # Reset accumulator so we don't double-count any partial restore.
        self._amber.reset_daily()

        seeded_rows = 0
        for state in states:
            try:
                power_value = float(state.state)
            except (TypeError, ValueError):
                continue
            # Match _read_grid_power() unit handling: kW → W.
            unit = (state.attributes.get("unit_of_measurement", "") or "").lower()
            power_w = power_value * 1000.0 if unit == "kw" else power_value
            ts = state.last_changed
            ts_iso = ts.isoformat()
            import_rate = _rate_at(general, ts_iso)
            export_rate = _rate_at(feed, ts_iso)
            if import_rate is None or export_rate is None:
                continue
            self._amber.set_current_rates(import_rate, export_rate)
            self._amber.update(power_w, ts)
            seeded_rows += 1

        _LOGGER.info(
            "Amber replay seeded: rows=%d import_kwh=%.3f export_kwh=%.3f "
            "import_cost=$%.4f export_credit=$%.4f",
            seeded_rows,
            self._amber.import_kwh_today,
            self._amber.export_kwh_today,
            self._amber.import_cost_today_c / 100.0,
            self._amber.export_earnings_today_c / 100.0,
        )

    async def async_persist_state(self) -> None:
        """Save engine state to Store.

        Phase 3.0g: stamp `_storage_version` so async_restore_state can
        validate the schema before loading. AEGIS rule: state restore
        MUST validate storage version (CLAUDE.md).
        """
        data: dict[str, Any] = {
            "_storage_version": STORAGE_VERSION,
            "globird": self._current_plan_provider.to_dict(),
            "amber_import_c": self._amber_import_c,
            "amber_export_c": self._amber_export_c,
            "wholesale_c": self._wholesale_c,
            "saving_month_aud": self._saving_month_aud,
            "last_month": self._last_month,
            "last_date": self._last_date,
            "price_history": self._price_history,
            "daily_wins": self._daily_wins,
            "daily_cost_history": self._daily_cost_history,
            "today_schedule": self._today_schedule,
        }
        if self._amber is not None:
            data["amber"] = self._amber.to_dict()
        if self._flow_power is not None:
            data["flow_power"] = self._flow_power.to_dict()
        if self._localvolts is not None:
            data["localvolts"] = self._localvolts.to_dict()
            data["localvolts_import_c"] = self._localvolts_import_c
            data["localvolts_export_c"] = self._localvolts_export_c
        if self._last_explanation is not None:
            data["last_explanation"] = self._last_explanation
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
        """Rebuild all providers with updated options.

        Phase 3.0c invariant: every entry has a cdr_plan. Options-flow
        reload should never produce a state without one.
        """
        cdr_plan = new_options.get("cdr_plan")
        if not cdr_plan:
            _LOGGER.error(
                "rebuild_engine called without cdr_plan in options; "
                "keeping existing provider — investigate options-flow"
            )
            return
        self._current_plan_provider = CdrPlanProvider(
            cdr_plan, entry_options=dict(new_options),
        )
        _LOGGER.info("Rebuilt with CdrPlanProvider (CDR plan %s)",
                     cdr_plan.get("data", {}).get("planId", "?"))
        self._providers = {self._current_plan_provider.id: self._current_plan_provider}

        self._amber = None
        amber_enabled = new_options.get(CONF_AMBER_ENABLED)
        if amber_enabled is None:
            amber_enabled = bool(self.config_entry.data.get(CONF_API_KEY))
        if amber_enabled:
            self._amber = AmberProvider(
                amber_network_daily_c=new_options.get(CONF_AMBER_NETWORK_DAILY_CHARGE, 0.0),
                amber_subscription_daily_c=new_options.get(CONF_AMBER_SUBSCRIPTION_FEE, 0.0),
            )
            self._providers[self._amber.id] = self._amber

        self._flow_power = None
        if new_options.get(CONF_FLOW_POWER_ENABLED):
            self._flow_power = FlowPowerProvider(new_options)
            self._providers[self._flow_power.id] = self._flow_power
        self._localvolts = None
        if new_options.get(CONF_LOCALVOLTS_ENABLED):
            self._localvolts = LocalVoltsProvider(new_options)
            self._providers[self._localvolts.id] = self._localvolts
        self._grid_power_entity = new_options.get(CONF_GRID_POWER_SENSOR, "")
        _LOGGER.info("Rebuilt providers with updated options")
