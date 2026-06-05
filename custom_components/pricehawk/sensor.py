"""Sensor platform for PriceHawk."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .data import PriceHawkConfigEntry


@dataclass(frozen=True, kw_only=True)
class PriceHawkSensorEntityDescription(SensorEntityDescription):
    """Class describing PriceHawk sensor entities."""

    unrecorded_attributes: frozenset[str] | None = None


if TYPE_CHECKING:
    from .coordinator import PriceHawkCoordinator

_LOGGER = logging.getLogger(__name__)

# Phase 8 PR-9 (HA Silver) — declare parallel updates explicitly. Sensors
# are CoordinatorEntity-backed: state is read from a single shared
# DataUpdateCoordinator, so concurrent entity updates are safe. 0 means
# unlimited concurrency.
PARALLEL_UPDATES = 0

# Peak-rate sensors only. Import/export rates are owned by GenericProviderRateSensor
# (registered in async_setup_entry's providers loop) — listing them here too caused
# unique_id collisions that dropped the entities the dashboard depends on.
# (key in coordinator.data, _attr_name, is_amber_dependent)
RATE_SENSORS: list[tuple[str, str, bool]] = [
    ("amber_peak_rate", "Amber Peak Rate", True),
    ("current_plan_peak_rate", "Current Plan Peak Rate", False),
    ("current_plan_import_rate", "Current Plan Import Rate", False),
    ("current_plan_export_rate", "Current Plan Feed In Tariff", False),
]


class PriceHawkBaseSensor(CoordinatorEntity["PriceHawkCoordinator"], SensorEntity):
    """Base sensor for all PriceHawk sensors."""

    _attr_has_entity_name = True

    # Parameterising ``CoordinatorEntity`` pins ``self.coordinator`` to the
    # concrete ``PriceHawkCoordinator`` for this class and every subclass, so
    # reads of PriceHawk-specific attributes (e.g. ``_current_plan_provider``)
    # type-check instead of resolving against the generic base coordinator.
    coordinator: PriceHawkCoordinator
    entity_description: PriceHawkSensorEntityDescription

    def __init__(
        self,
        coordinator: Any,
        entry: ConfigEntry,
        key: str,
        description: PriceHawkSensorEntityDescription | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        if description is None:
            description = PriceHawkSensorEntityDescription(
                key=key,
                name=getattr(self, "_attr_name", None),
                native_unit_of_measurement=getattr(self, "_attr_native_unit_of_measurement", None),
                device_class=getattr(self, "_attr_device_class", None),
                state_class=getattr(self, "_attr_state_class", None),
                suggested_display_precision=getattr(
                    self, "_attr_suggested_display_precision", None
                ),
                unrecorded_attributes=getattr(self, "_unrecorded_attributes", None),
            )
        self.entity_description = description
        if description.unrecorded_attributes:
            self._unrecorded_attributes = description.unrecorded_attributes

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="PriceHawk",
            manufacturer="PriceHawk",
            model="Energy Rate Comparator",
            entry_type=DeviceEntryType.SERVICE,
        )


class PriceHawkRateSensor(PriceHawkBaseSensor):
    """Rate sensor (c/kWh) reading directly from coordinator data."""

    _attr_native_unit_of_measurement = "c/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: Any,
        entry: ConfigEntry,
        key: str,
        name: str,
        *,
        amber_dependent: bool = False,
    ) -> None:
        super().__init__(coordinator, entry, key)
        self._attr_name = name
        self._amber_dependent = amber_dependent

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get(self._key)

    @property
    def available(self) -> bool:
        if self._amber_dependent:
            return super().available and self.coordinator.data.get("amber_import_rate") is not None
        # Non-Amber rate sensors (e.g. current_plan_peak_rate) are unavailable
        # when the coordinator hasn't computed a value yet — surfacing "unknown"
        # for a TOU plan with no peak window defined is misleading.
        return super().available and self.coordinator.data.get(self._key) is not None


class BestProviderSensor(PriceHawkBaseSensor):
    """Shows which provider has the cheapest current import rate."""

    _attr_name = "Best Provider"

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "best_provider")

    @property
    def native_value(self) -> str:
        amber = self.coordinator.data.get("amber_import_rate")
        current_plan = self.coordinator.data.get("current_plan_import_rate")
        current_plan_name = self.coordinator.data.get("current_plan_name") or "Current Plan"
        if amber is None:
            return current_plan_name
        if current_plan is None:
            return "Amber Electric"
        return "Amber Electric" if amber <= current_plan else current_plan_name


class CheapestTodaySensor(PriceHawkBaseSensor):
    """Shows which provider is cheapest by total daily cost."""

    _attr_name = "Cheapest Today"

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "cheapest_today")

    @property
    def native_value(self) -> str:
        amber = self.coordinator.data.get("amber_daily_cost")
        current_plan = self.coordinator.data.get("current_plan_daily_cost")
        current_plan_name = self.coordinator.data.get("current_plan_name") or "Current Plan"
        if amber is None:
            return current_plan_name
        if current_plan is None:
            return "Amber Electric"
        return "Amber Electric" if amber <= current_plan else current_plan_name


class BestRateSensor(PriceHawkBaseSensor):
    """The cheaper provider's current import rate in c/kWh."""

    _attr_name = "Best Rate"
    _attr_native_unit_of_measurement = "c/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "best_rate")

    @property
    def native_value(self) -> float | None:
        """Return the cheapest current import rate across both providers."""
        amber = self.coordinator.data.get("amber_import_rate")
        current_plan = self.coordinator.data.get("current_plan_import_rate")
        if amber is None:
            return current_plan
        if current_plan is None:
            return amber
        return min(amber, current_plan)


class SavingTodaySensor(PriceHawkBaseSensor):
    """Directional saving based on current provider. Positive = save by switching."""

    _attr_name = "Saving Today"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "AUD"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "saving_today")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("saving_today")

    @property
    def last_reset(self) -> datetime:
        now = dt_util.now()
        return now.replace(hour=0, minute=0, second=0, microsecond=0)


class SavingMonthSensor(PriceHawkBaseSensor):
    """Monthly accumulated saving read from coordinator."""

    _attr_name = "Saving Month"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "AUD"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "saving_month")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("saving_month_aud")

    @property
    def last_reset(self) -> datetime:
        now = dt_util.now()
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class MetricsWonSensor(PriceHawkBaseSensor):
    """How many comparison metrics Amber wins, e.g. '2/3'."""

    _attr_name = "Metrics Won"

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "metrics_won")

    @property
    def native_value(self) -> str | None:
        # Coordinator owns metrics_won (computed once, with a single
        # source of truth for "no comparator available" → None).
        # Inline-compute fallback was dead code post-Phase 3.0g.
        return self.coordinator.data.get("metrics_won")

    @property
    def available(self) -> bool:
        # Unavailable when no comparator (Amber absent or not yet computed).
        return super().available and self.coordinator.data.get("metrics_won") is not None


class AmberDailyChargesSensor(PriceHawkBaseSensor):
    """Combined Amber network + subscription daily charges."""

    _attr_name = "Amber Daily Charges"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "AUD"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "amber_daily_fixed_charges")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("amber_daily_fixed_charges")


class ProviderDailyCostSensor(PriceHawkBaseSensor):
    """Daily total cost for a provider (energy + supply charges)."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "AUD"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: Any, entry: ConfigEntry, data_key: str, name: str) -> None:
        super().__init__(coordinator, entry, data_key)
        self._attr_name = name

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get(self._key)

    @property
    def last_reset(self) -> datetime | None:
        now = dt_util.now()
        return now.replace(hour=0, minute=0, second=0, microsecond=0)


class ChosenPlanCostSensor(PriceHawkBaseSensor):
    """Today's cost for the chosen plan — Energy Dashboard pickable.

    Phase 9 PR-11. unique_id is provider-INDEPENDENT so the entity_id
    stays stable when the user changes their CDR plan or swaps to a
    DWT entry. device_class + unit + state_class + last_reset together
    qualify the sensor for HA's Energy Dashboard cost picker (per
    https://www.home-assistant.io/docs/energy/individual-devices/).
    """

    _attr_name = "Today Cost"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "AUD"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, key="_chosen_plan_today_cost")
        self._attr_unique_id = f"{entry.entry_id}_chosen_plan_today_cost"

    @property
    def native_value(self) -> float | None:
        provider = getattr(self.coordinator, "_current_plan_provider", None)
        if provider is None:
            return None
        return float(provider.net_daily_cost_aud)

    @property
    def last_reset(self) -> datetime | None:
        now = dt_util.now()
        return now.replace(hour=0, minute=0, second=0, microsecond=0)


class LastUpdatedSensor(PriceHawkBaseSensor):
    """Timestamp of the last successful coordinator update."""

    _unrecorded_attributes = frozenset(
        {
            "price_history",
            "today_schedule",
            "daily_cost_history",
            "daily_wins",
        }
    )

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        desc = PriceHawkSensorEntityDescription(
            key="last_updated",
            name="Last Updated",
            device_class=SensorDeviceClass.TIMESTAMP,
            unrecorded_attributes=self._unrecorded_attributes,
        )
        super().__init__(coordinator, entry, "last_updated", desc)

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data.get("last_updated")

    @property
    def extra_state_attributes(self) -> dict:
        """Expose price history as entity attribute for dashboard chart."""
        return {
            "price_history": self.coordinator.data.get("price_history", []),
            "today_schedule": self.coordinator.data.get("today_schedule", []),
            "amber_import_kwh": self.coordinator.data.get("amber_import_kwh", 0),
            "amber_export_kwh": self.coordinator.data.get("amber_export_kwh", 0),
            "current_plan_import_kwh": self.coordinator.data.get("current_plan_import_kwh", 0),
            "current_plan_export_kwh": self.coordinator.data.get("current_plan_export_kwh", 0),
            # Phase 3.0g (CodeRabbit/Sourcery): default to empty dict.
            # daily_wins is provider-id keyed (e.g.,
            # `globird_GLO731031MR@VEC`, `amber`, `flow_power`) —
            # hardcoding `{"amber": 0, "current_plan": 0}` never matched
            # the dynamic per-plan ids introduced in Phase 3.0a.
            "daily_wins": self.coordinator.data.get("daily_wins", {}),
            "daily_cost_history": self.coordinator.data.get("daily_cost_history", []),
        }


class CurrentPlanDailySupplySensor(PriceHawkBaseSensor):
    """Current-plan daily supply charge (fixed value, no state_class).

    Phase 3.0e: renamed from GloBirdDailySupplySensor. Works for any
    retailer's plan, not just GloBird.
    """

    _attr_name = "Current Plan Daily Supply"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "AUD"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "current_plan_daily_supply_aud")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("current_plan_daily_supply_aud")


class ZeroHeroStatusSensor(PriceHawkBaseSensor):
    """GloBird ZeroHero daily credit status."""

    _attr_name = "ZeroHero Status"
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "zerohero_status")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.get("current_plan_zerohero_status")


# -- Generic per-provider sensors (pricehawk_<provider>_*) -------------------


class GenericProviderRateSensor(PriceHawkBaseSensor):
    """Provider import or export rate, sourced from data['providers'][id]."""

    _attr_native_unit_of_measurement = "c/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: Any,
        entry: ConfigEntry,
        provider_id: str,
        provider_name: str,
        kind: str,
    ) -> None:
        # kind: "import" or "export"
        super().__init__(coordinator, entry, f"{provider_id}_{kind}_rate")
        suffix = "Import Rate" if kind == "import" else "Feed In Tariff"
        self._attr_name = f"{provider_name} {suffix}"
        self._provider_id = provider_id
        self._kind = kind

    @property
    def native_value(self) -> float | None:
        provs = self.coordinator.data.get("providers", {})
        prov = provs.get(self._provider_id)
        if prov is None:
            return None
        key = "import_rate_c_kwh" if self._kind == "import" else "export_rate_c_kwh"
        return prov.get(key)


class GenericProviderCostSensor(PriceHawkBaseSensor):
    """Provider net daily cost (AUD)."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "AUD"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: Any,
        entry: ConfigEntry,
        provider_id: str,
        provider_name: str,
    ) -> None:
        super().__init__(coordinator, entry, f"{provider_id}_cost_today")
        self._attr_name = f"{provider_name} Cost Today"
        self._provider_id = provider_id

    @property
    def native_value(self) -> float | None:
        provs = self.coordinator.data.get("providers", {})
        prov = provs.get(self._provider_id)
        if prov is None:
            return None
        return prov.get("net_daily_cost_aud")

    @property
    def last_reset(self) -> datetime:
        now = dt_util.now()
        return now.replace(hour=0, minute=0, second=0, microsecond=0)


class GenericProviderBreakdownSensor(PriceHawkBaseSensor):
    """Breakdown metric (import cost, export credit, daily supply) for a provider."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "AUD"
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: Any,
        entry: ConfigEntry,
        provider_id: str,
        provider_name: str,
        kind: str,
    ) -> None:
        super().__init__(coordinator, entry, f"{provider_id}_{kind}_today")
        nice = {
            "import_cost": "Import Cost",
            "export_credit": "Export Credit",
            "daily_supply": "Daily Supply",
        }[kind]
        self._attr_name = f"{provider_name} {nice}"
        self._provider_id = provider_id
        self._kind = kind
        if kind != "daily_supply":
            self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self) -> float | None:
        provs = self.coordinator.data.get("providers", {}) if self.coordinator.data else {}
        prov = provs.get(self._provider_id)
        if prov is None:
            return None
        key = {
            "import_cost": "import_cost_today_aud",
            "export_credit": "export_credit_today_aud",
            "daily_supply": "daily_fixed_charges_aud",
        }[self._kind]
        return prov.get(key)

    @property
    def last_reset(self) -> datetime | None:
        if self._kind == "daily_supply":
            return None
        now = dt_util.now()
        return now.replace(hour=0, minute=0, second=0, microsecond=0)


class AmberForecastSensor(PriceHawkBaseSensor):
    """Amber 24-hour forecast peak / dip / average price.

    State = c/kWh, attributes carry the timestamp of the peak/dip and
    the full 48-interval forecast list.
    """

    _unrecorded_attributes = frozenset({"intervals"})

    def __init__(
        self,
        coordinator: Any,
        entry: ConfigEntry,
        kind: str,  # "peak" / "dip" / "avg"
    ) -> None:
        self._kind = kind
        nice = {"peak": "Peak", "dip": "Dip", "avg": "Average"}[kind]
        icon = {"peak": "mdi:trending-up", "dip": "mdi:trending-down", "avg": "mdi:chart-line"}[
            kind
        ]
        unrec = self._unrecorded_attributes if kind == "avg" else None
        desc = PriceHawkSensorEntityDescription(
            key=f"amber_forecast_{kind}",
            name=f"Amber Forecast {nice}",
            icon=icon,
            native_unit_of_measurement="c/kWh",
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
            unrecorded_attributes=unrec,
        )
        super().__init__(coordinator, entry, f"amber_forecast_{kind}", desc)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get(f"amber_forecast_{self._kind}_c_kwh")

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data.get(f"amber_forecast_{self._kind}_c_kwh") is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        attrs: dict[str, Any] = {}
        if self._kind in ("peak", "dip"):
            attrs["at"] = data.get(f"amber_forecast_{self._kind}_at")
        if self._kind == "avg":
            attrs["intervals"] = data.get("amber_forecast_intervals", [])
        return attrs


class WinnerExplanationSensor(PriceHawkBaseSensor):
    """Most-recent end-of-day winner explanation. State = section label."""

    _unrecorded_attributes = frozenset({"bullets"})

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        desc = PriceHawkSensorEntityDescription(
            key="winner_explanation",
            name="Winner Explanation",
            icon="mdi:trophy",
            unrecorded_attributes=self._unrecorded_attributes,
        )
        super().__init__(coordinator, entry, "winner_explanation", desc)

    @property
    def native_value(self) -> str | None:
        exp = self.coordinator.data.get("last_explanation")
        if not exp:
            return None
        return exp.get("section_label")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        exp = self.coordinator.data.get("last_explanation") or {}
        return {
            "winner_id": exp.get("winner_id"),
            "winner_name": exp.get("winner_name"),
            "margin_aud": exp.get("margin_aud"),
            "bullets": exp.get("bullets", []),
        }


class RankedAlternativesSensor(PriceHawkBaseSensor):
    """Phase 3.1 commit 6 — top-K cheaper alternatives sensor.

    State: number of ranked alternative plans (0..top_k). 0 means
    the daily ranking job hasn't produced results yet (first run
    pending, no eligible plans for the user's postcode, or all
    competitor retailers down).

    Attributes:
      - ``alternatives``: list of per-plan summaries (plan_id,
        display_name, brand, peak/supply in cents, cheap-rank score).
        Sorted ascending by score so attributes[0] is cheapest.
      - ``last_run``: ISO timestamp of last successful ranking job
        (None until the first run completes).

    Cheap-rank only for now; deep-rank fields land when Phase 3.2
    backfill enables consumption replay.
    """

    _unrecorded_attributes = frozenset({"alternatives"})

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        desc = PriceHawkSensorEntityDescription(
            key="ranked_alternatives_count",
            name="Ranked Alternatives",
            icon="mdi:format-list-numbered",
            unrecorded_attributes=self._unrecorded_attributes,
        )
        super().__init__(coordinator, entry, "ranked_alternatives_count", desc)

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.get("ranked_alternatives", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "alternatives": list(self.coordinator.data.get("ranked_alternatives", [])),
            "last_run": self.coordinator.data.get("ranking_last_run_at"),
        }


class BackfillStatusSensor(PriceHawkBaseSensor):
    """Phase 3.2 commit 4 — universal HA-history backfill status.

    State: ``idle | running | complete | failed``. The state machine
    lives on the coordinator (``_backfill_status``); this sensor is a
    pure read-through.

    Attributes:
      - ``last_run``: ISO timestamp of the last completed (or failed)
        backfill run, or ``None`` until the first run finishes.
      - ``days_loaded``: number of days currently in
        ``daily_cost_history`` (cap 180).
      - ``plans_replayed``: number of plans the last run replayed
        history against (current plan + top-K alts).
      - ``error``: failure message when ``state == "failed"``,
        otherwise ``None``.

    First run is auto-kicked at integration setup after the first
    ranking job completes (so the alternatives are populated).
    User-triggerable via the ``pricehawk.backfill_history`` service.
    """

    _attr_name = "Backfill Status"
    _attr_icon = "mdi:database-refresh"

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "backfill_status")

    @property
    def native_value(self) -> str:
        return getattr(self.coordinator, "_backfill_status", "idle")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        last_run = getattr(c, "_backfill_last_run_at", None)
        return {
            "last_run": last_run.isoformat() if last_run else None,
            "days_loaded": getattr(c, "_backfill_days_loaded", 0),
            "plans_replayed": getattr(c, "_backfill_plans_replayed", 0),
            "error": getattr(c, "_backfill_error", None),
        }


class PeriodRollupSensor(PriceHawkBaseSensor):
    """Phase 3.3 — rolling-window cost rollup sensor.

    Subclasses set ``_ROLLUP_KIND`` (``current | best_alt | savings``)
    and ``_METRIC_LABEL`` (human-readable infix for the entity name).
    The base class owns the unique-id, name, and ``native_value`` /
    ``extra_state_attributes`` dispatch — three kinds inlined with
    ``if`` rather than a Strategy interface (per plan §8.6).

    Reads ``coordinator.data["daily_cost_history"]`` (populated by the
    live coordinator's daily rollover and by Phase 3.2's backfill).
    Recomputes on every coordinator tick — `filter_window` is a 365-row
    list-scan, negligible cost vs the 30s tick cadence.

    ``last_reset`` is only set on the ``today`` window (resets at
    midnight). Rolling windows (week/month/3month/year) leave it UNSET
    because they're not snapshot-resettable totals — HA's TOTAL
    state-class tolerates this and treats them as monotonic-with-
    occasional-drops, which matches their semantics.
    """

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "AUD"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    _ROLLUP_KIND: str = ""  # overridden by subclass
    _METRIC_LABEL: str = ""  # overridden by subclass

    # Human-readable suffixes for the 5 windows. ``"3 Month"`` is forced
    # because ``.title()`` on the raw key produces ``"3Month"`` (no
    # space before the capital — Python's titlecase doesn't split on
    # the digit→letter boundary).
    _WINDOW_LABELS: dict[str, str] = {
        "today": "Today",
        "week": "Week",
        "month": "Month",
        "3month": "3 Month",
        "year": "Year",
    }

    def __init__(self, coordinator: Any, entry: ConfigEntry, window: str) -> None:
        super().__init__(
            coordinator,
            entry,
            f"{self._ROLLUP_KIND}_cost_{window}",
        )
        self._window = window
        self._attr_name = f"{self._METRIC_LABEL} {self._WINDOW_LABELS.get(window, window.title())}"

    @property
    def native_value(self) -> float | None:
        # Local import: avoids loading the rollup module at sensor-class
        # definition time (mirrors the pattern used by the coordinator's
        # lazy backfill import; cdr.rollup is cheap to import but
        # keeping the pattern consistent eases future test-mock setups).
        from .cdr.rollup import (  # noqa: PLC0415
            WindowName,
            best_alternative_for_window,
            filter_window,
            savings,
            sum_window,
        )

        history = self.coordinator.data.get("daily_cost_history") or []
        # ``_window`` is one of the 5 literal names by construction
        # (set in ``__init__`` from the registration loop in
        # ``async_setup_entry``); cast appeases mypy's Literal check.
        rows = filter_window(history, cast(WindowName, self._window), now=dt_util.now())
        if not rows:
            return None
        # Defensive: the "current" and "savings" rollups need the
        # active provider's id as the column key. The coordinator
        # normally guarantees ``_current_plan_provider`` exists (a
        # missing ``cdr_plan`` raises ConfigEntryNotReady at setup),
        # but downstream code paths — restart races, partial restore,
        # tests using a mocked coordinator — can briefly land here
        # without it. Returning ``None`` keeps the sensor in
        # ``unknown`` rather than raising AttributeError.
        if self._ROLLUP_KIND in ("current", "savings"):
            provider = getattr(self.coordinator, "_current_plan_provider", None)
            if not provider or not getattr(provider, "id", None):
                return None
        if self._ROLLUP_KIND == "current":
            current_key = self.coordinator._current_plan_provider.id
            value, _ = sum_window(rows, current_key)
            return value
        if self._ROLLUP_KIND == "best_alt":
            _, value, _ = best_alternative_for_window(rows)
            return value
        if self._ROLLUP_KIND == "savings":
            current_key = self.coordinator._current_plan_provider.id
            current_sum, _ = sum_window(rows, current_key)
            _, alt_sum, _ = best_alternative_for_window(rows)
            return savings(current_sum, alt_sum)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose window name, day-count coverage, and (when relevant)
        the winning alternative plan_id for the dashboard."""
        from .cdr.rollup import (  # noqa: PLC0415
            WindowName,
            best_alternative_for_window,
            filter_window,
        )

        history = self.coordinator.data.get("daily_cost_history") or []
        rows = filter_window(history, cast(WindowName, self._window), now=dt_util.now())
        attrs: dict[str, Any] = {
            "window": self._window,
            "days_in_window": len(rows),
        }
        if self._ROLLUP_KIND in ("best_alt", "savings"):
            best_plan_id, _, _ = best_alternative_for_window(rows)
            attrs["best_alternative_plan_id"] = best_plan_id
        return attrs

    @property
    def last_reset(self) -> datetime | None:
        """``today`` rollups reset at midnight; rolling week/month/year
        leave ``last_reset`` unset.

        HA's TOTAL state-class expects either a fixed-reset cadence
        (``last_reset`` populated) or a slowly-drifting cumulative
        total. Rolling windows fluctuate downward as old days drop out
        — they're neither, so we leave ``last_reset`` unset and let HA
        treat them as TOTAL-with-occasional-corrections. Setting an
        artificial midnight reset on the rolling windows would falsely
        attribute the previous day's value as "spent" each midnight.
        """
        if self._window != "today":
            return None
        now = dt_util.now()
        return now.replace(hour=0, minute=0, second=0, microsecond=0)


class CurrentCostRollupSensor(PeriodRollupSensor):
    """Sum of the user's CURRENT plan cost across the rolling window."""

    _ROLLUP_KIND = "current"
    _METRIC_LABEL = "Current Cost"


class BestAlternativeRollupSensor(PeriodRollupSensor):
    """Sum of the cheapest ranked alternative across the rolling window.

    Winner picked by lowest ``sum_window`` per alt key; ties broken
    lexicographically by plan_id so the choice is deterministic across
    coordinator ticks (avoids dashboard flicker)."""

    _ROLLUP_KIND = "best_alt"
    _METRIC_LABEL = "Best Alternative Cost"


class SavingsRollupSensor(PeriodRollupSensor):
    """``current - best_alt`` across the rolling window.

    Sign-preserving: positive = you'd save by switching, negative = your
    current plan is already cheaper than every ranked alternative.
    Returns ``None`` (``unknown``) when either side lacks data — better
    than implying a real zero saving."""

    _ROLLUP_KIND = "savings"
    _METRIC_LABEL = "Savings"


class NamedComparatorRollupSensor(PeriodRollupSensor):
    """Phase 3.4 — rolling cost on the user-pinned named comparator plan.

    The named comparator is registered in ``coordinator._providers``
    under the literal ``"named"`` key when the user pins a plan via
    the OptionsFlow ``named_comparator`` step (see Phase 3.4 commit
    1/2). The daily rollover loop writes that key's cost to
    ``daily_cost_history``, and this sensor sums it across the
    rolling window — same windowing as the other rollup sensors so
    dashboards line up exactly.

    Overrides ``native_value`` and ``extra_state_attributes`` rather
    than extending the base's ``_ROLLUP_KIND`` dispatch — the kinds
    enum is documented at the base-class level and Phase 3.3 just
    shipped, so we localise the new behaviour here instead of
    rewriting that contract for one extra kind.

    Skipped at registration time when ``"named"`` isn't in
    ``coordinator._providers`` — see ``async_setup_entry``.
    """

    _ROLLUP_KIND = "named"
    _METRIC_LABEL = "Named Comparator Cost"

    @property
    def native_value(self) -> float | None:
        from .cdr.rollup import (  # noqa: PLC0415
            WindowName,
            filter_window,
            sum_window,
        )

        history = self.coordinator.data.get("daily_cost_history") or []
        rows = filter_window(history, cast(WindowName, self._window), now=dt_util.now())
        if not rows:
            return None
        value, _ = sum_window(rows, "named")
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        from .cdr.rollup import (  # noqa: PLC0415
            WindowName,
            filter_window,
            sum_window,
        )

        history = self.coordinator.data.get("daily_cost_history") or []
        rows = filter_window(history, cast(WindowName, self._window), now=dt_util.now())
        _, day_count = sum_window(rows, "named")
        return {
            "window": self._window,
            "days_in_window": day_count,
            "plan_key": "named",
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PriceHawkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PriceHawk sensors from a config entry."""
    # HA's platform-setup lifecycle guarantees this runs after async_setup_entry
    # in __init__.py has populated entry.runtime_data. The explicit raise narrows
    # the Optional[PriceHawkData] for the type checker and loud-fails any test
    # fixture that violates the lifecycle, instead of producing an AttributeError
    # on a downstream .coordinator access. Using `raise` rather than `assert` so
    # the invariant survives `python -O` (asserts are stripped under -O).
    data = entry.runtime_data
    if data is None:
        raise RuntimeError(
            "entry.runtime_data missing — async_setup_entry in __init__.py must run first"
        )
    coordinator = data.coordinator

    entities: list[SensorEntity] = []

    # 6 rate sensors
    for key, name, amber_dep in RATE_SENSORS:
        entities.append(
            PriceHawkRateSensor(coordinator, entry, key, name, amber_dependent=amber_dep)
        )

    # Comparison and cost sensors
    # Phase 9 PR-11 — Energy-Dashboard-pickable chosen-plan cost sensor.
    entities.append(ChosenPlanCostSensor(coordinator, entry))
    entities.append(BestProviderSensor(coordinator, entry))
    entities.append(BestRateSensor(coordinator, entry))
    entities.append(CheapestTodaySensor(coordinator, entry))
    entities.append(SavingTodaySensor(coordinator, entry))
    entities.append(SavingMonthSensor(coordinator, entry))
    entities.append(MetricsWonSensor(coordinator, entry))

    # Amber daily fixed charges
    entities.append(AmberDailyChargesSensor(coordinator, entry))

    # Per-provider daily total cost
    entities.append(
        ProviderDailyCostSensor(coordinator, entry, "amber_daily_cost", "Amber Cost Today")
    )
    entities.append(
        ProviderDailyCostSensor(
            coordinator, entry, "current_plan_daily_cost", "Current Plan Cost Today"
        )
    )

    # Import/export cost breakdowns
    entities.append(
        ProviderDailyCostSensor(coordinator, entry, "amber_import_cost_aud", "Amber Import Cost")
    )
    entities.append(
        ProviderDailyCostSensor(
            coordinator, entry, "amber_export_credit_aud", "Amber Export Credit"
        )
    )
    entities.append(
        ProviderDailyCostSensor(
            coordinator, entry, "current_plan_import_cost_aud", "Current Plan Import Cost"
        )
    )
    entities.append(
        ProviderDailyCostSensor(
            coordinator,
            entry,
            "current_plan_export_credit_aud",
            "Current Plan Export Credit",
        )
    )

    # Daily supply charge (fixed value — no state_class)
    entities.append(CurrentPlanDailySupplySensor(coordinator, entry))

    # Timestamp
    entities.append(LastUpdatedSensor(coordinator, entry))

    # Bonus: ZeroHero status
    entities.append(ZeroHeroStatusSensor(coordinator, entry))

    # Generic per-provider sensors (pricehawk_<provider>_*) — registered for
    # every comparator provider currently active in the coordinator.
    # Phase 3.0g (UAT): SKIP the user's CURRENT plan provider — its
    # rate/cost/kwh metrics already have hardcoded `current_plan_*`
    # sensors registered above. Registering both produces duplicate
    # entities (`sensor.pricehawk_<brand>_<planid>_*` vs
    # `sensor.pricehawk_current_plan_*`). Comparators (Amber, Flow
    # Power, LocalVolts) keep their per-provider entities.
    providers_block = coordinator.data.get("providers", {}) if coordinator.data else {}
    current_plan_id = (
        coordinator._current_plan_provider.id
        if hasattr(coordinator, "_current_plan_provider")
        else None
    )
    for provider_id, snap in providers_block.items():
        if provider_id == current_plan_id:
            continue
        # Phase 3.4: avoid unique_id collision with NamedComparatorRollupSensor.
        # The "named" provider is exposed via its own rollup sensor family
        # (NamedComparatorRollupSensor for each window); a
        # GenericProviderCostSensor for it would clash on
        # "named_cost_today" and one of the two would be dropped from the
        # entity registry. Skip the rate sensors too so we don't litter
        # HA with three duplicate-looking entities — the rollup family
        # covers the dashboard's needs for the named comparator.
        if provider_id == "named":
            continue
        provider_name = snap.get("name", provider_id.title())
        entities.append(
            GenericProviderRateSensor(coordinator, entry, provider_id, provider_name, "import")
        )
        entities.append(
            GenericProviderRateSensor(coordinator, entry, provider_id, provider_name, "export")
        )
        entities.append(GenericProviderCostSensor(coordinator, entry, provider_id, provider_name))

        if provider_id != "amber":
            entities.append(
                GenericProviderBreakdownSensor(
                    coordinator, entry, provider_id, provider_name, "import_cost"
                )
            )
            entities.append(
                GenericProviderBreakdownSensor(
                    coordinator, entry, provider_id, provider_name, "export_credit"
                )
            )
            entities.append(
                GenericProviderBreakdownSensor(
                    coordinator, entry, provider_id, provider_name, "daily_supply"
                )
            )

    # Amber 24h forecast — only when Amber is registered as a provider
    if "amber" in providers_block:
        entities.append(AmberForecastSensor(coordinator, entry, "peak"))
        entities.append(AmberForecastSensor(coordinator, entry, "dip"))
        entities.append(AmberForecastSensor(coordinator, entry, "avg"))

    # Winner explanation (state = section label, attributes = bullets)
    entities.append(WinnerExplanationSensor(coordinator, entry))

    # Phase 3.1 commit 6 — cheaper-alternatives ranking (populated by
    # the daily 00:30 ranking job; refreshable via the
    # pricehawk.rank_alternatives service).
    entities.append(RankedAlternativesSensor(coordinator, entry))

    # Phase 3.2 commit 4 — universal HA-history backfill status sensor.
    # Auto-kicked once at setup after the first ranking run completes;
    # user-triggerable via the ``pricehawk.backfill_history`` service.
    entities.append(BackfillStatusSensor(coordinator, entry))

    # Phase 3.3 — 15 rolling-window cost rollup sensors covering
    # (current_cost | best_alternative_cost | savings) ×
    # (today | week | month | 3month | year). All read from
    # ``daily_cost_history`` populated by the live coordinator daily
    # rollover and by Phase 3.2's backfill.
    for window in ("today", "week", "month", "3month", "year"):
        entities.append(CurrentCostRollupSensor(coordinator, entry, window))
        entities.append(BestAlternativeRollupSensor(coordinator, entry, window))
        entities.append(SavingsRollupSensor(coordinator, entry, window))

    # Phase 3.4 — 5 named-comparator rollup sensors, but ONLY when the
    # user has pinned a plan via the OptionsFlow ``named_comparator``
    # step. Skipped otherwise so we don't litter HA with five
    # permanently-unavailable entities for users who haven't opted in.
    # Reading from ``_providers`` directly (not ``data["providers"]``)
    # so the registration check fires on first setup before the
    # coordinator has populated its data dict.
    if "named" in getattr(coordinator, "_providers", {}):
        for window in ("today", "week", "month", "3month", "year"):
            entities.append(NamedComparatorRollupSensor(coordinator, entry, window))

    _LOGGER.info("Registering %d PriceHawk sensor entities", len(entities))
    async_add_entities(entities)
