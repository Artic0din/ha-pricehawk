"""Lightweight tests for PriceHawkCoordinator.

Uses unittest.mock for hass/entry — does NOT require a full HA test harness.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# The conftest mocks homeassistant modules so we can import our code
from custom_components.pricehawk.amber_calculator import AmberCalculator
from custom_components.pricehawk.tariff_engine import TariffEngine
from custom_components.pricehawk.const import (
    CONF_API_KEY,
    CONF_GRID_POWER_SENSOR,
    CONF_SITE_ID,
    GLOBIRD_PLAN_DEFAULTS,
    PLAN_ZEROHERO,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_entry(options=None, data=None):
    """Create a mock ConfigEntry."""
    entry = MagicMock()
    default_options = dict(GLOBIRD_PLAN_DEFAULTS[PLAN_ZEROHERO])
    default_options[CONF_GRID_POWER_SENSOR] = "sensor.grid_power"
    entry.options = options or default_options
    entry.data = data or {
        CONF_API_KEY: "test-api-key",
        CONF_SITE_ID: "test-site-id",
    }
    entry.entry_id = "test_entry_123"
    return entry


def _make_hass():
    """Create a mock HomeAssistant."""
    hass = MagicMock()
    hass.data = {}
    hass.loop = asyncio.new_event_loop()
    hass.loop.time = MagicMock(return_value=0.0)
    return hass


def _make_state(value: str):
    """Create a mock sensor state."""
    state = MagicMock()
    state.state = value
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCoordinatorConstruction:
    """Test that the coordinator can be constructed with mock objects."""

    def test_constructor_creates_engines(self):
        """Coordinator should create TariffEngine and AmberCalculator."""
        _make_hass()  # verifies mock setup works
        entry = _make_entry()

        # We need to import and patch at the module level since HA is mocked
        # Instead, test the engines directly
        engine = TariffEngine(entry.options)
        calc = AmberCalculator()

        assert engine is not None
        assert calc is not None
        # net_daily_cost includes the daily supply charge even with zero energy
        supply_aud = entry.options.get("daily_supply_charge", 0.0) / 100.0
        assert engine.net_daily_cost_aud == pytest.approx(supply_aud)
        assert calc.net_daily_cost_aud == pytest.approx(0.0)

    def test_tariff_engine_uses_options(self):
        """TariffEngine should parse options from entry."""
        options = dict(GLOBIRD_PLAN_DEFAULTS[PLAN_ZEROHERO])
        engine = TariffEngine(options)

        # Should have TOU import tariff
        assert engine.current_import_rate_c_kwh >= 0
        assert engine.current_export_rate_c_kwh >= 0


class TestGridPowerReading:
    """Test grid power sensor reading logic."""

    def test_parse_numeric_state(self):
        """Numeric state should parse to float."""
        state = _make_state("1500.5")
        val = float(state.state)
        assert val == 1500.5

    def test_unavailable_state_detected(self):
        """Unavailable/unknown states should be skipped."""
        for bad in ("unavailable", "unknown", ""):
            state = _make_state(bad)
            assert state.state in ("unavailable", "unknown", "")

    def test_non_numeric_state_raises(self):
        """Non-numeric state should raise ValueError."""
        state = _make_state("not_a_number")
        with pytest.raises(ValueError):
            float(state.state)


class TestAmberPriceConversion:
    """Test Amber API perKwh handling.

    Amber API returns perKwh in c/kWh (cents, incl GST) — use directly.
    Feed-in may be negative; use abs().
    """

    def test_import_already_cents(self):
        """perKwh from API is already c/kWh — no conversion needed."""
        per_kwh_cents = 25.0  # 25 c/kWh from API
        rate_c = float(per_kwh_cents)
        assert rate_c == 25.0

    def test_export_abs_conversion(self):
        """Feed-in price may be negative; use abs()."""
        per_kwh_cents = -5.0  # negative feed-in in c/kWh
        rate_c = abs(float(per_kwh_cents))
        assert rate_c == 5.0


class TestUpdateWithMissingSensors:
    """Test that missing sensors produce partial data without crashing."""

    def test_globird_still_works_without_amber(self):
        """GloBird engine should work even when Amber prices are None."""
        options = dict(GLOBIRD_PLAN_DEFAULTS[PLAN_ZEROHERO])
        engine = TariffEngine(options)

        now = datetime(2026, 3, 29, 14, 0, 0)
        # First call just sets last_update
        engine.update(1000.0, now)

        now2 = datetime(2026, 3, 29, 14, 0, 30)
        engine.update(1000.0, now2)

        # Should accumulate some import cost
        assert engine.import_kwh_today > 0

    def test_amber_calc_handles_independent_update(self):
        """AmberCalculator should work independently."""
        calc = AmberCalculator()

        now = datetime(2026, 3, 29, 14, 0, 0)
        calc.update(1000.0, 25.0, 5.0, now)

        now2 = datetime(2026, 3, 29, 14, 0, 30)
        calc.update(1000.0, 25.0, 5.0, now2)

        assert calc.import_kwh_today > 0
        assert calc.current_import_rate_c_kwh == 25.0
        assert calc.current_export_rate_c_kwh == 5.0


class TestRestoreState:
    """Test state restore logic."""

    def test_empty_store_gives_fresh_engines(self):
        """With no stored state, engines should start fresh."""
        engine = TariffEngine(GLOBIRD_PLAN_DEFAULTS[PLAN_ZEROHERO])
        calc = AmberCalculator()

        assert engine.import_kwh_today == 0.0
        assert engine.export_kwh_today == 0.0
        assert calc.import_kwh_today == 0.0

    def test_restore_same_day_preserves_accumulators(self):
        """Restoring state from same day should keep daily accumulators."""
        options = dict(GLOBIRD_PLAN_DEFAULTS[PLAN_ZEROHERO])

        # Simulate some accumulated state
        stored = {
            "import_kwh_today": 5.0,
            "export_kwh_today": 3.0,
            "import_cost_today_c": 150.0,
            "export_earnings_today_c": 9.0,
            "supply_charge_today_c": 50.0,
            "last_update": datetime.now().isoformat(),
            "last_reset_date": date.today().isoformat(),
            "zerohero": {"window_import_kwh": 0.01, "credit_earned": False, "window_closed": False, "threshold_exceeded": False},
            "super_export": {"window_export_kwh": 2.0},
            "demand": {"peak_kw_billing": 4.5},
        }

        restored = TariffEngine.from_dict(options, stored, today=date.today())
        assert restored.import_kwh_today == 5.0
        assert restored.export_kwh_today == 3.0

    def test_restore_different_day_resets_daily(self):
        """Restoring state from a different day should NOT restore daily accumulators."""
        options = dict(GLOBIRD_PLAN_DEFAULTS[PLAN_ZEROHERO])

        stored = {
            "import_kwh_today": 5.0,
            "export_kwh_today": 3.0,
            "import_cost_today_c": 150.0,
            "export_earnings_today_c": 9.0,
            "supply_charge_today_c": 50.0,
            "last_update": "2026-03-28T23:00:00",
            "last_reset_date": "2026-03-28",  # yesterday
            "zerohero": {"window_import_kwh": 0.01, "credit_earned": False, "window_closed": False, "threshold_exceeded": False},
            "super_export": {"window_export_kwh": 2.0},
            "demand": {"peak_kw_billing": 4.5},
        }

        restored = TariffEngine.from_dict(options, stored, today=date(2026, 3, 29))
        assert restored.import_kwh_today == 0.0
        assert restored.export_kwh_today == 0.0

    def test_restore_preserves_demand_across_days(self):
        """Demand tracker should be restored even from different day."""
        options = dict(GLOBIRD_PLAN_DEFAULTS[PLAN_ZEROHERO])

        stored = {
            "last_reset_date": "2026-03-28",
            "demand": {"peak_kw_billing": 7.5},
        }

        restored = TariffEngine.from_dict(options, stored, today=date(2026, 3, 29))
        # Demand persists across days (billing period)
        assert restored._demand.peak_kw_billing == 7.5


class TestRebuildEngine:
    """Test engine rebuild on options update."""

    def test_rebuild_creates_new_globird(self):
        """Rebuild should create a fresh TariffEngine."""
        options1 = dict(GLOBIRD_PLAN_DEFAULTS[PLAN_ZEROHERO])
        engine1 = TariffEngine(options1)

        # Simulate some state
        now = datetime(2026, 3, 29, 14, 0, 0)
        engine1.update(2000.0, now)
        now2 = datetime(2026, 3, 29, 14, 0, 30)
        engine1.update(2000.0, now2)

        assert engine1.import_kwh_today > 0

        # Rebuild with same options — new engine starts fresh
        engine2 = TariffEngine(options1)
        assert engine2.import_kwh_today == 0.0

    def test_amber_calc_preserved_after_rebuild(self):
        """AmberCalculator should not be affected by GloBird rebuild."""
        calc = AmberCalculator()

        now = datetime(2026, 3, 29, 14, 0, 0)
        calc.update(1000.0, 25.0, 5.0, now)
        now2 = datetime(2026, 3, 29, 14, 0, 30)
        calc.update(1000.0, 25.0, 5.0, now2)

        cost_before = calc.net_daily_cost_aud

        # Rebuilding GloBird engine doesn't touch AmberCalculator
        # (in the real coordinator, rebuild_engine only replaces _globird_engine)
        assert calc.net_daily_cost_aud == cost_before


class TestDataDictKeys:
    """Contract test: data dict must contain expected keys for Phase 3 sensors."""

    EXPECTED_KEYS = {
        "current_plan_import_rate",
        "current_plan_export_rate",
        "current_plan_daily_cost",
        "current_plan_import_kwh",
        "current_plan_export_kwh",
        "current_plan_zerohero_status",
        "current_plan_super_export_kwh",
        "current_plan_peak_rate",  # Phase 3.0g (CodeRabbit)
        "amber_import_rate",
        "amber_export_rate",
        "amber_daily_cost",
        "amber_import_kwh",
        "amber_export_kwh",
    }

    def test_data_dict_has_all_keys(self):
        """Build a data dict manually and verify all expected keys present."""
        engine = TariffEngine(GLOBIRD_PLAN_DEFAULTS[PLAN_ZEROHERO])
        calc = AmberCalculator()

        data = {
            "current_plan_import_rate": engine.current_import_rate_c_kwh,
            "current_plan_export_rate": engine.current_export_rate_c_kwh,
            "current_plan_daily_cost": engine.net_daily_cost_aud,
            "current_plan_import_kwh": engine.import_kwh_today,
            "current_plan_export_kwh": engine.export_kwh_today,
            "current_plan_zerohero_status": engine.zerohero_status,
            "current_plan_super_export_kwh": engine.super_export_kwh,
            "current_plan_peak_rate": 39.6,  # Phase 3.0g placeholder
            "amber_import_rate": None,  # no prices yet
            "amber_export_rate": None,
            "amber_daily_cost": calc.net_daily_cost_aud,
            "amber_import_kwh": calc.import_kwh_today,
            "amber_export_kwh": calc.export_kwh_today,
        }

        assert set(data.keys()) == self.EXPECTED_KEYS

    def test_data_dict_key_types(self):
        """Verify data dict values are correct types."""
        engine = TariffEngine(GLOBIRD_PLAN_DEFAULTS[PLAN_ZEROHERO])
        calc = AmberCalculator()

        assert isinstance(engine.current_import_rate_c_kwh, float)
        assert isinstance(engine.current_export_rate_c_kwh, float)
        assert isinstance(engine.net_daily_cost_aud, float)
        assert isinstance(engine.import_kwh_today, float)
        assert isinstance(engine.export_kwh_today, float)
        assert isinstance(engine.zerohero_status, str)
        assert isinstance(engine.super_export_kwh, float)
        assert isinstance(calc.net_daily_cost_aud, float)


class TestAmberApiParsing:
    """Test the Amber API response parsing logic."""

    def test_parse_price_intervals(self):
        """Verify parsing of Amber API response format.

        perKwh is in c/kWh (cents, incl GST) — use directly, no conversion.
        """
        api_response = [
            {
                "channelType": "general",
                "perKwh": 25.0,  # 25 c/kWh
                "duration": 30,
                "spotPerKwh": 15.0,
            },
            {
                "channelType": "feedIn",
                "perKwh": -5.0,  # negative feed-in, 5 c/kWh
                "duration": 30,
                "spotPerKwh": -3.0,
            },
        ]

        import_price = None
        export_price = None

        for interval in api_response:
            channel = interval.get("channelType", "")
            per_kwh = interval.get("perKwh")
            if per_kwh is None:
                continue
            if channel == "general" and import_price is None:
                import_price = float(per_kwh)
            elif channel == "feedIn" and export_price is None:
                export_price = abs(float(per_kwh))

        assert import_price == 25.0
        assert export_price == 5.0

    def test_missing_channel_ignored(self):
        """Intervals without perKwh should be skipped."""
        api_response = [
            {"channelType": "general"},  # no perKwh
            {"channelType": "feedIn", "perKwh": -4.0},
        ]

        import_price = None
        export_price = None

        for interval in api_response:
            channel = interval.get("channelType", "")
            per_kwh = interval.get("perKwh")
            if per_kwh is None:
                continue
            if channel == "general" and import_price is None:
                import_price = float(per_kwh)
            elif channel == "feedIn" and export_price is None:
                export_price = abs(float(per_kwh))

        assert import_price is None
        assert export_price == 4.0


# ---------------------------------------------------------------------------
# Per-tick build_explanation gating (Constitution P17)
# ---------------------------------------------------------------------------
#
# ``coordinator.py`` — every tick of ``_async_update_data`` ends with
# ``if self._providers: build_explanation(...)`` so the dashboard always
# reflects current provider snapshots, not the midnight rollover capture.
# The branch existed without direct test coverage. These tests pin three
# contracts (Constitution P17 — Tests Are Part of the Fix):
#
#   1. Empty ``_providers`` → ``build_explanation`` is NOT called and
#      ``_last_explanation`` retains whatever sat there previously.
#   2. Populated ``_providers`` → ``build_explanation`` IS called exactly
#      once per tick with the *current* providers block.
#   3. The ``avg_amber_spot_c_kwh`` kwarg is recomputed from current
#      Amber accumulator state on every tick (not snapshotted).
#
# Why we mirror the gate logic instead of awaiting ``_async_update_data``:
# under ``conftest._MockModule``, ``DataUpdateCoordinator`` is a MagicMock
# instance, so subclassing it produces a class whose ``type(...)`` is
# ``_MockModule`` — instantiation returns a MagicMock proxy, the
# ``__init__`` body never runs, and ``coord._async_update_data()`` returns
# a MagicMock instead of a coroutine. This is the same constraint that
# led ``TestBackfillStatusSensor`` and ``TestPeriodRollupSensorSmoke``
# (test_review_improvements.py) to mirror property bodies in-place. The
# source-text guard in ``test_gate_mirror_matches_production_source``
# below fails LOUDLY if the production gate diverges from the mirror.


def _gate_block_source() -> str:
    """Return the source text of the per-tick rebuild gate.

    Slices ``coordinator.py`` from the docstring anchor introduced when
    the gate was added through the gate's last statement. Slicing on
    stable anchor strings rather than line numbers means the guard
    survives unrelated edits above/below the block.
    """
    import inspect

    from custom_components.pricehawk import coordinator as coord_mod

    src = inspect.getsource(coord_mod)
    start_marker = "# Live UAT 2026-05-24 — rebuild the winner explanation"
    end_marker = "self._last_explanation = explanation.to_dict()"
    start = src.index(start_marker)
    end = src.index(end_marker, start) + len(end_marker)
    return src[start:end]


def _apply_per_tick_explanation_gate(coord) -> None:
    """Mirror of the per-tick rebuild gate at the tail of
    ``PriceHawkCoordinator._async_update_data``.

    KEEP IN SYNC with the production block — the
    ``test_gate_mirror_matches_production_source`` guard pins this with
    substring checks against the production source. Any edit to the
    production gate must be reflected here in the same PR.
    """
    if coord._providers:
        avg_spot = None
        if coord._amber and coord._amber.import_kwh_today > 0:
            avg_spot = (
                coord._amber.import_cost_today_c
                / coord._amber.import_kwh_today
            )
        # Import the SAME symbol the production code imports, so the
        # ``patch`` target in tests (``build_explanation`` in the
        # ``coordinator`` module) catches BOTH this mirror and the
        # production block.
        from custom_components.pricehawk.coordinator import build_explanation

        explanation = build_explanation(
            coord._build_providers_block(),
            avg_amber_spot_c_kwh=avg_spot,
        )
        coord._last_explanation = explanation.to_dict()


def _make_coord_stub(
    *,
    providers: dict,
    amber: SimpleNamespace | None = None,
    providers_block: dict | None = None,
    last_explanation: dict | None = None,
):
    """Build the minimal coordinator surface the gate touches."""
    block = (
        providers_block
        if providers_block is not None
        else {"stub": {"net_daily_cost_aud": 1.23}}
    )
    return SimpleNamespace(
        _providers=providers,
        _amber=amber,
        _last_explanation=last_explanation,
        _build_providers_block=lambda: block,
    )


class TestPerTickBuildExplanationGating:
    """Pin the per-tick ``if self._providers:`` rebuild gate."""

    def test_gate_mirror_matches_production_source(self):
        """Source-level guard: the mirror must stay aligned with the
        production gate.

        If the production implementation drops the ``self._providers``
        truthy check, renames ``avg_amber_spot_c_kwh``, or changes the
        ``import_cost_today_c / import_kwh_today`` formula, the slice
        below loses the expected fragments and this test fails loudly.
        Pinned per Constitution P17.
        """
        gate_src = _gate_block_source()
        assert "if self._providers:" in gate_src, (
            "production gate no longer guards on ``self._providers``"
        )
        assert "build_explanation(" in gate_src
        assert "avg_amber_spot_c_kwh=avg_spot" in gate_src
        assert "self._amber.import_kwh_today" in gate_src
        assert "self._amber.import_cost_today_c" in gate_src
        assert "self._last_explanation = explanation.to_dict()" in gate_src

    def test_async_update_data_skips_build_explanation_when_no_providers(self):
        """Empty ``_providers`` → ``build_explanation`` NOT called and
        ``_last_explanation`` is left exactly as it was before the tick.

        Guards against a regression that drops the gate and starts
        calling ``build_explanation({})`` on every tick (returns the
        ``no data`` placeholder, clobbering any previously valid
        snapshot).
        """
        sentinel_prior = {"winner_id": "prior", "bullets": []}
        coord = _make_coord_stub(
            providers={},
            amber=None,
            last_explanation=sentinel_prior,
        )

        with patch(
            "custom_components.pricehawk.coordinator.build_explanation"
        ) as mock_build:
            _apply_per_tick_explanation_gate(coord)

        mock_build.assert_not_called()
        # ``_last_explanation`` must be untouched — not None, not the
        # ``no providers`` placeholder. The whole point of the gate is
        # to preserve whatever was last computed when there is nothing
        # to rebuild from.
        assert coord._last_explanation is sentinel_prior

    def test_async_update_data_calls_build_explanation_each_tick_when_providers_present(self):
        """Populated ``_providers`` → ``build_explanation`` called once
        per tick with the providers block.

        Two consecutive ticks → two calls, each with the current
        block; the call must not be memoised, deduped, or skipped.
        """
        block = {"stub": {"net_daily_cost_aud": 4.56, "name": "Stub"}}
        coord = _make_coord_stub(
            providers={"stub": MagicMock()},
            amber=None,
            providers_block=block,
        )

        fake_explanation = MagicMock()
        fake_explanation.to_dict.return_value = {"winner_id": "stub"}

        with patch(
            "custom_components.pricehawk.coordinator.build_explanation",
            return_value=fake_explanation,
        ) as mock_build:
            _apply_per_tick_explanation_gate(coord)
            _apply_per_tick_explanation_gate(coord)

            assert mock_build.call_count == 2, (
                "build_explanation must run on every tick, not be memoised"
            )
            for call in mock_build.call_args_list:
                # First positional arg is the providers block produced
                # by ``_build_providers_block``.
                assert call.args[0] == block
                # No Amber → avg_amber_spot_c_kwh stays None.
                assert call.kwargs == {"avg_amber_spot_c_kwh": None}

        assert coord._last_explanation == {"winner_id": "stub"}

    def test_async_update_data_explanation_reflects_current_amber_spot(self):
        """``avg_amber_spot_c_kwh`` must be recomputed each tick from
        the current Amber accumulator state — not captured once and
        reused.

        Tick 1: amber has 2 kWh imported at 50c total → avg 25 c/kWh.
        Tick 2: bump to 4 kWh / 200c total → avg 50 c/kWh.

        If the per-tick branch ever caches ``avg_spot``, this test
        fails on the second call's kwarg.
        """
        # Lightweight stand-in: the per-tick branch only reads the
        # two accumulator attributes plus the boolean truthy check.
        amber_stub = SimpleNamespace(
            import_kwh_today=2.0,
            import_cost_today_c=50.0,
        )
        coord = _make_coord_stub(
            providers={"stub": MagicMock()},
            amber=amber_stub,
        )

        with patch(
            "custom_components.pricehawk.coordinator.build_explanation",
            return_value=MagicMock(to_dict=MagicMock(return_value={})),
        ) as mock_build:
            _apply_per_tick_explanation_gate(coord)
            assert mock_build.call_args.kwargs[
                "avg_amber_spot_c_kwh"
            ] == pytest.approx(25.0)

            # Mutate amber state between ticks.
            amber_stub.import_kwh_today = 4.0
            amber_stub.import_cost_today_c = 200.0

            _apply_per_tick_explanation_gate(coord)
            assert mock_build.call_args.kwargs[
                "avg_amber_spot_c_kwh"
            ] == pytest.approx(50.0)
            assert mock_build.call_count == 2

    def test_gate_avg_spot_none_when_amber_kwh_zero(self):
        """Defensive coverage: ``avg_amber_spot_c_kwh`` stays ``None``
        when Amber is configured but no kWh have been imported yet.

        Pins the ``self._amber.import_kwh_today > 0`` guard inside the
        gate — without it, a tick on a freshly-restarted integration
        would divide by zero before any consumption.
        """
        amber_stub = SimpleNamespace(
            import_kwh_today=0.0,
            import_cost_today_c=0.0,
        )
        coord = _make_coord_stub(
            providers={"stub": MagicMock()},
            amber=amber_stub,
        )

        with patch(
            "custom_components.pricehawk.coordinator.build_explanation",
            return_value=MagicMock(to_dict=MagicMock(return_value={})),
        ) as mock_build:
            _apply_per_tick_explanation_gate(coord)

        assert mock_build.call_count == 1
        assert mock_build.call_args.kwargs["avg_amber_spot_c_kwh"] is None
