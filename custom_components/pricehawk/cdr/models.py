"""Boundary pydantic v2 models for CDR evaluator inputs.

Minimal by design — pydantic is used only at the public API boundary
(`evaluate(plan, consumption)`). Internal walk-the-dict logic in
`evaluator.py` stays untyped because CDR `electricityContract` is a
deeply optional structure where every retailer drops different fields.
Locking down the inner schema with pydantic creates a maintenance
burden that pays back nothing.

Use these models for input validation + IDE hints at the call site.
Once Phase 2 (wizard config flow) wraps this, the wizard owns
construction of `PlanDetail` from CDR fetch + caller passes us a
guaranteed-valid object.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConsumptionSlot(BaseModel):
    """One half-hour consumption observation.

    Slot timestamps are ISO-8601 with timezone (Australia/Sydney AEST/AEDT
    aware). UTC parallel timestamp is optional but useful for DST debugging.
    """

    model_config = ConfigDict(extra="allow")

    ts_local: str
    grid_import_kwh: float = 0.0
    grid_export_kwh: float = 0.0
    solar_kwh: float = 0.0


class ConsumptionWindow(BaseModel):
    """Container for a period of consumption slots.

    Phase 0 fixture also carries `_phase0_meta`; we accept-extra so meta
    survives round-trip via `model_dump()` if a caller wants it.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    slots: list[ConsumptionSlot]


class PlanDetail(BaseModel):
    """Thin wrapper around CDR PlanDetailV2 `data` object.

    We do NOT enumerate every field — CDR `electricityContract` has 30+
    optional keys and retailers populate different subsets. We only assert
    that `planId` exists and `electricityContract` is present as a dict.
    Internal evaluator walks the dict directly.
    """

    model_config = ConfigDict(extra="allow")

    planId: str = Field(..., description="Opaque retailer plan ID, e.g. GLO731031MR@VEC")
    electricityContract: dict[str, Any] = Field(default_factory=dict)


class PlanDetailEnvelope(BaseModel):
    """Top-level CDR envelope `{"data": PlanDetail}`.

    EME endpoint returns this shape. Our fixtures store the same shape
    plus `_phase0_meta` at the top level (phase 0 only).
    """

    model_config = ConfigDict(extra="allow")

    data: PlanDetail
