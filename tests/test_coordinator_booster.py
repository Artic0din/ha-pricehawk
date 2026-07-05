import asyncio
import sys
from datetime import datetime, timedelta, timezone, date
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, ANY
from aioresponses import aioresponses
import aiohttp

# Patch the mock dt module's UTC attribute to be a real timezone object
if "homeassistant.util.dt" in sys.modules:
    sys.modules["homeassistant.util.dt"].UTC = timezone.utc

from homeassistant.exceptions import ConfigEntryNotReady, ConfigEntryAuthFailed

from custom_components.pricehawk.storage import STORAGE_VERSION
from custom_components.pricehawk.coordinator import (
    PRICING_MODE_OFF,
    PRICING_MODE_LIVE_API,
    PRICING_MODE_STATIC_PRD,
    PROVIDER_DWT_OE,
    PROVIDER_DWT_AEMO,
    PROVIDER_AMBER,
    PROVIDER_LOCALVOLTS,
)
from custom_components.pricehawk.const import (
    CONF_AMBER_PRICING_MODE,
    CONF_AMBER_STATIC_PLAN,
    CONF_FLOW_POWER_PRICING_MODE,
    CONF_LOCALVOLTS_PRICING_MODE,
    CONF_LOCALVOLTS_STATIC_PLAN,
    CONF_LOCALVOLTS_API_KEY,
    CONF_LOCALVOLTS_PARTNER_ID,
    CONF_LOCALVOLTS_NMI,
    CONF_DWT_OE_ENABLED,
    CONF_DWT_AEMO_ENABLED,
    CONF_DWT_OE_API_KEY,
    CONF_DWT_REGION,
    CONF_CURRENT_PROVIDER,
)
from tests.test_coordinator import _make_bare_coordinator


def _make_full_bare_coordinator():
    coord = _make_bare_coordinator()
    coord._wholesale_c = None
    coord._wholesale_settlement = ""
    coord._last_aemo_poll = 0.0
    coord._forecast_peak_c = None
    coord._forecast_peak_at = ""
    coord._forecast_dip_c = None
    coord._forecast_dip_at = ""
    coord._forecast_avg_c = None
    coord._forecast_intervals = []
    coord._localvolts_import_c = None
    coord._localvolts_export_c = None
    coord._last_localvolts_poll = 0.0
    coord._api_key = "test-api-key"
    coord._site_id = "test-site-id"
    coord._amber_import_c = None
    coord._amber_export_c = None
    coord._last_amber_poll = 0.0
    coord._price_history = []
    coord._saving_month_aud = 0.0
    coord._last_month = 6
    coord._last_date = 4
    coord._daily_wins = {}
    coord._daily_cost_history = []
    coord._last_explanation = None
    coord._today_schedule = []
    coord._store = MagicMock()
    coord._persist_unsub = None
    coord._cheap_ranked_alternatives = []
    coord._ranking_last_run_at = None
    coord._ranking_plan_cache = {}
    coord._ranking_cache_date = None
    coord._ranking_unsub = None
    coord._ranking_lock = asyncio.Lock()
    coord._backfill_status = "idle"
    coord._backfill_last_run_at = None
    coord._backfill_days_loaded = 0
    coord._backfill_plans_replayed = 0
    coord._backfill_error = None
    coord._reauth_provider_id = None
    coord._grid_sensor_missing_ticks = 0
    coord._active_repair_ids = set()
    coord._external_stats_backfill_done = False
    coord._external_stats_cumulative = {}
    return coord


@pytest.mark.asyncio
async def test_build_dwt_provider_validation_failures():
    coord = _make_full_bare_coordinator()

    # 1. OE selected as current, but not enabled
    options = {CONF_CURRENT_PROVIDER: PROVIDER_DWT_OE}
    data = {CONF_DWT_OE_ENABLED: False, CONF_DWT_OE_API_KEY: "key"}
    with pytest.raises(ConfigEntryNotReady) as exc:
        coord._build_dwt_provider(options, data)
    assert "DWT-OpenElectricity selected" in str(exc.value)

    # 2. OE selected, but no API key
    options = {CONF_CURRENT_PROVIDER: PROVIDER_DWT_OE}
    data = {CONF_DWT_OE_ENABLED: True, CONF_DWT_OE_API_KEY: ""}
    with pytest.raises(ConfigEntryNotReady) as exc:
        coord._build_dwt_provider(options, data)
    assert "missing API key" in str(exc.value)

    # 3. AEMO selected, but not enabled
    options = {CONF_CURRENT_PROVIDER: PROVIDER_DWT_AEMO}
    data = {CONF_DWT_AEMO_ENABLED: False}
    with pytest.raises(ConfigEntryNotReady) as exc:
        coord._build_dwt_provider(options, data)
    assert "DWT-AEMO selected" in str(exc.value)


@pytest.mark.asyncio
async def test_build_dwt_provider_success():
    coord = _make_full_bare_coordinator()

    # 1. OE success
    options = {CONF_CURRENT_PROVIDER: PROVIDER_DWT_OE}
    data = {CONF_DWT_OE_ENABLED: True, CONF_DWT_OE_API_KEY: "key", CONF_DWT_REGION: "QLD1"}

    # Patch OpenElectricityPriceSource inside providers.openelectricity
    with patch("custom_components.pricehawk.providers.openelectricity.OpenElectricityPriceSource"):
        prov = coord._build_dwt_provider(options, data)
        assert prov is not None
        assert prov.region == "QLD1"
        assert prov.id == PROVIDER_DWT_OE

    # 2. AEMO success
    options = {CONF_CURRENT_PROVIDER: PROVIDER_DWT_AEMO}
    data = {CONF_DWT_AEMO_ENABLED: True, CONF_DWT_REGION: "VIC1"}

    prov = coord._build_dwt_provider(options, data)
    assert prov is not None
    assert prov.region == "VIC1"
    assert prov.id == PROVIDER_DWT_AEMO


@pytest.mark.asyncio
async def test_refresh_dwt_price_staleness_guard():
    coord = _make_full_bare_coordinator()

    # Setup mock provider and price with fresh interval_end_utc
    mock_provider = MagicMock()
    mock_price = MagicMock()
    mock_price.interval_end_utc = datetime.now(tz=timezone.utc) - timedelta(seconds=10)
    mock_provider.last_price = mock_price

    coord._dwt_provider = mock_provider

    # Should return early without calling fetch_current_price
    await coord._refresh_dwt_price()
    assert not mock_provider.price_source.fetch_current_price.called


@pytest.mark.asyncio
async def test_refresh_dwt_price_failures():
    coord = _make_full_bare_coordinator()

    mock_provider = MagicMock()
    mock_provider.last_price = None
    mock_provider.region = "NSW1"
    coord._dwt_provider = mock_provider

    # 1. Auth failure
    mock_provider.price_source.fetch_current_price = AsyncMock(
        side_effect=ConfigEntryAuthFailed("auth_fail")
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._refresh_dwt_price()
    assert coord._reauth_provider_id == PROVIDER_DWT_OE

    # 2. ConfigEntryNotReady
    mock_provider.price_source.fetch_current_price = AsyncMock(
        side_effect=ConfigEntryNotReady("not_ready")
    )
    with pytest.raises(ConfigEntryNotReady):
        await coord._refresh_dwt_price()

    # 3. Generic Exception, falls back to last good
    mock_provider.price_source.fetch_current_price = AsyncMock(
        side_effect=Exception("network_fail")
    )
    mock_provider.price_source.last_good.return_value = {"price": 10.0}
    await coord._refresh_dwt_price()
    mock_provider.set_live_price.assert_called_with({"price": 10.0})


@pytest.mark.asyncio
async def test_fetch_amber_with_retry_all_paths():
    coord = _make_full_bare_coordinator()
    coord._site_id = "site1"
    coord._api_key = "key"

    url = "https://api.amber.com.au/v1/sites/site1/prices/current?next=48&previous=0"

    async with aiohttp.ClientSession() as session:
        with patch(
            "custom_components.pricehawk.coordinator.async_get_clientsession", return_value=session
        ):
            # 1. 200 OK success
            with aioresponses() as m:
                m.get(
                    url,
                    status=200,
                    payload=[{"channelType": "general", "type": "CurrentInterval", "perKwh": 25.0}],
                )
                res = await coord._fetch_amber_with_retry()
                assert res == [
                    {"channelType": "general", "type": "CurrentInterval", "perKwh": 25.0}
                ]

            # 2. 401 Unauthorized -> raises ConfigEntryAuthFailed
            with aioresponses() as m:
                m.get(url, status=401)
                with pytest.raises(ConfigEntryAuthFailed):
                    await coord._fetch_amber_with_retry()
                assert coord._reauth_provider_id == PROVIDER_AMBER

            # 3. 429 Too Many Requests -> sleep and retry
            with aioresponses() as m:
                m.get(url, status=429, headers={"Retry-After": "1"})
                m.get(url, status=200, payload=[{"type": "CurrentInterval"}])
                with patch("asyncio.sleep") as mock_sleep:
                    res = await coord._fetch_amber_with_retry()
                    assert res == [{"type": "CurrentInterval"}]
                    mock_sleep.assert_called_with(1)

            # 4. ClientError/Timeout Exception -> retry and fail
            with aioresponses() as m:
                m.get(url, exception=aiohttp.ClientError("connection refused"))
                m.get(url, exception=aiohttp.ClientError("connection refused"))
                m.get(url, exception=aiohttp.ClientError("connection refused"))
                with patch("asyncio.sleep") as mock_sleep:
                    res = await coord._fetch_amber_with_retry()
                    assert res is None


@pytest.mark.asyncio
async def test_poll_amber_prices():
    coord = _make_full_bare_coordinator()

    payload = [
        {"channelType": "general", "type": "CurrentInterval", "perKwh": 20.0},
        {"channelType": "feedIn", "type": "CurrentInterval", "perKwh": -5.0},
        {
            "channelType": "general",
            "type": "ForecastInterval",
            "startTime": "2026-06-04T16:00:00Z",
            "perKwh": 18.0,
        },
    ]

    coord._fetch_amber_with_retry = AsyncMock(return_value=payload)
    coord._update_amber_forecast = MagicMock()

    await coord._poll_amber_prices()
    assert coord._amber_import_c == 20.0
    assert coord._amber_export_c == 5.0
    coord._update_amber_forecast.assert_called_once()


@pytest.mark.asyncio
async def test_apply_options_to_state_amber_static_plan_validation():
    coord = _make_full_bare_coordinator()

    # Amber pricing mode static_prd but no static plan, strict=True -> raises ConfigEntryNotReady
    options = {
        CONF_AMBER_PRICING_MODE: PRICING_MODE_STATIC_PRD,
        CONF_AMBER_STATIC_PLAN: None,
        "cdr_plan": {"data": {"planId": "some_cdr_plan"}},
    }
    data = {}
    with pytest.raises(ConfigEntryNotReady):
        coord._apply_options_to_state(options, data, strict=True)

    # strict=False -> logs warning and falls back to OFF
    coord._apply_options_to_state(options, data, strict=False)
    assert coord._amber_mode == PRICING_MODE_OFF


@pytest.mark.asyncio
async def test_apply_options_to_state_localvolts_static_plan_validation():
    coord = _make_full_bare_coordinator()

    # LocalVolts pricing mode static_prd but no static plan, strict=True -> raises ConfigEntryNotReady
    options = {
        CONF_LOCALVOLTS_PRICING_MODE: PRICING_MODE_STATIC_PRD,
        CONF_LOCALVOLTS_STATIC_PLAN: None,
        "cdr_plan": {"data": {"planId": "some_cdr_plan"}},
    }
    data = {}
    with pytest.raises(ConfigEntryNotReady):
        coord._apply_options_to_state(options, data, strict=True)

    # strict=False -> logs warning and falls back to OFF
    coord._apply_options_to_state(options, data, strict=False)
    assert coord._localvolts_mode == PRICING_MODE_OFF


@pytest.mark.asyncio
async def test_apply_options_to_state_flow_power_static_prd_fallback():
    coord = _make_full_bare_coordinator()

    options = {
        CONF_FLOW_POWER_PRICING_MODE: PRICING_MODE_STATIC_PRD,
        "cdr_plan": {"data": {"planId": "some_cdr_plan"}},
    }
    data = {}
    coord._apply_options_to_state(options, data, strict=False)
    assert coord._flow_power_mode == PRICING_MODE_LIVE_API


@pytest.mark.asyncio
async def test_build_data_dict_complete():
    coord = _make_full_bare_coordinator()
    coord._site_id = "site1"
    coord._api_key = "key"

    mock_current = MagicMock()
    mock_current.name = "GloBird ZeroHero"
    mock_current.current_import_rate_c_kwh = 35.0
    mock_current.current_export_rate_c_kwh = 10.0
    mock_current.net_daily_cost_aud = 2.50
    mock_current.import_cost_today_c = 150.0
    mock_current.export_earnings_today_c = 40.0
    mock_current.import_kwh_today = 5.0
    mock_current.export_kwh_today = 4.0
    mock_current.extras = {"zerohero_status": "active", "super_export_kwh": 1.2}
    coord._current_plan_provider = mock_current

    mock_amber = MagicMock()
    mock_amber.net_daily_cost_aud = 2.10
    mock_amber.daily_fixed_charges_aud = 0.80
    mock_amber.import_cost_today_c = 130.0
    mock_amber.export_earnings_today_c = 30.0
    mock_amber.import_kwh_today = 5.0
    mock_amber.export_kwh_today = 4.0
    coord._amber = mock_amber

    coord._amber_import_c = 25.0
    coord._amber_export_c = 8.0
    coord._wholesale_c = 12.0
    coord._saving_month_aud = 10.50
    coord._forecast_peak_c = 40.0
    coord._forecast_peak_at = "2026-06-04T18:00:00Z"
    coord._forecast_dip_c = 10.0
    coord._forecast_dip_at = "2026-06-04T12:00:00Z"
    coord._forecast_avg_c = 22.0
    coord._forecast_intervals = [{"start_time": "2026-06-04T12:00:00Z", "c_kwh": 10.0}]
    coord._daily_wins = {"amber": 5, "globird": 2}
    coord._daily_cost_history = [{"date": "2026-06-03", "amber": 2.10}]
    coord._ranking_last_run_at = datetime(2026, 6, 4, 0, 30, tzinfo=timezone.utc)
    coord._price_history = []
    coord._today_schedule = []

    coord._cheap_ranked_alternatives = [
        {
            "planId": "plan1",
            "displayName": "Plan One",
            "brand": "Origin",
            "customerType": "RESIDENTIAL",
            "electricityContract": {
                "tariffPeriod": [
                    {
                        "dailySupplyCharge": 1.15,
                        "timeOfUseRates": [{"rates": [{"unitPrice": 0.35}]}],
                    }
                ]
            },
        }
    ]

    coord._providers = {"amber": mock_amber, "globird": mock_current}
    coord.config_entry.options = {"incentives": {"zerohero_credit": True}}

    dt_util = sys.modules["homeassistant.util.dt"]
    dt_util.now = MagicMock(return_value=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc))

    data = coord._build_data_dict()
    assert data["current_plan_import_rate"] == 35.0
    assert data["saving_today"] == pytest.approx(-0.40)
    assert len(data["ranked_alternatives"]) == 1
    assert data["ranked_alternatives"][0]["plan_id"] == "plan1"
    assert data["metrics_won"] == "2/3"


@pytest.mark.asyncio
async def test_build_data_dict_no_amber():
    coord = _make_full_bare_coordinator()
    coord._amber = None
    coord._amber_import_c = None
    coord._amber_export_c = None

    mock_current = MagicMock()
    mock_current.name = "GloBird ZeroHero"
    mock_current.current_import_rate_c_kwh = 35.0
    mock_current.current_export_rate_c_kwh = 10.0
    mock_current.net_daily_cost_aud = 2.50
    mock_current.import_cost_today_c = 150.0
    mock_current.export_earnings_today_c = 40.0
    mock_current.import_kwh_today = 5.0
    mock_current.export_kwh_today = 4.0
    mock_current.extras = {}
    coord._current_plan_provider = mock_current

    coord._providers = {"globird": mock_current}
    coord._price_history = []
    coord._today_schedule = []
    coord._cheap_ranked_alternatives = []
    coord._ranking_last_run_at = None

    dt_util = sys.modules["homeassistant.util.dt"]
    dt_util.now = MagicMock(return_value=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc))

    data = coord._build_data_dict()
    assert data["amber_import_rate"] is None
    assert data["saving_today"] is None
    assert data["metrics_won"] is None


@pytest.mark.asyncio
async def test_async_restore_state_none():
    coord = _make_full_bare_coordinator()
    coord._amber = MagicMock()
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=None)
    coord._replay_amber_today_from_api = AsyncMock()

    await coord.async_restore_state()
    coord._replay_amber_today_from_api.assert_called_once()


@pytest.mark.asyncio
async def test_async_restore_state_invalid_version():
    coord = _make_full_bare_coordinator()
    coord._amber = MagicMock()
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value={"_storage_version": 999})
    coord._replay_amber_today_from_api = AsyncMock()

    await coord.async_restore_state()
    coord._replay_amber_today_from_api.assert_called_once()


@pytest.mark.asyncio
async def test_async_restore_state_success():
    coord = _make_full_bare_coordinator()

    stored_data = {
        "_storage_version": STORAGE_VERSION,
        "globird": {"some": "data"},
        "amber": {"amber": "data"},
        "flow_power": {"fp": "data"},
        "localvolts": {"lv": "data"},
        "named": {"named": "data"},
        "amber_import_c": 22.0,
        "amber_export_c": 6.0,
        "wholesale_c": 11.0,
        "localvolts_import_c": 20.0,
        "localvolts_export_c": 5.0,
        "saving_month_aud": 5.0,
        "last_month": 6,
        "last_date": 4,
        "price_history": [{"t": "2026-06-04T12:00:00Z", "ai": 22.0}],
        "daily_wins": {"amber": 1},
        "daily_cost_history": [{"date": "2026-06-03", "amber": 2.10}],
        "today_schedule": [],
        "last_explanation": "some explanation",
    }

    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=stored_data)
    coord._replay_amber_today_from_api = AsyncMock()

    coord._current_plan_provider = MagicMock()
    coord._amber = MagicMock()
    coord._flow_power = MagicMock()
    coord._localvolts = MagicMock()
    coord._named_comparator = MagicMock()

    dt_util = sys.modules["homeassistant.util.dt"]
    dt_util.now = MagicMock(return_value=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc))

    await coord.async_restore_state()

    coord._current_plan_provider.from_dict.assert_called_once_with(
        {"some": "data"}, today=date(2026, 6, 4)
    )
    coord._amber.from_dict.assert_called_once()
    coord._flow_power.from_dict.assert_called_once()
    coord._localvolts.from_dict.assert_called_once()
    coord._named_comparator.from_dict.assert_called_once()

    assert coord._amber_import_c == 22.0
    assert coord._amber_export_c == 6.0
    assert coord._wholesale_c == 11.0
    assert coord._localvolts_import_c == 20.0
    assert coord._localvolts_export_c == 5.0
    assert coord._saving_month_aud == 5.0
    assert coord._last_month == 6
    assert coord._last_date == 4
    assert coord._price_history == [{"t": "2026-06-04T12:00:00Z", "ai": 22.0}]
    assert coord._daily_wins == {"amber": 1}
    assert coord._last_explanation == "some explanation"
    coord._replay_amber_today_from_api.assert_not_called()


@pytest.mark.asyncio
async def test_replay_amber_today_from_api_skipped():
    coord = _make_full_bare_coordinator()

    # 1. Missing api_key/site_id/grid sensor
    coord._api_key = None
    await coord._replay_amber_today_from_api()

    # 2. Amber is None
    coord._api_key = "key"
    coord._site_id = "site"
    coord._grid_power_entity = "sensor.grid"
    coord._amber = None
    await coord._replay_amber_today_from_api()


@pytest.mark.asyncio
async def test_replay_amber_today_from_api_recorder_missing():
    coord = _make_full_bare_coordinator()
    coord._api_key = "key"
    coord._site_id = "site"
    coord._grid_power_entity = "sensor.grid"
    coord._amber = MagicMock()

    with patch.dict(sys.modules, {"homeassistant.components.recorder": None}):
        await coord._replay_amber_today_from_api()


@pytest.mark.asyncio
async def test_async_setup_stats():
    coord = _make_full_bare_coordinator()
    coord._daily_cost_history = [{"date": "2026-06-03", "amber": 2.10}]

    with patch(
        "custom_components.pricehawk.coordinator.async_backfill_external_statistics", return_value=1
    ) as mock_backfill:
        await coord.async_setup_stats()
        mock_backfill.assert_called_once_with(
            coord.hass, coord._entry.entry_id, coord._daily_cost_history
        )
        assert coord._external_stats_backfill_done is True
        assert coord._external_stats_cumulative["amber"] == 2.10

        mock_backfill.reset_mock()
        await coord.async_setup_stats()
        mock_backfill.assert_not_called()


@pytest.mark.asyncio
async def test_async_setup_stats_exception():
    coord = _make_full_bare_coordinator()
    coord._daily_cost_history = [{"date": "2026-06-03", "amber": 2.10}]

    with patch(
        "custom_components.pricehawk.coordinator.async_backfill_external_statistics",
        side_effect=Exception("backfill error"),
    ):
        await coord.async_setup_stats()
        assert coord._external_stats_backfill_done is True
        assert coord._external_stats_cumulative["amber"] == 2.10


@pytest.mark.asyncio
async def test_check_repairs():
    coord = _make_full_bare_coordinator()
    coord._grid_power_entity = "sensor.grid"
    coord._ranking_last_run_at = datetime.now(tz=timezone.utc)

    with (
        patch("custom_components.pricehawk.coordinator.ir.async_create_issue") as mock_create,
        patch("custom_components.pricehawk.coordinator.ir.async_delete_issue") as mock_delete,
    ):
        for _ in range(9):
            coord._check_repairs(None, datetime.now(tz=timezone.utc))
            mock_create.assert_not_called()

        coord._check_repairs(None, datetime.now(tz=timezone.utc))
        mock_create.assert_any_call(
            coord.hass,
            "pricehawk",
            f"{coord._entry.entry_id}_grid_sensor_unavailable",
            is_fixable=False,
            severity=ANY,
            translation_key="grid_sensor_unavailable",
            translation_placeholders={"entity_id": "sensor.grid"},
        )

        mock_create.reset_mock()
        coord._check_repairs(2000.0, datetime.now(tz=timezone.utc))
        mock_delete.assert_any_call(
            coord.hass, "pricehawk", f"{coord._entry.entry_id}_grid_sensor_unavailable"
        )

        # Now trigger stale ranking
        coord._ranking_last_run_at = datetime.now(tz=timezone.utc) - timedelta(hours=37)
        mock_create.reset_mock()
        coord._check_repairs(2000.0, datetime.now(tz=timezone.utc))
        mock_create.assert_any_call(
            coord.hass,
            "pricehawk",
            f"{coord._entry.entry_id}_ranking_stale",
            is_fixable=False,
            severity=ANY,
            translation_key="ranking_stale",
            translation_placeholders=ANY,
        )


@pytest.mark.asyncio
async def test_read_grid_power():
    coord = _make_full_bare_coordinator()

    coord._grid_power_entity = None
    assert coord._read_grid_power() is None

    coord._grid_power_entity = "sensor.grid"
    coord.hass.states.get = MagicMock(return_value=None)
    assert coord._read_grid_power() is None

    mock_state = MagicMock()
    mock_state.state = "unavailable"
    coord.hass.states.get = MagicMock(return_value=mock_state)
    assert coord._read_grid_power() is None

    mock_state.state = "1500"
    mock_state.attributes = {"unit_of_measurement": "W"}
    assert coord._read_grid_power() == 1500.0

    mock_state.state = "1.5"
    mock_state.attributes = {"unit_of_measurement": "kW"}
    assert coord._read_grid_power() == 1500.0

    mock_state.state = "abc"
    assert coord._read_grid_power() is None


@pytest.mark.asyncio
async def test_schedule_cancel_persist():
    coord = _make_full_bare_coordinator()

    mock_unsub = MagicMock()
    coord._persist_unsub = mock_unsub
    coord.cancel_persist()
    mock_unsub.assert_called_once()
    assert coord._persist_unsub is None

    with patch("custom_components.pricehawk.coordinator.async_call_later") as mock_call_later:
        coord.schedule_persist()
        mock_call_later.assert_called_once()


@pytest.mark.asyncio
async def test_schedule_cancel_ranking():
    coord = _make_full_bare_coordinator()

    mock_unsub = MagicMock()
    coord._ranking_unsub = mock_unsub
    coord.cancel_ranking()
    mock_unsub.assert_called_once()
    assert coord._ranking_unsub is None

    with patch("custom_components.pricehawk.coordinator.async_track_time_change") as mock_track:
        coord.schedule_daily_ranking()
        mock_track.assert_called_once()


@pytest.mark.asyncio
async def test_async_run_ranking_job():
    coord = _make_full_bare_coordinator()

    mock_results = [{"planId": "plan1"}]
    with (
        patch("custom_components.pricehawk.coordinator.run_ranking_job", return_value=mock_results),
        patch("custom_components.pricehawk.coordinator.async_get_clientsession"),
    ):
        res = await coord.async_run_ranking_job()
        assert res == mock_results
        assert coord._cheap_ranked_alternatives == mock_results
        assert coord._ranking_last_run_at is not None

    with (
        patch(
            "custom_components.pricehawk.coordinator.run_ranking_job",
            side_effect=Exception("ranking pipeline fail"),
        ),
        patch("custom_components.pricehawk.coordinator.async_get_clientsession"),
    ):
        res = await coord.async_run_ranking_job()
        assert res == mock_results


@pytest.mark.asyncio
async def test_async_run_backfill():
    coord = _make_full_bare_coordinator()

    coord._build_backfill_plan_set = MagicMock(return_value={"plan1": {}})
    coord._daily_cost_history = []

    with (
        patch(
            "custom_components.pricehawk.backfill.backfill_daily_cost_history",
            return_value=[{"date": "2026-06-03"}],
        ),
        patch.object(coord, "async_persist_state", new_callable=AsyncMock) as mock_persist,
    ):
        res = await coord.async_run_backfill(days_back=5)
        assert res == 1
        assert coord._daily_cost_history == [{"date": "2026-06-03"}]
        assert coord._backfill_status == "complete"
        assert coord._backfill_last_run_at is not None
        mock_persist.assert_called_once()

    coord._backfill_status = "running"
    res = await coord.async_run_backfill()
    assert res == 0

    coord._backfill_status = "idle"
    with patch(
        "custom_components.pricehawk.backfill.backfill_daily_cost_history",
        side_effect=Exception("backfill failed"),
    ):
        res = await coord.async_run_backfill()
        assert res == 0
        assert coord._backfill_status == "failed"
        assert coord._backfill_error == "backfill failed"


@pytest.mark.asyncio
async def test_rebuild_engine():
    coord = _make_full_bare_coordinator()
    coord._apply_options_to_state = MagicMock()

    coord.rebuild_engine({"option": "value"})
    coord._apply_options_to_state.assert_called_once_with(
        {"option": "value"}, coord._entry.data, strict=False
    )


@pytest.mark.asyncio
async def test_async_update_data_no_rollover():
    coord = _make_full_bare_coordinator()
    coord._last_amber_poll = 100.0
    coord._amber_mode = PRICING_MODE_OFF
    coord._flow_power = None
    coord._localvolts = None
    coord._dwt_provider = None

    dt_util = sys.modules["homeassistant.util.dt"]
    mock_now = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.now = MagicMock(return_value=mock_now)

    coord._last_month = 6
    coord._last_date = 4

    coord._read_grid_power = MagicMock(return_value=1500.0)

    mock_provider = MagicMock()
    mock_provider.name = "GloBird"
    mock_provider.net_daily_cost_aud = 2.50
    mock_provider.import_cost_today_c = 150.0
    mock_provider.export_earnings_today_c = 0.0
    mock_provider.import_kwh_today = 5.0
    mock_provider.export_kwh_today = 0.0
    mock_provider.extras = {"super_export_kwh": 0.0, "zerohero_status": "inactive"}
    coord._current_plan_provider = mock_provider
    coord._providers = {"globird": mock_provider}

    coord.async_persist_state = AsyncMock()
    coord._check_repairs = MagicMock()
    coord._build_data_dict = MagicMock(return_value={"success": True})

    res = await coord._async_update_data()
    assert res == {"success": True}
    mock_provider.update.assert_called_once_with(1500.0, mock_now)
    coord._check_repairs.assert_called_once_with(1500.0, mock_now)


@pytest.mark.asyncio
async def test_async_update_data_monthly_reset():
    coord = _make_full_bare_coordinator()
    coord._last_amber_poll = 100.0
    coord._amber_mode = PRICING_MODE_OFF
    coord._flow_power = None
    coord._localvolts = None
    coord._dwt_provider = None

    dt_util = sys.modules["homeassistant.util.dt"]
    mock_now = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.now = MagicMock(return_value=mock_now)

    coord._last_month = 6
    coord._last_date = 4
    coord._saving_month_aud = 15.0
    coord._daily_wins = {"globird": 1}

    coord._read_grid_power = MagicMock(return_value=1500.0)

    mock_provider = MagicMock()
    mock_provider.name = "GloBird"
    mock_provider.net_daily_cost_aud = 2.50
    mock_provider.import_cost_today_c = 150.0
    mock_provider.export_earnings_today_c = 0.0
    mock_provider.import_kwh_today = 5.0
    mock_provider.export_kwh_today = 0.0
    mock_provider.extras = {"super_export_kwh": 0.0, "zerohero_status": "inactive"}
    coord._current_plan_provider = mock_provider
    coord._providers = {"globird": mock_provider}

    coord.async_persist_state = AsyncMock()
    coord._check_repairs = MagicMock()
    coord._build_data_dict = MagicMock(return_value={"success": True})

    await coord._async_update_data()

    assert coord._saving_month_aud == 0.0
    assert coord._daily_wins == {"globird": 0}
    assert coord._last_month == 7


@pytest.mark.asyncio
async def test_async_update_data_daily_rollover():
    coord = _make_full_bare_coordinator()
    coord._last_amber_poll = 100.0
    coord._amber_mode = PRICING_MODE_OFF
    coord._flow_power = None
    coord._localvolts = None
    coord._dwt_provider = None

    dt_util = sys.modules["homeassistant.util.dt"]
    mock_now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.now = MagicMock(return_value=mock_now)

    coord._last_month = 6
    coord._last_date = 4
    coord._saving_month_aud = 10.0

    coord._read_grid_power = MagicMock(return_value=1500.0)

    mock_current = MagicMock()
    mock_current.name = "GloBird"
    mock_current.net_daily_cost_aud = 5.0
    mock_current.import_cost_today_c = 150.0
    mock_current.export_earnings_today_c = 0.0
    mock_current.import_kwh_today = 5.0
    mock_current.export_kwh_today = 0.0
    mock_current.current_import_rate_c_kwh = 35.0
    mock_current.current_export_rate_c_kwh = 10.0
    mock_current.extras = {"super_export_kwh": 0.0, "zerohero_status": "inactive"}

    mock_amber = MagicMock()
    mock_amber.name = "Amber"
    mock_amber.net_daily_cost_aud = 3.0
    mock_amber.import_cost_today_c = 130.0
    mock_amber.export_earnings_today_c = 0.0
    mock_amber.import_kwh_today = 5.0
    mock_amber.export_kwh_today = 0.0
    mock_amber.current_import_rate_c_kwh = 25.0
    mock_amber.current_export_rate_c_kwh = 8.0
    mock_amber.extras = {"super_export_kwh": 0.0, "zerohero_status": "inactive"}

    coord._current_plan_provider = mock_current
    coord._amber = mock_amber
    coord._providers = {"globird": mock_current, "amber": mock_amber}
    coord._daily_wins = {"globird": 0, "amber": 0}

    coord.async_persist_state = AsyncMock()
    coord._check_repairs = MagicMock()
    coord._build_data_dict = MagicMock(return_value={"success": True})

    with patch(
        "custom_components.pricehawk.coordinator.async_push_daily_cost_to_statistics",
        new_callable=AsyncMock,
    ) as mock_push_stats:
        await coord._async_update_data()

        assert coord._daily_wins["amber"] == 1
        assert coord._saving_month_aud == 12.0
        mock_push_stats.assert_called()
        mock_current.reset_daily.assert_called_once()
        mock_amber.reset_daily.assert_called_once()
        assert coord._last_date == 5


@pytest.mark.asyncio
async def test_maybe_poll_amber():
    coord = _make_full_bare_coordinator()

    coord._amber_mode = PRICING_MODE_OFF
    coord._poll_amber_prices = AsyncMock()
    await coord._maybe_poll_amber()
    coord._poll_amber_prices.assert_not_called()

    coord._amber_mode = PRICING_MODE_LIVE_API
    coord._last_amber_poll = 1000.0
    coord.hass.loop.time = MagicMock(return_value=1010.0)
    await coord._maybe_poll_amber()
    coord._poll_amber_prices.assert_not_called()

    coord.hass.loop.time = MagicMock(return_value=1400.0)
    await coord._poll_amber_prices()  # Wait, wait, this should call maybe_poll_amber!
    # Let's verify we actually call _maybe_poll_amber here.
    # In the original test it was:
    # await coord._maybe_poll_amber()
    # coord._poll_amber_prices.assert_called_once()
    coord._poll_amber_prices.reset_mock()
    await coord._maybe_poll_amber()
    coord._poll_amber_prices.assert_called_once()


@pytest.mark.asyncio
async def test_maybe_poll_aemo():
    coord = _make_full_bare_coordinator()

    coord._flow_power = None
    coord._last_aemo_poll = 0.0
    await coord._maybe_poll_aemo()

    coord._flow_power = MagicMock()
    coord._last_aemo_poll = 1000.0
    coord.hass.loop.time = MagicMock(return_value=1005.0)
    await coord._maybe_poll_aemo()

    coord.hass.loop.time = MagicMock(return_value=1400.0)

    session = aiohttp.ClientSession()
    with (
        patch(
            "custom_components.pricehawk.coordinator.async_get_clientsession", return_value=session
        ),
        patch(
            "custom_components.pricehawk.coordinator.fetch_current_rrp",
            new_callable=AsyncMock,
            return_value=(15.0, "settle_time"),
        ) as mock_fetch,
    ):
        await coord._maybe_poll_aemo()
        mock_fetch.assert_called_once()
        assert coord._wholesale_c == 15.0
        assert coord._wholesale_settlement == "settle_time"

    await session.close()


@pytest.mark.asyncio
async def test_maybe_poll_localvolts():
    coord = _make_full_bare_coordinator()
    coord.hass.loop.time = MagicMock(return_value=100.0)

    coord._localvolts = None
    await coord._maybe_poll_localvolts()

    coord._localvolts = MagicMock()
    coord._localvolts_mode = PRICING_MODE_OFF
    await coord._maybe_poll_localvolts()

    coord._localvolts_mode = PRICING_MODE_LIVE_API
    coord._entry.options = {}
    await coord._maybe_poll_localvolts()

    coord._entry.options = {
        CONF_LOCALVOLTS_API_KEY: "api",
        CONF_LOCALVOLTS_PARTNER_ID: "partner",
        CONF_LOCALVOLTS_NMI: "nmi",
    }
    coord._last_localvolts_poll = 0.0
    coord.hass.loop.time = MagicMock(return_value=100.0)

    from custom_components.pricehawk.localvolts_api import LocalVoltsAPIError

    session = aiohttp.ClientSession()
    with (
        patch(
            "custom_components.pricehawk.coordinator.async_get_clientsession", return_value=session
        ),
        patch(
            "custom_components.pricehawk.coordinator.fetch_recent_intervals",
            new_callable=AsyncMock,
            side_effect=LocalVoltsAPIError("auth failed 401"),
        ),
    ):
        with pytest.raises(ConfigEntryAuthFailed):
            await coord._maybe_poll_localvolts()
        assert coord._reauth_provider_id == PROVIDER_LOCALVOLTS

    coord.hass.loop.time = MagicMock(return_value=100.0)
    coord._last_localvolts_poll = 0.0
    with (
        patch(
            "custom_components.pricehawk.coordinator.async_get_clientsession", return_value=session
        ),
        patch(
            "custom_components.pricehawk.coordinator.fetch_recent_intervals",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "custom_components.pricehawk.coordinator.aggregate_to_half_hour",
            return_value=(20.0, -5.0),
        ),
    ):
        await coord._maybe_poll_localvolts()
        assert coord._localvolts_import_c == 20.0
        assert coord._localvolts_export_c == -5.0

    await session.close()
