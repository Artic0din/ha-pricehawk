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

# (key in coordinator.data, _attr_name, is_amber_dependent)
RATE_SENSORS: list[tuple[str, str, bool]] = [
    ("amber_import_rate", "Amber Import Rate", True),
    ("amber_export_rate", "Amber Feed In Tariff", True),
    ("amber_peak_rate", "Amber Peak Rate", True),
    ("globird_import_rate", "GloBird Import Rate", False),
    ("globird_export_rate", "GloBird Feed In Tariff", False),
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
        "daily_cost_history",
        "daily_wins",
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
            "amber_import_kwh": self.coordinator.data.get("amber_import_kwh", 0),
            "amber_export_kwh": self.coordinator.data.get("amber_export_kwh", 0),
            "globird_import_kwh": self.coordinator.data.get("globird_import_kwh", 0),
            "globird_export_kwh": self.coordinator.data.get("globird_export_kwh", 0),
            "daily_wins": self.coordinator.data.get("daily_wins", {"amber": 0, "globird": 0}),
            "daily_cost_history": self.coordinator.data.get("daily_cost_history", []),
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

    _LOGGER.info("Registering %d PriceHawk sensor entities", len(entities))
    async_add_entities(entities)
