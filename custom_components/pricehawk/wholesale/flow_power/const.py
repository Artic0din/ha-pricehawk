"""Constants for the vendored Flow Power calculation modules.

This file is built up additively across the PR 3 slices from upstream
``custom_components/flow_power_ha/const.py``
(https://github.com/bolagnaise/Flow-Power-HA, commit
``3c2a9bb77dfa30eab3646a31703e10ad6743d10f``).

- **PR 3a** vendored the constants :mod:`.pricing` imports.
- **PR 3b** (this slice) appends the constants :mod:`.tariff_utils` imports
  plus the related network/region tables.
- **PR 3c** will append AEMO + Flow Power portal URLs for ``api_clients.py``.

When all three slices have landed, this file matches upstream byte-for-byte
**except** for the four forked rows documented in ``NOTICES.md`` (Codex P1
findings on PR 3b). Each fork is marked ``# FORK(#TBD):`` and must be
re-applied on every upstream SHA bump.
"""
from datetime import time

# PEA (Price Efficiency Adjustment) Constants
FLOW_POWER_MARKET_AVG = 8.0  # Default TWAP fallback when insufficient data (c/kWh)
FLOW_POWER_BENCHMARK = 1.7  # BPEA - benchmark customer performance (c/kWh)
FLOW_POWER_DEFAULT_BASE_RATE = 34.0  # Default Flow Power base rate (c/kWh)

# NEM Regions
NEM_REGIONS = {
    "NSW1": "New South Wales",
    "QLD1": "Queensland",
    "VIC1": "Victoria",
    "SA1": "South Australia",
    "TAS1": "Tasmania",
}

# GST multiplier
FLOW_POWER_GST = 1.1

# Network tariff configuration keys
CONF_FP_NETWORK = "fp_network"
CONF_FP_TARIFF_CODE = "fp_tariff_code"

# NEM region → list of DNSP display names
# FORK(#186): "Evoenergy" added to NSW1. Upstream omits it but ACT (Evoenergy's
# service area) is priced in the NSW1 NEM region, and the network/module/URL
# tables below already include Evoenergy. Without this, the region-driven
# selection flow blocks ACT customers from picking their DNSP.
REGION_NETWORKS = {
    "NSW1": ["Ausgrid", "Endeavour", "Essential", "Evoenergy"],  # FORK(#186)
    "QLD1": ["Energex", "Ergon"],
    "VIC1": ["Powercor", "CitiPower", "AusNet", "Jemena", "United"],
    "SA1": ["SAPN"],
    "TAS1": ["TasNetworks"],
}

# Display name → aemo_to_tariff network parameter (for spot_to_tariff() calls)
# FORK(#186): "United" → "united" (upstream: "victoria"). The aemo_to_tariff
# library exposes a dedicated `united` backend with United Energy's actual
# tariff schedule; routing through `victoria` returns generic placeholder
# rates and miscalculates every United customer's PEA.
NETWORK_API_NAME = {
    "Ausgrid": "ausgrid",
    "Endeavour": "endeavour",
    "Essential": "essential",
    "Energex": "energex",
    "Ergon": "ergon",
    "SAPN": "sapn",
    "Powercor": "powercor",
    "CitiPower": "victoria",
    "AusNet": "ausnet",
    "Jemena": "jemena",
    "United": "united",  # FORK(#186)
    "TasNetworks": "tasnetworks",
    "Evoenergy": "evoenergy",
}

# Display name → aemo_to_tariff module name (for importlib imports)
# FORK(#186): "United" → "united" (upstream: "victoria") — paired with
# NETWORK_API_NAME above so importlib loads the right module's tariffs.
NETWORK_MODULE_NAME = {
    "Ausgrid": "ausgrid",
    "Endeavour": "endeavour",
    "Essential": "essential",
    "Energex": "energex",
    "Ergon": "ergon",
    "SAPN": "sapower",
    "Powercor": "powercor",
    "CitiPower": "victoria",
    "AusNet": "ausnet",
    "Jemena": "jemena",
    "United": "united",  # FORK(#186)
    "TasNetworks": "tasnetworks",
    "Evoenergy": "evoenergy",
}

# Display name → tariff lookup URL for each DNSP
NETWORK_TARIFF_URL = {
    "Ausgrid": "https://www.ausgrid.com.au/Your-energy-use/Meters/Tariffs-on-your-meter",
    "Endeavour": "https://www.endeavourenergy.com.au/your-energy/understand-your-energy/network-prices",
    "Essential": "https://www.essentialenergy.com.au/our-network/network-pricing",
    "Energex": "https://www.energex.com.au/home/our-services/pricing-And-tariffs/residential-tariffs",
    "Ergon": "https://www.ergon.com.au/network/network-management/network-tariffs",
    "SAPN": "https://www.sapowernetworks.com.au/industry/pricing/current-network-prices/",
    "Powercor": "https://www.powercor.com.au/industry/pricing-and-tariffs/network-tariff-rates/",
    "CitiPower": "https://www.powercor.com.au/industry/pricing-and-tariffs/network-tariff-rates/",
    "United": "https://www.powercor.com.au/industry/pricing-and-tariffs/network-tariff-rates/",
    "AusNet": "https://www.ausnetservices.com.au/about/network-prices/electricity-distribution-prices",
    "Jemena": "https://jemena.com.au/price-and-availability/electricity-prices",
    "TasNetworks": "https://www.tasnetworks.com.au/config/getattachment/3d6ca9fb-b3d2-464e-9d90-dfe26ae84c8e/tariff-schedule.pdf",
    "Evoenergy": "https://www.evoenergy.com.au/residents/understanding-electricity-pricing",
}

# FORK(#186): aemo_to_tariff network param → IANA timezone. New table (no
# upstream equivalent), required by the fork in :func:`.tariff_utils.compute_avg_daily_tariff`
# so the 48-slot sweep is anchored at the DNSP's local midnight rather than
# fixed AEST. Without this, NSW/VIC/TAS (DST) and SA (+9:30) sample the
# wrong half-hour windows and bias the average.
NETWORK_TIMEZONE = {
    "ausgrid": "Australia/Sydney",
    "endeavour": "Australia/Sydney",
    "essential": "Australia/Sydney",
    "evoenergy": "Australia/Sydney",  # ACT, NSW1 region
    "energex": "Australia/Brisbane",
    "ergon": "Australia/Brisbane",
    "sapn": "Australia/Adelaide",
    "sapower": "Australia/Adelaide",  # module alias
    "powercor": "Australia/Melbourne",
    "victoria": "Australia/Melbourne",
    "ausnet": "Australia/Melbourne",
    "jemena": "Australia/Melbourne",
    "united": "Australia/Melbourne",
    "tasnetworks": "Australia/Hobart",
}

# Export Rates by Region (Happy Hour rates in $/kWh)
FLOW_POWER_EXPORT_RATES = {
    "NSW1": 0.45,  # 45c/kWh
    "QLD1": 0.45,  # 45c/kWh
    "SA1": 0.45,   # 45c/kWh
    "VIC1": 0.35,  # 35c/kWh
    "TAS1": 0.00,  # No Happy Hour in Tasmania
}

# Happy Hour Time Window (local time)
HAPPY_HOUR_START = time(17, 30)  # 5:30 PM
HAPPY_HOUR_END = time(19, 30)    # 7:30 PM
