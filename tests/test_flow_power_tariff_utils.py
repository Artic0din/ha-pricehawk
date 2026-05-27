"""Smoke tests for the vendored Flow Power tariff_utils module.

Tests that only need the const tables run unconditionally. Tests that
exercise the ``aemo_to_tariff`` library skip cleanly when the library
isn't installed — PR 4 will declare it in ``manifest.json`` so HA
installs it on integration setup, at which point these tests light up
in CI without further code changes.
"""

from __future__ import annotations

import pytest

from custom_components.pricehawk.wholesale.flow_power import tariff_utils
from custom_components.pricehawk.wholesale.flow_power.const import (
    NEM_REGIONS,
    NETWORK_API_NAME,
    NETWORK_MODULE_NAME,
    NETWORK_TARIFF_URL,
    REGION_NETWORKS,
)


def test_get_networks_for_region_returns_expected_dnsps() -> None:
    """Region → DNSP list lookup is a pure const read; no library needed."""
    assert tariff_utils.get_networks_for_region("NSW1") == [
        "Ausgrid", "Endeavour", "Essential",
    ]
    assert tariff_utils.get_networks_for_region("SA1") == ["SAPN"]
    assert tariff_utils.get_networks_for_region("VIC1") == [
        "Powercor", "CitiPower", "AusNet", "Jemena", "United",
    ]


def test_get_networks_for_region_unknown_returns_empty_list() -> None:
    """Unknown region → empty list, never None or raise."""
    assert tariff_utils.get_networks_for_region("UNKNOWN") == []
    assert tariff_utils.get_networks_for_region("") == []


def test_constant_tables_are_consistent() -> None:
    """Every DNSP listed in REGION_NETWORKS must have entries in all
    three lookup dicts. Catches drift if upstream re-vendors and forgets
    a row."""
    all_dnsps_in_regions = {
        dnsp for dnsps in REGION_NETWORKS.values() for dnsp in dnsps
    }
    for dnsp in all_dnsps_in_regions:
        assert dnsp in NETWORK_API_NAME, f"{dnsp} missing from NETWORK_API_NAME"
        assert dnsp in NETWORK_MODULE_NAME, f"{dnsp} missing from NETWORK_MODULE_NAME"
        assert dnsp in NETWORK_TARIFF_URL, f"{dnsp} missing from NETWORK_TARIFF_URL"


def test_nem_regions_match_region_networks_keys() -> None:
    """NEM_REGIONS keys are the canonical NEM region codes; REGION_NETWORKS
    keys must match exactly."""
    assert set(NEM_REGIONS.keys()) == set(REGION_NETWORKS.keys())


def test_get_tariff_codes_for_unknown_network_returns_empty() -> None:
    """Unknown DNSP → empty list (warns but doesn't raise)."""
    pytest.importorskip("aemo_to_tariff")
    assert tariff_utils.get_tariff_codes_for_network("NotAnyRealDNSP") == []


def test_get_tariff_codes_for_known_network() -> None:
    """A known DNSP returns a non-empty list of tariff codes from the library."""
    pytest.importorskip("aemo_to_tariff")
    codes = tariff_utils.get_tariff_codes_for_network("Ausgrid")
    assert isinstance(codes, list)
    assert len(codes) > 0
    assert all(isinstance(code, str) for code in codes)


def test_get_network_tariff_rate_returns_float_or_none() -> None:
    """spot_to_tariff path: signature works, returns float or None on error."""
    pytest.importorskip("aemo_to_tariff")
    from datetime import datetime, timezone

    result = tariff_utils.get_network_tariff_rate(
        dt=datetime.now(timezone.utc),
        network="ausgrid",
        tariff_code="EA025",  # may or may not be valid; test asserts shape only
    )
    assert result is None or isinstance(result, float)
