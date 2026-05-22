"""Provider package — retailer implementations behind a common Protocol.

Phase 3.0d: legacy GloBirdProvider (manual-tariff path) removed. Every
PriceHawk entry now uses CdrPlanProvider (in `cdr_plan.py`) for the
user's CURRENT plan. Amber/FlowPower/LocalVolts remain as optional
truth-source overlays exposed via this package.
"""

from __future__ import annotations

from .amber import AmberProvider
from .base import Provider
from .cdr_plan import CdrPlanProvider
from .dynamic_wholesale_tariff import DynamicWholesaleTariffProvider
from .flow_power import FlowPowerProvider
from .localvolts import LocalVoltsProvider

__all__ = [
    "AmberProvider",
    "CdrPlanProvider",
    "DynamicWholesaleTariffProvider",
    "FlowPowerProvider",
    "LocalVoltsProvider",
    "Provider",
]
