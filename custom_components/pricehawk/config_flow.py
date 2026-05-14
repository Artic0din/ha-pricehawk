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

from .cdr.cdr_client import (
    CdrAPIError,
    CdrPlanNotFound,
    CdrUnavailable,
    fetch_plan_detail,
    fetch_plan_list,
)
from .cdr.registry import (
    RetailerEndpoint,
    get_registry,
)
from .const import (
    CDR_SKIP_REASON_AFTER_ERROR,
    CDR_SKIP_REASON_NO_RETAILER,
    CDR_SKIP_REASON_RETRY_EXHAUSTED,
    CDR_SKIP_REASON_USER_AT_PLAN,
    CDR_SKIP_REASON_USER_AT_RETAILER,
    CONF_AMBER_ENABLED,
    CONF_AMBER_NETWORK_DAILY_CHARGE,
    CONF_AMBER_SUBSCRIPTION_FEE,
    CONF_API_KEY,
    CONF_CDR_PLAN,
    CONF_CDR_SKIP_REASON,
    CONF_CURRENT_PROVIDER,
    CONF_DAILY_SUPPLY_CHARGE,
    CONF_DEMAND_CHARGE,
    CONF_EXPORT_TARIFF,
    CONF_FLOW_POWER_BASE_RATE,
    CONF_FLOW_POWER_DAILY_SUPPLY,
    CONF_FLOW_POWER_ENABLED,
    CONF_FLOW_POWER_PEA_ENABLED,
    CONF_FLOW_POWER_PEA_OVERRIDE,
    CONF_FLOW_POWER_REGION,
    CONF_GRID_POWER_SENSOR,
    CONF_HA_TOKEN,
    CONF_IMPORT_TARIFF,
    CONF_INCENTIVES,
    CONF_LOCALVOLTS_API_KEY,
    CONF_LOCALVOLTS_BUY_CEILING,
    CONF_LOCALVOLTS_DAILY_SUPPLY,
    CONF_LOCALVOLTS_ENABLED,
    CONF_LOCALVOLTS_NMI,
    CONF_LOCALVOLTS_PARTNER_ID,
    CONF_LOCALVOLTS_SELL_FLOOR,
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
    PROVIDER_AMBER,
    PROVIDER_FLOW_POWER,
    PROVIDER_GLOBIRD,
    PROVIDER_LOCALVOLTS,
    TARIFF_FLAT_STEPPED,
    TARIFF_TOU,
)

import json as _json  # avoid colliding with any future `json` param names

# Sentinel value emitted by the CDR retailer dropdown when the user wants
# to bypass CDR and fill in rates manually. The empty-string convention
# matches HA select-selector idioms used elsewhere in the wizard.
CDR_SKIP_SENTINEL = "__manual__"
CDR_ANY_DISTRIBUTOR_SENTINEL = "__any__"
CONF_CDR_RETAILER_ID = "cdr_retailer_id"
CONF_CDR_POSTCODE = "cdr_postcode"
CONF_CDR_STATE = "cdr_state"
CONF_CDR_DISTRIBUTOR = "cdr_distributor"
CONF_CDR_PLAN_ID = "cdr_plan_id"
CONF_CDR_CONFIRM_ACTION = "cdr_confirm_action"
CONF_CDR_RETRY_ACTION = "cdr_retry_action"
CONF_CDR_OVERRIDE_JSON = "cdr_override_json"

# Phase 2.9 — confirmation step actions.
CDR_CONFIRM_ACCEPT = "accept"
CDR_CONFIRM_PICK_DIFFERENT = "pick_different"
CDR_CONFIRM_MANUAL = "manual"

# AU state-by-postcode ranges. Source: Australia Post — public ranges.
# ACT is a subset of the 2xxx postcode space; we test it BEFORE the NSW
# range so the ACT slice wins.
_AU_POSTCODE_TO_STATE: list[tuple[int, int, str]] = [
    (2600, 2618, "ACT"),
    (2900, 2920, "ACT"),
    (200, 299, "ACT"),    # PO boxes — legacy
    (1000, 2599, "NSW"),
    (2619, 2899, "NSW"),
    (2921, 2999, "NSW"),
    (3000, 3999, "VIC"),
    (8000, 8999, "VIC"),
    (4000, 4999, "QLD"),
    (9000, 9999, "QLD"),
    (5000, 5999, "SA"),
    (6000, 6797, "WA"),
    (6800, 6999, "WA"),
    (7000, 7999, "TAS"),
    (800, 999, "NT"),
]

# Free-text patterns that identify state names in retailer displayName
# strings. Matched case-insensitively. The first hit wins, so order by
# specificity (full names before abbreviations).
STATE_DISTRIBUTORS: dict[str, list[str]] = {
    "NSW": ["Ausgrid", "Endeavour", "Essential Energy"],
    "VIC": ["AusNet", "CitiPower", "Jemena", "Powercor", "United Energy"],
    "QLD": ["Energex", "Ergon"],
    "SA":  ["SA Power", "SAPN", "SA Power Networks"],
    "TAS": ["TasNetworks"],
    "ACT": ["Evoenergy", "ActewAGL"],
    "WA":  ["Western Power", "Horizon Power"],
    "NT":  ["Power and Water"],
}


def _postcode_to_state(postcode: str) -> str | None:
    """Map a 4-digit AU postcode to a state code. Returns ``None`` for
    invalid input (non-numeric, wrong length, unmapped range)."""
    s = postcode.strip()
    if not s.isdigit() or len(s) not in (3, 4):
        return None
    n = int(s)
    for lo, hi, state in _AU_POSTCODE_TO_STATE:
        if lo <= n <= hi:
            return state
    return None


def _filter_plans_by_geography(
    plans: list[dict[str, Any]],
    *,
    postcode: str | None = None,
    state: str | None = None,
    distributor: str | None = None,
) -> list[dict[str, Any]]:
    """Filter CDR plan list by ``geography.includedPostcodes`` and
    ``geography.distributors`` — fields the LIST endpoint actually
    returns per plan. Falls back to a fuzzy displayName match for
    retailers that omit ``geography`` entirely.

    Filter precedence (most specific first):
    1. ``postcode`` set → keep plans whose ``includedPostcodes`` contains
       it. If a plan has no geography block, fall back to displayName
       state-keyword match (best-effort).
    2. ``state`` set (postcode not) → keep plans whose ``distributors``
       intersect ``STATE_DISTRIBUTORS[state]`` OR plans whose
       ``includedPostcodes`` overlap the state's postcode range.
    3. ``distributor`` set (and not the "any" sentinel) → keep plans
       whose ``geography.distributors`` contains the exact name
       (case-insensitive). AND-ed with the locality filter.

    All filters skipped → return list unchanged.
    """
    if not postcode and not state and (
        distributor is None or distributor == CDR_ANY_DISTRIBUTOR_SENTINEL
    ):
        return list(plans)

    state_dists_upper: list[str] = []
    state_pc_ranges: list[tuple[int, int]] = []
    if state:
        state_dists_upper = [d.upper() for d in STATE_DISTRIBUTORS.get(state, [])]
        state_pc_ranges = [
            (lo, hi) for lo, hi, s in _AU_POSTCODE_TO_STATE if s == state
        ]

    dist_target = (
        distributor.lower()
        if distributor and distributor != CDR_ANY_DISTRIBUTOR_SENTINEL
        else None
    )

    out: list[dict[str, Any]] = []
    for p in plans:
        geo = p.get("geography") or {}
        included = geo.get("includedPostcodes") or []
        distributors = geo.get("distributors") or []
        name_upper = (p.get("displayName") or "").upper()

        # Locality (postcode > state).
        loc_ok = True
        if postcode:
            if included:
                loc_ok = postcode in included
            else:
                # No geography — best-effort displayName match.
                loc_ok = any(
                    k in name_upper for k in [
                        *(d.upper() for d in STATE_DISTRIBUTORS.get(state or "", []))
                    ]
                ) if state else True
        elif state:
            if distributors and state_dists_upper:
                loc_ok = any(d.upper() in state_dists_upper for d in distributors)
            elif included and state_pc_ranges:
                loc_ok = any(
                    lo <= int(pc) <= hi
                    for pc in included if pc.isdigit()
                    for lo, hi in state_pc_ranges
                )
            else:
                # No geography on plan — fall back to displayName.
                loc_ok = any(k in name_upper for k in [
                    state.upper(),
                    *(d.upper() for d in STATE_DISTRIBUTORS.get(state, [])),
                ])

        # Distributor (additional AND).
        dist_ok = True
        if dist_target:
            if distributors:
                dist_ok = any(dist_target in d.lower() for d in distributors)
            else:
                dist_ok = dist_target in (p.get("displayName") or "").lower()

        if loc_ok and dist_ok:
            out.append(p)
    return out


def _dedupe_plans_by_displayName(
    plans: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse plans sharing a ``displayName`` into one entry per name.
    Keeps the entry with the most recent ``effectiveFrom`` so the user
    picks the LATEST revision of each plan shape.

    AGL ships 4-6× variants per displayName (cohort splits across
    distributors); this turns 67 plans into ~16 unique shapes per the
    UAT cascade.
    """
    by_name: dict[str, dict[str, Any]] = {}
    for p in plans:
        name = (p.get("displayName") or "").strip()
        if not name:
            continue
        eff = str(p.get("effectiveFrom") or "")
        existing = by_name.get(name)
        if existing is None or eff > str(existing.get("effectiveFrom") or ""):
            by_name[name] = p
    return list(by_name.values())


def _build_state_options() -> list[dict[str, str]]:
    """HA dropdown options for the 7 AU electricity-network states + skip."""
    return [
        {"value": CDR_SKIP_SENTINEL, "label": "Skip filter — show all plans"},
        {"value": "NSW", "label": "New South Wales"},
        {"value": "VIC", "label": "Victoria"},
        {"value": "QLD", "label": "Queensland"},
        {"value": "SA",  "label": "South Australia"},
        {"value": "TAS", "label": "Tasmania"},
        {"value": "ACT", "label": "Australian Capital Territory"},
        {"value": "WA",  "label": "Western Australia"},
    ]


def _build_distributor_options(state: str | None) -> list[dict[str, str]]:
    """Distributors for a given state, plus an "Any distributor" sentinel.
    If ``state`` is None or unknown, returns just the Any sentinel."""
    options: list[dict[str, str]] = [
        {"value": CDR_ANY_DISTRIBUTOR_SENTINEL, "label": "Any distributor (skip filter)"}
    ]
    if state and state in STATE_DISTRIBUTORS:
        options.extend(
            {"value": d, "label": d} for d in STATE_DISTRIBUTORS[state]
        )
    return options

# CDR retry action values (Phase 2.3)
CDR_RETRY_ACTION_RETRY = "retry"
CDR_RETRY_ACTION_SKIP = "skip"

# Cap the number of automatic retries the user can request before the
# wizard forces a fall-through. Two retries is enough to ride out a brief
# DNS hiccup but not enough to wedge a stubborn user against a permanently
# offline retailer DH.
CDR_MAX_RETRIES = 2

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
    try:
        parts = t.strip().split(":")
        h = int(parts[0])
        m = int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError("Time out of range")
        return h * 60 + m
    except (ValueError, IndexError):
        _LOGGER.debug("Invalid time format: %s", t)
        return 0


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


# ---------------------------------------------------------------------------
# CDR wizard helpers (Phase 2.2 — pure-Python; unit-testable without HA)
# ---------------------------------------------------------------------------


def _build_cdr_retailer_options(
    endpoints: list[RetailerEndpoint],
) -> list[dict[str, str]]:
    """Convert a list of RetailerEndpoint into HA SelectSelector option dicts.

    The "manual entry" sentinel is always offered first so users can opt
    out of CDR when their retailer is missing or they prefer hand-entry.
    """
    sorted_eps = sorted(endpoints, key=lambda e: e.brand_name.lower())
    options: list[dict[str, str]] = [
        {"value": CDR_SKIP_SENTINEL, "label": "Skip CDR — enter rates manually"}
    ]
    options.extend(
        {"value": e.brand_id, "label": e.brand_name} for e in sorted_eps
    )
    return options


def _deep_merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``overlay`` onto ``base`` and return a new dict.

    Lists in ``overlay`` REPLACE the corresponding list in ``base`` (not
    concatenate) — appending fragments would silently distort schemas
    like `timeOfUse` windows. Scalars in ``overlay`` replace scalars.
    Nested dicts recurse. Keys only in ``base`` survive unchanged.

    Pure function — does not mutate inputs. Designed for the Phase 2.5
    override branch where a CDR PlanDetailV2 envelope is patched with a
    user-supplied JSON fragment.
    """
    out: dict[str, Any] = dict(base)
    for k, v in overlay.items():
        if (
            k in out
            and isinstance(out[k], dict)
            and isinstance(v, dict)
        ):
            out[k] = _deep_merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def _summarise_cdr_plan(detail: dict[str, Any]) -> dict[str, str]:
    """Phase 2.9 — Distil a CDR PlanDetailV2 envelope into human-readable
    strings the confirmation form renders via description_placeholders.

    Returned dict keys MUST match placeholder names in strings.json:
    ``brand``, ``plan_name``, ``effective``, ``daily_supply``,
    ``import_rate``, ``feed_in``, ``incentives``. All values are strings
    (HA placeholder substitution does not coerce).

    Designed for the UI summary only — not a substitute for the full
    evaluator. The rate fields collapse multiple tariff periods to a
    single representative line ("Peak 39.6 / Shoulder 27.5 / OffPeak 0
    c/kWh inc-GST" or "Flat 33 c/kWh inc-GST").
    """
    data = detail.get("data") if isinstance(detail, dict) else None
    if not isinstance(data, dict):
        return {
            "brand": "?", "plan_name": "?", "effective": "?",
            "daily_supply": "?", "import_rate": "?", "feed_in": "?",
            "incentives": "?",
        }

    brand = data.get("brandName") or data.get("brand") or "?"
    plan_name = data.get("displayName") or "?"
    effective = data.get("effectiveFrom") or "?"
    if effective != "?":
        effective = str(effective)[:10]

    elec = data.get("electricityContract") or {}

    # Daily supply charge — CDR spec puts this at electricityContract.dailySupplyCharges
    # but actual retailer JSON varies wildly:
    # - AGL: per-tariffPeriod ``dailySupplyCharge`` (singular)
    # - GloBird: omitted entirely (must come from PDF override)
    # - Origin/EnergyAustralia: ``dailySupplyCharges`` (plural, top-level)
    # Probe each location until something hits.
    raw_supply: Any = elec.get("dailySupplyCharges") or elec.get("dailySupplyCharge")
    if raw_supply is None:
        for tp in elec.get("tariffPeriod") or []:
            if not isinstance(tp, dict):
                continue
            cand = tp.get("dailySupplyCharge") or tp.get("dailySupplyCharges")
            if cand:
                raw_supply = cand
                break
    try:
        daily_supply = (
            f"{float(raw_supply) * 110:.2f} c/day inc-GST"
            if raw_supply is not None and str(raw_supply).strip() != ""
            else "not published"
        )
    except (TypeError, ValueError):
        daily_supply = "?"

    # Import-rate summary — peek inside tariffPeriod[].rates[] if present
    # (TOU), otherwise look for singleRate (flat). Rates in CDR are
    # ex-GST $/kWh; multiply by 110 to get inc-GST cents.
    import_rate = _summarise_import_rate(elec)
    feed_in = _summarise_fit(elec)

    incentives = elec.get("incentives") or []
    if incentives:
        # Show every incentive — the user is verifying the plan against
        # their bill, so hidden incentives defeat the purpose.
        names = [i.get("displayName") or "?" for i in incentives]
        incentives_str = ", ".join(names)
    else:
        incentives_str = "none"

    return {
        "brand": str(brand),
        "plan_name": str(plan_name),
        "effective": effective,
        "daily_supply": daily_supply,
        "import_rate": import_rate,
        "feed_in": feed_in,
        "incentives": incentives_str,
    }


def _summarise_import_rate(elec: dict[str, Any]) -> str:
    """Walk TOU first, then flat. Return a 1-line human summary in
    inc-GST cents/kWh. Returns ``"?"`` if no rate found.

    CDR PlanDetailV2 puts rates inside ``tariffPeriod[].{rateBlockUType}[]``
    where ``rateBlockUType`` is one of ``timeOfUseRates``, ``singleRate``,
    ``flexibleRate``, ``demandCharges``, etc. Each entry has a ``type``
    label and a ``rates[]`` array with ``unitPrice`` strings ex-GST per
    kWh. The legacy path of ``tariffPeriod[].rates[]`` direct also
    works for retailers that simplified their schema.
    """
    tariff_periods = elec.get("tariffPeriod") or []
    if isinstance(tariff_periods, list) and tariff_periods:
        entries: list[tuple[str, str]] = []
        for p in tariff_periods:
            if not isinstance(p, dict):
                continue
            # Resolve which nested key holds the rates. CDR shape varies:
            # - timeOfUseRates / flexibleRate / blockTariff → LIST of blocks
            # - singleRate / demandCharges → DICT (one block)
            block_key = p.get("rateBlockUType")
            blocks: list = []
            block_val = p.get(block_key) if block_key else None
            if isinstance(block_val, list):
                blocks = block_val
            elif isinstance(block_val, dict):
                # Single-block shape — wrap so the loop below stays uniform.
                blocks = [{
                    "type": block_val.get("type") or block_val.get("displayName") or "FLAT",
                    "rates": block_val.get("rates") or [],
                }]
            elif p.get("timeOfUseRates"):
                blocks = p["timeOfUseRates"]
            elif p.get("rates"):
                blocks = [{"type": p.get("type") or p.get("displayName") or "?", "rates": p["rates"]}]

            for b in blocks:
                if not isinstance(b, dict):
                    continue
                tname = (b.get("type") or b.get("displayName") or "?").strip()
                rates = b.get("rates") or []
                if not rates:
                    continue
                try:
                    r = float(rates[0].get("unitPrice", 0))
                    entries.append((tname, f"{r * 110:.1f}"))
                except (TypeError, ValueError, IndexError, AttributeError):
                    continue
        if entries:
            return " / ".join(f"{n} {r}" for n, r in entries) + " c/kWh inc-GST"

    single = elec.get("singleRate") or {}
    rates = single.get("rates") or []
    if rates:
        try:
            r = float(rates[0].get("unitPrice", 0))
            return f"Flat {r * 110:.2f} c/kWh inc-GST"
        except (TypeError, ValueError, AttributeError):
            return "?"
    return "?"


def _summarise_fit(elec: dict[str, Any]) -> str:
    """Solar feed-in summary across all blocks. Returns ``"none"`` if no
    FIT published.

    CDR shape variations:
    - ``singleTariff`` (one flat rate) → "5.50 c/kWh inc-GST"
    - ``timeVaryingTariffs`` (TOU FIT, e.g. GloBird Combo) → walks
      each PEAK/SHOULDER/OFF_PEAK entry → "PEAK 3.3 / SHOULDER 0.1 c/kWh inc-GST"
    - Multiple FIT blocks (RETAILER + GOVERNMENT) → summed
    """
    fits = elec.get("solarFeedInTariff") or []
    if not isinstance(fits, list) or not fits:
        return "none"

    parts: list[str] = []
    for f in fits:
        if not isinstance(f, dict):
            continue
        u_type = f.get("tariffUType")

        # singleTariff: one flat rate
        if u_type == "singleTariff" or f.get("singleTariff"):
            single = (f.get("singleTariff") or {}).get("rates") or []
            if single:
                try:
                    r = float(single[0].get("unitPrice", 0))
                    parts.append(f"{r * 110:.2f}")
                except (TypeError, ValueError, AttributeError):
                    pass
            continue

        # timeVaryingTariffs: walk each TOU period
        if u_type == "timeVaryingTariffs" or f.get("timeVaryingTariffs"):
            tou = f.get("timeVaryingTariffs") or []
            tou_entries: list[str] = []
            for t in tou:
                if not isinstance(t, dict):
                    continue
                tname = (t.get("type") or t.get("displayName") or "?").strip()
                rates = t.get("rates") or []
                if not rates:
                    continue
                try:
                    r = float(rates[0].get("unitPrice", 0))
                    tou_entries.append(f"{tname} {r * 110:.1f}")
                except (TypeError, ValueError, AttributeError):
                    continue
            if tou_entries:
                parts.append(" / ".join(tou_entries))
            continue

    if parts:
        return " + ".join(parts) + " c/kWh inc-GST"
    return "none"


def _parse_override_json(text: str) -> dict[str, Any] | None:
    """Parse a user-pasted JSON fragment. Returns parsed dict or ``None``
    for empty/whitespace input. Raises ``ValueError`` if the text is
    syntactically invalid or doesn't parse to a dict.
    """
    stripped = text.strip()
    if not stripped:
        return None
    parsed = _json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("override JSON must parse to an object/dict at root")
    return parsed


def _build_cdr_plan_options(
    plans: list[dict[str, Any]],
    *,
    dedupe: bool = True,
) -> list[dict[str, str]]:
    """Convert a CDR list response's ``plans`` array into dropdown options.

    Filters to entries with both ``planId`` and ``displayName`` populated.
    When ``dedupe`` is True (default) collapses 4-6× cohort variants per
    displayName via ``_dedupe_plans_by_displayName`` so the user sees
    one row per plan shape, not 67 for AGL+postcode 3977.

    Sorts by ``displayName`` lower-case for stable wizard ordering. Label
    appends ``effectiveFrom`` date sliced to YYYY-MM-DD.
    """
    usable = [
        p
        for p in plans
        if p.get("planId") and p.get("displayName")
    ]
    if dedupe:
        usable = _dedupe_plans_by_displayName(usable)
    usable.sort(key=lambda p: p["displayName"].lower())
    return [
        {
            "value": p["planId"],
            "label": (
                f"{p['displayName']} (eff {(p.get('effectiveFrom') or '?')[:10]})"
            ),
        }
        for p in usable
    ]


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
        """Step 1: ask the user who their current energy retailer is.

        The selection drives savings calculations and determines whether
        provider-specific credential steps (Amber API key, LocalVolts API
        key) are needed up-front. Other providers are auto-enabled as
        comparators with default settings the user can refine later.
        """
        if user_input is not None:
            self._data[CONF_CURRENT_PROVIDER] = user_input[CONF_CURRENT_PROVIDER]
            choice = user_input[CONF_CURRENT_PROVIDER]
            if choice == PROVIDER_AMBER:
                return await self.async_step_amber_credentials()
            if choice == PROVIDER_LOCALVOLTS:
                return await self.async_step_localvolts_credentials()
            if choice == PROVIDER_FLOW_POWER:
                return await self.async_step_flow_power_credentials()
            # GloBird primary needs no upfront credentials; the next step
            # is the CDR plan picker which (on success) skips the manual
            # GloBird tariff entry path.
            return await self.async_step_cdr_retailer()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CURRENT_PROVIDER, default=PROVIDER_AMBER
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": PROVIDER_AMBER, "label": "Amber Electric"},
                                {"value": PROVIDER_GLOBIRD, "label": "GloBird Energy"},
                                {"value": PROVIDER_FLOW_POWER, "label": "Flow Power"},
                                {"value": PROVIDER_LOCALVOLTS, "label": "LocalVolts"},
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_amber_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Amber API key entry and validation. Reached only when the user
        picks Amber as their current provider, OR opts to add Amber as a
        comparator from a later step.
        """
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
            step_id="amber_credentials",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_flow_power_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Flow Power region + base rate + supply charge.

        Wholesale spot price is sourced from AEMO NEMWeb (no Amber API key
        needed). Only invoked when the user picks Flow Power as their
        current provider.
        """
        if user_input is not None:
            self._data[CONF_FLOW_POWER_ENABLED] = True
            self._data[CONF_FLOW_POWER_REGION] = user_input[
                CONF_FLOW_POWER_REGION
            ]
            self._data[CONF_FLOW_POWER_BASE_RATE] = user_input[
                CONF_FLOW_POWER_BASE_RATE
            ]
            self._data[CONF_FLOW_POWER_DAILY_SUPPLY] = user_input[
                CONF_FLOW_POWER_DAILY_SUPPLY
            ]
            self._data[CONF_FLOW_POWER_PEA_ENABLED] = user_input[
                CONF_FLOW_POWER_PEA_ENABLED
            ]
            await self.async_set_unique_id(
                f"flow_power_{user_input[CONF_FLOW_POWER_REGION]}"
            )
            self._abort_if_unique_id_configured()
            return await self.async_step_cdr_retailer()

        return self.async_show_form(
            step_id="flow_power_credentials",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_FLOW_POWER_REGION, default="NSW1"
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=["NSW1", "QLD1", "VIC1", "SA1", "TAS1"],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_FLOW_POWER_BASE_RATE, default=34.0
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=0.1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_FLOW_POWER_DAILY_SUPPLY, default=100.0
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=500,
                            step=0.1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_FLOW_POWER_PEA_ENABLED, default=True
                    ): BooleanSelector(),
                }
            ),
        )

    async def async_step_localvolts_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """LocalVolts API key + partner + NMI entry. Only collected when
        the user picks LocalVolts as their current provider.
        """
        if user_input is not None:
            self._data[CONF_LOCALVOLTS_ENABLED] = True
            self._data[CONF_LOCALVOLTS_API_KEY] = user_input[
                CONF_LOCALVOLTS_API_KEY
            ]
            self._data[CONF_LOCALVOLTS_PARTNER_ID] = user_input[
                CONF_LOCALVOLTS_PARTNER_ID
            ]
            self._data[CONF_LOCALVOLTS_NMI] = user_input[CONF_LOCALVOLTS_NMI]
            self._data[CONF_LOCALVOLTS_DAILY_SUPPLY] = user_input[
                CONF_LOCALVOLTS_DAILY_SUPPLY
            ]
            await self.async_set_unique_id(
                f"localvolts_{user_input[CONF_LOCALVOLTS_NMI]}"
            )
            self._abort_if_unique_id_configured()
            return await self.async_step_cdr_retailer()

        return self.async_show_form(
            step_id="localvolts_credentials",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOCALVOLTS_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Required(CONF_LOCALVOLTS_PARTNER_ID): TextSelector(),
                    vol.Required(CONF_LOCALVOLTS_NMI): TextSelector(),
                    vol.Required(
                        CONF_LOCALVOLTS_DAILY_SUPPLY, default=110.0
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=500,
                            step=0.1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
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
            return await self.async_step_cdr_retailer()

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

    async def _cdr_route_error(
        self, kind: str, detail: str
    ) -> config_entries.ConfigFlowResult:
        """Stash error context and route to the retry form. Used by both
        retailer and plan-select steps so they share a single error UI."""
        self._data["_cdr_error_kind"] = kind
        self._data["_cdr_error_detail"] = detail
        return await self.async_step_cdr_error()

    async def async_step_cdr_retailer(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Phase 2.2 — CDR happy-path entry. Show retailer dropdown sourced
        from the live jxeeno registry (with baked-in fallback). The "Skip
        CDR" sentinel routes to the legacy manual GloBird flow so v1.4.x
        behaviour is preserved for users whose retailer is not in CDR.

        On registry-load failure, routes to async_step_cdr_error (Phase
        2.3) so the user can retry or pick "Skip" deliberately.
        """
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        if user_input is not None:
            choice = user_input[CONF_CDR_RETAILER_ID]
            if choice == CDR_SKIP_SENTINEL:
                _LOGGER.debug("CDR skipped by user; routing to manual GloBird flow")
                self._data["_cdr_skip_reason"] = CDR_SKIP_REASON_USER_AT_RETAILER
                return await self.async_step_globird_plan()
            # Find the chosen endpoint in the registry we already loaded.
            endpoints: list[RetailerEndpoint] = self._data.get(
                "_cdr_endpoints", []
            )
            picked = next((e for e in endpoints if e.brand_id == choice), None)
            if picked is None:
                # Shouldn't happen — dropdown values come from the same list.
                _LOGGER.warning(
                    "CDR retailer %s not in cached endpoints; falling through",
                    choice,
                )
                self._data["_cdr_skip_reason"] = CDR_SKIP_REASON_NO_RETAILER
                return await self.async_step_globird_plan()
            self._data["_cdr_retailer"] = picked
            return await self.async_step_cdr_locale()

        # First entry into the step: load registry.
        try:
            session = async_get_clientsession(self.hass)
            endpoints, source = await get_registry(session)
            _LOGGER.info(
                "CDR registry loaded (%s): %d retailers", source, len(endpoints)
            )
        except Exception as err:  # noqa: BLE001 — see _cdr_route_error
            _LOGGER.warning(
                "CDR registry load failed (%s); routing to retry form", err,
            )
            return await self._cdr_route_error("registry", str(err))

        # Stash endpoints so the second pass through this step (after user
        # input) can resolve the chosen brand_id without re-fetching.
        self._data["_cdr_endpoints"] = endpoints
        options = _build_cdr_retailer_options(endpoints)

        return self.async_show_form(
            step_id="cdr_retailer",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CDR_RETAILER_ID, default=CDR_SKIP_SENTINEL
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_cdr_locale(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Phase 2.8 — Narrow the plan list by AU state or postcode.

        Big retailers (GloBird, AGL, Origin) publish hundreds of plans
        across every distributor; an unfiltered dropdown is unusable.
        This step asks for a postcode (4-digit) OR a state code. The
        postcode is mapped to a state via ``_postcode_to_state``; if
        both are provided, the explicit state field wins.

        Skipping (empty postcode + ``CDR_SKIP_SENTINEL`` state) bypasses
        the filter and shows all plans — useful for users whose plan
        lives outside the keyword patterns we know.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            postcode = (user_input.get(CONF_CDR_POSTCODE) or "").strip()
            state_choice = user_input.get(CONF_CDR_STATE, CDR_SKIP_SENTINEL)

            resolved_state: str | None = None
            if state_choice and state_choice != CDR_SKIP_SENTINEL:
                resolved_state = state_choice
            elif postcode:
                resolved_state = _postcode_to_state(postcode)
                if resolved_state is None:
                    errors[CONF_CDR_POSTCODE] = "cdr_invalid_postcode"

            if not errors:
                self._data["_cdr_state"] = resolved_state  # may be None = skip
                # Phase 2.10: stash the postcode so the geography filter
                # can match per-plan ``includedPostcodes`` precisely.
                self._data["_cdr_postcode"] = postcode if postcode else None
                return await self.async_step_cdr_distributor()

        return self.async_show_form(
            step_id="cdr_locale",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_CDR_POSTCODE, default=""): TextSelector(
                        TextSelectorConfig()
                    ),
                    vol.Optional(
                        CONF_CDR_STATE, default=CDR_SKIP_SENTINEL
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=_build_state_options(),
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_cdr_distributor(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Phase 2.8 — Pick a distributor (network operator) inside the
        chosen state. Skipping (``CDR_ANY_DISTRIBUTOR_SENTINEL``) keeps
        the state-only filter; the plan_select step still narrows the
        list to plans whose displayName contains the state code or any
        distributor known for that state.

        If no state was set (user skipped locale), this step short-
        circuits straight to plan select with no filter.
        """
        state: str | None = self._data.get("_cdr_state")
        if state is None:
            # No state was selected — skip distributor entirely.
            self._data["_cdr_distributor"] = None
            return await self.async_step_cdr_plan_select()

        if user_input is not None:
            choice = user_input[CONF_CDR_DISTRIBUTOR]
            self._data["_cdr_distributor"] = (
                None if choice == CDR_ANY_DISTRIBUTOR_SENTINEL else choice
            )
            return await self.async_step_cdr_plan_select()

        return self.async_show_form(
            step_id="cdr_distributor",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CDR_DISTRIBUTOR,
                        default=CDR_ANY_DISTRIBUTOR_SENTINEL,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=_build_distributor_options(state),
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            description_placeholders={"state": state},
        )

    async def async_step_cdr_plan_select(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Phase 2.2 — CDR plan dropdown for the selected retailer. On
        selection, fetches PlanDetailV2 and stores it as ``CONF_CDR_PLAN``
        in ``self._data``; the coordinator picks `CdrGloBirdProvider`
        whenever this key is set.

        Phase 2.3 — list-fetch and detail-fetch failures now route to
        async_step_cdr_error so the user can retry or skip deliberately.

        Phase 2.8 — list is post-filtered by stored state + distributor.
        If 0 matches after filtering, falls back to the unfiltered list
        with a log warning so the user is never blocked.
        """
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        retailer: RetailerEndpoint | None = self._data.get("_cdr_retailer")
        if retailer is None:
            # Step entered without a retailer choice — bail to manual.
            self._data["_cdr_skip_reason"] = CDR_SKIP_REASON_NO_RETAILER
            return await self.async_step_globird_plan()

        if user_input is not None:
            chosen_plan_id = user_input[CONF_CDR_PLAN_ID]
            if chosen_plan_id == CDR_SKIP_SENTINEL:
                self._data["_cdr_skip_reason"] = CDR_SKIP_REASON_USER_AT_PLAN
                return await self.async_step_globird_plan()
            try:
                session = async_get_clientsession(self.hass)
                detail = await fetch_plan_detail(
                    session, retailer.base_uri, chosen_plan_id
                )
            except (CdrPlanNotFound, CdrUnavailable, CdrAPIError) as err:
                _LOGGER.warning(
                    "CDR detail fetch failed for %s/%s (%s); routing to retry",
                    retailer.brand_name, chosen_plan_id, err,
                )
                return await self._cdr_route_error("detail", str(err))
            self._data[CONF_CDR_PLAN] = detail
            _LOGGER.info(
                "CDR plan selected: %s / %s — routing to confirm step",
                retailer.brand_name, chosen_plan_id,
            )
            # Phase 2.9: confirmation screen before commit. User sees the
            # actual rates/incentives this plan publishes and can back out
            # to pick a different plan or fall through to manual entry if
            # nothing matches.
            return await self.async_step_cdr_confirm()

        # First entry — fetch list.
        try:
            session = async_get_clientsession(self.hass)
            plans = await fetch_plan_list(session, retailer.base_uri)
        except (CdrUnavailable, CdrAPIError) as err:
            _LOGGER.warning(
                "CDR list fetch failed for %s (%s); routing to retry",
                retailer.brand_name, err,
            )
            return await self._cdr_route_error("list", str(err))

        # Phase 2.8 + 2.10 — narrow the list by geography (postcode +
        # state + distributor matched against `geography.includedPostcodes`
        # and `geography.distributors` from the CDR list response). Empty
        # filter falls back to unfiltered with a warning so the wizard
        # never blocks even on retailers that publish no geography.
        postcode = self._data.get("_cdr_postcode")
        state = self._data.get("_cdr_state")
        distributor = self._data.get("_cdr_distributor")
        filtered = _filter_plans_by_geography(
            plans,
            postcode=postcode,
            state=state,
            distributor=distributor,
        )
        if filtered:
            plans_to_show = filtered
            _LOGGER.info(
                "CDR plan list narrowed: %d/%d match postcode=%s state=%s distributor=%s",
                len(filtered), len(plans), postcode, state, distributor,
            )
        else:
            plans_to_show = plans
            _LOGGER.warning(
                "CDR filter (postcode=%s state=%s distributor=%s) matched 0 plans; "
                "showing unfiltered list (%d plans)",
                postcode, state, distributor, len(plans),
            )

        options = _build_cdr_plan_options(plans_to_show)
        if not options:
            _LOGGER.info(
                "CDR list for %s returned 0 usable plans; routing to retry",
                retailer.brand_name,
            )
            return await self._cdr_route_error("empty", "0 usable plans")

        # Prepend "Skip" sentinel so the user can back out without errors.
        options = [
            {"value": CDR_SKIP_SENTINEL, "label": "Skip — enter rates manually"}
        ] + options

        return self.async_show_form(
            step_id="cdr_plan_select",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CDR_PLAN_ID, default=CDR_SKIP_SENTINEL
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_cdr_error(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Phase 2.3 — Retry / skip form shown when a CDR fetch fails.

        The form is reached by `_cdr_route_error` from either retailer or
        plan-select steps. State on entry: `_cdr_error_kind` is one of
        `registry` | `list` | `detail` | `empty`. Retry count is bumped
        each visit; after ``CDR_MAX_RETRIES`` consecutive retries fail,
        the form forces a fall-through to manual.
        """
        retry_count = int(self._data.get("_cdr_retry_count", 0))
        kind = self._data.get("_cdr_error_kind", "list")

        if user_input is not None:
            action = user_input[CONF_CDR_RETRY_ACTION]
            if action == CDR_RETRY_ACTION_SKIP:
                _LOGGER.info("CDR retry form: user picked skip → manual flow")
                self._data["_cdr_skip_reason"] = CDR_SKIP_REASON_AFTER_ERROR
                return await self.async_step_globird_plan()
            # action == retry
            retry_count += 1
            self._data["_cdr_retry_count"] = retry_count
            if retry_count > CDR_MAX_RETRIES:
                _LOGGER.warning(
                    "CDR retry exhausted after %d attempts; forcing manual",
                    retry_count,
                )
                self._data["_cdr_skip_reason"] = CDR_SKIP_REASON_RETRY_EXHAUSTED
                return await self.async_step_globird_plan()
            # Re-enter the step that originally failed. `registry` failures
            # restart from cdr_retailer (which re-loads registry). Other
            # kinds replay cdr_plan_select (which re-fetches the list, or
            # the user picks a plan to re-fetch detail).
            if kind == "registry":
                return await self.async_step_cdr_retailer()
            return await self.async_step_cdr_plan_select()

        # First entry: show the form.
        return self.async_show_form(
            step_id="cdr_error",
            errors={"base": f"cdr_{kind}_unavailable"},
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CDR_RETRY_ACTION, default=CDR_RETRY_ACTION_RETRY
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": CDR_RETRY_ACTION_RETRY, "label": "Retry"},
                                {"value": CDR_RETRY_ACTION_SKIP, "label": "Skip CDR — enter rates manually"},
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            description_placeholders={
                "kind": kind,
                "attempt": str(retry_count + 1),
                "max": str(CDR_MAX_RETRIES + 1),
            },
        )

    async def async_step_cdr_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Phase 2.9 — Read-only summary of the fetched CDR plan. User
        verifies tariffs/rates/incentives against their actual bill and
        accepts, goes back to pick a different plan, or falls through to
        manual entry.

        Surfaces the bug catch: CDR data goes stale, retailers publish
        wrong rates, EME-proxy strips fields. Without this step the
        wizard silently commits whatever CDR returned.
        """
        detail = self._data.get(CONF_CDR_PLAN, {})
        summary = _summarise_cdr_plan(detail)

        if user_input is not None:
            action = user_input[CONF_CDR_CONFIRM_ACTION]
            if action == CDR_CONFIRM_ACCEPT:
                _LOGGER.info(
                    "CDR plan %s confirmed by user", summary.get("plan_name")
                )
                return await self.async_step_cdr_override()
            if action == CDR_CONFIRM_PICK_DIFFERENT:
                # Clear the stored CDR plan and go back to plan select.
                self._data.pop(CONF_CDR_PLAN, None)
                return await self.async_step_cdr_plan_select()
            # action == CDR_CONFIRM_MANUAL
            self._data.pop(CONF_CDR_PLAN, None)
            self._data["_cdr_skip_reason"] = CDR_SKIP_REASON_USER_AT_PLAN
            return await self.async_step_globird_plan()

        return self.async_show_form(
            step_id="cdr_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CDR_CONFIRM_ACTION, default=CDR_CONFIRM_ACCEPT
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": CDR_CONFIRM_ACCEPT, "label": "Yes — these rates match my bill"},
                                {"value": CDR_CONFIRM_PICK_DIFFERENT, "label": "No — pick a different plan"},
                                {"value": CDR_CONFIRM_MANUAL, "label": "No — enter rates manually instead"},
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            description_placeholders=summary,
        )

    async def async_step_cdr_override(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Phase 2.5 — Optional override step shown AFTER a successful
        CDR plan pick. Accepts a JSON fragment that gets deep-merged onto
        the PlanDetailV2 ``data`` block before storage. Use cases:
        - Stale rates in CDR (paste the corrected fields).
        - Missing FIT block (paste a hand-built `solarFeedInTariff`).
        - Custom incentives that need override of CDR-published copy.

        Empty input ⇒ no override, proceed to sensor select. Invalid
        JSON ⇒ re-show form with error. Valid JSON that doesn't parse to
        a dict at root ⇒ same error.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            raw = user_input.get(CONF_CDR_OVERRIDE_JSON, "").strip()
            if not raw:
                # Empty — user opted out of overrides, proceed.
                return await self.async_step_sensor_select()
            try:
                overlay = _parse_override_json(raw)
            except (ValueError, _json.JSONDecodeError):
                errors["base"] = "cdr_override_invalid_json"
                overlay = None
            if not errors and overlay is not None:
                cdr_plan = self._data.get(CONF_CDR_PLAN, {})
                base_data = cdr_plan.get("data", {}) if isinstance(cdr_plan, dict) else {}
                merged_data = _deep_merge_dict(base_data, overlay)
                # Rebuild the envelope preserving everything outside `data`.
                self._data[CONF_CDR_PLAN] = {
                    **(cdr_plan if isinstance(cdr_plan, dict) else {}),
                    "data": merged_data,
                }
                # Audit field so debugging can spot overridden entries.
                self._data["_cdr_override_applied"] = True
                _LOGGER.info(
                    "CDR override applied: %d top-level keys patched",
                    len(overlay),
                )
                return await self.async_step_sensor_select()

        return self.async_show_form(
            step_id="cdr_override",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_CDR_OVERRIDE_JSON, default=""
                    ): TextSelector(
                        TextSelectorConfig(multiline=True)
                    ),
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
        """Final step: optional HA access token. Builds the entry data
        and options dicts with conditional provider enables.

        - Amber/LocalVolts enabled iff the user is actually a customer
          (their primary). Other users can't supply credentials so those
          providers stay off.
        - GloBird and Flow Power are universally enabled comparators.
        """
        if user_input is not None:
            current_provider = self._data.get(
                CONF_CURRENT_PROVIDER, PROVIDER_AMBER
            )
            data = {
                CONF_API_KEY: self._data.get(CONF_API_KEY, ""),
                CONF_SITE_ID: self._data.get(CONF_SITE_ID, ""),
                CONF_HA_TOKEN: user_input.get(CONF_HA_TOKEN, ""),
                CONF_CURRENT_PROVIDER: current_provider,
            }

            # Provider enables based on the primary choice
            amber_enabled = current_provider == PROVIDER_AMBER
            localvolts_enabled = current_provider == PROVIDER_LOCALVOLTS
            # Flow Power is always on as a comparator. If the primary IS
            # Flow Power, the region/base/supply were set at the
            # credentials step; otherwise default to NSW1 / 34c / 100c.
            flow_power_enabled = True

            options: dict[str, Any] = {
                CONF_PLAN_TYPE: self._data.get(CONF_PLAN_TYPE, PLAN_ZEROHERO),
                CONF_DAILY_SUPPLY_CHARGE: self._data.get(
                    CONF_DAILY_SUPPLY_CHARGE, 0.0
                ),
                CONF_DEMAND_CHARGE: self._data.get(CONF_DEMAND_CHARGE, 0.0),
                CONF_IMPORT_TARIFF: self._data.get(CONF_IMPORT_TARIFF, {}),
                CONF_EXPORT_TARIFF: self._data.get(CONF_EXPORT_TARIFF, {}),
                CONF_INCENTIVES: self._data.get(CONF_INCENTIVES, {}),
                CONF_GRID_POWER_SENSOR: self._data[CONF_GRID_POWER_SENSOR],
                CONF_AMBER_NETWORK_DAILY_CHARGE: self._data.get(
                    CONF_AMBER_NETWORK_DAILY_CHARGE, 0.0
                ),
                CONF_AMBER_SUBSCRIPTION_FEE: self._data.get(
                    CONF_AMBER_SUBSCRIPTION_FEE, 0.0
                ),
                CONF_AMBER_ENABLED: amber_enabled,
                CONF_FLOW_POWER_ENABLED: flow_power_enabled,
                CONF_FLOW_POWER_REGION: self._data.get(
                    CONF_FLOW_POWER_REGION, "NSW1"
                ),
                CONF_FLOW_POWER_BASE_RATE: self._data.get(
                    CONF_FLOW_POWER_BASE_RATE, 34.0
                ),
                CONF_FLOW_POWER_DAILY_SUPPLY: self._data.get(
                    CONF_FLOW_POWER_DAILY_SUPPLY, 100.0
                ),
                CONF_FLOW_POWER_PEA_ENABLED: self._data.get(
                    CONF_FLOW_POWER_PEA_ENABLED, True
                ),
                CONF_LOCALVOLTS_ENABLED: localvolts_enabled,
            }

            if localvolts_enabled:
                options[CONF_LOCALVOLTS_API_KEY] = self._data.get(
                    CONF_LOCALVOLTS_API_KEY, ""
                )
                options[CONF_LOCALVOLTS_PARTNER_ID] = self._data.get(
                    CONF_LOCALVOLTS_PARTNER_ID, ""
                )
                options[CONF_LOCALVOLTS_NMI] = self._data.get(
                    CONF_LOCALVOLTS_NMI, ""
                )
                options[CONF_LOCALVOLTS_DAILY_SUPPLY] = self._data.get(
                    CONF_LOCALVOLTS_DAILY_SUPPLY, 110.0
                )

            # Phase 2.2: when wizard branch A succeeded, persist the CDR
            # plan envelope so the coordinator wires `CdrGloBirdProvider`
            # instead of the legacy GloBirdProvider.
            cdr_plan = self._data.get(CONF_CDR_PLAN)
            if cdr_plan:
                options[CONF_CDR_PLAN] = cdr_plan
            else:
                # Phase 2.4: persist branch identification (branch C
                # deliberate-manual vs branch B failure-skip) as a
                # read-only audit field. Coordinator ignores this.
                skip_reason = self._data.get("_cdr_skip_reason")
                if skip_reason:
                    options[CONF_CDR_SKIP_REASON] = skip_reason

            # Phase 2.5: audit field — was the CDR plan patched via the
            # override step? Logs only; coordinator ignores.
            if self._data.get("_cdr_override_applied"):
                options["cdr_override_applied"] = True

            _LOGGER.info(
                "Creating PriceHawk entry: primary=%s amber=%s lv=%s cdr=%s skip=%s override=%s",
                current_provider, amber_enabled, localvolts_enabled,
                bool(cdr_plan), self._data.get("_cdr_skip_reason"),
                self._data.get("_cdr_override_applied", False),
            )
            return self.async_create_entry(
                title="PriceHawk", data=data, options=options
            )

        return self.async_show_form(
            step_id="dashboard_token",
            data_schema=vol.Schema(
                {
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
            menu_options=[
                "amber_api_key",
                "cdr_pick",
                "globird_plan",
                "amber_fees",
                "flow_power",
                "localvolts",
                "sensor_select",
            ],
        )

    # ------------------------------------------------------------------
    # Phase 2.7 — CDR re-pick (options flow mirror of wizard branch A)
    # ------------------------------------------------------------------

    async def async_step_cdr_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show retailer dropdown so user can swap CDR plans post-install
        without removing/re-adding the integration. Mirrors the wizard's
        ``async_step_cdr_retailer`` minus the override step (deferred to
        v1.5.1 for options flow).
        """
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        if user_input is not None:
            choice = user_input[CONF_CDR_RETAILER_ID]
            if choice == CDR_SKIP_SENTINEL:
                # User backed out — return to init menu, options unchanged.
                return await self.async_step_init()
            endpoints: list[RetailerEndpoint] = self._data.get(
                "_cdr_endpoints", []
            )
            picked = next((e for e in endpoints if e.brand_id == choice), None)
            if picked is None:
                _LOGGER.warning(
                    "options: CDR retailer %s missing from cached registry",
                    choice,
                )
                return await self.async_step_init()
            self._data["_cdr_retailer"] = picked
            return await self.async_step_cdr_plan_pick()

        # First entry — load registry.
        try:
            session = async_get_clientsession(self.hass)
            endpoints, source = await get_registry(session)
            _LOGGER.info(
                "options: CDR registry loaded (%s): %d retailers",
                source, len(endpoints),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "options: CDR registry load failed (%s); returning to menu",
                err,
            )
            return await self.async_step_init()

        self._data["_cdr_endpoints"] = endpoints
        options = _build_cdr_retailer_options(endpoints)

        return self.async_show_form(
            step_id="cdr_pick",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CDR_RETAILER_ID, default=CDR_SKIP_SENTINEL
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_cdr_plan_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Plan dropdown for the selected retailer. On selection, persists
        the new CDR plan into ``entry.options`` immediately (no further
        menu interaction needed) by returning ``async_create_entry``.

        Failure modes (list fetch / detail fetch) silently return to init
        menu — the existing options stay intact. Phase 2.x may add a
        retry UI in the options flow; for v1.5.0 the wizard branch B
        carries the bulk of the retry UX.
        """
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        retailer: RetailerEndpoint | None = self._data.get("_cdr_retailer")
        if retailer is None:
            return await self.async_step_init()

        if user_input is not None:
            chosen_plan_id = user_input[CONF_CDR_PLAN_ID]
            if chosen_plan_id == CDR_SKIP_SENTINEL:
                return await self.async_step_init()
            try:
                session = async_get_clientsession(self.hass)
                detail = await fetch_plan_detail(
                    session, retailer.base_uri, chosen_plan_id
                )
            except (CdrPlanNotFound, CdrUnavailable, CdrAPIError) as err:
                _LOGGER.warning(
                    "options: CDR detail fetch failed for %s/%s (%s)",
                    retailer.brand_name, chosen_plan_id, err,
                )
                return await self.async_step_init()
            # Replace the stored CDR plan and clear any prior skip-reason
            # audit (the user is actively choosing CDR now).
            self._data[CONF_CDR_PLAN] = detail
            self._data.pop(CONF_CDR_SKIP_REASON, None)
            # Strip internal keys before commit.
            self._data.pop("_cdr_endpoints", None)
            self._data.pop("_cdr_retailer", None)
            _LOGGER.info(
                "options: CDR plan updated → %s / %s",
                retailer.brand_name, chosen_plan_id,
            )
            return self.async_create_entry(data=self._data)

        try:
            session = async_get_clientsession(self.hass)
            plans = await fetch_plan_list(session, retailer.base_uri)
        except (CdrUnavailable, CdrAPIError) as err:
            _LOGGER.warning(
                "options: CDR list fetch failed for %s (%s)",
                retailer.brand_name, err,
            )
            return await self.async_step_init()

        plan_options = _build_cdr_plan_options(plans)
        if not plan_options:
            _LOGGER.info(
                "options: CDR list for %s returned 0 usable plans",
                retailer.brand_name,
            )
            return await self.async_step_init()

        plan_options = [
            {"value": CDR_SKIP_SENTINEL, "label": "Cancel (keep current plan)"}
        ] + plan_options

        return self.async_show_form(
            step_id="cdr_plan_pick",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CDR_PLAN_ID, default=CDR_SKIP_SENTINEL
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=plan_options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    # ------------------------------------------------------------------
    # Flow Power options step
    # ------------------------------------------------------------------

    async def async_step_flow_power(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure Flow Power as an additional comparator."""
        if user_input is not None:
            self._data[CONF_FLOW_POWER_ENABLED] = user_input[
                CONF_FLOW_POWER_ENABLED
            ]
            self._data[CONF_FLOW_POWER_REGION] = user_input[
                CONF_FLOW_POWER_REGION
            ]
            self._data[CONF_FLOW_POWER_BASE_RATE] = user_input[
                CONF_FLOW_POWER_BASE_RATE
            ]
            self._data[CONF_FLOW_POWER_DAILY_SUPPLY] = user_input[
                CONF_FLOW_POWER_DAILY_SUPPLY
            ]
            self._data[CONF_FLOW_POWER_PEA_ENABLED] = user_input[
                CONF_FLOW_POWER_PEA_ENABLED
            ]
            if user_input.get(CONF_FLOW_POWER_PEA_OVERRIDE) is not None:
                self._data[CONF_FLOW_POWER_PEA_OVERRIDE] = user_input[
                    CONF_FLOW_POWER_PEA_OVERRIDE
                ]
            return await self.async_step_init()

        return self.async_show_form(
            step_id="flow_power",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_FLOW_POWER_ENABLED,
                        default=self._data.get(CONF_FLOW_POWER_ENABLED, False),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_FLOW_POWER_REGION,
                        default=self._data.get(CONF_FLOW_POWER_REGION, "NSW1"),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=["NSW1", "QLD1", "VIC1", "SA1", "TAS1"],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_FLOW_POWER_BASE_RATE,
                        default=self._data.get(CONF_FLOW_POWER_BASE_RATE, 34.0),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=100, step=0.1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_FLOW_POWER_DAILY_SUPPLY,
                        default=self._data.get(
                            CONF_FLOW_POWER_DAILY_SUPPLY, 100.0
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=500, step=0.1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_FLOW_POWER_PEA_ENABLED,
                        default=self._data.get(
                            CONF_FLOW_POWER_PEA_ENABLED, True
                        ),
                    ): BooleanSelector(),
                    vol.Optional(
                        CONF_FLOW_POWER_PEA_OVERRIDE,
                        default=self._data.get(CONF_FLOW_POWER_PEA_OVERRIDE),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=-50, max=50, step=0.1, mode=NumberSelectorMode.BOX
                        )
                    ),
                }
            ),
            description_placeholders={
                "wholesale_source": (
                    "Flow Power requires Amber as the wholesale spot source "
                    "(uses spotPerKwh from /v1/sites/{id}/prices/current)."
                ),
            },
        )

    # ------------------------------------------------------------------
    # LocalVolts options step
    # ------------------------------------------------------------------

    async def async_step_localvolts(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure LocalVolts as an additional comparator."""
        if user_input is not None:
            self._data[CONF_LOCALVOLTS_ENABLED] = user_input[
                CONF_LOCALVOLTS_ENABLED
            ]
            self._data[CONF_LOCALVOLTS_API_KEY] = user_input.get(
                CONF_LOCALVOLTS_API_KEY, ""
            )
            self._data[CONF_LOCALVOLTS_PARTNER_ID] = user_input.get(
                CONF_LOCALVOLTS_PARTNER_ID, ""
            )
            self._data[CONF_LOCALVOLTS_NMI] = user_input.get(
                CONF_LOCALVOLTS_NMI, ""
            )
            self._data[CONF_LOCALVOLTS_DAILY_SUPPLY] = user_input[
                CONF_LOCALVOLTS_DAILY_SUPPLY
            ]
            if user_input.get(CONF_LOCALVOLTS_BUY_CEILING) is not None:
                self._data[CONF_LOCALVOLTS_BUY_CEILING] = user_input[
                    CONF_LOCALVOLTS_BUY_CEILING
                ]
            if user_input.get(CONF_LOCALVOLTS_SELL_FLOOR) is not None:
                self._data[CONF_LOCALVOLTS_SELL_FLOOR] = user_input[
                    CONF_LOCALVOLTS_SELL_FLOOR
                ]
            return await self.async_step_init()

        return self.async_show_form(
            step_id="localvolts",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LOCALVOLTS_ENABLED,
                        default=self._data.get(CONF_LOCALVOLTS_ENABLED, False),
                    ): BooleanSelector(),
                    vol.Optional(
                        CONF_LOCALVOLTS_API_KEY,
                        default=self._data.get(CONF_LOCALVOLTS_API_KEY, ""),
                    ): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(
                        CONF_LOCALVOLTS_PARTNER_ID,
                        default=self._data.get(CONF_LOCALVOLTS_PARTNER_ID, ""),
                    ): TextSelector(),
                    vol.Optional(
                        CONF_LOCALVOLTS_NMI,
                        default=self._data.get(CONF_LOCALVOLTS_NMI, ""),
                    ): TextSelector(),
                    vol.Required(
                        CONF_LOCALVOLTS_DAILY_SUPPLY,
                        default=self._data.get(
                            CONF_LOCALVOLTS_DAILY_SUPPLY, 110.0
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=500, step=0.1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_LOCALVOLTS_BUY_CEILING,
                        default=self._data.get(CONF_LOCALVOLTS_BUY_CEILING),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=200, step=0.1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_LOCALVOLTS_SELL_FLOOR,
                        default=self._data.get(CONF_LOCALVOLTS_SELL_FLOOR),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=-20, max=100, step=0.1, mode=NumberSelectorMode.BOX
                        )
                    ),
                }
            ),
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
        """Grid power sensor selection (options). Accessible from menu or tariff flow."""
        if user_input is not None:
            # Clean up internal keys
            self._data.pop("_defaults", None)

            # Update sensor in current options
            self._data[CONF_GRID_POWER_SENSOR] = user_input[CONF_GRID_POWER_SENSOR]

            # Preserve every option already set in self._data (including any
            # Flow Power / LocalVolts keys from the new menu steps) rather
            # than rebuilding from a hardcoded list.
            options = dict(self._data)
            # Ensure the canonical keys exist with sensible defaults
            options.setdefault(CONF_PLAN_TYPE, PLAN_ZEROHERO)
            options.setdefault(CONF_DAILY_SUPPLY_CHARGE, 0.0)
            options.setdefault(CONF_DEMAND_CHARGE, 0.0)
            options.setdefault(CONF_IMPORT_TARIFF, {})
            options.setdefault(CONF_EXPORT_TARIFF, {})
            options.setdefault(CONF_INCENTIVES, {})
            options.setdefault(CONF_AMBER_NETWORK_DAILY_CHARGE, 0.0)
            options.setdefault(CONF_AMBER_SUBSCRIPTION_FEE, 0.0)
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
