"""Unit tests for sensor entities in sensor.py."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from homeassistant.util import dt as dt_util

import pytest

from custom_components.pricehawk import sensor


class TestSensorsCoverage:
    def test_sensor_instantiation_and_properties(self):
        today_date = datetime.now()
        dt_util.now = MagicMock(return_value=today_date)
        today_str = today_date.strftime("%Y-%m-%d")

        coordinator = MagicMock()
        # Setup mock coordinator data
        coordinator.data = {
            "current_plan_import_rate": 35.0,
            "current_plan_export_rate": 10.0,
            "current_plan_daily_cost": 5.0,
            "current_plan_daily_supply_aud": 1.20,
            "current_plan_import_cost_aud": 6.0,
            "current_plan_export_credit_aud": 2.0,
            "amber_import_rate": 30.0,
            "amber_export_rate": 8.0,
            "amber_daily_cost": 4.5,
            "amber_daily_fixed_charges": 1.10,
            "amber_import_cost_aud": 5.5,
            "amber_export_credit_aud": 1.5,
            "best_provider": "Amber Electric",
            "cheapest_today": "Amber Electric",
            "best_rate": 30.0,
            "saving_today": 0.5,
            "saving_month": 15.0,
            "saving_month_aud": 15.0,
            "metrics_won": "2/3",
            "last_updated": datetime.now(),
            "providers": {
                "amber": {
                    "name": "Amber Electric",
                    "import_rate_c_kwh": 30.0,
                    "export_rate_c_kwh": 8.0,
                    "net_daily_cost_aud": 4.5,
                },
                "flow_power": {
                    "name": "Flow Power",
                    "import_rate_c_kwh": 28.0,
                    "export_rate_c_kwh": 7.0,
                    "net_daily_cost_aud": 4.2,
                    "import_cost_today_aud": 4.8,
                    "export_credit_today_aud": 1.0,
                    "daily_fixed_charges_aud": 0.90,
                },
            },
            "daily_cost_history": [
                {
                    "date": today_str,
                    "current": 5.0,
                    "alt_best": 4.2,
                    "savings": 0.8,
                    "named": 4.5,
                }
            ],
            "current_plan_zerohero_status": "active",
        }

        entry = MagicMock()
        entry.entry_id = "test_entry"

        # Instantiate rate sensors
        rate_sensor_1 = sensor.PriceHawkRateSensor(
            coordinator, entry, "current_plan_import_rate", "Test Import Rate"
        )
        assert rate_sensor_1.native_value == 35.0
        assert rate_sensor_1.available is True
        assert isinstance(rate_sensor_1.entity_description, sensor.PriceHawkSensorEntityDescription)

        # Test base sensor device_info
        assert rate_sensor_1.device_info["name"] == "PriceHawk"

        # BestProviderSensor
        best_provider = sensor.BestProviderSensor(coordinator, entry)
        assert best_provider.native_value == "Amber Electric"

        # CheapestTodaySensor
        cheapest_today = sensor.CheapestTodaySensor(coordinator, entry)
        assert cheapest_today.native_value == "Amber Electric"

        # BestRateSensor
        best_rate = sensor.BestRateSensor(coordinator, entry)
        assert best_rate.native_value == 30.0

        # SavingTodaySensor
        saving_today = sensor.SavingTodaySensor(coordinator, entry)
        assert saving_today.native_value == 0.5

        # SavingMonthSensor
        saving_month = sensor.SavingMonthSensor(coordinator, entry)
        assert saving_month.native_value == 15.0

        # MetricsWonSensor
        metrics_won = sensor.MetricsWonSensor(coordinator, entry)
        assert metrics_won.native_value == "2/3"

        # AmberDailyChargesSensor
        amber_charges = sensor.AmberDailyChargesSensor(coordinator, entry)
        assert amber_charges.native_value == 1.10

        # ProviderDailyCostSensor
        provider_cost = sensor.ProviderDailyCostSensor(
            coordinator, entry, "current_plan_daily_cost", "Current Plan Daily Cost"
        )
        assert provider_cost.native_value == 5.0
        assert provider_cost.last_reset is not None

        # ChosenPlanCostSensor
        # We need mock provider for ChosenPlanCostSensor
        provider = MagicMock()
        provider.net_daily_cost_aud = 5.0
        provider.id = "current"
        coordinator._current_plan_provider = provider
        chosen_cost = sensor.ChosenPlanCostSensor(coordinator, entry)
        assert chosen_cost.native_value == 5.0
        assert chosen_cost.last_reset is not None

        # LastUpdatedSensor
        last_updated = sensor.LastUpdatedSensor(coordinator, entry)
        assert last_updated.native_value is not None
        assert "price_history" in last_updated.extra_state_attributes

        # CurrentPlanDailySupplySensor
        supply = sensor.CurrentPlanDailySupplySensor(coordinator, entry)
        assert supply.native_value == 1.20

        # ZeroHeroStatusSensor
        zerohero = sensor.ZeroHeroStatusSensor(coordinator, entry)
        assert zerohero.native_value == "active"

        # GenericProviderRateSensor
        g_import = sensor.GenericProviderRateSensor(
            coordinator, entry, "flow_power", "Flow Power", "import"
        )
        assert g_import.native_value == 28.0

        # GenericProviderCostSensor
        g_cost = sensor.GenericProviderCostSensor(coordinator, entry, "flow_power", "Flow Power")
        assert g_cost.native_value == 4.2
        assert g_cost.last_reset is not None

        # GenericProviderBreakdownSensor
        g_breakdown = sensor.GenericProviderBreakdownSensor(
            coordinator, entry, "flow_power", "Flow Power", "import_cost"
        )
        assert g_breakdown.native_value == 4.8
        assert g_breakdown.last_reset is not None

        # AmberForecastSensor
        coordinator.data["amber_forecast_peak_c_kwh"] = 40.0
        forecast = sensor.AmberForecastSensor(coordinator, entry, "peak")
        assert forecast.native_value == 40.0

        # WinnerExplanationSensor
        coordinator.data["last_explanation"] = {
            "section_label": "Amber Won",
            "bullets": ["point 1"],
        }
        winner_exp = sensor.WinnerExplanationSensor(coordinator, entry)
        assert winner_exp.native_value == "Amber Won"
        assert winner_exp.extra_state_attributes["bullets"] == ["point 1"]

        # Rollup sensors
        cur_rollup = sensor.CurrentCostRollupSensor(coordinator, entry, "today")
        assert cur_rollup.native_value == 5.0

        best_rollup = sensor.BestAlternativeRollupSensor(coordinator, entry, "today")
        assert best_rollup.native_value == 4.2

        save_rollup = sensor.SavingsRollupSensor(coordinator, entry, "today")
        assert save_rollup.native_value == pytest.approx(0.8)

        named_rollup = sensor.NamedComparatorRollupSensor(coordinator, entry, "today")
        assert named_rollup.native_value == 4.5

    @pytest.mark.asyncio
    async def test_async_setup_entry(self):
        hass = MagicMock()
        entry = MagicMock()

        coordinator = MagicMock()
        coordinator.data = {
            "providers": {
                "amber": {"name": "Amber Electric"},
                "flow_power": {"name": "Flow Power"},
                "named": {"name": "Pinned Named Comparator"},
            }
        }
        coordinator._current_plan_provider.id = "globird"
        coordinator._providers = {
            "globird": MagicMock(),
            "amber": MagicMock(),
            "flow_power": MagicMock(),
            "named": MagicMock(),
        }

        from custom_components.pricehawk.data import PriceHawkData

        entry.runtime_data = PriceHawkData(coordinator=coordinator)

        entities_added = []

        def _add_entities(list_of_entities):
            entities_added.extend(list_of_entities)

        await sensor.async_setup_entry(hass, entry, _add_entities)

        assert len(entities_added) > 0
        named_rollups = [
            e for e in entities_added if isinstance(e, sensor.NamedComparatorRollupSensor)
        ]
        assert len(named_rollups) == 5

        amber_forecasts = [e for e in entities_added if isinstance(e, sensor.AmberForecastSensor)]
        assert len(amber_forecasts) == 3
