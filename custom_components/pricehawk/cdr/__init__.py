"""CDR-native tariff engine package.

Phase 1 refactor of the legacy `tariff_engine.py` (GloBird-specific, config-
dict driven). The CDR package consumes AER Consumer Data Right
PlanDetailV2 JSON and works across all AU energy retailers.

Public surface:
    from custom_components.pricehawk.cdr import evaluate, CostBreakdown
    from custom_components.pricehawk.cdr.models import PlanDetail, ConsumptionWindow

Phase 0 prototype (`scripts/cdr_evaluator_proto.py`) was the working
spec for this package. Behaviour is preserved; only the typing and
packaging shape changed.
"""
from __future__ import annotations

from .evaluator import CostBreakdown, evaluate

__all__ = ["CostBreakdown", "evaluate"]
