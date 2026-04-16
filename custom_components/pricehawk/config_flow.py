"""Config flow for PriceHawk integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_AMBER_NETWORK_DAILY_CHARGE,
    CONF_AMBER_SUBSCRIPTION_FEE,
    CONF_API_KEY,
    CONF_CURRENT_PROVIDER,
    CONF_DAILY_SUPPLY_CHARGE,
    CONF_DEMAND_CHARGE,
    CONF_HA_TOKEN,
    PROVIDER_AMBER,
    PROVIDER_GLOBIRD,
    CONF_EXPORT_TARIFF,
    CONF_GRID_POWER_SENSOR,
    CONF_IMPORT_TARIFF,
    CONF_INCENTIVES,
    CONF_PLAN_TYPE,
    CONF_SITE_ID,
    DEFAULT_TOU_IMPORT_WINDOWS,
    DOMAIN,
    EXPORT_WINDOWS,
    GLOBIRD_PLAN_DEFAULTS,
    PLAN_BOOST,
    PLAN_CUSTOM,
    PLAN_FOUR4FREE,
    PLAN_GLOSAVE,
    PLAN_ZEROHERO,
    TARIFF_FLAT_STEPPED,
    TARIFF_TOU,
)

_LOGGER = logging.getLogger(__name__)


class InvalidAuth(Exception):
    """Error to indicate invalid authentication."""


class CannotConnect(Exception):
    """Error to indicate connection failure."""


class NoActiveSites(Exception):
    """Error to indicate no active sites on the Amber account."""


async def fetch_amber_sites(hass: HomeAssistant, api_key: str) -> list[dict]:
    """Validate API key and return all sites from the Amber API."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with session.get(
            "https://api.amber.com.au/v1/sites",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status in (401, 403):
                raise InvalidAuth("Invalid API key")
            if resp.status != 200:
                raise CannotConnect(f"Amber API returned {resp.status}")
            data = await resp.json()
    except InvalidAuth:
        raise
    except Exception as err:
        raise CannotConnect from err

    if not data:
        raise NoActiveSites("No sites found on account")
    return data


# --- Selector helpers ---

def _number_selector(
    min_val: float = 0,
    max_val: float = 500,
    step: float = 0.01,
    unit: str = "c/kWh",
) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=min_val,
            max=max_val,
            step=step,
            unit_of_measurement=unit,
            mode=NumberSelectorMode.BOX,
        )
    )


PLAN_OPTIONS = [
    {"value": PLAN_ZEROHERO, "label": "ZEROHERO (TOU)"},
    {"value": PLAN_FOUR4FREE, "label": "FOUR4FREE (Two Rate, Stepped)"},
    {"value": PLAN_BOOST, "label": "BOOST (Flat Rate, Stepped)"},
    {"value": PLAN_GLOSAVE, "label": "GLOSAVE (Flat Rate, Stepped)"},
    {"value": PLAN_CUSTOM, "label": "Custom (manual entry)"},
]

TARIFF_TYPE_OPTIONS = [
    {"value": TARIFF_TOU, "label": "Time of Use (TOU)"},
    {"value": TARIFF_FLAT_STEPPED, "label": "Flat Rate (Stepped)"},
]


def _windows_to_str(windows: list[list[str]]) -> str:
    """Convert window list [["16:00","23:00"],["14:00","16:00"]] to '16:00-23:00, 14:00-16:00'."""
    return ", ".join(f"{w[0]}-{w[1]}" for w in windows)


def _str_to_windows(text: str) -> list[list[str]]:
    """Parse '16:00-23:00, 14:00-16:00' to [["16:00","23:00"],["14:00","16:00"]]."""
    windows = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            windows.append([start.strip(), end.strip()])
    return windows


def _time_to_minutes(t: str) -> int:
    """Convert 'HH:MM' to minutes since midnight."""
    parts = t.strip().split(":")
    return int(parts[0]) * 60 + int(parts[1])


def _expand_to_slots(windows: list[list[str]]) -> set[int]:
    """Expand time windows to a set of half-hour slots (0-47, handles midnight crossing)."""
    slots: set[int] = set()
    for w in windows:
        start = _time_to_minutes(w[0])
        end = _time_to_minutes(w[1])
        if end <= start:  # crosses midnight
            for m in range(start, 24 * 60, 30):
                slots.add(m // 30)
            for m in range(0, end, 30):
                slots.add(m // 30)
        else:
            for m in range(start, end, 30):
                slots.add(m // 30)
    return slots


def _windows_overlap(windows_a: list[list[str]], windows_b: list[list[str]]) -> bool:
    """Check if any time windows overlap (handles midnight crossing)."""
    return bool(_expand_to_slots(windows_a) & _expand_to_slots(windows_b))


def _validate_no_overlap(
    peak_str: str, shoulder_str: str, offpeak_str: str
) -> str | None:
    """Validate that peak, shoulder, offpeak windows don't overlap. Returns error key or None."""
    peak_w = _str_to_windows(peak_str)
    shoulder_w = _str_to_windows(shoulder_str)
    offpeak_w = _str_to_windows(offpeak_str)

    if _windows_overlap(peak_w, shoulder_w):
        return "peak_shoulder_overlap"
    if _windows_overlap(peak_w, offpeak_w):
        return "peak_offpeak_overlap"
    if _windows_overlap(shoulder_w, offpeak_w):
        return "shoulder_offpeak_overlap"
    return None


def _validate_full_coverage(
    peak_str: str, shoulder_str: str, offpeak_str: str
) -> bool:
    """Return True if peak + shoulder + offpeak windows cover all 48 half-hour slots."""
    all_slots = (
        _expand_to_slots(_str_to_windows(peak_str))
        | _expand_to_slots(_str_to_windows(shoulder_str))
        | _expand_to_slots(_str_to_windows(offpeak_str))
    )
    return all_slots == set(range(48))


def _get_tariff_type(plan_type: str) -> str:
    """Return the tariff type for a given plan."""
    if plan_type == PLAN_CUSTOM:
        return TARIFF_TOU  # default for custom, user picks in rates step
    defaults = GLOBIRD_PLAN_DEFAULTS.get(plan_type, {})
    return defaults.get("tariff_type", TARIFF_TOU)


def _build_import_tariff(
    tariff_type: str,
    user_input: dict[str, Any],
    plan_type: str,
) -> dict[str, Any]:
    """Build the import_tariff dict from user input."""
    if tariff_type == TARIFF_TOU:
        return {
            "type": TARIFF_TOU,
            "periods": {
                "peak": {
                    "rate": user_input["peak_rate"],
                    "windows": _str_to_windows(user_input.get("peak_windows", "")),
                },
                "shoulder": {
                    "rate": user_input["shoulder_rate"],
                    "windows": _str_to_windows(user_input.get("shoulder_windows", "")),
                },
                "offpeak": {
                    "rate": user_input["offpeak_rate"],
                    "windows": _str_to_windows(user_input.get("offpeak_windows", "")),
                },
            },
        }

    # flat_stepped
    return {
        "type": TARIFF_FLAT_STEPPED,
        "step1_threshold_kwh": user_input["step1_threshold_kwh"],
        "step1_rate": user_input["step1_rate"],
        "step2_rate": user_input["step2_rate"],
    }


def _build_export_tariff(
    user_input: dict[str, Any],
    plan_type: str,
) -> dict[str, Any]:
    """Build the export_tariff dict from user input."""
    return {
        "type": TARIFF_TOU,
        "periods": {
            "peak": {
                "rate": user_input["export_peak_rate"],
                "windows": _str_to_windows(user_input.get("export_peak_windows", "")),
            },
            "shoulder": {
                "rate": user_input["export_shoulder_rate"],
                "windows": _str_to_windows(user_input.get("export_shoulder_windows", "")),
            },
            "offpeak": {
                "rate": user_input["export_offpeak_rate"],
                "windows": _str_to_windows(user_input.get("export_offpeak_windows", "")),
            },
        },
    }


def _build_rates_schema(
    plan_type: str,
    tariff_type: str,
    defaults: dict[str, Any],
    current_import: dict[str, Any] | None = None,
    current_supply: float | None = None,
) -> dict[Any, Any]:
    """Build the import-rates schema fields shared by ConfigFlow and OptionsFlow.

    Args:
        plan_type: The selected GloBird plan identifier.
        tariff_type: 'tou' or 'flat_stepped'.
        defaults: Plan preset dict from GLOBIRD_PLAN_DEFAULTS.
        current_import: Existing import_tariff from config entry (options flow only).
        current_supply: Existing daily supply charge (options flow only).
    """
    schema_fields: dict[Any, Any] = {}

    # Daily supply charge
    supply_default = defaults.get("daily_supply_charge") or current_supply
    schema_fields[
        vol.Required(CONF_DAILY_SUPPLY_CHARGE, default=supply_default)
    ] = _number_selector(max_val=500, unit="c/day")

    # Demand charge
    demand_default = defaults.get("demand_charge", 0.0)
    if current_import is not None:
        # Options flow: prefer current value
        demand_default = current_import.get("demand_charge", demand_default)
    schema_fields[
        vol.Optional(CONF_DEMAND_CHARGE, default=demand_default)
    ] = _number_selector(max_val=500, unit="c/kW/day")

    ci = current_import or {}

    if plan_type == PLAN_CUSTOM:
        current_type = ci.get("type", TARIFF_TOU)
        schema_fields[
            vol.Required("tariff_type", default=current_type)
        ] = SelectSelector(
            SelectSelectorConfig(
                options=TARIFF_TYPE_OPTIONS,
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
        current_periods = ci.get("periods", {})
        peak_p = current_periods.get("peak", {})
        shoulder_p = current_periods.get("shoulder", {})
        offpeak_p = current_periods.get("offpeak", {})
        schema_fields[vol.Optional("peak_rate", default=peak_p.get("rate", 0.0))] = _number_selector()
        schema_fields[vol.Optional("peak_windows", default=_windows_to_str(peak_p.get("windows", DEFAULT_TOU_IMPORT_WINDOWS["peak"])))] = TextSelector(TextSelectorConfig())
        schema_fields[vol.Optional("shoulder_rate", default=shoulder_p.get("rate", 0.0))] = _number_selector()
        schema_fields[vol.Optional("shoulder_windows", default=_windows_to_str(shoulder_p.get("windows", DEFAULT_TOU_IMPORT_WINDOWS["shoulder"])))] = TextSelector(TextSelectorConfig())
        schema_fields[vol.Optional("offpeak_rate", default=offpeak_p.get("rate", 0.0))] = _number_selector()
        schema_fields[vol.Optional("offpeak_windows", default=_windows_to_str(offpeak_p.get("windows", DEFAULT_TOU_IMPORT_WINDOWS["offpeak"])))] = TextSelector(TextSelectorConfig())
        schema_fields[vol.Optional("step1_threshold_kwh", default=ci.get("step1_threshold_kwh", 0.0))] = _number_selector(max_val=100, unit="kWh/day")
        schema_fields[vol.Optional("step1_rate", default=ci.get("step1_rate", 0.0))] = _number_selector()
        schema_fields[vol.Optional("step2_rate", default=ci.get("step2_rate", 0.0))] = _number_selector()
    elif tariff_type == TARIFF_TOU:
        import_tariff = defaults.get("import_tariff", ci)
        periods = import_tariff.get("periods", {})
        peak_p = periods.get("peak", {})
        shoulder_p = periods.get("shoulder", {})
        offpeak_p = periods.get("offpeak", {})
        schema_fields[vol.Required("peak_rate", default=peak_p.get("rate"))] = _number_selector()
        schema_fields[vol.Required("peak_windows", default=_windows_to_str(peak_p.get("windows", [])))] = TextSelector(TextSelectorConfig())
        schema_fields[vol.Required("shoulder_rate", default=shoulder_p.get("rate"))] = _number_selector()
        schema_fields[vol.Required("shoulder_windows", default=_windows_to_str(shoulder_p.get("windows", [])))] = TextSelector(TextSelectorConfig())
        schema_fields[vol.Required("offpeak_rate", default=offpeak_p.get("rate"))] = _number_selector()
        schema_fields[vol.Required("offpeak_windows", default=_windows_to_str(offpeak_p.get("windows", [])))] = TextSelector(TextSelectorConfig())
    else:
        # Flat stepped
        schema_fields[vol.Required("step1_threshold_kwh", default=defaults.get("step1_threshold_kwh") or ci.get("step1_threshold_kwh"))] = _number_selector(max_val=100, unit="kWh/day")
        schema_fields[vol.Required("step1_rate", default=defaults.get("step1_rate") or ci.get("step1_rate"))] = _number_selector()
        schema_fields[vol.Required("step2_rate", default=defaults.get("step2_rate") or ci.get("step2_rate"))] = _number_selector()

    return schema_fields


def _build_export_schema(
    defaults: dict[str, Any],
    current_export: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build the export-rates schema shared by ConfigFlow and OptionsFlow.

    Args:
        defaults: Plan preset dict from GLOBIRD_PLAN_DEFAULTS.
        current_export: Existing export_tariff from config entry (options flow only).
    """
    export_tariff = defaults.get("export_tariff", current_export or {})
    periods = export_tariff.get("periods", {})
    peak_p = periods.get("peak", {})
    shoulder_p = periods.get("shoulder", {})
    offpeak_p = periods.get("offpeak", {})

    return vol.Schema(
        {
            vol.Required("export_peak_rate", default=peak_p.get("rate", 3.00)): _number_selector(),
            vol.Required("export_peak_windows", default=_windows_to_str(peak_p.get("windows", EXPORT_WINDOWS["peak"]))): TextSelector(TextSelectorConfig()),
            vol.Required("export_shoulder_rate", default=shoulder_p.get("rate", 0.10)): _number_selector(),
            vol.Required("export_shoulder_windows", default=_windows_to_str(shoulder_p.get("windows", EXPORT_WINDOWS["shoulder"]))): TextSelector(TextSelectorConfig()),
            vol.Required("export_offpeak_rate", default=offpeak_p.get("rate", 0.00)): _number_selector(),
            vol.Required("export_offpeak_windows", default=_windows_to_str(offpeak_p.get("windows", EXPORT_WINDOWS["offpeak"]))): TextSelector(TextSelectorConfig()),
        }
    )


def _build_incentives_schema(
    plan_type: str,
    current_incentives: dict[str, Any] | None = None,
) -> dict[Any, Any]:
    """Build the incentives schema fields shared by ConfigFlow and OptionsFlow.

    Args:
        plan_type: The selected GloBird plan identifier.
        current_incentives: Existing incentives dict (options flow only).
            When None, uses plan defaults (True for ZEROHERO, False for CUSTOM).
    """
    ci = current_incentives or {}
    schema_fields: dict[Any, Any] = {}

    # Default toggle values depend on whether we have current config or plan type
    if current_incentives is not None:
        # Options flow: use existing values as defaults
        zh_default = ci.get("zerohero_credit", plan_type == PLAN_ZEROHERO)
        se_default = ci.get("super_export", plan_type == PLAN_ZEROHERO)
    else:
        # Config flow: use plan-based defaults
        zh_default = plan_type == PLAN_ZEROHERO
        se_default = plan_type == PLAN_ZEROHERO

    schema_fields[vol.Required("zerohero_credit", default=zh_default)] = BooleanSelector()
    schema_fields[vol.Optional("zerohero_window_start", default=ci.get("zerohero_window_start", "18:00"))] = TextSelector(TextSelectorConfig())
    schema_fields[vol.Optional("zerohero_window_end", default=ci.get("zerohero_window_end", "21:00"))] = TextSelector(TextSelectorConfig())
    schema_fields[vol.Required("super_export", default=se_default)] = BooleanSelector()
    schema_fields[vol.Optional("super_export_cap_kwh", default=ci.get("super_export_cap_kwh", 15.0))] = _number_selector(min_val=1, max_val=50, step=0.5, unit="kWh")
    schema_fields[vol.Optional("super_export_window_start", default=ci.get("super_export_window_start", "18:00"))] = TextSelector(TextSelectorConfig())
    schema_fields[vol.Optional("super_export_window_end", default=ci.get("super_export_window_end", "21:00"))] = TextSelector(TextSelectorConfig())
    schema_fields[vol.Optional("super_export_rate", default=ci.get("super_export_rate", 15.0))] = _number_selector(max_val=100, step=0.1, unit="c/kWh")

    return schema_fields


class EnergyCompareConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PriceHawk."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialise flow."""
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1: Amber API key entry and validation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                _LOGGER.debug("Validating Amber API key")
                sites = await fetch_amber_sites(
                    self.hass, user_input[CONF_API_KEY]
                )
                _LOGGER.info(
                    "Amber API key validated, found %d site(s)", len(sites)
                )
                self._data[CONF_API_KEY] = user_input[CONF_API_KEY]
                self._data["_sites"] = sites

                # If only one site, auto-select it
                if len(sites) == 1:
                    site_id = sites[0]["id"]
                    self._data[CONF_SITE_ID] = site_id
                    await self.async_set_unique_id(site_id)
                    self._abort_if_unique_id_configured()
                    return await self.async_step_amber_fees()
                return await self.async_step_site_select()
            except InvalidAuth:
                _LOGGER.warning("Amber API key validation failed: invalid auth")
                errors[CONF_API_KEY] = "invalid_auth"
            except NoActiveSites:
                _LOGGER.warning("Amber API key valid but no active sites found")
                errors["base"] = "no_active_sites"
            except CannotConnect:
                _LOGGER.warning("Cannot connect to Amber API")
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_site_select(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1b: Select Amber site (when multiple sites exist)."""
        if user_input is not None:
            site_id = user_input[CONF_SITE_ID]
            self._data[CONF_SITE_ID] = site_id
            await self.async_set_unique_id(site_id)
            self._abort_if_unique_id_configured()
            return await self.async_step_amber_fees()

        sites = self._data.get("_sites", [])
        site_options = []
        for site in sites:
            nmi = site.get("nmi", "Unknown")
            status = site.get("status", "unknown")
            network = site.get("network", "")
            label = f"{nmi} ({status})"
            if network:
                label = f"{nmi} — {network} ({status})"
            site_options.append({"value": site["id"], "label": label})

        return self.async_show_form(
            step_id="site_select",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SITE_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=site_options,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_amber_fees(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1c: Amber fixed daily fees (network + subscription)."""
        if user_input is not None:
            self._data[CONF_AMBER_NETWORK_DAILY_CHARGE] = user_input.get(
                CONF_AMBER_NETWORK_DAILY_CHARGE, 0.0
            )
            self._data[CONF_AMBER_SUBSCRIPTION_FEE] = user_input.get(
                CONF_AMBER_SUBSCRIPTION_FEE, 0.0
            )
            return await self.async_step_globird_plan()

        return self.async_show_form(
            step_id="amber_fees",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_AMBER_NETWORK_DAILY_CHARGE, default=0.0
                    ): _number_selector(max_val=500, step=0.01, unit="c/day"),
                    vol.Optional(
                        CONF_AMBER_SUBSCRIPTION_FEE, default=0.0
                    ): _number_selector(max_val=500, step=0.01, unit="c/day"),
                }
            ),
        )

    async def async_step_globird_plan(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2: GloBird plan type selection."""
        if user_input is not None:
            plan_type = user_input[CONF_PLAN_TYPE]
            self._data[CONF_PLAN_TYPE] = plan_type

            # Load defaults for known plans
            if plan_type in GLOBIRD_PLAN_DEFAULTS:
                defaults = GLOBIRD_PLAN_DEFAULTS[plan_type]
                self._data["_defaults"] = defaults

            return await self.async_step_globird_rates()

        return self.async_show_form(
            step_id="globird_plan",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PLAN_TYPE): SelectSelector(
                        SelectSelectorConfig(
                            options=PLAN_OPTIONS,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_globird_rates(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 3: Import rates and daily supply charge."""
        plan_type = self._data[CONF_PLAN_TYPE]
        tariff_type = self._data.get("_tariff_type_override", _get_tariff_type(plan_type))
        defaults = self._data.get("_defaults", {})
        errors: dict[str, str] = {}

        if user_input is not None:
            if plan_type == PLAN_CUSTOM and "tariff_type" in user_input:
                tariff_type = user_input["tariff_type"]

            if tariff_type == TARIFF_TOU and "peak_windows" in user_input:
                overlap = _validate_no_overlap(
                    user_input.get("peak_windows", ""),
                    user_input.get("shoulder_windows", ""),
                    user_input.get("offpeak_windows", ""),
                )
                if overlap:
                    errors["base"] = overlap

            if tariff_type == TARIFF_TOU and "peak_windows" in user_input and not errors:
                if not _validate_full_coverage(
                    user_input.get("peak_windows", ""),
                    user_input.get("shoulder_windows", ""),
                    user_input.get("offpeak_windows", ""),
                ):
                    errors["base"] = "incomplete_tou_coverage"

            if not errors:
                self._data[CONF_DAILY_SUPPLY_CHARGE] = user_input[CONF_DAILY_SUPPLY_CHARGE]
                self._data[CONF_DEMAND_CHARGE] = user_input.get(CONF_DEMAND_CHARGE, 0.0)
                self._data[CONF_IMPORT_TARIFF] = _build_import_tariff(
                    tariff_type, user_input, plan_type
                )
                return await self.async_step_globird_export()

        schema_fields = _build_rates_schema(plan_type, tariff_type, defaults)

        return self.async_show_form(
            step_id="globird_rates",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
        )

    async def async_step_globird_export(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 4: Export/feed-in tariff rates."""
        plan_type = self._data[CONF_PLAN_TYPE]
        defaults = self._data.get("_defaults", {})

        if user_input is not None:
            self._data[CONF_EXPORT_TARIFF] = _build_export_tariff(
                user_input, plan_type
            )
            return await self.async_step_incentives()

        return self.async_show_form(
            step_id="globird_export",
            data_schema=_build_export_schema(defaults),
        )

    async def async_step_incentives(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 5: Incentive toggles."""
        plan_type = self._data[CONF_PLAN_TYPE]

        # Plans without engine-backed incentives — skip
        if plan_type not in (PLAN_ZEROHERO, PLAN_CUSTOM):
            self._data[CONF_INCENTIVES] = {}
            return await self.async_step_sensor_select()

        if user_input is not None:
            self._data[CONF_INCENTIVES] = user_input
            return await self.async_step_sensor_select()

        schema_fields = _build_incentives_schema(plan_type)

        return self.async_show_form(
            step_id="incentives",
            data_schema=vol.Schema(schema_fields),
        )

    async def async_step_sensor_select(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 6: Grid power sensor entity selection."""
        if user_input is not None:
            self._data[CONF_GRID_POWER_SENSOR] = user_input[CONF_GRID_POWER_SENSOR]
            return await self.async_step_dashboard_token()

        return self.async_show_form(
            step_id="sensor_select",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GRID_POWER_SENSOR): EntitySelector(
                        EntitySelectorConfig(domain="sensor")
                    ),
                }
            ),
        )

    async def async_step_dashboard_token(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 7: Current provider + HA access token for dashboard."""
        if user_input is not None:
            data = {
                CONF_API_KEY: self._data[CONF_API_KEY],
                CONF_SITE_ID: self._data[CONF_SITE_ID],
                CONF_HA_TOKEN: user_input.get(CONF_HA_TOKEN, ""),
                CONF_CURRENT_PROVIDER: user_input.get(CONF_CURRENT_PROVIDER, PROVIDER_AMBER),
            }
            options = {
                CONF_PLAN_TYPE: self._data[CONF_PLAN_TYPE],
                CONF_DAILY_SUPPLY_CHARGE: self._data[CONF_DAILY_SUPPLY_CHARGE],
                CONF_DEMAND_CHARGE: self._data.get(CONF_DEMAND_CHARGE, 0.0),
                CONF_IMPORT_TARIFF: self._data[CONF_IMPORT_TARIFF],
                CONF_EXPORT_TARIFF: self._data[CONF_EXPORT_TARIFF],
                CONF_INCENTIVES: self._data.get(CONF_INCENTIVES, {}),
                CONF_GRID_POWER_SENSOR: self._data[CONF_GRID_POWER_SENSOR],
                CONF_AMBER_NETWORK_DAILY_CHARGE: self._data.get(CONF_AMBER_NETWORK_DAILY_CHARGE, 0.0),
                CONF_AMBER_SUBSCRIPTION_FEE: self._data.get(CONF_AMBER_SUBSCRIPTION_FEE, 0.0),
            }
            _LOGGER.info("Creating PriceHawk config entry: plan=%s, provider=%s", options[CONF_PLAN_TYPE], data[CONF_CURRENT_PROVIDER])
            return self.async_create_entry(
                title="PriceHawk", data=data, options=options
            )

        return self.async_show_form(
            step_id="dashboard_token",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CURRENT_PROVIDER, default=PROVIDER_AMBER): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": PROVIDER_AMBER, "label": "Amber Electric"},
                                {"value": PROVIDER_GLOBIRD, "label": "GloBird Energy"},
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Optional(CONF_HA_TOKEN, default=""): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> EnergyCompareOptionsFlow:
        """Get the options flow handler."""
        return EnergyCompareOptionsFlow()


class EnergyCompareOptionsFlow(config_entries.OptionsFlowWithReload):
    """Handle options flow for PriceHawk (tariff editing)."""

    def __init__(self) -> None:
        """Initialise options flow."""
        super().__init__()
        self._data: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Entry point - show menu to edit API key or tariffs."""
        self._data = dict(self.config_entry.options)
        return self.async_show_menu(
            step_id="init",
            menu_options=["amber_api_key", "globird_plan", "amber_fees"],
        )

    async def async_step_amber_api_key(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Re-enter Amber API key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                sites = await fetch_amber_sites(
                    self.hass, user_input[CONF_API_KEY]
                )
                self._amber_key = user_input[CONF_API_KEY]
                self._amber_sites = sites

                if len(sites) == 1:
                    new_data = {**self.config_entry.data, CONF_API_KEY: self._amber_key, CONF_SITE_ID: sites[0]["id"]}
                    self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
                    return self.async_create_entry(data=self._data)
                return await self.async_step_options_site_select()
            except InvalidAuth:
                errors[CONF_API_KEY] = "invalid_auth"
            except NoActiveSites:
                errors["base"] = "no_active_sites"
            except CannotConnect:
                errors["base"] = "cannot_connect"

        current_key = self.config_entry.data.get(CONF_API_KEY, "")
        return self.async_show_form(
            step_id="amber_api_key",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY, default=current_key): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_options_site_select(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Select Amber site from options flow."""
        if user_input is not None:
            new_data = {**self.config_entry.data, CONF_API_KEY: self._amber_key, CONF_SITE_ID: user_input[CONF_SITE_ID]}
            self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
            return self.async_create_entry(data=self._data)

        sites = getattr(self, "_amber_sites", [])
        site_options = []
        current_site = self.config_entry.data.get(CONF_SITE_ID, "")
        for site in sites:
            nmi = site.get("nmi", "Unknown")
            status = site.get("status", "unknown")
            network = site.get("network", "")
            label = f"{nmi} ({status})"
            if network:
                label = f"{nmi} — {network} ({status})"
            site_options.append({"value": site["id"], "label": label})

        return self.async_show_form(
            step_id="options_site_select",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SITE_ID, default=current_site): SelectSelector(
                        SelectSelectorConfig(
                            options=site_options,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_amber_fees(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Edit Amber fixed daily fees (options)."""
        if user_input is not None:
            self._data[CONF_AMBER_NETWORK_DAILY_CHARGE] = user_input.get(
                CONF_AMBER_NETWORK_DAILY_CHARGE, 0.0
            )
            self._data[CONF_AMBER_SUBSCRIPTION_FEE] = user_input.get(
                CONF_AMBER_SUBSCRIPTION_FEE, 0.0
            )
            return self.async_create_entry(data=self._data)

        current_network = self._data.get(CONF_AMBER_NETWORK_DAILY_CHARGE, 0.0)
        current_sub = self._data.get(CONF_AMBER_SUBSCRIPTION_FEE, 0.0)

        return self.async_show_form(
            step_id="amber_fees",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_AMBER_NETWORK_DAILY_CHARGE, default=current_network
                    ): _number_selector(max_val=500, step=0.01, unit="c/day"),
                    vol.Optional(
                        CONF_AMBER_SUBSCRIPTION_FEE, default=current_sub
                    ): _number_selector(max_val=500, step=0.01, unit="c/day"),
                }
            ),
        )

    async def async_step_globird_plan(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Plan type selection (options)."""
        if user_input is not None:
            plan_type = user_input[CONF_PLAN_TYPE]
            self._data[CONF_PLAN_TYPE] = plan_type
            if plan_type in GLOBIRD_PLAN_DEFAULTS:
                self._data["_defaults"] = GLOBIRD_PLAN_DEFAULTS[plan_type]
            else:
                self._data.pop("_defaults", None)
            return await self.async_step_globird_rates()

        current_plan = self._data.get(CONF_PLAN_TYPE, PLAN_ZEROHERO)
        return self.async_show_form(
            step_id="globird_plan",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PLAN_TYPE, default=current_plan): SelectSelector(
                        SelectSelectorConfig(
                            options=PLAN_OPTIONS,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_globird_rates(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Import rates (options)."""
        plan_type = self._data[CONF_PLAN_TYPE]
        tariff_type = _get_tariff_type(plan_type)
        defaults = self._data.get("_defaults", {})
        errors: dict[str, str] = {}

        current_import = self._data.get(CONF_IMPORT_TARIFF, {})
        current_supply = self._data.get(CONF_DAILY_SUPPLY_CHARGE)

        if user_input is not None:
            if plan_type == PLAN_CUSTOM and "tariff_type" in user_input:
                tariff_type = user_input["tariff_type"]

            if tariff_type == TARIFF_TOU and "peak_windows" in user_input:
                overlap = _validate_no_overlap(
                    user_input.get("peak_windows", ""),
                    user_input.get("shoulder_windows", ""),
                    user_input.get("offpeak_windows", ""),
                )
                if overlap:
                    errors["base"] = overlap

            if tariff_type == TARIFF_TOU and "peak_windows" in user_input and not errors:
                if not _validate_full_coverage(
                    user_input.get("peak_windows", ""),
                    user_input.get("shoulder_windows", ""),
                    user_input.get("offpeak_windows", ""),
                ):
                    errors["base"] = "incomplete_tou_coverage"

            if not errors:
                self._data[CONF_DAILY_SUPPLY_CHARGE] = user_input[CONF_DAILY_SUPPLY_CHARGE]
                self._data[CONF_DEMAND_CHARGE] = user_input.get(CONF_DEMAND_CHARGE, 0.0)
                self._data[CONF_IMPORT_TARIFF] = _build_import_tariff(
                    tariff_type, user_input, plan_type
                )
                return await self.async_step_globird_export()

        # Options flow passes demand_charge via current_import for the shared builder
        options_import = dict(current_import)
        options_import["demand_charge"] = self._data.get(CONF_DEMAND_CHARGE, 0.0)
        schema_fields = _build_rates_schema(
            plan_type, tariff_type, defaults,
            current_import=options_import,
            current_supply=current_supply,
        )

        return self.async_show_form(
            step_id="globird_rates",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
        )

    async def async_step_globird_export(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Export rates (options)."""
        plan_type = self._data[CONF_PLAN_TYPE]
        defaults = self._data.get("_defaults", {})

        if user_input is not None:
            self._data[CONF_EXPORT_TARIFF] = _build_export_tariff(
                user_input, plan_type
            )
            return await self.async_step_incentives()

        return self.async_show_form(
            step_id="globird_export",
            data_schema=_build_export_schema(
                defaults,
                current_export=self._data.get(CONF_EXPORT_TARIFF, {}),
            ),
        )

    async def async_step_incentives(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Incentive toggles (options)."""
        plan_type = self._data[CONF_PLAN_TYPE]

        if plan_type not in (PLAN_ZEROHERO, PLAN_CUSTOM):
            self._data[CONF_INCENTIVES] = {}
            return await self.async_step_sensor_select()

        if user_input is not None:
            self._data[CONF_INCENTIVES] = user_input
            return await self.async_step_sensor_select()

        schema_fields = _build_incentives_schema(
            plan_type,
            current_incentives=self._data.get(CONF_INCENTIVES, {}),
        )

        return self.async_show_form(
            step_id="incentives",
            data_schema=vol.Schema(schema_fields),
        )

    async def async_step_sensor_select(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Grid power sensor selection (options)."""
        if user_input is not None:
            # Clean up internal keys
            self._data.pop("_defaults", None)

            options = {
                CONF_PLAN_TYPE: self._data[CONF_PLAN_TYPE],
                CONF_DAILY_SUPPLY_CHARGE: self._data[CONF_DAILY_SUPPLY_CHARGE],
                CONF_DEMAND_CHARGE: self._data.get(CONF_DEMAND_CHARGE, 0.0),
                CONF_IMPORT_TARIFF: self._data[CONF_IMPORT_TARIFF],
                CONF_EXPORT_TARIFF: self._data[CONF_EXPORT_TARIFF],
                CONF_INCENTIVES: self._data.get(CONF_INCENTIVES, {}),
                CONF_GRID_POWER_SENSOR: user_input[CONF_GRID_POWER_SENSOR],
                CONF_AMBER_NETWORK_DAILY_CHARGE: self._data.get(CONF_AMBER_NETWORK_DAILY_CHARGE, 0.0),
                CONF_AMBER_SUBSCRIPTION_FEE: self._data.get(CONF_AMBER_SUBSCRIPTION_FEE, 0.0),
            }
            return self.async_create_entry(data=options)

        current_sensor = self._data.get(CONF_GRID_POWER_SENSOR, "")
        return self.async_show_form(
            step_id="sensor_select",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_GRID_POWER_SENSOR, default=current_sensor
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                }
            ),
        )
