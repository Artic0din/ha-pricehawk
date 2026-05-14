"""Phase 1.3 coordinator feature-flag selection test.

Verifies the coordinator picks `CdrGloBirdProvider` when
`entry.options["cdr_plan"]` is present, else falls back to the legacy
`GloBirdProvider`. This is the single decision that gates v1.5.0
rollout — once a user's config_entry has a `cdr_plan`, they switch
to the CDR engine; otherwise they continue on the v1.4.x path.

We can't easily instantiate `PriceHawkCoordinator` in unit tests
(it constructs an HA `DataUpdateCoordinator` which needs a real
HomeAssistant runtime). Instead we test the selection logic in
isolation: import both provider classes and verify the dispatch
predicate works.
"""
from __future__ import annotations

import json
from pathlib import Path

from custom_components.pricehawk.providers.globird import GloBirdProvider
from custom_components.pricehawk.providers.globird_cdr import CdrGloBirdProvider

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "phase0"


def _select_provider(options: dict):
    """Replicates coordinator.py's selection branch exactly."""
    cdr_plan = options.get("cdr_plan")
    if cdr_plan:
        return CdrGloBirdProvider(cdr_plan)
    return GloBirdProvider(options)


def test_select_legacy_when_no_cdr_plan() -> None:
    """v1.4.x install: no cdr_plan key -> legacy GloBirdProvider."""
    legacy_options = {
        "daily_supply_charge": 113.30,
        "demand_charge": 0.0,
        "import_tariff": {
            "type": "tou",
            "periods": {
                "peak": {"rate": 38.50, "windows": [["16:00", "23:00"]]},
                "offpeak": {"rate": 0.00, "windows": [["11:00", "14:00"]]},
                "shoulder": {"rate": 26.95, "windows": [["23:00", "00:00"], ["00:00", "11:00"], ["14:00", "16:00"]]},
            },
        },
        "export_tariff": {"type": "tou", "periods": {}},
        "incentives": [],
    }
    p = _select_provider(legacy_options)
    assert isinstance(p, GloBirdProvider)
    assert p.id == "globird"


def test_select_cdr_when_plan_present() -> None:
    """v1.5.0 install: cdr_plan in options -> CdrGloBirdProvider."""
    cdr_plan = json.loads(
        (FIXTURE_DIR / "plan_globird_GLO731031MR@VEC.json").read_text()
    )
    options = {"cdr_plan": cdr_plan}
    p = _select_provider(options)
    assert isinstance(p, CdrGloBirdProvider)
    assert p.id == "globird"
    assert "CDR" in p.name


def test_both_providers_satisfy_protocol() -> None:
    """Provider Protocol conformance for both paths."""
    from custom_components.pricehawk.providers.base import Provider

    cdr_plan = json.loads(
        (FIXTURE_DIR / "plan_globird_GLO731031MR@VEC.json").read_text()
    )
    cdr_provider = CdrGloBirdProvider(cdr_plan)
    assert isinstance(cdr_provider, Provider)

    legacy_options = {
        "daily_supply_charge": 113.30,
        "import_tariff": {"type": "tou", "periods": {}},
        "export_tariff": {"type": "tou", "periods": {}},
        "incentives": [],
    }
    legacy_provider = GloBirdProvider(legacy_options)
    assert isinstance(legacy_provider, Provider)


def test_cdr_provider_drop_in_property_shape() -> None:
    """Drop-in replacement: every property the coordinator reads from
    legacy GloBirdProvider must exist on CdrGloBirdProvider with the
    same return type."""
    cdr_plan = json.loads(
        (FIXTURE_DIR / "plan_globird_GLO731031MR@VEC.json").read_text()
    )
    p = CdrGloBirdProvider(cdr_plan)

    # Properties read by coordinator._build_data_dict()
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
