"""PriceHawk coordinator — orchestrates Amber API polling and cost calculation."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp
import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_change
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
    CONF_AMBER_PRICING_MODE,
    CONF_AMBER_STATIC_PLAN,
    CONF_DWT_AEMO_DAILY_SUPPLY,
    CONF_DWT_AEMO_ENABLED,
    CONF_DWT_OE_API_KEY,
    CONF_DWT_OE_DAILY_SUPPLY,
    CONF_DWT_OE_ENABLED,
    CONF_DWT_REGION,
    CONF_FLOW_POWER_ENABLED,
    CONF_FLOW_POWER_PRICING_MODE,
    CONF_FLOW_POWER_REGION,
    CONF_LOCALVOLTS_API_KEY,
    CONF_LOCALVOLTS_ENABLED,
    CONF_LOCALVOLTS_NMI,
    CONF_LOCALVOLTS_PARTNER_ID,
    CONF_LOCALVOLTS_PRICING_MODE,
    CONF_LOCALVOLTS_STATIC_PLAN,
    CONF_NAMED_COMPARATOR_PLAN,
    LOCALVOLTS_API_POLL_INTERVAL,
    PRICING_MODE_LIVE_API,
    PRICING_MODE_OFF,
    PRICING_MODE_STATIC_PRD,
    PROVIDER_DWT_AEMO,
    PROVIDER_DWT_OE,
    PROVIDER_LOCALVOLTS,
)
from .static_pricing import evaluate_static_rates, resolve_pricing_mode
from .statistics import (
    async_backfill_external_statistics,
    async_push_daily_cost_to_statistics,
)
from .cdr.ranking import DEFAULT_TOP_K, summarize_for_sensor
from .cdr.ranking_job import run_ranking_job
from .explanation import build_explanation
from .localvolts_api import (
    LocalVoltsAPIError,
    aggregate_to_half_hour,
    fetch_recent_intervals,
)
from .providers.cdr_plan import CdrPlanProvider
from .providers.dynamic_wholesale_tariff import DynamicWholesaleTariffProvider
from .providers.nemweb import NEMWebPriceSource
from .providers.openelectricity import OpenElectricityPriceSource
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

# Daily ranking job runs at this local time. 00:30 is after midnight
# rollover so today's daily_cost_history is already final, and well
# before users' morning HA dashboards refresh. Competitor retailer
# list lives in ``cdr.ranking_job`` so it stays testable without HA.
_RANKING_RUN_HOUR = 0
_RANKING_RUN_MINUTE = 30

# DWT price-refresh dedup: OE/NEMWeb publish at 5-min cadence, coordinator
# ticks at 30s. Skip the SDK call when the cached price is fresher than
# this threshold — gives a 1-min slack before the next 5-min dispatch
# interval, bounding to ≤ 1 fetch / 4min / region / entry.
_DWT_PRICE_STALENESS_SECONDS = 240.0


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


def build_backfill_plan_set(
    *,
    options: dict[str, Any],
    current_plan_id: str,
    ranked_alternatives: list[dict[str, Any]],
    plan_cache: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Pure-logic helper composing ``{plan_key: plan_body}`` for the
    Phase 3.2 backfill replay. Lives outside ``PriceHawkCoordinator``
    so it's unit-testable without HA's app context (the coordinator's
    ``DataUpdateCoordinator[T]`` base gets mocked away by
    ``tests/conftest.py``, breaking direct instance construction).

    Composition:
      - The user's CURRENT plan (key = ``current_plan_id``) so its
        column in ``daily_cost_history`` matches the live-loop writer.
      - Top-K ranked alternatives keyed ``alt_<planId>`` so the rollup
        sensors (Phase 3.3) can group them via the ``alt_`` prefix
        filter. The full plan body comes from ``plan_cache`` (populated
        by the cheap-rank pipeline) because the summarised alt dict
        lacks the tariffPeriod the evaluator needs.
      - (Phase 3.4) named comparator keyed ``"named"`` joins later.

    When ``current_plan_id`` cannot be resolved in ``plan_cache`` (i.e.
    no usable ``cdr_plan.data`` envelope is available on ``options``),
    the returned mapping will not contain an entry for the "current"
    plan, but may still contain entries for ranked alternatives keyed
    ``alt_<planId>``. Callers should treat the absence of the
    current-plan entry as a "no-signal" condition for the active plan
    at that time — alts-only backfill is intentionally permitted so
    rollup sensors can still surface comparative data.
    """
    plans: dict[str, dict[str, Any]] = {}

    current_plan = options.get("cdr_plan") or {}
    current_data = (current_plan.get("data")
                    if isinstance(current_plan, dict) else None)
    if isinstance(current_data, dict):
        plans[current_plan_id] = current_data

    for alt in ranked_alternatives:
        if not isinstance(alt, dict):
            continue
        plan_id = alt.get("planId")
        if not isinstance(plan_id, str) or not plan_id:
            continue
        full = plan_cache.get(plan_id)
        if isinstance(full, dict):
            plans[f"alt_{plan_id}"] = full
        elif alt.get("electricityContract"):
            # Some cheap-rank pipelines stash the full body on the alt
            # itself — accept that as a fallback so backfill works
            # even when the per-day cache hasn't been populated yet.
            plans[f"alt_{plan_id}"] = alt
    return plans


def build_named_comparator_provider(
    options: dict[str, Any],
) -> CdrPlanProvider | None:
    """Phase 3.4 — pure-logic constructor for the named-comparator provider.

    Returns a ``CdrPlanProvider`` wrapping the user-pinned plan if
    ``CONF_NAMED_COMPARATOR_PLAN`` is present in ``options`` and the
    body looks like a CDR envelope (``dict``), else ``None``. Lives
    outside ``PriceHawkCoordinator`` so it's unit-testable without
    HA's app context (same justification as :func:`build_backfill_plan_set`).

    The caller is responsible for registering the result in
    ``self._providers`` under the literal ``"named"`` key — keying is
    the coordinator's responsibility so the daily-rollover loop can
    write a stable ``"named"`` column to ``daily_cost_history``
    irrespective of which plan the user pinned.
    """
    plan = options.get(CONF_NAMED_COMPARATOR_PLAN)
    if not isinstance(plan, dict) or not plan:
        return None
    return CdrPlanProvider(plan, entry_options=dict(options))


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

        # Phase 7 PR-2b — Dynamic Wholesale Tariff branch. When the user
        # picked DWT-OE or DWT-AEMO at setup, the current-plan slot is
        # filled by DynamicWholesaleTariffProvider instead of
        # CdrPlanProvider (no cdr_plan exists for DWT entries).
        self._dwt_provider: DynamicWholesaleTariffProvider | None = None
        dwt_provider = self._build_dwt_provider(entry)
        if dwt_provider is not None:
            self._current_plan_provider: Provider = dwt_provider
            self._dwt_provider = dwt_provider
            _LOGGER.info(
                "Using DynamicWholesaleTariffProvider (id=%s region=%s)",
                dwt_provider.id, dwt_provider.region,
            )
        else:
            # Phase 3.0c: every non-DWT entry has a `cdr_plan` envelope.
            # The legacy manual-tariff path (GloBirdProvider) is dead
            # code now. Existing entries from Phase 2.x without cdr_plan
            # are unsupported per the no-migration policy.
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
            self._current_plan_provider = CdrPlanProvider(
                cdr_plan, entry_options=dict(entry.options),
            )
            _LOGGER.info("Using CdrPlanProvider (CDR plan %s)",
                         cdr_plan.get("data", {}).get("planId", "?"))
        self._providers: dict[str, Provider] = {
            self._current_plan_provider.id: self._current_plan_provider,
        }

        # Phase 7 PR-4 — per-comparator three-state pricing mode (off /
        # live_api / static_prd). The resolver back-compats legacy
        # CONF_<P>_ENABLED entries (truthy → live_api; else → off).
        # AMBER: ditto, with a further "no API key + no static plan →
        # off regardless" defensive gate.
        amber_mode = resolve_pricing_mode(
            dict(entry.options), dict(entry.data),
            mode_key=CONF_AMBER_PRICING_MODE,
            legacy_enabled_key=CONF_AMBER_ENABLED,
        )
        if amber_mode == PRICING_MODE_LIVE_API and not entry.data.get(CONF_API_KEY):
            # Legacy back-compat default: amber_enabled was None → falls
            # through to bool(entry.data[CONF_API_KEY]). If we resolve to
            # live_api without a key, that's an off entry from the old
            # path — preserve the old behaviour.
            if entry.options.get(CONF_AMBER_PRICING_MODE) is None:
                amber_mode = PRICING_MODE_OFF
        self._amber_mode = amber_mode
        self._amber: AmberProvider | None = None
        self._amber_static_plan: dict[str, Any] | None = None
        if amber_mode != PRICING_MODE_OFF:
            if amber_mode == PRICING_MODE_STATIC_PRD:
                self._amber_static_plan = entry.options.get(CONF_AMBER_STATIC_PLAN)
                if not self._amber_static_plan:
                    raise ConfigEntryNotReady(
                        "Amber pricing_mode=static_prd but no static plan "
                        "stored. Reconfigure the entry to pick a CDR plan."
                    )
            self._amber = AmberProvider(
                amber_network_daily_c=entry.options.get(CONF_AMBER_NETWORK_DAILY_CHARGE, 0.0),
                amber_subscription_daily_c=entry.options.get(CONF_AMBER_SUBSCRIPTION_FEE, 0.0),
            )
            self._providers[self._amber.id] = self._amber

        # FLOW POWER: Wave-1 PR-4 only ships live_api + off for Flow Power.
        # static_prd is deferred — Flow Power's internal margin is derived
        # from set_wholesale_rate (NEM spot), not set_current_rates; the
        # bridge to feed already-final static rates needs flow_power.py
        # changes which are out of this PR's boundary. Surfacing the mode
        # key now lets the OptionsFlow render the selector consistently
        # across all three comparators.
        flow_power_mode = resolve_pricing_mode(
            dict(entry.options), dict(entry.data),
            mode_key=CONF_FLOW_POWER_PRICING_MODE,
            legacy_enabled_key=CONF_FLOW_POWER_ENABLED,
        )
        if flow_power_mode == PRICING_MODE_STATIC_PRD:
            _LOGGER.warning(
                "Flow Power static_prd is deferred to a future PR — "
                "falling back to live_api for this entry. Track via "
                "DECISIONS.md > D-P7-12."
            )
            flow_power_mode = PRICING_MODE_LIVE_API
        self._flow_power_mode = flow_power_mode
        self._flow_power: FlowPowerProvider | None = None
        if flow_power_mode != PRICING_MODE_OFF:
            self._flow_power = FlowPowerProvider(entry.options)
            self._providers[self._flow_power.id] = self._flow_power

        # LOCALVOLTS: live_api requires API key + partner + NMI from
        # Phase 2.12 OptionsFlow; static_prd consumes a CDR plan envelope.
        localvolts_mode = resolve_pricing_mode(
            dict(entry.options), dict(entry.data),
            mode_key=CONF_LOCALVOLTS_PRICING_MODE,
            legacy_enabled_key=CONF_LOCALVOLTS_ENABLED,
        )
        self._localvolts_mode = localvolts_mode
        self._localvolts: LocalVoltsProvider | None = None
        self._localvolts_static_plan: dict[str, Any] | None = None
        if localvolts_mode != PRICING_MODE_OFF:
            if localvolts_mode == PRICING_MODE_STATIC_PRD:
                self._localvolts_static_plan = entry.options.get(
                    CONF_LOCALVOLTS_STATIC_PLAN
                )
                if not self._localvolts_static_plan:
                    raise ConfigEntryNotReady(
                        "LocalVolts pricing_mode=static_prd but no static "
                        "plan stored. Reconfigure the entry."
                    )
            self._localvolts = LocalVoltsProvider(entry.options)
            self._providers[self._localvolts.id] = self._localvolts

        # Phase 3.4 — Named comparator drill-in. When the user pins one
        # CDR plan via the OptionsFlow ``named_comparator`` step, build
        # a second ``CdrPlanProvider`` for it and register it under the
        # fixed ``"named"`` key. It then participates in the existing
        # 30s tick loop (no new tick path) and contributes a
        # ``"named"`` column to ``daily_cost_history`` at rollover.
        # The DICT KEY (``"named"``) is what flows into rollup sensors
        # and the providers block, not the provider's own ``.id`` (which
        # remains the brand+plan-id slug like other CdrPlanProvider
        # instances). Stable key → rollup sensors don't churn when the
        # user re-pins to a different plan.
        self._named_comparator: CdrPlanProvider | None = (
            build_named_comparator_provider(entry.options)
        )
        if self._named_comparator is not None:
            self._providers["named"] = self._named_comparator
            named_plan = entry.options.get(CONF_NAMED_COMPARATOR_PLAN) or {}
            _LOGGER.info(
                "Registered named comparator (CDR plan %s)",
                named_plan.get("data", {}).get("planId", "?"),
            )

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

        # Phase 3.1 — multi-plan ranking. Top-K cheaper alternatives
        # populated by the daily 00:30 ranking job; consumed by the
        # ranked-alternatives sensor (Phase 3.1 commit 6) and HA service
        # (commit 5). Deep-rank (consumption replay through evaluator)
        # is deferred to Phase 3.2 when HA-history backfill exists —
        # without enough recorded slots, deep-rank has no signal.
        self._cheap_ranked_alternatives: list[dict[str, Any]] = []
        self._ranking_last_run_at: datetime | None = None
        # Plan-detail cache reused across same-day runs so a manual
        # rerun via the rank_alternatives service skips re-fetching
        # plans already pulled by the morning scheduled run. Cache
        # clears on the FIRST run of a new calendar day so overnight
        # republished plans get refreshed.
        self._ranking_plan_cache: dict[str, dict[str, Any]] = {}
        self._ranking_cache_date: date | None = None
        self._ranking_unsub: CALLBACK_TYPE | None = None
        # CR-fix: scheduled callback + manual service trigger can both
        # call async_run_ranking_job concurrently. A second concurrent
        # entry would interleave _ranking_plan_cache mutations and
        # duplicate every expensive CDR detail fetch. Lock serialises.
        self._ranking_lock = asyncio.Lock()

        # Phase 3.2 — universal HA-history backfill status. Surfaced as
        # ``sensor.pricehawk_backfill_status`` (commit 4) with these
        # attributes. State machine transitions:
        #   idle    → initial / between runs
        #   running → backfill in progress (auto-kickoff or service)
        #   complete → last run finished successfully
        #   failed  → last run raised; ``_backfill_error`` carries why
        # Reuses ``_ranking_lock`` to serialise vs the ranking job
        # because both mutate ``_daily_cost_history``. REVISIT: split
        # to a dedicated lock if contention observed; cost of being
        # wrong is brief serialisation of two rare operations.
        self._backfill_status: str = "idle"
        self._backfill_last_run_at: datetime | None = None
        self._backfill_days_loaded: int = 0
        self._backfill_plans_replayed: int = 0
        self._backfill_error: str | None = None

        # Phase 8 PR-5 — reauth provider tag. Set BEFORE raising
        # ConfigEntryAuthFailed so the ConfigFlow.async_step_reauth
        # dispatcher can route to the correct per-provider sub-step.
        self._reauth_provider_id: str | None = None

        # Phase 8 PR-8 — repair issue counters. grid_sensor_unavailable
        # raises after 10 consecutive None reads (5 min @ 30s);
        # ranking_stale checked each tick against _ranking_last_run_at.
        self._grid_sensor_missing_ticks: int = 0
        self._active_repair_ids: set[str] = set()

        # Phase 9 PR-10 — external statistics dual-write. Backfill runs
        # once on first setup; cumulative-sum tracker stays warm across
        # tick lifetime so per-day pushes get a monotonic ``sum`` field.
        self._external_stats_backfill_done: bool = False
        self._external_stats_cumulative: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Dynamic Wholesale Tariff (Phase 7 PR-2b)
    # ------------------------------------------------------------------

    def _build_dwt_provider(
        self, entry: ConfigEntry
    ) -> DynamicWholesaleTariffProvider | None:
        """Build DynamicWholesaleTariffProvider when entry was set up for DWT.

        Returns None when the entry is a CDR-plan entry (no DWT enable flag
        in options or data). Raises ConfigEntryNotReady on inconsistent
        config (current_provider says DWT but enable flags missing) — AC-10c.
        """
        def opt(key: str) -> Any:
            return entry.options.get(key, entry.data.get(key))

        oe_enabled = bool(opt(CONF_DWT_OE_ENABLED))
        aemo_enabled = bool(opt(CONF_DWT_AEMO_ENABLED))
        current_provider = opt(CONF_CURRENT_PROVIDER)
        is_dwt_oe_marker = current_provider == PROVIDER_DWT_OE
        is_dwt_aemo_marker = current_provider == PROVIDER_DWT_AEMO

        # AC-10c — refuse setup on inconsistent state.
        if is_dwt_oe_marker and not (oe_enabled and opt(CONF_DWT_OE_API_KEY)):
            raise ConfigEntryNotReady(
                "DWT-OpenElectricity selected as current provider but config "
                "is incomplete (missing API key or ENABLED flag). "
                "Reconfigure the entry."
            )
        if is_dwt_aemo_marker and not aemo_enabled:
            raise ConfigEntryNotReady(
                "DWT-AEMO selected as current provider but ENABLED flag is "
                "missing. Reconfigure the entry."
            )

        if oe_enabled and is_dwt_oe_marker:
            api_key = opt(CONF_DWT_OE_API_KEY)
            region = opt(CONF_DWT_REGION) or "NSW1"
            daily_supply = float(opt(CONF_DWT_OE_DAILY_SUPPLY) or 110.0)
            # OpenElectricity SDK manages its own session (audit M2 finding:
            # AsyncOEClient signature is (api_key, base_url) — no session
            # kwarg). Trade-off accepted in PR-2.
            price_source: OpenElectricityPriceSource | NEMWebPriceSource = (
                OpenElectricityPriceSource(api_key=api_key)
            )
            return DynamicWholesaleTariffProvider(
                price_source=price_source,
                region=region,
                daily_supply_c=daily_supply,
                provider_id=PROVIDER_DWT_OE,
                name="Dynamic Wholesale Tariff — OpenElectricity",
            )

        if aemo_enabled and is_dwt_aemo_marker:
            region = opt(CONF_DWT_REGION) or "NSW1"
            daily_supply = float(opt(CONF_DWT_AEMO_DAILY_SUPPLY) or 110.0)
            price_source = NEMWebPriceSource(
                session=async_get_clientsession(self.hass)
            )
            return DynamicWholesaleTariffProvider(
                price_source=price_source,
                region=region,
                daily_supply_c=daily_supply,
                provider_id=PROVIDER_DWT_AEMO,
                name="Dynamic Wholesale Tariff — AEMO Direct",
            )

        return None

    async def _refresh_dwt_price(self) -> None:
        """Async price-refresh hook — called every coordinator tick.

        Dedups SDK calls via the 4-minute staleness guard (AC-10b): when
        the cached last-good price is fresher than _DWT_PRICE_STALENESS_SECONDS,
        skip the fetch entirely. OE/NEMWeb publish at 5-min cadence;
        fetching every 30s is wasteful and 429-prone.

        On auth failure, re-raises ConfigEntryAuthFailed so HA's reauth
        flow takes over (full reauth wiring is Phase 8 PR-5).
        """
        provider = self._dwt_provider
        if provider is None:
            return

        # AC-10b: staleness guard. Skip when cached price is fresh.
        last = provider.last_price
        if last is not None:
            age = (
                datetime.now(tz=dt_util.UTC) - last.interval_end_utc
            ).total_seconds()
            if age < _DWT_PRICE_STALENESS_SECONDS:
                return

        try:
            result = await provider.price_source.fetch_current_price(
                provider.region
            )
        except ConfigEntryAuthFailed:
            # Phase 8 PR-5 — tag for reauth dispatcher. Only OE has a
            # key; AEMO Direct can't auth-fail (no key).
            self._reauth_provider_id = PROVIDER_DWT_OE
            raise
        except ConfigEntryNotReady:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "DWT price refresh failed for %s: %s",
                provider.region, exc,
            )
            result = None

        if result is None:
            result = provider.price_source.last_good(provider.region)
        if result is not None:
            provider.set_live_price(result)

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

                    # Phase 8 PR-5 — auth-failure → HA reauth flow. Tag the
                    # failed provider so the dispatcher in async_step_reauth
                    # knows which sub-step to route to. Key is in the
                    # Authorization header (not in the response body or URL)
                    # so str(exc) is safe to log.
                    if resp.status in (401, 403):
                        self._reauth_provider_id = PROVIDER_AMBER
                        raise ConfigEntryAuthFailed(
                            f"Amber API rejected the key (HTTP {resp.status})"
                        )

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
        """Poll Amber API if enough time has elapsed since last poll.

        Phase 7 PR-4: skip entirely when Amber is in PRICING_MODE_STATIC_PRD —
        the static-PRD path needs no live API hit.
        """
        if self._amber_mode != PRICING_MODE_LIVE_API:
            return
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
        """Poll LocalVolts API every LOCALVOLTS_API_POLL_INTERVAL seconds.

        Phase 7 PR-4: skip entirely when LocalVolts is in
        PRICING_MODE_STATIC_PRD — static path uses no API hit.
        """
        if self._localvolts is None:
            return
        if self._localvolts_mode != PRICING_MODE_LIVE_API:
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
        try:
            intervals = await fetch_recent_intervals(
                session, api_key, partner_id, nmi
            )
        except LocalVoltsAPIError as err:
            # Phase 8 PR-5 — auth-failure → HA reauth. Detect 401/403
            # via substring match on the message format from
            # localvolts_api.py:79-81. Non-auth LocalVoltsAPIError
            # re-raises as-is (caller / DataUpdateCoordinator handles).
            msg = str(err).lower()
            if "auth failed" in msg or "401" in msg or "403" in msg:
                self._reauth_provider_id = PROVIDER_LOCALVOLTS
                raise ConfigEntryAuthFailed(
                    "LocalVolts API rejected credentials"
                ) from err
            raise
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
        # 0. On first run, fetch today's full price schedule for the rate chart.
        # Phase 7 PR-4 (codex fix): gate on live API mode. Without this,
        # static/off Amber entries (and all DWT entries) hit the Amber
        # schedule endpoint every 30s with stale or missing credentials
        # because _maybe_poll_amber returns without touching
        # _last_amber_poll, leaving it stuck at 0.0 forever.
        if (
            self._last_amber_poll == 0.0
            and self._amber_mode == PRICING_MODE_LIVE_API
        ):
            await self._fetch_today_price_schedule()

        # 1. Poll Amber API (rate-limited to every 5 min)
        await self._maybe_poll_amber()

        # 1a. Poll AEMO NEMWeb for wholesale RRP (Flow Power input).
        # Independent of Amber — works for users with no Amber account.
        await self._maybe_poll_aemo()

        # 1b. Poll LocalVolts API (rate-limited)
        await self._maybe_poll_localvolts()

        # 1c. Refresh DWT wholesale price (rate-limited via 4-min
        # staleness guard; no-op when entry is not a DWT entry).
        await self._refresh_dwt_price()

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
            daily_saving: float | None = None
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

            # Phase 9 PR-10 — external stats dual-write. JSON Store
            # (above) remains the source of truth until PR-12; stats
            # are additive. Monotonic-sum tracker is seeded by the
            # one-shot backfill in async_setup_stats.
            yesterday_date = (now_local - timedelta(days=1)).date()
            for pid, p in self._providers.items():
                cost = float(p.net_daily_cost_aud)
                self._external_stats_cumulative[pid] = (
                    self._external_stats_cumulative.get(pid, 0.0) + cost
                )
                try:
                    await async_push_daily_cost_to_statistics(
                        self.hass,
                        self.config_entry.entry_id,
                        pid,
                        yesterday_date,
                        cost,
                        self._external_stats_cumulative[pid],
                    )
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning(
                        "external stats push failed for %s: %s",
                        pid, exc,
                    )

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
                "Daily rollover: winner=%s saving=%s month=$%.2f wins=%s",
                winner_id,
                f"${daily_saving:.2f}" if daily_saving is not None else "n/a (Amber not configured)",
                self._saving_month_aud,
                self._daily_wins,
            )
            self._last_date = now_local.day

            # Codex P0-2 (2026-05-23) — reset every registered provider's
            # daily accumulators AFTER history capture but BEFORE the new
            # day's tick. Without this, DWT, CdrPlan, FlowPower, and
            # LocalVolts providers accumulate `today_cost` across days,
            # corrupting Energy-Dashboard cost sensors + external
            # statistics. Previously only `self._amber.reset_daily()` ran,
            # and only inside the monthly-reset branch lower down.
            for provider in self._providers.values():
                try:
                    provider.reset_daily()
                except Exception:  # noqa: BLE001
                    _LOGGER.warning(
                        "reset_daily() raised for provider %s — daily "
                        "counters may carry over into the next day",
                        getattr(provider, "id", "<unknown>"),
                        exc_info=True,
                    )

            # Persist immediately after rollover to avoid data loss on crash
            await self.async_persist_state()

        # 5. Push current rates into providers that need them.
        # Phase 7 PR-4: mode-gated. live_api → existing live-poll rates;
        # static_prd → evaluate from stored CDR PRD envelope each tick.
        if self._amber is not None:
            if self._amber_mode == PRICING_MODE_STATIC_PRD:
                imp, exp = evaluate_static_rates(
                    self._amber_static_plan, now_local
                )
                self._amber.set_current_rates(imp, exp)
            else:
                self._amber.set_current_rates(
                    self._amber_import_c, self._amber_export_c
                )
        if self._flow_power is not None:
            # Flow Power static_prd deferred (see __init__ note); always
            # uses live wholesale path for now.
            self._flow_power.set_wholesale_rate(self._wholesale_c)
        if self._localvolts is not None:
            if self._localvolts_mode == PRICING_MODE_STATIC_PRD:
                imp, exp = evaluate_static_rates(
                    self._localvolts_static_plan, now_local
                )
                self._localvolts.set_current_rates(imp, exp)
            else:
                self._localvolts.set_current_rates(
                    self._localvolts_import_c, self._localvolts_export_c
                )

        # 6. Tick every registered provider (no-ops gracefully if a provider
        # is missing rates).
        if grid_power_w is not None:
            for provider in self._providers.values():
                provider.update(grid_power_w, now_local)

        # Phase 8 PR-8 — repair-issue detection sites (cheap; no I/O).
        self._check_repairs(grid_power_w, now_local)

        # 7. Return data dict for sensor entities
        return self._build_data_dict()

    # ------------------------------------------------------------------
    # External statistics (Phase 9 PR-10)
    # ------------------------------------------------------------------

    async def async_setup_stats(self) -> None:
        """One-shot backfill of external statistics from daily_cost_history.

        Called from async_setup_entry AFTER state restore. Seeds the
        cumulative-sum tracker so subsequent daily-rollover pushes
        produce a monotonic ``sum`` field per HA stats contract.
        """
        if self._external_stats_backfill_done:
            return
        if self._daily_cost_history:
            try:
                count = await async_backfill_external_statistics(
                    self.hass,
                    self.config_entry.entry_id,
                    self._daily_cost_history,
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "external stats backfill failed: %s", exc,
                )
                count = 0
            for entry in self._daily_cost_history:
                for pid, val in entry.items():
                    if pid == "date" or not isinstance(val, (int, float)):
                        continue
                    self._external_stats_cumulative[pid] = (
                        self._external_stats_cumulative.get(pid, 0.0)
                        + float(val)
                    )
            _LOGGER.info(
                "external stats backfill complete: %d entries", count,
            )
        self._external_stats_backfill_done = True

    # ------------------------------------------------------------------
    # Repairs platform (Phase 8 PR-8)
    # ------------------------------------------------------------------

    def _set_repair(
        self,
        issue_id: str,
        on: bool,
        *,
        severity: ir.IssueSeverity = ir.IssueSeverity.WARNING,
        translation_placeholders: dict[str, str] | None = None,
    ) -> None:
        """Toggle a repair issue. Deduped via _active_repair_ids set."""
        scoped = f"{self.config_entry.entry_id}_{issue_id}"
        if on:
            if scoped in self._active_repair_ids:
                return
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                scoped,
                is_fixable=False,
                severity=severity,
                translation_key=issue_id,
                translation_placeholders=translation_placeholders,
            )
            self._active_repair_ids.add(scoped)
        else:
            if scoped not in self._active_repair_ids:
                return
            ir.async_delete_issue(self.hass, DOMAIN, scoped)
            self._active_repair_ids.discard(scoped)

    def _check_repairs(
        self, grid_power_w: float | None, now_local: datetime
    ) -> None:
        """Per-tick repair detection. Cheap; no I/O."""
        # grid_sensor_unavailable: 10+ consecutive None reads = 5 min.
        if grid_power_w is None:
            self._grid_sensor_missing_ticks += 1
            if self._grid_sensor_missing_ticks >= 10:
                self._set_repair(
                    "grid_sensor_unavailable", True,
                    translation_placeholders={
                        "entity_id": self._grid_power_entity or "(unset)",
                    },
                )
        else:
            self._grid_sensor_missing_ticks = 0
            self._set_repair("grid_sensor_unavailable", False)

        # ranking_stale: _ranking_last_run_at None for > 24h since first
        # tick, OR > 36h since last successful run.
        last_rank = self._ranking_last_run_at
        if last_rank is None:
            # No run yet — only flag if the integration has been alive
            # long enough for the 00:30 scheduled run to have fired.
            return  # Stay quiet on cold-boot; nightly job will fix.
        age_hours = (now_local - last_rank).total_seconds() / 3600.0
        if age_hours > 36.0:
            self._set_repair(
                "ranking_stale", True,
                translation_placeholders={
                    "hours": f"{age_hours:.1f}",
                },
            )
        else:
            self._set_repair("ranking_stale", False)

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
            # Phase 3.1 commit 6 — exposed as RankedAlternativesSensor
            # attributes. Summarised (not full PlanDetailV2 bodies) to
            # keep HA recorder attribute payloads under the warning
            # threshold (~2 KB per entity vs ~5-15 KB per raw plan).
            "ranked_alternatives": [
                summarize_for_sensor(p) for p in self._cheap_ranked_alternatives
            ],
            "ranking_last_run_at": (
                self._ranking_last_run_at.isoformat()
                if self._ranking_last_run_at else None
            ),
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
            # CR PR #28: unversioned payloads (pre-Phase 1.x writes, or
            # truncated state) must be rejected too, not silently restored.
            if stored_version != STORAGE_VERSION:
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
            # Phase 3.4: restore the named-comparator accumulator.
            # `today` is `dt_util.now().date()` above — HA-timezone
            # aware per the AEGIS rule that from_dict MUST receive an
            # explicit HA-timezone date (no `date.today()` fallback).
            if self._named_comparator is not None and stored.get("named"):
                self._named_comparator.from_dict(stored["named"], today=today)
                _LOGGER.debug("Restored named comparator provider state")

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
        # Phase 3.4: persist the named-comparator provider so its
        # accumulator survives HA restart. Without this, the named
        # provider's import_cost_today / kwh would reset to zero on
        # every restart while the active and Amber providers keep
        # their state — the rollup deltas would lie.
        if self._named_comparator is not None:
            data["named"] = self._named_comparator.to_dict()
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
    # Phase 3.1 — daily multi-plan ranking job
    # ------------------------------------------------------------------

    def schedule_daily_ranking(self) -> None:
        """Register the 00:30 local-time daily ranking job.

        Uses ``async_track_time_change`` so the callback fires regardless
        of the integration's 30s update tick. Safe to call twice; the
        second call replaces the first (no double-schedule).
        """
        self.cancel_ranking()

        async def _ranking_callback(_now: datetime) -> None:
            await self.async_run_ranking_job()

        self._ranking_unsub = async_track_time_change(
            self.hass,
            _ranking_callback,
            hour=_RANKING_RUN_HOUR,
            minute=_RANKING_RUN_MINUTE,
            second=0,
        )

    def cancel_ranking(self) -> None:
        """Cancel the scheduled daily ranking callback."""
        if self._ranking_unsub is not None:
            self._ranking_unsub()
            self._ranking_unsub = None

    async def async_run_ranking_job(
        self, *, top_k: int = DEFAULT_TOP_K
    ) -> list[dict[str, Any]]:
        """Run the daily ranking pipeline. Returns the persisted top-K.

        Called from the scheduled callback at 00:30 local, and also from
        the future ``pricehawk.rank_alternatives`` HA service (Phase 3.1
        commit 5) on user request. Idempotent: re-runs use the per-plan
        cache so unchanged plans skip re-fetching.

        Thin wrapper around ``cdr.ranking_job.run_ranking_job``: this
        method owns HA-side side effects (session, exception
        swallowing, state persistence) while the pure logic stays
        unit-testable without HA's app context.
        """
        # Serialise to prevent overlapping runs (scheduled callback +
        # manual service trigger). Second caller blocks briefly then
        # returns freshly populated results from the cache.
        async with self._ranking_lock:
            # Date-rollover cache reset BEFORE the run, not after.
            # Keeps same-day reruns warm; new local-day run starts
            # from empty cache so overnight republished plans get
            # fresh data.
            today = dt_util.now().date()
            if self._ranking_cache_date != today:
                self._ranking_plan_cache.clear()
                self._ranking_cache_date = today

            session = async_get_clientsession(self.hass)
            try:
                ranked = await run_ranking_job(
                    session,
                    dict(self.config_entry.options),
                    top_k=top_k,
                    plan_cache=self._ranking_plan_cache,
                )
            except Exception:  # noqa: BLE001 — daily job must not raise
                _LOGGER.exception("ranking: pipeline raised; keeping prior results")
                return self._cheap_ranked_alternatives

            if ranked:
                # Only overwrite prior results when the run actually
                # produced something. An empty list usually means "no
                # retailers resolved" or "all retailers down" — both
                # transient; better to keep yesterday's ranking.
                self._cheap_ranked_alternatives = ranked
                self._ranking_last_run_at = dt_util.now()
                _LOGGER.info(
                    "ranking: persisted %d alternative(s)", len(ranked),
                )
            return ranked or self._cheap_ranked_alternatives

    # ------------------------------------------------------------------
    # Phase 3.2 — universal HA-history backfill
    # ------------------------------------------------------------------

    def _build_backfill_plan_set(self) -> dict[str, dict[str, Any]]:
        """Instance wrapper that pulls inputs and delegates to the
        module-level pure function ``build_backfill_plan_set`` so the
        composition logic stays unit-testable outside the coordinator
        (which can't be constructed under the test harness)."""
        return build_backfill_plan_set(
            options=dict(self.config_entry.options),
            current_plan_id=self._current_plan_provider.id,
            ranked_alternatives=list(self._cheap_ranked_alternatives),
            plan_cache=dict(self._ranking_plan_cache),
        )

    async def async_run_backfill(
        self, *, days_back: int = 30
    ) -> int:
        """Run the universal HA-history backfill.

        Returns the number of NEW days added by this run (delta against
        the pre-existing ``_daily_cost_history``), not the total
        merged-history length. A short-circuited concurrent call
        returns 0.

        Auto-kicked once after the first ranking job completes (see
        ``__init__.py:async_setup_entry``) and user-triggerable via the
        ``pricehawk.backfill_history`` service. Idempotent — concurrent
        callers see the in-progress run via ``_backfill_status`` and
        short-circuit to 0 rather than queueing a second pass.
        """
        # Short-circuit BEFORE acquiring lock: cheap status read avoids unnecessary
        # blocking when a backfill is already in progress.
        if self._backfill_status == "running":
            return 0
        # Reuse ranking lock to serialise against the ranking job.
        # Both mutate ``_daily_cost_history`` so concurrent runs would
        # race on the final merge.
        async with self._ranking_lock:
            # Re-check inside the lock to guard against the race between the
            # outer check and lock acquisition.
            if self._backfill_status == "running":
                return 0
            self._backfill_status = "running"
            self._backfill_error = None
            # Capture pre-backfill length so ``_backfill_days_loaded``
            # reports the delta (new days added by THIS run), not the
            # total merged-history length. Without this, a user with 60
            # days of pre-existing history and a 30-day backfill would
            # see "60 days loaded" even when no new days were added.
            prev_len = len(self._daily_cost_history)
            try:
                plans = self._build_backfill_plan_set()
                self._backfill_plans_replayed = len(plans)
                # Local import — defers HA recorder import to runtime.
                # Matches the pattern at
                # ``coordinator._replay_amber_today_from_api`` (line
                # 1050-1054) so the recorder is not loaded on module
                # import (some HA configs lack it).
                from .backfill import (  # noqa: PLC0415  defer recorder import
                    backfill_daily_cost_history,
                )
                result = await backfill_daily_cost_history(
                    self.hass,
                    self._grid_power_entity,
                    plans,
                    days_back=days_back,
                    entry_options=dict(self.config_entry.options),
                    existing_history=list(self._daily_cost_history),
                )
            except Exception as err:  # noqa: BLE001  status-tracked job
                _LOGGER.exception("backfill: failed")
                # Reset stale success metadata from prior runs so the
                # status sensor doesn't surface misleading counts after a
                # failure. ``_backfill_last_run_at`` is set to NOW to
                # record the timestamp of THIS (failed) run, matching the
                # success-path semantics on line 1441.
                self._backfill_days_loaded = 0
                self._backfill_plans_replayed = 0
                self._backfill_last_run_at = dt_util.now()
                self._backfill_error = str(err)
                self._backfill_status = "failed"
                return 0

            self._daily_cost_history = result
            # Delta vs. merged total. Clamped to zero because callers
            # may pass shorter ``existing_history`` slices in tests.
            new_days = max(0, len(result) - prev_len)
            self._backfill_days_loaded = new_days
            self._backfill_status = "complete"
            self._backfill_last_run_at = dt_util.now()
            await self.async_persist_state()
            _LOGGER.info(
                "backfill: complete, %d new day(s) added (total %d) "
                "across %d plan(s)",
                new_days, len(result), self._backfill_plans_replayed,
            )
            return new_days

    # ------------------------------------------------------------------
    # Options update / engine rebuild
    # ------------------------------------------------------------------

    def rebuild_engine(self, new_options: dict) -> None:
        """Rebuild all providers with updated options.

        Phase 3.0c invariant: every entry has a cdr_plan OR a DWT
        enable flag. Options-flow reload should never produce a state
        without one.
        """
        # Phase 7 PR-2b — DWT branch (mirrors __init__).
        dwt_oe = new_options.get(CONF_DWT_OE_ENABLED)
        dwt_aemo = new_options.get(CONF_DWT_AEMO_ENABLED)
        if dwt_oe or dwt_aemo:
            region = new_options.get(CONF_DWT_REGION) or "NSW1"
            if dwt_oe:
                api_key = new_options.get(
                    CONF_DWT_OE_API_KEY,
                    self.config_entry.data.get(CONF_DWT_OE_API_KEY, ""),
                )
                daily_supply = float(
                    new_options.get(CONF_DWT_OE_DAILY_SUPPLY) or 110.0
                )
                src: OpenElectricityPriceSource | NEMWebPriceSource = (
                    OpenElectricityPriceSource(api_key=api_key)
                )
                dwt_id = PROVIDER_DWT_OE
                dwt_name = "Dynamic Wholesale Tariff — OpenElectricity"
            else:
                daily_supply = float(
                    new_options.get(CONF_DWT_AEMO_DAILY_SUPPLY) or 110.0
                )
                src = NEMWebPriceSource(
                    session=async_get_clientsession(self.hass)
                )
                dwt_id = PROVIDER_DWT_AEMO
                dwt_name = "Dynamic Wholesale Tariff — AEMO Direct"
            self._dwt_provider = DynamicWholesaleTariffProvider(
                price_source=src,
                region=region,
                daily_supply_c=daily_supply,
                provider_id=dwt_id,
                name=dwt_name,
            )
            self._current_plan_provider = self._dwt_provider
            _LOGGER.info(
                "Rebuilt with DynamicWholesaleTariffProvider (id=%s region=%s)",
                dwt_id, region,
            )
            self._providers = {
                self._current_plan_provider.id: self._current_plan_provider
            }
        else:
            self._dwt_provider = None
            cdr_plan = new_options.get("cdr_plan")
            if not cdr_plan:
                _LOGGER.error(
                    "rebuild_engine called without cdr_plan or DWT flag; "
                    "keeping existing provider — investigate options-flow"
                )
                return
            self._current_plan_provider = CdrPlanProvider(
                cdr_plan, entry_options=dict(new_options),
            )
            _LOGGER.info("Rebuilt with CdrPlanProvider (CDR plan %s)",
                         cdr_plan.get("data", {}).get("planId", "?"))
            self._providers = {
                self._current_plan_provider.id: self._current_plan_provider
            }

        # Phase 7 PR-4 — mode-aware comparator rebuild (mirrors __init__).
        # AMBER
        self._amber = None
        self._amber_static_plan = None
        amber_mode = resolve_pricing_mode(
            dict(new_options), dict(self.config_entry.data),
            mode_key=CONF_AMBER_PRICING_MODE,
            legacy_enabled_key=CONF_AMBER_ENABLED,
        )
        if amber_mode == PRICING_MODE_LIVE_API and not self.config_entry.data.get(CONF_API_KEY):
            if new_options.get(CONF_AMBER_PRICING_MODE) is None:
                amber_mode = PRICING_MODE_OFF
        self._amber_mode = amber_mode
        if amber_mode != PRICING_MODE_OFF:
            if amber_mode == PRICING_MODE_STATIC_PRD:
                self._amber_static_plan = new_options.get(CONF_AMBER_STATIC_PLAN)
                if not self._amber_static_plan:
                    _LOGGER.warning(
                        "rebuild_engine: Amber static_prd without stored plan "
                        "— falling back to off."
                    )
                    self._amber_mode = PRICING_MODE_OFF
            if self._amber_mode != PRICING_MODE_OFF:
                self._amber = AmberProvider(
                    amber_network_daily_c=new_options.get(CONF_AMBER_NETWORK_DAILY_CHARGE, 0.0),
                    amber_subscription_daily_c=new_options.get(CONF_AMBER_SUBSCRIPTION_FEE, 0.0),
                )
                self._providers[self._amber.id] = self._amber

        # FLOW POWER (static_prd deferred; falls back to live_api)
        self._flow_power = None
        fp_mode = resolve_pricing_mode(
            dict(new_options), dict(self.config_entry.data),
            mode_key=CONF_FLOW_POWER_PRICING_MODE,
            legacy_enabled_key=CONF_FLOW_POWER_ENABLED,
        )
        if fp_mode == PRICING_MODE_STATIC_PRD:
            fp_mode = PRICING_MODE_LIVE_API
        self._flow_power_mode = fp_mode
        if fp_mode != PRICING_MODE_OFF:
            self._flow_power = FlowPowerProvider(new_options)
            self._providers[self._flow_power.id] = self._flow_power

        # LOCALVOLTS
        self._localvolts = None
        self._localvolts_static_plan = None
        lv_mode = resolve_pricing_mode(
            dict(new_options), dict(self.config_entry.data),
            mode_key=CONF_LOCALVOLTS_PRICING_MODE,
            legacy_enabled_key=CONF_LOCALVOLTS_ENABLED,
        )
        self._localvolts_mode = lv_mode
        if lv_mode != PRICING_MODE_OFF:
            if lv_mode == PRICING_MODE_STATIC_PRD:
                self._localvolts_static_plan = new_options.get(
                    CONF_LOCALVOLTS_STATIC_PLAN
                )
                if not self._localvolts_static_plan:
                    _LOGGER.warning(
                        "rebuild_engine: LocalVolts static_prd without "
                        "stored plan — falling back to off."
                    )
                    self._localvolts_mode = PRICING_MODE_OFF
            if self._localvolts_mode != PRICING_MODE_OFF:
                self._localvolts = LocalVoltsProvider(new_options)
                self._providers[self._localvolts.id] = self._localvolts

        # Phase 3.4 — rebuild the named comparator from updated options.
        # Same construction as ``__init__``; absence of the option key
        # cleanly drops the provider on the next reload.
        self._named_comparator = build_named_comparator_provider(new_options)
        if self._named_comparator is not None:
            self._providers["named"] = self._named_comparator
            named_plan = new_options.get(CONF_NAMED_COMPARATOR_PLAN) or {}
            _LOGGER.info(
                "Rebuilt named comparator (CDR plan %s)",
                named_plan.get("data", {}).get("planId", "?"),
            )

        self._grid_power_entity = new_options.get(CONF_GRID_POWER_SENSOR, "")
        _LOGGER.info("Rebuilt providers with updated options")
