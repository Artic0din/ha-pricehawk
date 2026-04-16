"""Accuracy validation tests — verify PriceHawk calculations match real billing data."""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from custom_components.pricehawk.amber_calculator import AmberCalculator
from custom_components.pricehawk.tariff_engine import TariffEngine

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"
AMBER_CSV = FIXTURES_DIR / "amber_sample.csv"

# ---------------------------------------------------------------------------
# Tariff fixtures (from existing test_tariff_engine.py, dict-style incentives)
# ---------------------------------------------------------------------------

ZEROHERO_IMPORT_PERIODS = {
    "peak": {"rate": 38.50, "windows": [["16:00", "23:00"]]},
    "shoulder": {
        "rate": 26.95,
        "windows": [["23:00", "00:00"], ["00:00", "11:00"], ["14:00", "16:00"]],
    },
    "offpeak": {"rate": 0.00, "windows": [["11:00", "14:00"]]},
}

ZEROHERO_EXPORT_PERIODS = {
    "peak": {"rate": 3.00, "windows": [["16:00", "21:00"]]},
    "shoulder": {
        "rate": 0.30,
        "windows": [["21:00", "00:00"], ["00:00", "10:00"], ["14:00", "16:00"]],
    },
    "offpeak": {"rate": 0.00, "windows": [["10:00", "14:00"]]},
}

ZEROHERO_OPTIONS = {
    "plan_type": "zerohero",
    "daily_supply_charge": 113.30,
    "demand_charge": 0.0,
    "import_tariff": {"type": "tou", "periods": ZEROHERO_IMPORT_PERIODS},
    "export_tariff": {"type": "tou", "periods": ZEROHERO_EXPORT_PERIODS},
    "incentives": {
        "zerohero_credit": True,
        "super_export": True,
        "free_power_window": True,
    },
}

BOOST_OPTIONS = {
    "plan_type": "boost",
    "daily_supply_charge": 111.10,
    "demand_charge": 0.0,
    "import_tariff": {
        "type": "flat_stepped",
        "step1_threshold_kwh": 25.0,
        "step1_rate": 21.23,
        "step2_rate": 25.30,
    },
    "export_tariff": {
        "type": "tou",
        "periods": {
            "peak": {"rate": 3.00, "windows": [["16:00", "21:00"]]},
            "shoulder": {
                "rate": 0.10,
                "windows": [
                    ["21:00", "00:00"],
                    ["00:00", "10:00"],
                    ["14:00", "16:00"],
                ],
            },
            "offpeak": {"rate": 0.00, "windows": [["10:00", "14:00"]]},
        },
    },
    "incentives": {},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _simulate_kwh(
    engine_or_calc: TariffEngine | AmberCalculator,
    power_w: float,
    start_dt: datetime,
    duration_minutes: int = 30,
    import_rate_c: float = 20.0,
    export_rate_c: float = 8.0,
) -> None:
    """Feed 30-second readings to simulate consuming energy over duration."""
    steps = duration_minutes * 2  # 30-second intervals
    for i in range(steps + 1):
        t = start_dt + timedelta(seconds=i * 30)
        if isinstance(engine_or_calc, AmberCalculator):
            engine_or_calc.update(power_w, import_rate_c, export_rate_c, t)
        else:
            engine_or_calc.update(power_w, t)


def _read_csv_slots(
    csv_path: Path, day_str: str
) -> dict[str, dict[str, dict[str, float]]]:
    """Read CSV and group rows by Start Time for a given day.

    Returns: {start_time_str: {"general": {price, usage, cost},
                                "feedIn": {price, usage, cost}}}
    """
    slots: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["Day"] != day_str:
                continue
            channel_type = row["Channel Type"]
            start_time = row["Start Time"]
            slots[start_time][channel_type] = {
                "price": float(row["Price"]),
                "usage": float(row["Usage"]),
                "cost": float(row["Cost"]),
            }
    return dict(slots)


# ---------------------------------------------------------------------------
# Amber CSV Replay Tests
# ---------------------------------------------------------------------------


class TestAmberCSVReplay:
    """Replay real Amber CSV data through AmberCalculator and compare costs."""

    @staticmethod
    def _replay_day(
        csv_path: Path, day_str: str
    ) -> tuple[float, float, float, float, float, float]:
        """Replay one day of CSV data through AmberCalculator.

        Returns:
            (csv_import_cost_c, ph_import_cost_c,
             csv_import_kwh, ph_import_kwh,
             csv_export_cost_c, ph_export_cost_c)

        CSV export cost is stored as negative (earnings), so we return
        the absolute value for comparison.
        """
        slots = _read_csv_slots(csv_path, day_str)
        calc = AmberCalculator(
            amber_network_daily_c=0.0, amber_subscription_daily_c=0.0
        )

        csv_import_cost_c = 0.0
        csv_import_kwh = 0.0
        csv_export_cost_c = 0.0
        csv_export_kwh = 0.0

        for start_time_str in sorted(slots.keys()):
            slot = slots[start_time_str]
            gen = slot.get("general", {"price": 0, "usage": 0, "cost": 0})
            feed = slot.get("feedIn", {"price": 0, "usage": 0, "cost": 0})

            # CSV accumulators
            csv_import_cost_c += gen["cost"]
            csv_import_kwh += gen["usage"]
            csv_export_cost_c += abs(feed["cost"])
            csv_export_kwh += feed["usage"]

            imp_kwh = gen["usage"]
            exp_kwh = feed["usage"]
            import_rate_c = gen["price"]
            export_rate_c = abs(feed["price"])

            start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
            replay_date = start_dt.date()

            # Split 30-min slot into import phase and export phase to avoid
            # netting out simultaneous import/export measurements. The meter
            # records both channels independently each half-hour.
            if imp_kwh > 0 and exp_kwh > 0:
                # Proportional time split based on energy
                total_kwh = imp_kwh + exp_kwh
                imp_minutes = 30.0 * (imp_kwh / total_kwh)
                exp_minutes = 30.0 * (exp_kwh / total_kwh)

                # Import phase: positive power
                imp_power_w = imp_kwh / (imp_minutes / 60.0) * 1000.0
                imp_steps = max(1, int(imp_minutes * 2))
                for i in range(imp_steps + 1):
                    t = start_dt + timedelta(seconds=i * 30)
                    if t.date() != replay_date:
                        break
                    calc.update(imp_power_w, import_rate_c, export_rate_c, t)

                # Export phase: negative power
                exp_start = start_dt + timedelta(seconds=imp_steps * 30)
                exp_power_w = -(exp_kwh / (exp_minutes / 60.0) * 1000.0)
                exp_steps = max(1, int(exp_minutes * 2))
                for i in range(exp_steps + 1):
                    t = exp_start + timedelta(seconds=i * 30)
                    if t.date() != replay_date:
                        break
                    calc.update(exp_power_w, import_rate_c, export_rate_c, t)
            else:
                # Pure import or pure export (or zero)
                net_kwh = imp_kwh - exp_kwh
                power_w = net_kwh / 0.5 * 1000.0

                # Feed 60 x 30-second readings per slot. Stop before any
                # timestamp that crosses into the next calendar day.
                for i in range(61):
                    t = start_dt + timedelta(seconds=i * 30)
                    if t.date() != replay_date:
                        break
                    calc.update(power_w, import_rate_c, export_rate_c, t)

        ph_import_cost_c = calc.import_cost_today_c
        ph_import_kwh = calc.import_kwh_today
        ph_export_cost_c = calc.export_earnings_today_c

        return (
            csv_import_cost_c,
            ph_import_cost_c,
            csv_import_kwh,
            ph_import_kwh,
            csv_export_cost_c,
            ph_export_cost_c,
        )

    @pytest.mark.parametrize("day_str", ["2026-04-12", "2026-04-13", "2026-04-14"])
    def test_daily_import_cost_within_1_percent(self, day_str: str) -> None:
        """PriceHawk import cost matches CSV import cost within 1%."""
        csv_cost, ph_cost, _, _, _, _ = self._replay_day(AMBER_CSV, day_str)
        if csv_cost == 0:
            pytest.skip(f"Zero CSV import cost on {day_str}")
        assert ph_cost == pytest.approx(csv_cost, rel=0.01), (
            f"{day_str}: PH={ph_cost:.4f}c vs CSV={csv_cost:.4f}c"
        )

    @pytest.mark.parametrize("day_str", ["2026-04-12", "2026-04-13", "2026-04-14"])
    def test_daily_export_within_5_percent(self, day_str: str) -> None:
        """PriceHawk export earnings match CSV within 5%."""
        _, _, _, _, csv_export, ph_export = self._replay_day(AMBER_CSV, day_str)
        if csv_export == 0:
            pytest.skip(f"Zero CSV export cost on {day_str}")
        assert ph_export == pytest.approx(csv_export, rel=0.05), (
            f"{day_str}: PH={ph_export:.4f}c vs CSV={csv_export:.4f}c"
        )

    @pytest.mark.parametrize("day_str", ["2026-04-12", "2026-04-13", "2026-04-14"])
    def test_energy_kwh_within_1_percent(self, day_str: str) -> None:
        """Total kWh accumulation matches CSV within 1%."""
        _, _, csv_kwh, ph_kwh, _, _ = self._replay_day(AMBER_CSV, day_str)
        if csv_kwh == 0:
            pytest.skip(f"Zero CSV import kWh on {day_str}")
        assert ph_kwh == pytest.approx(csv_kwh, rel=0.01), (
            f"{day_str}: PH={ph_kwh:.4f}kWh vs CSV={csv_kwh:.4f}kWh"
        )


# ---------------------------------------------------------------------------
# GloBird TOU Accuracy Tests
# ---------------------------------------------------------------------------


class TestGloBirdTOUAccuracy:
    """Verify TOU rate calculations match expected costs for known scenarios."""

    def test_peak_10kwh_cost(self) -> None:
        """10kWh at peak (17:00) should cost ~385.0c."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        # 10kWh in 30 min = 20kW = 20000W
        start = datetime(2026, 4, 12, 17, 0, 0)
        _simulate_kwh(engine, 20000.0, start, duration_minutes=30)
        assert engine.import_cost_today_c == pytest.approx(385.0, abs=1.0)

    def test_offpeak_free(self) -> None:
        """5kWh at offpeak (12:00) should cost ~0.0c."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        # 5kWh in 30 min = 10kW = 10000W
        start = datetime(2026, 4, 12, 12, 0, 0)
        _simulate_kwh(engine, 10000.0, start, duration_minutes=30)
        assert engine.import_cost_today_c == pytest.approx(0.0, abs=0.01)

    def test_shoulder_cost(self) -> None:
        """5kWh at shoulder (15:00) should cost ~134.75c."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        # 5kWh in 30 min = 10kW = 10000W
        start = datetime(2026, 4, 12, 15, 0, 0)
        _simulate_kwh(engine, 10000.0, start, duration_minutes=30)
        assert engine.import_cost_today_c == pytest.approx(134.75, abs=1.0)

    def test_rate_boundary_crossing(self) -> None:
        """Readings from 22:50 to 23:10 cross peak->shoulder boundary."""
        engine = TariffEngine(ZEROHERO_OPTIONS)
        start = datetime(2026, 4, 12, 22, 50, 0)
        # Feed 20 minutes of 3kW (1kWh total)
        _simulate_kwh(engine, 3000.0, start, duration_minutes=20)

        # 10 min at peak (22:50-23:00) + 10 min at shoulder (23:00-23:10)
        # Each half = 0.5 kWh
        peak_portion_c = 0.5 * 38.50
        shoulder_portion_c = 0.5 * 26.95
        expected_c = peak_portion_c + shoulder_portion_c
        assert engine.import_cost_today_c == pytest.approx(expected_c, abs=2.0)
        # Verify it's NOT all at one rate
        all_peak_c = 1.0 * 38.50
        all_shoulder_c = 1.0 * 26.95
        assert engine.import_cost_today_c != pytest.approx(all_peak_c, abs=0.5)
        assert engine.import_cost_today_c != pytest.approx(all_shoulder_c, abs=0.5)


# ---------------------------------------------------------------------------
# GloBird Stepped Accuracy Tests
# ---------------------------------------------------------------------------


class TestGloBirdSteppedAccuracy:
    """Verify stepped pricing calculations match expected costs."""

    def test_30kwh_stepped_cost(self) -> None:
        """30kWh over a day should cost 25*21.23 + 5*25.30 = 657.25c."""
        engine = TariffEngine(BOOST_OPTIONS)
        # Spread 30kWh over 6 hours (5kW constant)
        start = datetime(2026, 4, 12, 6, 0, 0)
        _simulate_kwh(engine, 5000.0, start, duration_minutes=360)
        expected_c = 25.0 * 21.23 + 5.0 * 25.30
        assert engine.import_cost_today_c == pytest.approx(expected_c, abs=1.0)

    def test_below_threshold_cost(self) -> None:
        """10kWh should cost ~212.30c (all at step1 rate)."""
        engine = TariffEngine(BOOST_OPTIONS)
        # 10kWh over 2 hours = 5kW
        start = datetime(2026, 4, 12, 8, 0, 0)
        _simulate_kwh(engine, 5000.0, start, duration_minutes=120)
        expected_c = 10.0 * 21.23
        assert engine.import_cost_today_c == pytest.approx(expected_c, abs=0.5)


# ---------------------------------------------------------------------------
# Incentive Accuracy Tests
# ---------------------------------------------------------------------------


class TestIncentiveAccuracy:
    """Verify incentive calculations produce correct financial outcomes."""

    def test_zerohero_credit_earned(self) -> None:
        """Zero import during 18:00-20:00 earns $1 credit in net daily cost."""
        engine = TariffEngine(ZEROHERO_OPTIONS)

        # Run some energy before the window to establish a baseline
        start = datetime(2026, 4, 12, 10, 0, 0)
        _simulate_kwh(engine, 0.0, start, duration_minutes=60)

        # During 18:00-20:00, zero import (pure solar/battery)
        window_start = datetime(2026, 4, 12, 18, 0, 0)
        _simulate_kwh(engine, 0.0, window_start, duration_minutes=120)

        # After window closes (20:01), check the credit
        post_window = datetime(2026, 4, 12, 20, 1, 0)
        engine.update(0.0, post_window)

        assert engine.zerohero_status == "earned"
        # Net daily cost = supply_charge - credit
        # 113.30c supply - 100c credit = 13.30c = 0.133 AUD
        expected_aud = (113.30 - 100.0) / 100.0
        assert engine.net_daily_cost_aud == pytest.approx(expected_aud, abs=0.01)

    def test_super_export_applied(self) -> None:
        """Export 5kWh during 18:00-20:00 uses 15.0 c/kWh super export rate."""
        engine = TariffEngine(ZEROHERO_OPTIONS)

        # Establish baseline just before window to avoid gap accumulation
        pre = datetime(2026, 4, 12, 17, 59, 30)
        engine.update(0.0, pre)

        # Export 5kWh in 30 min = -10kW = -10000W during super export window
        start = datetime(2026, 4, 12, 18, 0, 0)
        _simulate_kwh(engine, -10000.0, start, duration_minutes=30)

        # ~5.08 kWh exported (30s baseline gap adds ~0.08 kWh)
        expected_earnings_c = 5.0 * 15.0  # 75.0c nominal
        assert engine.export_earnings_today_c == pytest.approx(
            expected_earnings_c, abs=2.0
        )
        assert engine.super_export_kwh == pytest.approx(5.0, rel=0.02)
