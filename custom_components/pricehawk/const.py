"""Constants for PriceHawk integration."""

DOMAIN = "pricehawk"

# Config keys - stored in config_entry.data
CONF_API_KEY = "api_key"
CONF_SITE_ID = "site_id"
CONF_HA_TOKEN = "ha_token"
CONF_CURRENT_PROVIDER = "current_provider"

# Provider choices
PROVIDER_AMBER = "amber"
PROVIDER_GLOBIRD = "globird"

# Option keys - stored in config_entry.options
CONF_PLAN_TYPE = "plan_type"
CONF_DAILY_SUPPLY_CHARGE = "daily_supply_charge"
CONF_DEMAND_CHARGE = "demand_charge"
CONF_IMPORT_TARIFF = "import_tariff"
CONF_EXPORT_TARIFF = "export_tariff"
CONF_INCENTIVES = "incentives"
CONF_GRID_POWER_SENSOR = "grid_power_sensor"
CONF_AMBER_NETWORK_DAILY_CHARGE = "amber_network_daily_charge"
CONF_AMBER_SUBSCRIPTION_FEE = "amber_subscription_fee"

# Plan type identifiers
PLAN_ZEROHERO = "zerohero"
PLAN_FOUR4FREE = "four4free"
PLAN_BOOST = "boost"
PLAN_GLOSAVE = "glosave"
PLAN_CUSTOM = "custom"

# Tariff types
TARIFF_TOU = "tou"
TARIFF_FLAT_STEPPED = "flat_stepped"

# ZEROHERO import TOU windows (local time, all year, every day)
# From PDF: Peak 4pm-11pm, Off-Peak 11am-2pm,
# Shoulder: 2pm-4pm + 11pm-12am + 12am-11am (everything else)
ZEROHERO_IMPORT_WINDOWS = {
    "peak": [["16:00", "23:00"]],
    "shoulder": [["23:00", "00:00"], ["00:00", "11:00"], ["14:00", "16:00"]],
    "offpeak": [["11:00", "14:00"]],
}

# FOUR4FREE import windows (Two Rate plan)
# From PDF: Peak = 2pm-12am + 12am-10am (everything except off-peak), Off-Peak = 10am-2pm (FREE)
FOUR4FREE_IMPORT_WINDOWS = {
    "peak": [["14:00", "00:00"], ["00:00", "10:00"]],
    "offpeak": [["10:00", "14:00"]],
}

# Export TOU windows - same structure for all plans (Variable FiT Option 2)
# From PDFs: Peak 4pm-9pm, Off-Peak 10am-2pm,
# Shoulder: 9pm-12am + 12am-10am + 2pm-4pm
EXPORT_WINDOWS = {
    "peak": [["16:00", "21:00"]],
    "shoulder": [["21:00", "00:00"], ["00:00", "10:00"], ["14:00", "16:00"]],
    "offpeak": [["10:00", "14:00"]],
}

# Default TOU windows for custom TOU plans
DEFAULT_TOU_IMPORT_WINDOWS = ZEROHERO_IMPORT_WINDOWS

# Incentive type identifiers (only those with engine-backed logic)
INCENTIVE_ZEROHERO_CREDIT = "zerohero_credit"
INCENTIVE_SUPER_EXPORT = "super_export"

# Legacy identifiers — kept for backward compat with existing config entries
# but no longer shown in config flow (no engine logic behind them)
INCENTIVE_FREE_POWER = "free_power_window"
INCENTIVE_CRITICAL_PEAK_EXPORT = "critical_peak_export"
INCENTIVE_CRITICAL_PEAK_IMPORT = "critical_peak_import"
INCENTIVE_PEAK_SOLAR_FEEDIN = "peak_solar_feedin"
INCENTIVE_PROMPT_PAYMENT = "prompt_payment_discount"

# All rates in c/kWh and c/day, inclusive of GST, from Energy Fact Sheets
# Updated 2026-04-06 from April 2026 fact sheets (GLO731031MR, GLO731660MR,
# GLO731591MR, GLO731580MR). These are pre-fill defaults only — users
# configure their own rates in the config flow.
GLOBIRD_PLAN_DEFAULTS = {
    PLAN_ZEROHERO: {
        "tariff_type": TARIFF_TOU,
        "daily_supply_charge": 115.50,
        "import_tariff": {
            "type": TARIFF_TOU,
            "periods": {
                "peak": {"rate": 39.60, "windows": ZEROHERO_IMPORT_WINDOWS["peak"]},
                "shoulder": {"rate": 27.50, "windows": ZEROHERO_IMPORT_WINDOWS["shoulder"]},
                "offpeak": {"rate": 0.00, "windows": ZEROHERO_IMPORT_WINDOWS["offpeak"]},
            },
        },
        "export_tariff": {
            "type": TARIFF_TOU,
            "periods": {
                "peak": {"rate": 0.00, "windows": EXPORT_WINDOWS["peak"]},
                "shoulder": {"rate": 0.00, "windows": EXPORT_WINDOWS["shoulder"]},
                "offpeak": {"rate": 0.00, "windows": EXPORT_WINDOWS["offpeak"]},
            },
        },
        "incentives": [
            INCENTIVE_ZEROHERO_CREDIT,
            INCENTIVE_SUPER_EXPORT,
            INCENTIVE_FREE_POWER,
            INCENTIVE_CRITICAL_PEAK_EXPORT,
            INCENTIVE_CRITICAL_PEAK_IMPORT,
            INCENTIVE_PEAK_SOLAR_FEEDIN,
        ],
    },
    PLAN_FOUR4FREE: {
        "tariff_type": TARIFF_TOU,
        "daily_supply_charge": 103.40,
        "step1_threshold_kwh": 15.0,
        "step1_rate": 27.72,
        "step2_rate": 30.25,
        "import_tariff": {
            "type": TARIFF_TOU,
            "periods": {
                "peak": {"rate": 27.72, "windows": FOUR4FREE_IMPORT_WINDOWS["peak"]},
                "offpeak": {"rate": 0.00, "windows": FOUR4FREE_IMPORT_WINDOWS["offpeak"]},
            },
        },
        "export_tariff": {
            "type": TARIFF_TOU,
            "periods": {
                "peak": {"rate": 5.00, "windows": [["16:00", "23:00"]]},
                "shoulder": {"rate": 0.00, "windows": [["23:00", "00:00"], ["00:00", "16:00"]]},
                "offpeak": {"rate": 0.00, "windows": []},
            },
        },
        "incentives": [INCENTIVE_FREE_POWER, INCENTIVE_PEAK_SOLAR_FEEDIN, INCENTIVE_PROMPT_PAYMENT],
    },
    PLAN_BOOST: {
        "tariff_type": TARIFF_FLAT_STEPPED,
        "daily_supply_charge": 110.00,
        "step1_threshold_kwh": 25.0,
        "step1_rate": 21.23,
        "step2_rate": 25.30,
        "import_tariff": {
            "type": TARIFF_FLAT_STEPPED,
            "step1_threshold_kwh": 25.0,
            "step1_rate": 21.23,
            "step2_rate": 25.30,
        },
        "export_tariff": {
            "type": TARIFF_TOU,
            "periods": {
                "peak": {"rate": 3.00, "windows": EXPORT_WINDOWS["peak"]},
                "shoulder": {"rate": 0.10, "windows": EXPORT_WINDOWS["shoulder"]},
                "offpeak": {"rate": 0.00, "windows": EXPORT_WINDOWS["offpeak"]},
            },
        },
        "incentives": [],
    },
    PLAN_GLOSAVE: {
        "tariff_type": TARIFF_FLAT_STEPPED,
        "daily_supply_charge": 88.00,
        "step1_threshold_kwh": 15.0,
        "step1_rate": 22.66,
        "step2_rate": 28.05,
        "import_tariff": {
            "type": TARIFF_FLAT_STEPPED,
            "step1_threshold_kwh": 15.0,
            "step1_rate": 22.66,
            "step2_rate": 28.05,
        },
        "export_tariff": {
            "type": TARIFF_TOU,
            "periods": {
                "peak": {"rate": 3.00, "windows": EXPORT_WINDOWS["peak"]},
                "shoulder": {"rate": 0.10, "windows": EXPORT_WINDOWS["shoulder"]},
                "offpeak": {"rate": 0.00, "windows": EXPORT_WINDOWS["offpeak"]},
            },
        },
        "incentives": [INCENTIVE_PROMPT_PAYMENT],
    },
}

# Incentive parameters — maps each incentive type to its calculation parameters
# Rates in c/kWh, credits in c/day unless noted
INCENTIVE_PARAMS = {
    INCENTIVE_ZEROHERO_CREDIT: {
        "description": "Daily bill credit for ZEROHERO plan",
        "credit_cents_per_day": 100.0,  # $1/day
    },
    INCENTIVE_SUPER_EXPORT: {
        "description": "Bonus export rate during super export window",
        "export_rate_c_kwh": 15.0,
        "cap_kwh": 15.0,
        "window_start": "18:00",
        "window_end": "21:00",
    },
    INCENTIVE_FREE_POWER: {
        "description": "Free import during off-peak window",
        "import_rate_c_kwh": 0.0,
        "window": [["11:00", "14:00"]],  # ZEROHERO off-peak
    },
    INCENTIVE_CRITICAL_PEAK_EXPORT: {
        "description": "Elevated export rate during critical peak events",
        "export_rate_c_kwh": 30.0,
        "event_driven": True,
    },
    INCENTIVE_CRITICAL_PEAK_IMPORT: {
        "description": "Credit for reducing import during critical peak events",
        "import_credit_c_kwh": 5.0,
        "event_driven": True,
    },
    INCENTIVE_PEAK_SOLAR_FEEDIN: {
        "description": "Enhanced feed-in during peak hours",
        "export_rate_c_kwh": 5.0,
        "window": [["16:00", "23:00"]],
    },
    INCENTIVE_PROMPT_PAYMENT: {
        "description": "Discount for on-time bill payment",
        "discount_percent": 2.0,
    },
}

# Coordinator
COORDINATOR_SCAN_INTERVAL = 30  # seconds
STORAGE_KEY = f"{DOMAIN}_state"
STORAGE_VERSION = 1
PERSIST_INTERVAL = 300  # seconds (5 minutes)
AMBER_API_POLL_INTERVAL = 300  # seconds (5 minutes)

# Amber API
AMBER_API_BASE_URL = "https://api.amber.com.au/v1"
