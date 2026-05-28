"""CdrPlanProvider construction + protocol conformance tests.

Phase 3.0d: legacy GloBirdProvider deleted. Every PriceHawk entry now
runs through CdrPlanProvider — there is no fallback path. The earlier
"select between CDR and legacy" tests from Phase 1.3 are obsolete.
What remains is verifying the provider satisfies the Protocol and
exposes every property the coordinator's data dict reads.
"""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.pricehawk.providers.cdr_plan import CdrPlanProvider

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "phase0"


def _load_globird_plan() -> dict:
    return json.loads((FIXTURE_DIR / "plan_globird_GLO731031MR@VEC.json").read_text())


def test_cdr_plan_provider_identity_from_envelope() -> None:
    """Phase 3.0a: id is `<brand>_<planId>`; name is plan.displayName."""
    p = CdrPlanProvider(_load_globird_plan())
    assert p.id.startswith("globird")
    assert "GLO731031MR@VEC" in p.id
    assert "GloBird" in p.name


def test_cdr_plan_provider_satisfies_protocol() -> None:
    """Provider Protocol conformance — coordinator + sensor.py rely on this."""
    from custom_components.pricehawk.providers.base import Provider

    p = CdrPlanProvider(_load_globird_plan())
    assert isinstance(p, Provider)


def test_cdr_plan_provider_drop_in_property_shape() -> None:
    """Every property the coordinator's data dict reads must exist with
    the right return type."""
    p = CdrPlanProvider(_load_globird_plan())

    assert isinstance(p.import_kwh_today, float)
    assert isinstance(p.export_kwh_today, float)
    assert isinstance(p.import_cost_today_c, float)
    assert isinstance(p.export_earnings_today_c, float)
    assert isinstance(p.net_daily_cost_aud, float)
    assert isinstance(p.current_import_rate_c_kwh, float)
    assert isinstance(p.current_export_rate_c_kwh, float)
    assert isinstance(p.daily_fixed_charges_aud, float)
    extras = p.extras
    assert isinstance(extras, dict)
    assert "zerohero_status" in extras
    assert "super_export_kwh" in extras
