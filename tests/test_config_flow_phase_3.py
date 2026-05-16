"""Phase 3.0g — wizard rewrite tests.

The HA config-flow step machinery needs a full HA test harness which
isn't available in the pure-Python mock layer. These tests cover the
pure helpers Phase 3 introduced + extracted from the wizard logic.
"""
from __future__ import annotations

from custom_components.pricehawk.config_flow import _api_provider_for_brand
from custom_components.pricehawk.const import (
    PROVIDER_AMBER,
    PROVIDER_FLOW_POWER,
    PROVIDER_LOCALVOLTS,
)


# --- _api_provider_for_brand ---------------------------------------


def test_amber_brand_maps_to_amber_provider():
    assert _api_provider_for_brand("amber") == PROVIDER_AMBER
    assert _api_provider_for_brand("amber-electric") == PROVIDER_AMBER
    assert _api_provider_for_brand("Amber Electric") == PROVIDER_AMBER


def test_flow_power_maps_to_flow_power_provider():
    assert _api_provider_for_brand("flow-power") == PROVIDER_FLOW_POWER
    assert _api_provider_for_brand("flow power") == PROVIDER_FLOW_POWER
    assert _api_provider_for_brand("Flow Power") == PROVIDER_FLOW_POWER


def test_localvolts_maps_to_localvolts_provider():
    assert _api_provider_for_brand("localvolts") == PROVIDER_LOCALVOLTS
    assert _api_provider_for_brand("LocalVolts") == PROVIDER_LOCALVOLTS


def test_globird_returns_none():
    """GloBird has no live consumer API — wizard skips API-connect."""
    assert _api_provider_for_brand("globird") is None


def test_origin_agl_red_return_none():
    """Big traditional retailers — no consumer API in v1.5.x."""
    assert _api_provider_for_brand("origin") is None
    assert _api_provider_for_brand("agl") is None
    assert _api_provider_for_brand("red-energy") is None


def test_unknown_brand_returns_none():
    assert _api_provider_for_brand("unknown-retailer") is None


def test_empty_returns_none():
    assert _api_provider_for_brand("") is None
    assert _api_provider_for_brand("   ") is None
