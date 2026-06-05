# ruff: noqa: E402
import importlib
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


import homeassistant.config_entries

# Save original config_entries classes before we stub them
_orig_config_flow = getattr(homeassistant.config_entries, "ConfigFlow", None)
_orig_options_flow = getattr(homeassistant.config_entries, "OptionsFlowWithReload", None)


# 1. Patch homeassistant.config_entries mock module with stubs
class StubConfigFlow:
    def __init__(self, *args, **kwargs):
        pass

    @classmethod
    def __init_subclass__(cls, **kwargs):
        pass


class StubOptionsFlowWithReload:
    def __init__(self, *args, **kwargs):
        pass

    @classmethod
    def __init_subclass__(cls, **kwargs):
        pass


homeassistant.config_entries.ConfigFlow = StubConfigFlow
homeassistant.config_entries.OptionsFlowWithReload = StubOptionsFlowWithReload

# 2. Reload config_flow module so EnergyCompareConfigFlow compiles as a real class
import custom_components.pricehawk.config_flow as cf

importlib.reload(cf)


@pytest.fixture(scope="module", autouse=True)
def restore_config_flow_stubs():
    yield
    # Restore original stubs on homeassistant.config_entries
    if _orig_config_flow is not None:
        homeassistant.config_entries.ConfigFlow = _orig_config_flow
    else:
        if hasattr(homeassistant.config_entries, "ConfigFlow"):
            delattr(homeassistant.config_entries, "ConfigFlow")

    if _orig_options_flow is not None:
        homeassistant.config_entries.OptionsFlowWithReload = _orig_options_flow
    else:
        if hasattr(homeassistant.config_entries, "OptionsFlowWithReload"):
            delattr(homeassistant.config_entries, "OptionsFlowWithReload")

    # Reload config_flow to re-compile against restored base classes
    import custom_components.pricehawk.config_flow as cf

    importlib.reload(cf)


# Now import the reloaded classes and functions
from custom_components.pricehawk.config_flow import (
    EnergyCompareConfigFlow,
    EnergyCompareOptionsFlow,
    _time_to_minutes,
    _windows_overlap,
    _str_to_windows,
    _windows_to_str,
    _filter_plans_by_geography,
    _get_tariff_type,
    _summarise_controlled_load,
    _summarise_import_rate,
    _summarise_fit,
    _summarise_cdr_plan,
    _build_rates_schema,
    _build_export_schema,
    _build_incentives_schema,
    fetch_amber_sites,
    PLAN_CUSTOM,
    PLAN_ZEROHERO,
    TARIFF_TOU,
)
from custom_components.pricehawk.cdr.cdr_client import CdrUnavailable, CdrAPIError


def test_time_to_minutes():
    assert _time_to_minutes("00:00") == 0
    assert _time_to_minutes("23:59") == 1439
    assert _time_to_minutes("24:00") == 0
    assert _time_to_minutes("invalid") == 0
    assert _time_to_minutes("25:00") == 0


def test_windows_overlap():
    assert not _windows_overlap([], [])
    assert not _windows_overlap([["10:00", "12:00"]], [["13:00", "15:00"]])
    assert _windows_overlap([["10:00", "13:00"]], [["12:00", "14:00"]])
    assert _windows_overlap([["12:00", "14:00"]], [["10:00", "13:00"]])
    assert not _windows_overlap([["10:00", "12:00"]], [["12:00", "13:00"]])  # boundary overlap


def test_str_to_windows_and_windows_to_str():
    assert _str_to_windows("") == []
    assert _str_to_windows("10:00-12:00, 14:00-15:00") == [["10:00", "12:00"], ["14:00", "15:00"]]
    assert _str_to_windows("invalid") == []

    assert _windows_to_str([]) == ""
    assert _windows_to_str([["10:00", "12:00"], ["14:00", "15:00"]]) == "10:00-12:00, 14:00-15:00"


def test_filter_plans_by_geography():
    plans = [
        {
            "displayName": "Plan NSW",
            "geography": {
                "includedPostcodes": ["2000", "2001"],
                "distributors": ["Ausgrid"],
            },
        },
        {
            "displayName": "Plan VIC",
            "geography": {
                "includedPostcodes": ["3000"],
                "distributors": ["Powercor"],
            },
        },
    ]
    res = _filter_plans_by_geography(plans, postcode="2000")
    assert len(res) == 1
    assert res[0]["displayName"] == "Plan NSW"

    res = _filter_plans_by_geography(plans, postcode="4000")
    assert len(res) == 0

    res = _filter_plans_by_geography(plans, state="NSW")
    assert len(res) == 1

    res = _filter_plans_by_geography(plans)
    assert len(res) == 2


def test_get_tariff_type():
    assert _get_tariff_type(PLAN_CUSTOM) == TARIFF_TOU
    assert _get_tariff_type(PLAN_ZEROHERO) == TARIFF_TOU
    assert _get_tariff_type("invalid_plan") == TARIFF_TOU


def test_summarise_helpers_exceptions():
    assert _summarise_controlled_load({}) == "none"
    assert _summarise_controlled_load({"controlledLoad": "invalid"}) == "none"
    assert _summarise_controlled_load({"controlledLoad": []}) == "none"
    assert (
        _summarise_controlled_load({"controlledLoad": [{"displayName": "CL1", "rates": []}]})
        == "none"
    )
    assert _summarise_import_rate({"tariffPeriod": [{"rateBlockUType": "invalid"}]}) == "?"
    assert (
        _summarise_import_rate(
            {
                "tariffPeriod": [
                    {
                        "rateBlockUType": "singleRate",
                        "singleRate": {"rates": [{"unitPrice": "invalid"}]},
                    }
                ]
            }
        )
        == "?"
    )
    assert _summarise_import_rate({"singleRate": {"rates": [{"unitPrice": "invalid"}]}}) == "?"

    detail = {
        "data": {
            "brandName": "GloBird",
            "displayName": "GloSave",
            "effectiveFrom": "2026-05-15",
            "electricityContract": {
                "dailySupplyCharges": "invalid",
            },
        }
    }
    summary = _summarise_cdr_plan(detail)
    assert summary["daily_supply"] == "?"


# --- Step Mock Helpers ---


def setup_flow_mocks(flow):
    flow.hass = MagicMock()
    flow.async_show_form = MagicMock(return_value={"type": "form"})
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    flow.async_abort = MagicMock(
        side_effect=lambda reason, **kwargs: {"type": "abort", "reason": reason}
    )
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_show_menu = MagicMock(return_value={"type": "menu"})


@pytest.mark.asyncio
async def test_step_user_redirects():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    flow.async_step_cdr_retailer = AsyncMock(return_value={"type": "retailer_form"})
    res = await flow.async_step_user()
    assert res == {"type": "retailer_form"}


@pytest.mark.asyncio
async def test_step_amber_credentials():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    # 1. No input
    res = await flow.async_step_amber_credentials()
    assert res["type"] == "form"

    # 2. Valid input (1 site)
    from custom_components.pricehawk.config_flow import InvalidAuth, CannotConnect, NoActiveSites

    with patch("custom_components.pricehawk.config_flow.fetch_amber_sites") as mock_fetch:
        mock_fetch.return_value = [{"id": "site1"}]
        flow.async_step_amber_fees = AsyncMock(return_value={"type": "fees"})
        res = await flow.async_step_amber_credentials({"api_key": "valid_key"})
        assert res == {"type": "fees"}
        assert flow._data["api_key"] == "valid_key"

    # 3. Valid input (multiple sites)
    with patch("custom_components.pricehawk.config_flow.fetch_amber_sites") as mock_fetch:
        mock_fetch.return_value = [{"id": "site1"}, {"id": "site2"}]
        flow.async_step_site_select = AsyncMock(return_value={"type": "site_select"})
        res = await flow.async_step_amber_credentials({"api_key": "valid_key"})
        assert res == {"type": "site_select"}

    # 4. Invalid Auth
    with patch("custom_components.pricehawk.config_flow.fetch_amber_sites") as mock_fetch:
        mock_fetch.side_effect = InvalidAuth("auth")
        res = await flow.async_step_amber_credentials({"api_key": "invalid"})
        assert res["type"] == "form"
        assert flow.async_show_form.call_args[1]["errors"] == {"api_key": "invalid_auth"}

    # 5. Cannot Connect
    with patch("custom_components.pricehawk.config_flow.fetch_amber_sites") as mock_fetch:
        mock_fetch.side_effect = CannotConnect("connect")
        res = await flow.async_step_amber_credentials({"api_key": "invalid"})
        assert res["type"] == "form"
        assert flow.async_show_form.call_args[1]["errors"] == {"base": "cannot_connect"}

    # 6. No Active Sites
    with patch("custom_components.pricehawk.config_flow.fetch_amber_sites") as mock_fetch:
        mock_fetch.side_effect = NoActiveSites("no sites")
        res = await flow.async_step_amber_credentials({"api_key": "invalid"})
        assert res["type"] == "form"
        assert flow.async_show_form.call_args[1]["errors"] == {"base": "no_active_sites"}


@pytest.mark.asyncio
async def test_step_flow_power_credentials():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    # No input
    res = await flow.async_step_flow_power_credentials()
    assert res["type"] == "form"

    # Valid input
    flow.async_step_cdr_retailer = AsyncMock(return_value={"type": "retailer_form"})
    res = await flow.async_step_flow_power_credentials(
        {
            "flow_power_region": "NSW1",
            "flow_power_base_rate": 20.0,
            "flow_power_daily_supply": 100.0,
            "flow_power_pea_enabled": True,
        }
    )
    assert res == {"type": "retailer_form"}
    assert flow._data["flow_power_region"] == "NSW1"


@pytest.mark.asyncio
async def test_step_localvolts_credentials():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    # No input
    res = await flow.async_step_localvolts_credentials()
    assert res["type"] == "form"

    # Valid input
    flow.async_step_cdr_retailer = AsyncMock(return_value={"type": "retailer_form"})
    res = await flow.async_step_localvolts_credentials(
        {
            "localvolts_api_key": "key",
            "localvolts_partner_id": "partner",
            "localvolts_nmi": "nmi",
            "localvolts_daily_supply": 100.0,
            "localvolts_buy_ceiling": 20.0,
            "localvolts_sell_floor": 0.0,
        }
    )
    assert res == {"type": "retailer_form"}


@pytest.mark.asyncio
async def test_step_site_select():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    flow._data["_sites"] = [{"id": "site1", "name": "Site 1"}]

    # No input
    res = await flow.async_step_site_select()
    assert res["type"] == "form"

    # Valid input
    flow.async_step_amber_fees = AsyncMock(return_value={"type": "fees"})
    res = await flow.async_step_site_select({"site_id": "site1"})
    assert res == {"type": "fees"}


@pytest.mark.asyncio
async def test_step_amber_fees():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    # No input
    res = await flow.async_step_amber_fees()
    assert res["type"] == "form"

    # Valid input
    flow.async_step_cdr_retailer = AsyncMock(return_value={"type": "retailer_form"})
    res = await flow.async_step_amber_fees(
        {
            "amber_subscription_fee": 15.0,
            "amber_network_daily_charge": 100.0,
        }
    )
    assert res == {"type": "retailer_form"}


@pytest.mark.asyncio
async def test_step_cdr_retailer():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    # 1. No input (succeeds loading registry)
    from custom_components.pricehawk.cdr.registry import RetailerEndpoint

    endpoint1 = RetailerEndpoint(
        brand_id="brand1", brand_name="Brand 1", base_uri="http://uri", cdr_brand="brand"
    )
    with patch("custom_components.pricehawk.config_flow.get_registry") as mock_reg:
        mock_reg.return_value = ([endpoint1], "cached")
        res = await flow.async_step_cdr_retailer()
        assert res["type"] == "form"
        assert flow._data["_cdr_endpoints"] == [endpoint1]

    # 2. No input (fails loading registry)
    with patch("custom_components.pricehawk.config_flow.get_registry") as mock_reg:
        mock_reg.side_effect = Exception("failed")
        flow.async_step_cdr_error = AsyncMock(return_value={"type": "error_form"})
        res = await flow.async_step_cdr_retailer()
        assert res == {"type": "error_form"}

    # 3. Valid input (DWT OE)
    flow.async_step_dwt_credentials = AsyncMock(return_value={"type": "dwt_oe"})
    res = await flow.async_step_cdr_retailer({"cdr_retailer_id": "dwt_openelectricity"})
    assert res == {"type": "dwt_oe"}

    # 4. Valid input (DWT AEMO)
    flow.async_step_dwt_aemo_setup = AsyncMock(return_value={"type": "dwt_aemo"})
    res = await flow.async_step_cdr_retailer({"cdr_retailer_id": "dwt_aemo_direct"})
    assert res == {"type": "dwt_aemo"}

    # 5. Valid input (normal endpoint)
    flow._data["_cdr_endpoints"] = [endpoint1]
    flow.async_step_cdr_locale = AsyncMock(return_value={"type": "locale"})
    res = await flow.async_step_cdr_retailer({"cdr_retailer_id": "brand1"})
    assert res == {"type": "locale"}
    assert flow._data["_cdr_retailer"] == endpoint1


@pytest.mark.asyncio
async def test_step_dwt_credentials():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    # 1. No input
    res = await flow.async_step_dwt_credentials()
    assert res["type"] == "form"

    # 2. Valid input
    with patch(
        "custom_components.pricehawk.providers.openelectricity.OpenElectricityPriceSource"
    ) as mock_src_class:
        mock_src = AsyncMock()
        mock_src_class.return_value = mock_src

        flow.async_step_sensor_select = AsyncMock(return_value={"type": "sensor"})
        res = await flow.async_step_dwt_credentials(
            {
                "dwt_oe_api_key": "valid_key",
                "dwt_region": "NSW1",
                "dwt_oe_daily_supply": 110.0,
            }
        )
        assert res == {"type": "sensor"}


@pytest.mark.asyncio
async def test_step_dwt_aemo_setup():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    # No input
    res = await flow.async_step_dwt_aemo_setup()
    assert res["type"] == "form"

    # Valid input
    flow.async_step_sensor_select = AsyncMock(return_value={"type": "sensor"})
    res = await flow.async_step_dwt_aemo_setup(
        {
            "dwt_region": "NSW1",
            "dwt_aemo_daily_supply": 110.0,
        }
    )
    assert res == {"type": "sensor"}


@pytest.mark.asyncio
async def test_step_cdr_locale():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    # 1. No input
    res = await flow.async_step_cdr_locale()
    assert res["type"] == "form"

    # 2. Valid input with state choice
    flow.async_step_cdr_distributor = AsyncMock(return_value={"type": "distributor"})
    res = await flow.async_step_cdr_locale(
        {
            "cdr_postcode": "",
            "cdr_state": "NSW",
        }
    )
    assert res == {"type": "distributor"}
    assert flow._data["_cdr_state"] == "NSW"

    # 3. Invalid postcode
    res = await flow.async_step_cdr_locale(
        {
            "cdr_postcode": "invalid",
            "cdr_state": "__manual__",
        }
    )
    assert res["type"] == "form"
    assert flow.async_show_form.call_args[1]["errors"]["cdr_postcode"] == "cdr_invalid_postcode"


@pytest.mark.asyncio
async def test_step_cdr_distributor():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    # 1. State is None -> skips distributor step
    flow._data["_cdr_state"] = None
    flow.async_step_cdr_plan_select = AsyncMock(return_value={"type": "plan_select"})
    res = await flow.async_step_cdr_distributor()
    assert res == {"type": "plan_select"}

    # 2. State is set, no input
    flow._data["_cdr_state"] = "NSW"
    res = await flow.async_step_cdr_distributor()
    assert res["type"] == "form"

    # 3. State is set, valid input
    res = await flow.async_step_cdr_distributor({"cdr_distributor": "Ausgrid"})
    assert res == {"type": "plan_select"}
    assert flow._data["_cdr_distributor"] == "Ausgrid"


@pytest.mark.asyncio
async def test_options_flow_init():
    flow = EnergyCompareOptionsFlow()
    setup_flow_mocks(flow)

    flow.config_entry = MagicMock()
    flow.config_entry.options = {"opt": "val"}

    res = await flow.async_step_init()
    assert res["type"] == "menu"
    assert flow._data["opt"] == "val"


@pytest.mark.asyncio
async def test_step_cdr_plan_select():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    # 1. Retailer is None -> redirects to cdr_retailer
    flow.async_step_cdr_retailer = AsyncMock(return_value={"type": "retailer_form"})
    res = await flow.async_step_cdr_plan_select()
    assert res == {"type": "retailer_form"}
    assert flow._data["_cdr_skip_reason"] == "step_entered_without_retailer"

    # 2. Retailer is set, first entry (list fetch)
    from custom_components.pricehawk.cdr.registry import RetailerEndpoint

    retailer = RetailerEndpoint(
        brand_id="brand1", brand_name="Brand 1", base_uri="http://uri", cdr_brand="brand"
    )
    flow._data["_cdr_retailer"] = retailer

    with patch("custom_components.pricehawk.config_flow.fetch_plan_list") as mock_list:
        mock_list.return_value = [{"displayName": "Plan A", "planId": "plan1"}]
        res = await flow.async_step_cdr_plan_select()
        assert res["type"] == "form"

    # 3. List fetch fails -> routes to cdr_error
    with patch("custom_components.pricehawk.config_flow.fetch_plan_list") as mock_list:
        mock_list.side_effect = CdrUnavailable("Unavailable")
        flow.async_step_cdr_error = AsyncMock(return_value={"type": "error_form"})
        res = await flow.async_step_cdr_plan_select()
        assert res == {"type": "error_form"}

    # 4. User input selection -> fetches detail
    with patch("custom_components.pricehawk.config_flow.fetch_plan_detail") as mock_detail:
        mock_detail.return_value = {"data": {"displayName": "Plan A"}}
        flow.async_step_cdr_confirm = AsyncMock(return_value={"type": "confirm_form"})
        res = await flow.async_step_cdr_plan_select({"cdr_plan_id": "plan1"})
        assert res == {"type": "confirm_form"}
        assert flow._data["cdr_plan"] == {"data": {"displayName": "Plan A"}}


@pytest.mark.asyncio
async def test_step_cdr_confirm():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    flow._data["cdr_plan"] = {
        "data": {
            "brand": "amber",
            "displayName": "Amber Flat",
            "effectiveFrom": "2026-05-15",
            "electricityContract": {},
        }
    }

    # 1. No user input -> show form
    res = await flow.async_step_cdr_confirm()
    assert res["type"] == "form"

    # 2. Action: accept, retailer with API (amber)
    flow.async_step_amber_credentials = AsyncMock(return_value={"type": "amber_cred"})
    res = await flow.async_step_cdr_confirm({"cdr_confirm_action": "accept"})
    assert res == {"type": "amber_cred"}

    # 3. Action: accept, retailer without API
    flow._data["cdr_plan"]["data"]["brand"] = "unknown_brand"
    flow.async_step_sensor_select = AsyncMock(return_value={"type": "sensor_select"})
    res = await flow.async_step_cdr_confirm({"cdr_confirm_action": "accept"})
    assert res == {"type": "sensor_select"}

    # 4. Action: pick different
    flow.async_step_cdr_plan_select = AsyncMock(return_value={"type": "plan_select"})
    res = await flow.async_step_cdr_confirm({"cdr_confirm_action": "pick_different"})
    assert res == {"type": "plan_select"}
    assert "cdr_plan" not in flow._data


@pytest.mark.asyncio
async def test_step_cdr_error():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    # 1. First entry (no input) -> show form
    flow._data["_cdr_error_kind"] = "list"
    res = await flow.async_step_cdr_error()
    assert res["type"] == "form"

    # 2. Action: skip -> manual flow
    flow.async_step_cdr_retailer = AsyncMock(return_value={"type": "retailer_form"})
    res = await flow.async_step_cdr_error({"cdr_retry_action": "skip"})
    assert res == {"type": "retailer_form"}
    assert flow._data["_cdr_skip_reason"] == "user_skipped_after_error"

    # 3. Action: retry, count <= max retries
    flow._data["_cdr_retry_count"] = 1
    flow._data["_cdr_error_kind"] = "list"
    flow.async_step_cdr_plan_select = AsyncMock(return_value={"type": "plan_select"})
    res = await flow.async_step_cdr_error({"cdr_retry_action": "retry"})
    assert res == {"type": "plan_select"}
    assert flow._data["_cdr_retry_count"] == 2

    # 4. Action: retry, count > max retries (exhausted)
    flow._data["_cdr_retry_count"] = 2
    flow.async_step_cdr_retailer = AsyncMock(return_value={"type": "retailer_form"})
    res = await flow.async_step_cdr_error({"cdr_retry_action": "retry"})
    assert res == {"type": "retailer_form"}
    assert flow._data["_cdr_skip_reason"] == "retry_exhausted"


import aiohttp
from aioresponses import aioresponses


@pytest.mark.asyncio
async def test_step_sensor_select():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    # 1. No user input -> show form
    res = await flow.async_step_sensor_select()
    assert res["type"] == "form"

    # 2. Valid user input -> routes to dashboard_token
    flow.async_step_dashboard_token = AsyncMock(return_value={"type": "dashboard_token"})
    res = await flow.async_step_sensor_select({"grid_power_sensor": "sensor.my_grid"})
    assert res == {"type": "dashboard_token"}
    assert flow._data["grid_power_sensor"] == "sensor.my_grid"


@pytest.mark.asyncio
async def test_step_dashboard_token():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    # Initialize some mock data in _data
    flow._data = {
        "grid_power_sensor": "sensor.my_grid",
        "current_provider": "amber",
        "api_key": "some_api_key",
        "site_id": "some_site_id",
        "_cdr_skip_reason": "test_skip",
    }

    # 1. No input -> show form
    res = await flow.async_step_dashboard_token()
    assert res["type"] == "form"

    # 2. With input -> create entry
    res = await flow.async_step_dashboard_token({"ha_token": "token123"})
    assert res["type"] == "create_entry"
    # Verify created entry data and options
    call_args = flow.async_create_entry.call_args[1]
    assert call_args["title"] == "PriceHawk"
    assert call_args["data"]["ha_token"] == "token123"
    assert call_args["options"]["grid_power_sensor"] == "sensor.my_grid"


@pytest.mark.asyncio
async def test_step_reauth_dispatch():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    mock_entry = MagicMock()
    mock_entry.runtime_data = None
    mock_entry.data = {"current_provider": "amber"}
    flow._get_reauth_entry = MagicMock(return_value=mock_entry)

    # 1. No coordinator, fallback to entry.data current_provider (amber)
    flow.async_step_reauth_amber = AsyncMock(return_value={"type": "reauth_amber"})
    res = await flow.async_step_reauth({})
    assert res == {"type": "reauth_amber"}

    # 2. Coordinator has _reauth_provider_id = localvolts
    mock_coordinator = MagicMock()
    mock_coordinator._reauth_provider_id = "localvolts"
    mock_entry.runtime_data = MagicMock(coordinator=mock_coordinator)
    flow.async_step_reauth_localvolts = AsyncMock(return_value={"type": "reauth_lv"})
    res = await flow.async_step_reauth({})
    assert res == {"type": "reauth_lv"}

    # 3. Coordinator has _reauth_provider_id = dwt_oe
    mock_coordinator._reauth_provider_id = "dwt_openelectricity"
    flow.async_step_reauth_dwt_oe = AsyncMock(return_value={"type": "reauth_dwt_oe"})
    res = await flow.async_step_reauth({})
    assert res == {"type": "reauth_dwt_oe"}

    # 4. Unknown provider -> abort
    mock_coordinator._reauth_provider_id = "unknown"
    res = await flow.async_step_reauth({})
    assert res["type"] == "abort"
    assert res["reason"] == "reauth_provider_unknown"


@pytest.mark.asyncio
async def test_step_reauth_amber():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    mock_entry = MagicMock()
    mock_entry.data = {"api_key": "old_key"}
    flow._get_reauth_entry = MagicMock(return_value=mock_entry)
    flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort_reload"})

    # 1. No user input -> form
    res = await flow.async_step_reauth_amber()
    assert res["type"] == "form"

    # 2. 401 unauthorized
    session = aiohttp.ClientSession()
    with (
        patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=session),
        aioresponses() as m,
    ):
        m.get("https://api.amber.com.au/v1/sites", status=401)
        res = await flow.async_step_reauth_amber({"api_key": "bad_key"})
        assert res["type"] == "form"
        assert "api_key" in flow.async_show_form.call_args[1]["errors"]

    # 3. 500 server error / other non-200
    with (
        patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=session),
        aioresponses() as m,
    ):
        m.get("https://api.amber.com.au/v1/sites", status=500)
        res = await flow.async_step_reauth_amber({"api_key": "bad_key"})
        assert res["type"] == "form"
        assert "base" in flow.async_show_form.call_args[1]["errors"]

    # 4. Timeout/ClientError
    with (
        patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=session),
        aioresponses() as m,
    ):
        m.get("https://api.amber.com.au/v1/sites", exception=TimeoutError())
        res = await flow.async_step_reauth_amber({"api_key": "bad_key"})
        assert res["type"] == "form"
        assert "base" in flow.async_show_form.call_args[1]["errors"]

    # 5. Success 200 OK
    with (
        patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=session),
        aioresponses() as m,
    ):
        m.get("https://api.amber.com.au/v1/sites", status=200, payload=[])
        res = await flow.async_step_reauth_amber({"api_key": "new_key"})
        assert res == {"type": "abort_reload"}
    await session.close()


@pytest.mark.asyncio
async def test_step_reauth_localvolts():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    mock_entry = MagicMock()
    mock_entry.options = {"localvolts_api_key": "old_key"}
    flow._get_reauth_entry = MagicMock(return_value=mock_entry)
    flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort_reload"})

    # 1. No input -> form
    res = await flow.async_step_reauth_localvolts()
    assert res["type"] == "form"

    # 2. LocalVoltsAPIError Auth failure
    with patch(
        "custom_components.pricehawk.localvolts_api.fetch_recent_intervals",
        side_effect=CdrAPIError("auth failed 401"),
    ):
        res = await flow.async_step_reauth_localvolts(
            {
                "localvolts_api_key": "new_key",
                "localvolts_partner_id": "new_partner",
                "localvolts_nmi": "new_nmi",
            }
        )
        assert res["type"] == "form"
        assert "base" in flow.async_show_form.call_args[1]["errors"]

    # 3. LocalVoltsAPIError generic failure
    with patch(
        "custom_components.pricehawk.localvolts_api.fetch_recent_intervals",
        side_effect=CdrAPIError("other error"),
    ):
        res = await flow.async_step_reauth_localvolts(
            {
                "localvolts_api_key": "new_key",
                "localvolts_partner_id": "new_partner",
                "localvolts_nmi": "new_nmi",
            }
        )
        assert res["type"] == "form"
        assert "base" in flow.async_show_form.call_args[1]["errors"]

    # 4. Success path
    with patch(
        "custom_components.pricehawk.localvolts_api.fetch_recent_intervals", return_value=[]
    ):
        res = await flow.async_step_reauth_localvolts(
            {
                "localvolts_api_key": "new_key",
                "localvolts_partner_id": "new_partner",
                "localvolts_nmi": "new_nmi",
            }
        )
        assert res == {"type": "abort_reload"}


@pytest.mark.asyncio
async def test_step_reauth_dwt_oe():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    mock_entry = MagicMock()
    mock_entry.data = {"dwt_oe_api_key": "old_key", "dwt_region": "NSW1"}
    flow._get_reauth_entry = MagicMock(return_value=mock_entry)
    flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort_reload"})

    # 1. No input -> form
    res = await flow.async_step_reauth_dwt_oe()
    assert res["type"] == "form"

    # 2. Auth failure
    from homeassistant.exceptions import ConfigEntryAuthFailed

    with patch(
        "custom_components.pricehawk.providers.openelectricity.OpenElectricityPriceSource"
    ) as mock_src_class:
        mock_src = MagicMock()
        mock_src.fetch_current_price = AsyncMock(side_effect=ConfigEntryAuthFailed("invalid key"))
        mock_src_class.return_value = mock_src

        res = await flow.async_step_reauth_dwt_oe({"dwt_oe_api_key": "bad_key"})
        assert res["type"] == "form"
        assert "dwt_oe_api_key" in flow.async_show_form.call_args[1]["errors"]

    # 3. Success path
    with patch(
        "custom_components.pricehawk.providers.openelectricity.OpenElectricityPriceSource"
    ) as mock_src_class:
        mock_src = MagicMock()
        mock_src.fetch_current_price = AsyncMock(return_value={"price": 10.0})
        mock_src_class.return_value = mock_src

        res = await flow.async_step_reauth_dwt_oe({"dwt_oe_api_key": "new_key"})
        assert res == {"type": "abort_reload"}


@pytest.mark.asyncio
async def test_step_reconfigure_dispatch():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    mock_entry = MagicMock()
    flow._get_reconfigure_entry = MagicMock(return_value=mock_entry)

    # 1. Amber
    mock_entry.data = {"current_provider": "amber"}
    flow.async_step_reconfigure_amber = AsyncMock(return_value={"type": "rec_amber"})
    res = await flow.async_step_reconfigure({})
    assert res == {"type": "rec_amber"}

    # 2. LocalVolts
    mock_entry.data = {"current_provider": "localvolts"}
    flow.async_step_reconfigure_localvolts = AsyncMock(return_value={"type": "rec_lv"})
    res = await flow.async_step_reconfigure({})
    assert res == {"type": "rec_lv"}

    # 3. DWT OE
    mock_entry.data = {"current_provider": "dwt_openelectricity"}
    flow.async_step_reconfigure_dwt_oe = AsyncMock(return_value={"type": "rec_dwt_oe"})
    res = await flow.async_step_reconfigure({})
    assert res == {"type": "rec_dwt_oe"}

    # 4. DWT AEMO
    mock_entry.data = {"current_provider": "dwt_aemo_direct"}
    flow.async_step_reconfigure_dwt_aemo = AsyncMock(return_value={"type": "rec_dwt_aemo"})
    res = await flow.async_step_reconfigure({})
    assert res == {"type": "rec_dwt_aemo"}

    # 5. Unsupported
    mock_entry.data = {"current_provider": "unsupported"}
    res = await flow.async_step_reconfigure({})
    assert res["type"] == "abort"
    assert res["reason"] == "reconfigure_unsupported"


@pytest.mark.asyncio
async def test_step_reconfigure_amber():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    mock_entry = MagicMock()
    mock_entry.options = {"amber_network_daily_charge": 50.0, "amber_subscription_fee": 10.0}
    flow._get_reconfigure_entry = MagicMock(return_value=mock_entry)
    flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort_reload"})

    # 1. No input
    res = await flow.async_step_reconfigure_amber()
    assert res["type"] == "form"

    # 2. Negative inputs
    res = await flow.async_step_reconfigure_amber(
        {
            "amber_network_daily_charge": -5.0,
            "amber_subscription_fee": -2.0,
        }
    )
    assert res["type"] == "form"
    assert "amber_network_daily_charge" in flow.async_show_form.call_args[1]["errors"]

    # 3. Valid inputs
    res = await flow.async_step_reconfigure_amber(
        {
            "amber_network_daily_charge": 60.0,
            "amber_subscription_fee": 12.0,
        }
    )
    assert res == {"type": "abort_reload"}


@pytest.mark.asyncio
async def test_step_reconfigure_localvolts():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    mock_entry = MagicMock()
    mock_entry.options = {
        "localvolts_daily_supply": 110.0,
        "localvolts_buy_ceiling": 50.0,
        "localvolts_sell_floor": 0.0,
    }
    flow._get_reconfigure_entry = MagicMock(return_value=mock_entry)
    flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort_reload"})

    # 1. No input
    res = await flow.async_step_reconfigure_localvolts()
    assert res["type"] == "form"

    # 2. Negative supply
    res = await flow.async_step_reconfigure_localvolts(
        {
            "localvolts_daily_supply": -110.0,
            "localvolts_buy_ceiling": 50.0,
            "localvolts_sell_floor": 0.0,
        }
    )
    assert res["type"] == "form"
    assert "localvolts_daily_supply" in flow.async_show_form.call_args[1]["errors"]

    # 3. Valid inputs
    res = await flow.async_step_reconfigure_localvolts(
        {
            "localvolts_daily_supply": 120.0,
            "localvolts_buy_ceiling": 60.0,
            "localvolts_sell_floor": 5.0,
        }
    )
    assert res == {"type": "abort_reload"}


@pytest.mark.asyncio
async def test_step_reconfigure_dwt_oe_and_aemo():
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)

    mock_entry = MagicMock()
    mock_entry.options = {"dwt_oe_daily_supply": 110.0, "dwt_aemo_daily_supply": 110.0}
    flow._get_reconfigure_entry = MagicMock(return_value=mock_entry)
    flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort_reload"})

    # OE reconfigure
    res = await flow.async_step_reconfigure_dwt_oe()
    assert res["type"] == "form"

    res = await flow.async_step_reconfigure_dwt_oe({"dwt_oe_daily_supply": -10.0})
    assert res["type"] == "form"

    res = await flow.async_step_reconfigure_dwt_oe({"dwt_oe_daily_supply": 120.0})
    assert res == {"type": "abort_reload"}

    # AEMO reconfigure
    res = await flow.async_step_reconfigure_dwt_aemo()
    assert res["type"] == "form"

    res = await flow.async_step_reconfigure_dwt_aemo({"dwt_aemo_daily_supply": -10.0})
    assert res["type"] == "form"

    res = await flow.async_step_reconfigure_dwt_aemo({"dwt_aemo_daily_supply": 120.0})
    assert res == {"type": "abort_reload"}


def test_build_rates_schema():
    # PLAN_CUSTOM
    res = _build_rates_schema(
        plan_type="custom",
        tariff_type="tou",
        defaults={},
        current_import={"type": "tou", "periods": {"peak": {"rate": 10.0, "windows": []}}},
        current_supply=100.0,
    )
    assert res is not None

    # TARIFF_TOU
    res = _build_rates_schema(
        plan_type="standard",
        tariff_type="tou",
        defaults={"import_tariff": {"periods": {"peak": {"rate": 10.0}}}},
    )
    assert res is not None

    # Flat stepped
    res = _build_rates_schema(
        plan_type="standard",
        tariff_type="flat_stepped",
        defaults={"step1_threshold_kwh": 10, "step1_rate": 20.0, "step2_rate": 30.0},
    )
    assert res is not None


def test_build_export_schema():
    res = _build_export_schema(
        defaults={"export_tariff": {"periods": {"peak": {"rate": 5.0}}}},
    )
    assert res is not None


def test_build_incentives_schema():
    res = _build_incentives_schema(
        plan_type="zerohero",
        current_incentives=None,
    )
    assert res is not None

    res = _build_incentives_schema(
        plan_type="custom",
        current_incentives={"zerohero_credit": True, "super_export": True},
    )
    assert res is not None


@pytest.mark.asyncio
async def test_step_dashboard_token_variations():
    # 1. Localvolts enabled
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)
    flow._data = {
        "grid_power_sensor": "sensor.my_grid",
        "current_provider": "localvolts",
        "localvolts_enabled": True,
        "localvolts_api_key": "lv_key",
        "localvolts_partner_id": "lv_partner",
        "localvolts_nmi": "lv_nmi",
        "localvolts_daily_supply": 120.0,
    }
    res = await flow.async_step_dashboard_token({"ha_token": "token123"})
    assert res["type"] == "create_entry"
    assert flow.async_create_entry.call_args[1]["options"]["localvolts_api_key"] == "lv_key"
    assert flow.async_create_entry.call_args[1]["options"]["localvolts_daily_supply"] == 120.0

    # 2. CDR plan present
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)
    flow._data = {
        "grid_power_sensor": "sensor.my_grid",
        "current_provider": "amber",
        "cdr_plan": {"some": "plan"},
    }
    res = await flow.async_step_dashboard_token({"ha_token": "token123"})
    assert res["type"] == "create_entry"
    assert flow.async_create_entry.call_args[1]["options"]["cdr_plan"] == {"some": "plan"}

    # 3. DWT OE enabled
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)
    flow._data = {
        "grid_power_sensor": "sensor.my_grid",
        "current_provider": "dwt_openelectricity",
        "dwt_oe_enabled": True,
        "dwt_oe_api_key": "oe_key",
        "dwt_region": "VIC1",
        "dwt_oe_daily_supply": 115.0,
    }
    res = await flow.async_step_dashboard_token({"ha_token": "token123"})
    assert res["type"] == "create_entry"
    assert flow.async_create_entry.call_args[1]["data"]["dwt_oe_api_key"] == "oe_key"
    assert flow.async_create_entry.call_args[1]["options"]["dwt_oe_daily_supply"] == 115.0

    # 4. DWT AEMO enabled
    flow = EnergyCompareConfigFlow()
    setup_flow_mocks(flow)
    flow._data = {
        "grid_power_sensor": "sensor.my_grid",
        "current_provider": "dwt_aemo_direct",
        "dwt_aemo_enabled": True,
        "dwt_region": "QLD1",
        "dwt_aemo_daily_supply": 130.0,
    }
    res = await flow.async_step_dashboard_token({"ha_token": "token123"})
    assert res["type"] == "create_entry"
    assert flow.async_create_entry.call_args[1]["data"]["dwt_region"] == "QLD1"
    assert flow.async_create_entry.call_args[1]["options"]["dwt_aemo_daily_supply"] == 130.0


@pytest.mark.asyncio
async def test_fetch_amber_sites_success():

    hass = MagicMock()
    session = aiohttp.ClientSession()
    with (
        patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=session),
        aioresponses() as m,
    ):
        m.get("https://api.amber.com.au/v1/sites", status=200, payload=[{"id": "site1"}])
        res = await fetch_amber_sites(hass, "my_key")
        assert res == [{"id": "site1"}]
    await session.close()


@pytest.mark.asyncio
async def test_fetch_amber_sites_unauthorized():
    from custom_components.pricehawk.config_flow import InvalidAuth

    hass = MagicMock()
    session = aiohttp.ClientSession()
    with (
        patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=session),
        aioresponses() as m,
    ):
        m.get("https://api.amber.com.au/v1/sites", status=401)
        with pytest.raises(InvalidAuth):
            await fetch_amber_sites(hass, "bad_key")
    await session.close()


@pytest.mark.asyncio
async def test_fetch_amber_sites_cannot_connect():
    from custom_components.pricehawk.config_flow import CannotConnect

    hass = MagicMock()
    session = aiohttp.ClientSession()
    with (
        patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=session),
        aioresponses() as m,
    ):
        m.get("https://api.amber.com.au/v1/sites", status=500)
        with pytest.raises(CannotConnect):
            await fetch_amber_sites(hass, "key")
    await session.close()


@pytest.mark.asyncio
async def test_fetch_amber_sites_exception():
    from custom_components.pricehawk.config_flow import CannotConnect

    hass = MagicMock()
    session = aiohttp.ClientSession()
    with (
        patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=session),
        aioresponses() as m,
    ):
        m.get("https://api.amber.com.au/v1/sites", exception=TimeoutError())
        with pytest.raises(CannotConnect):
            await fetch_amber_sites(hass, "key")
    await session.close()


@pytest.mark.asyncio
async def test_fetch_amber_sites_no_sites():
    from custom_components.pricehawk.config_flow import NoActiveSites

    hass = MagicMock()
    session = aiohttp.ClientSession()
    with (
        patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=session),
        aioresponses() as m,
    ):
        m.get("https://api.amber.com.au/v1/sites", status=200, payload=[])
        with pytest.raises(NoActiveSites):
            await fetch_amber_sites(hass, "key")
    await session.close()


def test_step_named_comparator_ui():
    from custom_components.pricehawk.config_flow import plan_named_comparator_step

    # 1. Empty ranked alternatives -> abort
    res = plan_named_comparator_step(
        ranked_alternatives=[],
        plan_cache={},
        user_input=None,
        current_options={},
    )
    assert res == ("abort", {"reason": "no_ranked_alternatives"})

    # 2. Empty plan cache -> abort
    res = plan_named_comparator_step(
        ranked_alternatives=[{"plan_id": "plan1"}],
        plan_cache={},
        user_input=None,
        current_options={},
    )
    assert res == ("abort", {"reason": "no_ranked_alternatives"})

    # 3. User input is clear sentinel -> create entry with pruned options
    res = plan_named_comparator_step(
        ranked_alternatives=[{"plan_id": "plan1"}],
        plan_cache={"plan1": {"name": "Plan 1"}},
        user_input={"named_comparator_plan_id": "__clear__"},
        current_options={
            "named_comparator_plan_id": "plan1",
            "named_comparator_plan": {"name": "Plan 1"},
        },
    )
    assert res[0] == "create_entry"
    assert "named_comparator_plan_id" not in res[1]["data"]

    # 4. User input is plan not in cache -> abort
    res = plan_named_comparator_step(
        ranked_alternatives=[{"plan_id": "plan1"}],
        plan_cache={"plan1": {"name": "Plan 1"}},
        user_input={"named_comparator_plan_id": "plan2"},
        current_options={},
    )
    assert res == ("abort", {"reason": "plan_not_in_cache"})

    # 5. User input valid plan -> create entry
    res = plan_named_comparator_step(
        ranked_alternatives=[{"plan_id": "plan1"}],
        plan_cache={"plan1": {"name": "Plan 1"}},
        user_input={"named_comparator_plan_id": "plan1"},
        current_options={},
    )
    assert res[0] == "create_entry"
    assert res[1]["data"]["named_comparator_plan_id"] == "plan1"

    # 6. No user input -> form with options, including invalid non-dict or invalid plan_id
    res = plan_named_comparator_step(
        ranked_alternatives=[
            "invalid_alt_not_dict",
            {"plan_id": 123},  # invalid plan_id type
            {"plan_id": ""},  # empty plan_id
            {"plan_id": "plan1", "brand": "BrandA", "display_name": "Plan 1"},
            {"plan_id": "plan2", "display_name": "Plan 2"},
            {"plan_id": "plan1"},  # duplicate
        ],
        plan_cache={
            "plan1": {"name": "Plan 1"},
            "plan2": {"name": "Plan 2"},
        },
        user_input=None,
        current_options={"named_comparator_plan_id": "plan2"},
    )
    assert res[0] == "form"
    assert len(res[1]["options"]) == 3  # clear sentinel + plan1 + plan2
    assert res[1]["default"] == "plan2"


def test_postcode_to_state():
    from custom_components.pricehawk.config_flow import _postcode_to_state

    assert _postcode_to_state("2000") == "NSW"
    assert _postcode_to_state("3000") == "VIC"
    assert _postcode_to_state("9999") == "QLD"
    assert _postcode_to_state("0000") is None
    assert _postcode_to_state("invalid") is None


def test_filter_plans_by_geography_detailed():
    from custom_components.pricehawk.config_flow import _filter_plans_by_geography

    plans = [
        {
            "displayName": "Plan A",
            "geography": {"includedPostcodes": ["2000", "2001"], "distributors": ["Ausgrid"]},
        },
        {"displayName": "Plan B VIC", "geography": {"distributors": ["Citipower"]}},
        {
            "displayName": "Plan C NSW Essential Energy",
        },
    ]
    # No filters
    assert len(_filter_plans_by_geography(plans)) == 3

    # Filter by postcode only (state is None)
    res = _filter_plans_by_geography(plans, postcode="2000")
    assert (
        len(res) == 3
    )  # Plan A (explicit postcode), Plan B & C (empty postcode list, state None, so loc_ok=True)

    # Filter by postcode + state
    res = _filter_plans_by_geography(plans, postcode="2000", state="NSW")
    assert (
        len(res) == 2
    )  # Plan A (explicit postcode match), Plan C NSW (fuzzy match NSW distributor Essential Energy)

    # Filter by postcode + state no geo matching
    res = _filter_plans_by_geography(plans, postcode="3000", state="NSW")
    assert len(res) == 1  # Plan C NSW only (fuzzy match NSW distributor Essential Energy)

    # Filter by state
    res = _filter_plans_by_geography(plans, state="VIC")
    assert len(res) == 1
    assert res[0]["displayName"] == "Plan B VIC"

    # Filter by distributor
    res = _filter_plans_by_geography(plans, distributor="Ausgrid")
    assert len(res) == 1
    assert res[0]["displayName"] == "Plan A"


def test_dedupe_plans_by_displayName():
    from custom_components.pricehawk.config_flow import _dedupe_plans_by_displayName

    plans = [
        {"displayName": "", "effectiveFrom": "2023-01-01"},
        {"displayName": "Plan A", "effectiveFrom": "2023-01-01"},
        {"displayName": "Plan A", "effectiveFrom": "2023-02-01"},
        {"displayName": "Plan B", "effectiveFrom": "2023-01-01"},
    ]
    res = _dedupe_plans_by_displayName(plans)
    assert len(res) == 2
    # Ensure it kept Plan A with effectiveFrom 2023-02-01
    plan_a = next(p for p in res if p["displayName"] == "Plan A")
    assert plan_a["effectiveFrom"] == "2023-02-01"


def test_api_provider_for_brand():
    from custom_components.pricehawk.config_flow import _api_provider_for_brand

    assert _api_provider_for_brand(None) is None
    assert _api_provider_for_brand("") is None
    assert _api_provider_for_brand("Amber Electric") == "amber"
    assert _api_provider_for_brand("Flow Power") == "flow_power"
    assert _api_provider_for_brand("Localvolts") == "localvolts"
    assert _api_provider_for_brand("Other brand") is None


def test_summarise_plan_v2_and_helpers():
    from custom_components.pricehawk.config_flow import (
        _summarise_cdr_plan,
        _summarise_controlled_load,
        _summarise_import_rate,
    )

    # 1. Test _summarise_cdr_plan with raw_supply None and non-dict tariffPeriod
    plan_detail = {
        "data": {
            "brandName": "Amber",
            "displayName": "Plan A",
            "effectiveFrom": "2023-01-01",
            "electricityContract": {
                "tariffPeriod": [
                    None,  # triggers non-dict check
                    {
                        "dailySupplyCharge": 1.20,
                        "rateBlockUType": "singleRate",
                        "singleRate": {"rates": [{"unitPrice": 0.25}]},
                    },
                ]
            },
        }
    }
    summary = _summarise_cdr_plan(plan_detail)
    assert summary["daily_supply"] == "132.00 c/day inc-GST"
    assert "27.5 c/kWh" in summary["import_rate"]

    # 2. Test _summarise_controlled_load with non-dict controlledLoad
    elec = {
        "controlledLoad": [
            None,  # triggers non-dict check
            {
                "displayName": "Hot Water",
                "rateBlockUType": "singleRate",
                "singleRate": {"rates": [{"unitPrice": 0.15}]},
            },
        ]
    }
    assert _summarise_controlled_load(elec) == "Hot Water: 16.5 c/kWh inc-GST"

    # 3. Test _summarise_import_rate empty rates and non-dict blocks and fallback singleRate
    elec_import = {
        "tariffPeriod": [
            {
                "rateBlockUType": "timeOfUseRates",
                "timeOfUseRates": [
                    None,  # triggers non-dict check
                    {"type": "Peak", "rates": []},  # triggers empty rates check
                    {"type": "Peak", "rates": [{"unitPrice": 0.30}]},
                ],
            }
        ]
    }
    assert _summarise_import_rate(elec_import) == "Peak 33.0 c/kWh inc-GST"

    # Test singleRate fallback raising TypeError/ValueError
    elec_single_bad = {"singleRate": {"rates": [{"unitPrice": "bad"}]}}
    assert _summarise_import_rate(elec_single_bad) == "?"

    # 4. Test _summarise_fit non-dict fit, invalid float, singleTariff failure, and timeVaryingTariffs
    elec_fit = {
        "solarFeedInTariff": [
            None,  # non-dict FIT
            {
                "tariffUType": "singleTariff",
                "singleTariff": {
                    "rates": [{"unitPrice": "invalid"}]
                },  # triggers ValueError in singleTariff
            },
            {
                "tariffUType": "timeVaryingTariffs",
                "timeVaryingTariffs": [
                    None,  # non-dict TOU FIT
                    {"type": "Peak", "rates": []},  # empty rates
                    {"type": "Peak", "rates": [{"unitPrice": "invalid"}]},  # invalid unitPrice
                    {"type": "Peak", "rates": [{"unitPrice": 0.08}]},
                ],
            },
        ]
    }
    assert _summarise_fit(elec_fit) == "Peak 8.8 c/kWh inc-GST"
