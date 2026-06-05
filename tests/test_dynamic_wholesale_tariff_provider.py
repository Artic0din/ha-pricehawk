"""Phase 7 PR-2b — DynamicWholesaleTariffProvider + config-flow wiring tests.

Covers AC-1 through AC-10 of 07-02b-PLAN. Provider unit-tests use a
mock ``WholesalePriceSource``. Config-flow routing is tested via the
module-level option-builder helpers + source-string asserts; the
ConfigFlow class itself cannot be instantiated under the conftest HA
stubs (its base class is a MagicMock), so the step methods are exercised
manually via UAT per the plan's verification section.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from custom_components.pricehawk.providers import (
    DynamicWholesaleTariffProvider as PackageExport,
)
from custom_components.pricehawk.providers import __all__ as providers_all
from custom_components.pricehawk.providers.base import Provider
from custom_components.pricehawk.providers.dynamic_wholesale_tariff import (
    STATE_VERSION,
    DynamicWholesaleTariffProvider,
)
from custom_components.pricehawk.providers.openelectricity import WholesalePrice


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


class _MockPriceSource:
    """Minimal WholesalePriceSource — fetch returns whatever you queue."""

    def __init__(self, queue: list[WholesalePrice | None] | None = None):
        self.queue = list(queue or [])
        self.fetch_calls = 0
        self.last_good_calls = 0
        self._cached: WholesalePrice | None = None

    async def fetch_current_price(self, region: str) -> WholesalePrice | None:
        self.fetch_calls += 1
        if not self.queue:
            return None
        result = self.queue.pop(0)
        if isinstance(result, Exception):
            raise result
        if result is not None:
            self._cached = result
        return result

    def last_good(self, region: str) -> WholesalePrice | None:
        self.last_good_calls += 1
        return self._cached


def _make_provider(
    *,
    region: str = "NSW1",
    daily_supply_c: float = 110.0,
    provider_id: str = "dwt_openelectricity",
    name: str = "Dynamic Wholesale Tariff — OpenElectricity",
) -> tuple[DynamicWholesaleTariffProvider, _MockPriceSource]:
    src = _MockPriceSource()
    p = DynamicWholesaleTariffProvider(
        price_source=src,  # type: ignore[arg-type]
        region=region,
        daily_supply_c=daily_supply_c,
        provider_id=provider_id,
        name=name,
    )
    return p, src


def _price(value_mwh: float, *, region: str = "NSW1", attribution: str = "X"):
    return WholesalePrice(
        price_aud_per_mwh=value_mwh,
        interval_end_utc=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
        region=region,
        attribution=attribution,
    )


# ----------------------------------------------------------------------
# Provider basics
# ----------------------------------------------------------------------


class TestProviderBasics:
    def test_provider_implements_protocol(self):
        p, _ = _make_provider()
        assert isinstance(p, Provider)

    def test_provider_id_and_name_passed_through_constructor(self):
        p_oe, _ = _make_provider(
            provider_id="dwt_openelectricity",
            name="Dynamic Wholesale Tariff — OpenElectricity",
        )
        p_aemo, _ = _make_provider(
            provider_id="dwt_aemo_direct",
            name="Dynamic Wholesale Tariff — AEMO Direct",
        )
        assert p_oe.id == "dwt_openelectricity"
        assert p_oe.name == "Dynamic Wholesale Tariff — OpenElectricity"
        assert p_aemo.id == "dwt_aemo_direct"
        assert p_aemo.name == "Dynamic Wholesale Tariff — AEMO Direct"

    def test_set_current_rates_is_noop(self):
        p, _ = _make_provider()
        p.set_current_rates(99.9, 12.3)
        assert p.current_import_rate_c_kwh == 0.0
        assert p.current_export_rate_c_kwh == 0.0


# ----------------------------------------------------------------------
# Cost math (AC-3, AC-4)
# ----------------------------------------------------------------------


class TestCostMath:
    def test_update_with_no_price_accumulates_nothing(self):
        p, _ = _make_provider()
        t0 = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
        p.update(grid_power_w=2000, now_local=t0)
        p.update(grid_power_w=2000, now_local=t0 + timedelta(seconds=30))
        assert p.import_kwh_today == 0.0
        assert p.import_cost_today_c == 0.0

    def test_update_with_positive_price_accumulates(self):
        p, _ = _make_provider(daily_supply_c=110.0)
        t0 = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
        p.update(grid_power_w=2000, now_local=t0)  # seed last_tick
        p.set_live_price(_price(85.42))
        p.update(grid_power_w=2000, now_local=t0 + timedelta(seconds=30))
        # 2kW * 30s = 60 Ws → 2000/1000 * 30/3600 = 0.01667 kWh
        # 85.42 $/MWh = 8.542 c/kWh
        # cost = 0.01667 * 8.542 ≈ 0.1424 c
        assert p.import_kwh_today == pytest.approx(2.0 * 30 / 3600, rel=1e-6)
        assert p.import_cost_today_c == pytest.approx((2.0 * 30 / 3600) * (85.42 / 10), rel=1e-6)
        assert p.daily_fixed_charges_aud == pytest.approx(1.10)

    def test_update_with_negative_price_handles_export(self):
        """AC-4: negative wholesale → exporter PAYS (negative earnings)."""
        p, _ = _make_provider()
        t0 = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
        p.update(grid_power_w=-3000, now_local=t0)
        p.set_live_price(_price(-15.0))
        p.update(grid_power_w=-3000, now_local=t0 + timedelta(seconds=30))
        # 3kW export for 30s = 0.025 kWh; -15 $/MWh = -1.5 c/kWh
        # earnings = 0.025 * -1.5 = -0.0375 c (exporter PAYS).
        assert p.export_kwh_today == pytest.approx(3.0 * 30 / 3600, rel=1e-6)
        assert p.export_earnings_today_c < 0
        assert p.export_earnings_today_c == pytest.approx(
            (3.0 * 30 / 3600) * (-15.0 / 10), rel=1e-6
        )

    def test_daily_fixed_charges_constant(self):
        p, _ = _make_provider(daily_supply_c=110.0)
        assert p.daily_fixed_charges_aud == pytest.approx(1.10)

    def test_update_resets_daily_counters_on_midnight_rollover(self):
        p, _ = _make_provider()
        t0 = datetime(2026, 5, 21, 23, 59, 30, tzinfo=timezone.utc)
        p.set_live_price(_price(85.42))
        p.update(grid_power_w=2000, now_local=t0)
        p.update(grid_power_w=2000, now_local=t0 + timedelta(seconds=20))
        assert p.import_kwh_today > 0
        assert p.import_cost_today_c > 0

        t1 = t0 + timedelta(seconds=40)
        p.update(grid_power_w=2000, now_local=t1)
        # It resets daily counters, then accumulates only the rollover interval's energy:
        # 2kW * 20s = 0.01111 kWh
        assert p.import_kwh_today == pytest.approx(2.0 * 20 / 3600, rel=1e-6)
        assert p.import_cost_today_c == pytest.approx((2.0 * 20 / 3600) * (85.42 / 10), rel=1e-6)

    def test_reset_daily_zeros_accumulators_keeps_price(self):
        p, _ = _make_provider()
        t0 = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
        p.set_live_price(_price(85.42))
        p.update(grid_power_w=2000, now_local=t0)
        p.update(grid_power_w=2000, now_local=t0 + timedelta(seconds=30))
        assert p.import_kwh_today > 0
        p.reset_daily()
        assert p.import_kwh_today == 0
        assert p.import_cost_today_c == 0
        assert p.export_kwh_today == 0
        assert p.export_earnings_today_c == 0
        # Price survives midnight.
        assert p.last_price is not None


# ----------------------------------------------------------------------
# Persistence (AC-2, AC-2b)
# ----------------------------------------------------------------------


class TestPersistence:
    def test_to_dict_from_dict_roundtrip(self):
        p1, _ = _make_provider()
        t0 = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
        p1.set_live_price(_price(85.42))
        p1.update(grid_power_w=2000, now_local=t0)
        p1.update(grid_power_w=2000, now_local=t0 + timedelta(seconds=30))
        snapshot = p1.to_dict()

        p2, _ = _make_provider()
        p2.from_dict(snapshot, today=date(2026, 5, 21))
        assert p2.import_kwh_today == p1.import_kwh_today
        assert p2.import_cost_today_c == p1.import_cost_today_c
        assert p2.last_price is not None
        assert p2.last_price.price_aud_per_mwh == 85.42

    def test_from_dict_requires_today(self):
        p, _ = _make_provider()
        snapshot = p.to_dict()
        with pytest.raises(TypeError):
            p.from_dict(snapshot, today=None)  # type: ignore[arg-type]

    def test_from_dict_rejects_wrong_version(self):
        p, _ = _make_provider()
        snapshot = p.to_dict()
        snapshot["version"] = STATE_VERSION + 1
        with pytest.raises(ValueError, match="not supported"):
            p.from_dict(snapshot, today=date(2026, 5, 21))

    def test_from_dict_rejects_missing_version(self):
        p, _ = _make_provider()
        with pytest.raises(ValueError, match="not supported"):
            p.from_dict({}, today=date(2026, 5, 21))

    def test_from_dict_resets_daily_counters_on_cross_midnight_restart(self):
        """Codex P1-5: a restart that crosses midnight must NOT restore
        yesterday's daily counters as today's. The fix stores the date
        the counters apply to (``state_date``) and compares it against
        the supplied HA-tz ``today``.
        """
        p1, _ = _make_provider()
        t0 = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
        p1.set_live_price(_price(85.42))
        p1.update(grid_power_w=2000, now_local=t0)
        p1.update(grid_power_w=2000, now_local=t0 + timedelta(seconds=30))
        snapshot = p1.to_dict()
        # state_date was persisted (it's the date of _last_tick).
        assert snapshot["state_date"] == "2026-05-21"
        # Sanity — counters in the snapshot are non-zero.
        assert snapshot["import_kwh_today"] > 0

        # Restore "the next day" — counters MUST be zeroed.
        p2, _ = _make_provider()
        p2.from_dict(snapshot, today=date(2026, 5, 22))
        assert p2.import_kwh_today == 0.0
        assert p2.export_kwh_today == 0.0
        assert p2.import_cost_today_c == 0.0
        assert p2.export_earnings_today_c == 0.0
        # last_price survives so the new day starts with a known rate.
        assert p2.last_price is not None
        assert p2.last_price.price_aud_per_mwh == 85.42

    def test_from_dict_preserves_counters_on_same_day_restart(self):
        """The other side of the fix — a same-day restart MUST still
        restore counters (existing roundtrip contract).
        """
        p1, _ = _make_provider()
        t0 = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
        p1.set_live_price(_price(85.42))
        p1.update(grid_power_w=2000, now_local=t0)
        p1.update(grid_power_w=2000, now_local=t0 + timedelta(seconds=30))
        snapshot = p1.to_dict()
        original_import = snapshot["import_kwh_today"]
        assert original_import > 0

        p2, _ = _make_provider()
        p2.from_dict(snapshot, today=date(2026, 5, 21))
        assert p2.import_kwh_today == original_import

    def test_from_dict_resets_when_state_date_missing(self):
        """Pre-fix state snapshots (or any future serializer that
        forgets to set state_date) get treated as a cross-midnight
        restart — safe default = zero the counters rather than risk
        carrying yesterday's totals into today.
        """
        p1, _ = _make_provider()
        t0 = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
        p1.set_live_price(_price(85.42))
        p1.update(grid_power_w=2000, now_local=t0)
        p1.update(grid_power_w=2000, now_local=t0 + timedelta(seconds=30))
        snapshot = p1.to_dict()
        # Simulate a pre-fix snapshot — drop state_date.
        del snapshot["state_date"]

        p2, _ = _make_provider()
        p2.from_dict(snapshot, today=date(2026, 5, 21))
        assert p2.import_kwh_today == 0.0, "Missing state_date must reset counters (safe default)."

    def test_from_dict_resets_when_state_date_malformed(self):
        """Junk state_date string is the same case as missing — reset
        rather than restore, and emit a WARNING so operators can see
        why their dashboard zeroed.
        """
        p1, _ = _make_provider()
        t0 = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
        p1.set_live_price(_price(85.42))
        p1.update(grid_power_w=2000, now_local=t0)
        p1.update(grid_power_w=2000, now_local=t0 + timedelta(seconds=30))
        snapshot = p1.to_dict()
        snapshot["state_date"] = "not-an-iso-date"

        p2, _ = _make_provider()
        p2.from_dict(snapshot, today=date(2026, 5, 21))
        assert p2.import_kwh_today == 0.0


# ----------------------------------------------------------------------
# Extras / attribution (AC-5)
# ----------------------------------------------------------------------


class TestExtras:
    def test_extras_surface_attribution_region_price_age(self):
        p, _ = _make_provider(region="NSW1")
        attrib = "Wholesale price data: Open Electricity (Superpower Institute), CC BY-NC 4.0"
        p.set_live_price(_price(85.42, region="NSW1", attribution=attrib))
        extras = p.extras
        assert extras["attribution"] == attrib
        assert extras["region"] == "NSW1"
        assert extras["wholesale_price_aud_per_mwh"] == 85.42
        assert isinstance(extras["wholesale_price_age_seconds"], int)
        assert extras["wholesale_price_age_seconds"] >= 0

    def test_extras_handles_no_price(self):
        p, _ = _make_provider(region="QLD1")
        extras = p.extras
        assert extras["region"] == "QLD1"
        assert extras["wholesale_price_aud_per_mwh"] is None
        assert extras["attribution"] is None


# ----------------------------------------------------------------------
# set_live_price idempotency
# ----------------------------------------------------------------------


class TestSetLivePriceIdempotency:
    def test_same_region_and_interval_is_noop(self):
        p, _ = _make_provider()
        p_price = _price(85.42)
        p.set_live_price(p_price)
        p.set_live_price(p_price)  # same instance — no-op
        assert p.last_price is p_price


# ----------------------------------------------------------------------
# Package export
# ----------------------------------------------------------------------


class TestPackageExport:
    def test_providers_init_exports_dynamic_wholesale_tariff_provider(self):
        assert "DynamicWholesaleTariffProvider" in providers_all
        assert PackageExport is DynamicWholesaleTariffProvider


# ----------------------------------------------------------------------
# Config-flow option builders (AC-6, AC-10)
# ----------------------------------------------------------------------


class TestConfigFlowOptionBuilders:
    def test_dwt_retailer_options_oe_first_then_aemo(self):
        """AC-6: DWT entries lead the retailer picker, OE before AEMO."""
        from custom_components.pricehawk.config_flow import (
            _build_dwt_retailer_options,
        )

        opts = _build_dwt_retailer_options()
        assert len(opts) == 2
        assert opts[0]["value"] == "dwt_openelectricity"
        assert opts[1]["value"] == "dwt_aemo_direct"
        assert "OpenElectricity" in opts[0]["label"]
        assert "API key required" in opts[0]["label"]
        assert "AEMO Direct" in opts[1]["label"]
        assert "no key" in opts[1]["label"]

    def test_dwt_region_options_aemo_excludes_wem(self):
        """AC-10: AEMO Direct (include_wem=False) — NEM only, no WEM."""
        from custom_components.pricehawk.config_flow import (
            _build_dwt_region_options,
        )

        opts = _build_dwt_region_options(include_wem=False)
        values = {o["value"] for o in opts}
        assert "WEM" not in values
        assert {"NSW1", "QLD1", "SA1", "TAS1", "VIC1"} == values

    def test_dwt_region_options_oe_includes_wem(self):
        """AC-10: OE (include_wem=True) — NEM + WEM."""
        from custom_components.pricehawk.config_flow import (
            _build_dwt_region_options,
        )

        opts = _build_dwt_region_options(include_wem=True)
        values = {o["value"] for o in opts}
        assert "WEM" in values
        assert {"NSW1", "QLD1", "SA1", "TAS1", "VIC1", "WEM"} == values

    def test_dwt_region_options_carry_grid_network_badge(self):
        """AC-10 audit S5: region labels include grid-network badge."""
        from custom_components.pricehawk.config_flow import (
            _build_dwt_region_options,
        )

        opts = _build_dwt_region_options(include_wem=True)
        labels = {o["value"]: o["label"] for o in opts}
        assert "NEM" in labels["NSW1"]
        assert "Western Australia" in labels["WEM"]


# ----------------------------------------------------------------------
# Config-flow routing — source-level (AC-6, AC-7, AC-8)
#
# The ConfigFlow class itself cannot be instantiated under the conftest
# HA stubs (its base class is a MagicMock — class creation produces a
# MagicMock instance, not a real class). The step routing dispatch is
# therefore covered via source-string asserts on the production module;
# end-to-end is manual UAT per the plan's verification section.
# ----------------------------------------------------------------------


class TestConfigFlowRoutingSource:
    @staticmethod
    def _source() -> str:
        return (
            Path(__file__).resolve().parents[1]
            / "custom_components"
            / "pricehawk"
            / "config_flow.py"
        ).read_text()

    def test_cdr_retailer_step_prepends_dwt_options(self):
        src = self._source()
        # Selector options must call _build_dwt_retailer_options() FIRST
        # then concatenate the CDR catalogue list.
        assert ("_build_dwt_retailer_options() + _build_cdr_retailer_options") in src

    def test_cdr_retailer_dispatch_to_dwt_credentials(self):
        src = self._source()
        assert "PROVIDER_DWT_OE" in src
        assert "async_step_dwt_credentials" in src
        # The dispatch arm exists in async_step_cdr_retailer.
        assert (
            "if choice == PROVIDER_DWT_OE:" in src
            and "return await self.async_step_dwt_credentials()" in src
        )

    def test_cdr_retailer_dispatch_to_dwt_aemo_setup(self):
        src = self._source()
        assert "PROVIDER_DWT_AEMO" in src
        assert "async_step_dwt_aemo_setup" in src
        assert (
            "if choice == PROVIDER_DWT_AEMO:" in src
            and "return await self.async_step_dwt_aemo_setup()" in src
        )

    def test_dwt_credentials_step_sets_invalid_api_key_on_authfailed(self):
        src = self._source()
        # AC-7 path: ConfigEntryAuthFailed → errors[CONF_DWT_OE_API_KEY] = "invalid_api_key"
        assert ('errors[CONF_DWT_OE_API_KEY] = "invalid_api_key"') in src

    def test_dwt_credentials_step_stores_oe_flags(self):
        src = self._source()
        # AC-7 success path stores the four DWT-OE keys + CONF_CURRENT_PROVIDER.
        for key in (
            "self._data[CONF_DWT_OE_ENABLED] = True",
            "self._data[CONF_DWT_OE_API_KEY]",
            "self._data[CONF_DWT_REGION]",
            "self._data[CONF_DWT_OE_DAILY_SUPPLY]",
            "self._data[CONF_CURRENT_PROVIDER] = PROVIDER_DWT_OE",
        ):
            assert key in src, f"missing in config_flow.py: {key}"

    def test_dwt_credentials_unique_id_pattern(self):
        src = self._source()
        # AC-10d non-collision: unique_id baked from region.
        assert 'f"dwt_openelectricity_{region}"' in src

    def test_dwt_aemo_setup_step_stores_aemo_flags_only(self):
        src = self._source()
        # AC-8 success path stores the three DWT-AEMO keys.
        for key in (
            "self._data[CONF_DWT_AEMO_ENABLED] = True",
            "self._data[CONF_DWT_REGION]",
            "self._data[CONF_DWT_AEMO_DAILY_SUPPLY]",
            "self._data[CONF_CURRENT_PROVIDER] = PROVIDER_DWT_AEMO",
        ):
            assert key in src, f"missing in config_flow.py: {key}"

    def test_dwt_aemo_setup_unique_id_pattern(self):
        src = self._source()
        assert 'f"dwt_aemo_direct_{region}"' in src


# ----------------------------------------------------------------------
# strings.json ↔ translations/en.json byte-identical
# ----------------------------------------------------------------------


class TestStringsTranslationsByteIdentical:
    def test_strings_translations_byte_identical(self):
        repo = Path(__file__).resolve().parents[1]
        a = repo / "custom_components" / "pricehawk" / "strings.json"
        b = repo / "custom_components" / "pricehawk" / "translations" / "en.json"
        assert a.read_bytes() == b.read_bytes(), (
            "strings.json and translations/en.json drifted — they must stay byte-identical."
        )
