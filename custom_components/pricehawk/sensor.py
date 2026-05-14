"""Sensor platform for PriceHawk."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
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

_LOGGER = logging.getLogger(__name__)

# Peak-rate sensors only. Import/export rates are owned by GenericProviderRateSensor
# (registered in async_setup_entry's providers loop) — listing them here too caused
# unique_id collisions that dropped the entities the dashboard depends on.
# (key in coordinator.data, _attr_name, is_amber_dependent)
RATE_SENSORS: list[tuple[str, str, bool]] = [
    ("amber_peak_rate", "Amber Peak Rate", True),
    ("globird_peak_rate", "GloBird Peak Rate", False),
]


class PriceHawkBaseSensor(CoordinatorEntity, SensorEntity):
    """Base sensor for all PriceHawk sensors."""

    def __init__(self, coordinator: Any, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"

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
            return (
                super().available
                and self.coordinator.data.get("amber_import_rate") is not None
            )
        return super().available


class BestProviderSensor(PriceHawkBaseSensor):
    """Shows which provider has the cheapest current import rate."""

    _attr_name = "PriceHawk Best Provider"

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "best_provider")

    @property
    def native_value(self) -> str:
        amber = self.coordinator.data.get("amber_import_rate")
        globird = self.coordinator.data.get("globird_import_rate")
        if amber is None:
            return "GloBird Energy"
        if globird is None:
            return "Amber Electric"
        return "Amber Electric" if amber <= globird else "GloBird Energy"


class CheapestTodaySensor(PriceHawkBaseSensor):
    """Shows which provider is cheapest by total daily cost."""

    _attr_name = "PriceHawk Cheapest Today"

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "cheapest_today")

    @property
    def native_value(self) -> str:
        amber = self.coordinator.data.get("amber_daily_cost")
        globird = self.coordinator.data.get("globird_daily_cost")
        if amber is None:
            return "GloBird Energy"
        if globird is None:
            return "Amber Electric"
        return "Amber Electric" if amber <= globird else "GloBird Energy"


class BestRateSensor(PriceHawkBaseSensor):
    """The cheaper provider's current import rate in c/kWh."""

    _attr_name = "PriceHawk Best Rate"
    _attr_native_unit_of_measurement = "c/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "best_rate")

    @property
    def native_value(self) -> float | None:
        """Return the cheapest current import rate across both providers."""
        amber = self.coordinator.data.get("amber_import_rate")
        globird = self.coordinator.data.get("globird_import_rate")
        if amber is None:
            return globird
        if globird is None:
            return amber
        return min(amber, globird)


class SavingTodaySensor(PriceHawkBaseSensor):
    """Directional saving based on current provider. Positive = save by switching."""

    _attr_name = "PriceHawk Saving Today"
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

    _attr_name = "PriceHawk Saving Month"
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

    _attr_name = "PriceHawk Metrics Won"

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "metrics_won")

    @property
    def native_value(self) -> str | None:
        val = self.coordinator.data.get("metrics_won")
        if val is not None:
            return val
        # Compute inline if coordinator doesn't provide it
        data = self.coordinator.data
        amber_import = data.get("amber_import_rate")
        globird_import = data.get("globird_import_rate")
        amber_export = data.get("amber_export_rate")
        globird_export = data.get("globird_export_rate")
        amber_daily = data.get("amber_daily_cost")
        globird_daily = data.get("globird_daily_cost")
        if amber_import is None or globird_import is None:
            return "0/3"
        metrics = [
            amber_import < globird_import,
            (amber_export or 0) > (globird_export or 0),
            (amber_daily or 0) < (globird_daily or 0),
        ]
        won = sum(metrics)
        return f"{won}/{len(metrics)}"


class AmberDailyChargesSensor(PriceHawkBaseSensor):
    """Combined Amber network + subscription daily charges."""

    _attr_name = "PriceHawk Amber Daily Charges"
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


class LastUpdatedSensor(PriceHawkBaseSensor):
    """Timestamp of the last successful coordinator update."""

    _attr_name = "PriceHawk Last Updated"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({
        "price_history",
        "today_schedule",
        "daily_cost_history",
        "daily_wins",
        "csv_comparison",
    })

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "last_updated")

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
            "globird_import_kwh": self.coordinator.data.get("globird_import_kwh", 0),
            "globird_export_kwh": self.coordinator.data.get("globird_export_kwh", 0),
            "daily_wins": self.coordinator.data.get("daily_wins", {"amber": 0, "globird": 0}),
            "daily_cost_history": self.coordinator.data.get("daily_cost_history", []),
            "csv_comparison": self.coordinator.data.get("csv_comparison"),
        }


class GloBirdDailySupplySensor(PriceHawkBaseSensor):
    """GloBird daily supply charge (fixed value, no state_class)."""

    _attr_name = "PriceHawk GloBird Daily Supply"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "AUD"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "globird_daily_supply_aud")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("globird_daily_supply_aud")


class ZeroHeroStatusSensor(PriceHawkBaseSensor):
    """GloBird ZeroHero daily credit status."""

    _attr_name = "PriceHawk ZeroHero Status"
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "zerohero_status")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.get("globird_zerohero_status")


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
        self._attr_name = f"PriceHawk {provider_name} {suffix}"
        self._provider_id = provider_id
        self._kind = kind

    @property
    def native_value(self) -> float | None:
        provs = self.coordinator.data.get("providers", {})
        prov = provs.get(self._provider_id)
        if prov is None:
            return None
        key = (
            "import_rate_c_kwh" if self._kind == "import" else "export_rate_c_kwh"
        )
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
        self._attr_name = f"PriceHawk {provider_name} Cost Today"
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


class AmberForecastSensor(PriceHawkBaseSensor):
    """Amber 24-hour forecast peak / dip / average price.

    State = c/kWh, attributes carry the timestamp of the peak/dip and
    the full 48-interval forecast list.
    """

    _attr_native_unit_of_measurement = "c/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: Any,
        entry: ConfigEntry,
        kind: str,  # "peak" / "dip" / "avg"
    ) -> None:
        super().__init__(coordinator, entry, f"amber_forecast_{kind}")
        self._kind = kind
        nice = {"peak": "Peak", "dip": "Dip", "avg": "Average"}[kind]
        self._attr_name = f"PriceHawk Amber Forecast {nice}"
        if kind == "peak":
            self._attr_icon = "mdi:trending-up"
        elif kind == "dip":
            self._attr_icon = "mdi:trending-down"
        else:
            self._attr_icon = "mdi:chart-line"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get(f"amber_forecast_{self._kind}_c_kwh")

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data.get(f"amber_forecast_{self._kind}_c_kwh")
            is not None
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

    _attr_name = "PriceHawk Winner Explanation"
    _attr_icon = "mdi:trophy"

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "winner_explanation")

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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PriceHawk sensors from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []

    # 6 rate sensors
    for key, name, amber_dep in RATE_SENSORS:
        entities.append(
            PriceHawkRateSensor(
                coordinator, entry, key, name, amber_dependent=amber_dep
            )
        )

    # Comparison and cost sensors
    entities.append(BestProviderSensor(coordinator, entry))
    entities.append(BestRateSensor(coordinator, entry))
    entities.append(CheapestTodaySensor(coordinator, entry))
    entities.append(SavingTodaySensor(coordinator, entry))
    entities.append(SavingMonthSensor(coordinator, entry))
    entities.append(MetricsWonSensor(coordinator, entry))

    # Amber daily fixed charges
    entities.append(AmberDailyChargesSensor(coordinator, entry))

    # Per-provider daily total cost
    entities.append(ProviderDailyCostSensor(coordinator, entry, "amber_daily_cost", "PriceHawk Amber Cost Today"))
    entities.append(ProviderDailyCostSensor(coordinator, entry, "globird_daily_cost", "PriceHawk GloBird Cost Today"))

    # Import/export cost breakdowns
    entities.append(ProviderDailyCostSensor(coordinator, entry, "amber_import_cost_aud", "PriceHawk Amber Import Cost"))
    entities.append(ProviderDailyCostSensor(coordinator, entry, "amber_export_credit_aud", "PriceHawk Amber Export Credit"))
    entities.append(ProviderDailyCostSensor(coordinator, entry, "globird_import_cost_aud", "PriceHawk GloBird Import Cost"))
    entities.append(ProviderDailyCostSensor(coordinator, entry, "globird_export_credit_aud", "PriceHawk GloBird Export Credit"))

    # Daily supply charge (fixed value — no state_class)
    entities.append(GloBirdDailySupplySensor(coordinator, entry))

    # Timestamp
    entities.append(LastUpdatedSensor(coordinator, entry))

    # Bonus: ZeroHero status
    entities.append(ZeroHeroStatusSensor(coordinator, entry))

    # Generic per-provider sensors (pricehawk_<provider>_*) — registered for
    # every provider currently active in the coordinator. Reads the canonical
    # data["providers"][<id>] block.
    providers_block = coordinator.data.get("providers", {}) if coordinator.data else {}
    for provider_id, snap in providers_block.items():
        provider_name = snap.get("name", provider_id.title())
        entities.append(
            GenericProviderRateSensor(
                coordinator, entry, provider_id, provider_name, "import"
            )
        )
        entities.append(
            GenericProviderRateSensor(
                coordinator, entry, provider_id, provider_name, "export"
            )
        )
        entities.append(
            GenericProviderCostSensor(
                coordinator, entry, provider_id, provider_name
            )
        )

    # Amber 24h forecast — only when Amber is registered as a provider
    if "amber" in providers_block:
        entities.append(AmberForecastSensor(coordinator, entry, "peak"))
        entities.append(AmberForecastSensor(coordinator, entry, "dip"))
        entities.append(AmberForecastSensor(coordinator, entry, "avg"))

    # Winner explanation (state = section label, attributes = bullets)
    entities.append(WinnerExplanationSensor(coordinator, entry))

    _LOGGER.info("Registering %d PriceHawk sensor entities", len(entities))
    async_add_entities(entities)
