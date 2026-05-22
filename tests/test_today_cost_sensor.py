"""Phase 9 PR-11 — ChosenPlanCostSensor source-level tests.

Sensor class instantiation requires HA's entity infrastructure (mocked
under conftest); production-class is a MagicMock here. Source-grep
asserts on sensor.py + ducktype the class via its __dict__.
"""

from __future__ import annotations

from pathlib import Path


def _sensor_source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "custom_components" / "pricehawk" / "sensor.py"
    ).read_text()


class TestChosenPlanCostSensor:
    def test_class_defined(self):
        assert "class ChosenPlanCostSensor(" in _sensor_source()

    def test_device_class_monetary(self):
        src = _sensor_source()
        start = src.index("class ChosenPlanCostSensor")
        block = src[start:start + 1500]
        assert "_attr_device_class = SensorDeviceClass.MONETARY" in block

    def test_unit_of_measurement_aud(self):
        src = _sensor_source()
        start = src.index("class ChosenPlanCostSensor")
        block = src[start:start + 1500]
        assert '_attr_native_unit_of_measurement = "AUD"' in block

    def test_state_class_total(self):
        src = _sensor_source()
        start = src.index("class ChosenPlanCostSensor")
        block = src[start:start + 1500]
        assert "_attr_state_class = SensorStateClass.TOTAL" in block

    def test_name_is_today_cost(self):
        src = _sensor_source()
        start = src.index("class ChosenPlanCostSensor")
        block = src[start:start + 1500]
        assert '_attr_name = "PriceHawk Today Cost"' in block

    def test_unique_id_provider_independent(self):
        """Stable across plan swaps — D-P9-2 contract."""
        src = _sensor_source()
        start = src.index("class ChosenPlanCostSensor")
        block = src[start:start + 1500]
        assert 'f"{entry.entry_id}_chosen_plan_today_cost"' in block
        # Must NOT include any provider id reference.
        assert "PROVIDER_AMBER" not in block
        assert "_current_plan_provider.id" not in block

    def test_native_value_reads_chosen_plan_cost(self):
        src = _sensor_source()
        start = src.index("class ChosenPlanCostSensor")
        block = src[start:start + 1500]
        assert "_current_plan_provider" in block
        assert "net_daily_cost_aud" in block

    def test_last_reset_at_midnight(self):
        src = _sensor_source()
        start = src.index("class ChosenPlanCostSensor")
        block = src[start:start + 1500]
        # Anchor: replace(hour=0, minute=0 ...).
        assert "hour=0, minute=0, second=0, microsecond=0" in block

    def test_registered_in_async_setup_entry(self):
        src = _sensor_source()
        assert (
            "entities.append(ChosenPlanCostSensor(coordinator, entry))" in src
        )
