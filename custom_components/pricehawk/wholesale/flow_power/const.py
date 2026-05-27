"""Constants for the vendored Flow Power calculation modules.

This file is the **PR 3a slice** of upstream
``custom_components/flow_power_ha/const.py``
(https://github.com/bolagnaise/Flow-Power-HA, commit
``3c2a9bb77dfa30eab3646a31703e10ad6743d10f``).

Only the constants that :mod:`.pricing` imports are vendored in this PR.
PR 3b appends the constants needed by ``tariff_utils.py``; PR 3c appends
those needed by ``api_clients.py``. When all three slices have landed,
this file matches upstream byte-for-byte.

Verbatim values — do NOT edit; if upstream changes, re-vendor via the
NOTICES-recorded SHA bump procedure.
"""
from datetime import time

# PEA (Price Efficiency Adjustment) Constants
FLOW_POWER_MARKET_AVG = 8.0  # Default TWAP fallback when insufficient data (c/kWh)
FLOW_POWER_BENCHMARK = 1.7  # BPEA - benchmark customer performance (c/kWh)
FLOW_POWER_DEFAULT_BASE_RATE = 34.0  # Default Flow Power base rate (c/kWh)

# GST multiplier
FLOW_POWER_GST = 1.1

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
