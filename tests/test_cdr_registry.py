"""Tests for cdr.registry — Phase 2.1 retailer endpoint registry.

Covers:
- Pure-Python envelope parsing against the jxeeno shape.
- Baked-in JSON is loadable, well-formed, and contains the big-4 retailers.
- ``fetch_live`` happy path returns parsed entries.
- ``fetch_live`` failure modes raise CdrUnavailable.
- ``get_registry`` falls back to baked-in when live fetch fails.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.pricehawk.cdr.cdr_client import CdrUnavailable
from custom_components.pricehawk.cdr.registry import (
    RetailerEndpoint,
    baked_in_path_for_test,
    fetch_live,
    find_by_brand,
    get_registry,
    load_baked_in,
    parse_entries_for_test,
)


# ---------------------------------------------------------------------------
# Pure-Python envelope parsing
# ---------------------------------------------------------------------------


class TestParseEntries:
    def test_parses_single_entry(self):
        raw = {
            "data": [
                {
                    "dataHolderBrandId": "abc",
                    "brandName": "Origin Energy",
                    "productReferenceDataBaseUri": "https://example/origin/",
                    "logoUri": "https://example/logo.png",
                    "abn": "12345",
                    "lastUpdated": "2026-05-01",
                }
            ]
        }
        result = parse_entries_for_test(raw)
        assert len(result) == 1
        e = result[0]
        assert e.brand_id == "abc"
        assert e.brand_name == "Origin Energy"
        # Trailing slash is stripped so callers can join URL segments cleanly.
        assert e.base_uri == "https://example/origin"
        assert e.logo_uri == "https://example/logo.png"

    def test_skips_entries_missing_required_fields(self):
        raw = {
            "data": [
                {"brandName": "X", "productReferenceDataBaseUri": "https://x"},
                {"dataHolderBrandId": "1", "brandName": "Y"},  # no base
                {
                    "dataHolderBrandId": "2",
                    "brandName": "Z",
                    "productReferenceDataBaseUri": "https://z",
                },
            ]
        }
        result = parse_entries_for_test(raw)
        # Entry 1 has no brand_id, entry 2 has no base — both skipped.
        # Entry 3 is complete.
        assert [e.brand_name for e in result] == ["Z"]

    def test_invalid_root_raises(self):
        with pytest.raises(ValueError):
            parse_entries_for_test([1, 2, 3])  # type: ignore[arg-type]

    def test_missing_data_field_raises(self):
        with pytest.raises(ValueError):
            parse_entries_for_test({"not_data": []})

    def test_slug_normalises_brand_name(self):
        e = RetailerEndpoint(brand_id="x", brand_name="Red Energy", base_uri="https://x")
        assert e.slug == "red_energy"
        e2 = RetailerEndpoint(brand_id="y", brand_name="Energy Locals", base_uri="https://y")
        assert e2.slug == "energy_locals"


# ---------------------------------------------------------------------------
# Baked-in registry health
# ---------------------------------------------------------------------------


class TestBakedIn:
    def test_baked_in_path_exists(self):
        assert baked_in_path_for_test().is_file()

    def test_baked_in_has_data_field(self):
        raw = json.loads(baked_in_path_for_test().read_text())
        assert "data" in raw
        assert isinstance(raw["data"], list)
        assert len(raw["data"]) > 10  # Sanity: jxeeno had 78 at time of bake

    def test_load_baked_in_contains_big_4(self):
        endpoints = load_baked_in()
        names = {e.brand_name.lower() for e in endpoints}
        # Big-4 AU retailers must be present; if not, the bake is stale.
        for required in ["origin", "agl", "energyaustralia", "red energy"]:
            assert any(required in n for n in names), (
                f"baked-in registry missing required brand fragment '{required}'"
            )

    def test_find_by_brand_substring(self):
        endpoints = load_baked_in()
        agl = find_by_brand(endpoints, "AGL")
        assert agl is not None
        assert "AGL" in agl.brand_name
        assert agl.base_uri.startswith("https://")

    def test_find_by_brand_miss(self):
        endpoints = load_baked_in()
        result = find_by_brand(endpoints, "NotARealRetailer123")
        assert result is None


# ---------------------------------------------------------------------------
# Async fetch + fallback
# ---------------------------------------------------------------------------


def _mock_session_for_url(status: int, body: dict | None) -> MagicMock:
    session = MagicMock()

    def _get(_url, **_kwargs):
        resp = MagicMock()
        resp.status = status
        resp.json = AsyncMock(return_value=body or {})
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    session.get = MagicMock(side_effect=_get)
    return session


def test_fetch_live_happy_path():
    body = {
        "data": [
            {
                "dataHolderBrandId": "id",
                "brandName": "Test Retailer",
                "productReferenceDataBaseUri": "https://test/",
            }
        ]
    }
    session = _mock_session_for_url(200, body)
    result = asyncio.run(fetch_live(session))
    assert len(result) == 1
    assert result[0].brand_name == "Test Retailer"


def test_fetch_live_non_200_raises_unavailable():
    session = _mock_session_for_url(503, None)
    with pytest.raises(CdrUnavailable):
        asyncio.run(fetch_live(session))


def test_fetch_live_network_error_raises_unavailable():
    session = MagicMock()

    def _get(_url, **_kwargs):
        # Simulate aiohttp.ClientError mid-request
        import aiohttp
        raise aiohttp.ClientConnectorError(MagicMock(), OSError("nx"))

    session.get = MagicMock(side_effect=_get)
    with pytest.raises(CdrUnavailable):
        asyncio.run(fetch_live(session))


def test_get_registry_prefers_live_when_available():
    body = {
        "data": [
            {
                "dataHolderBrandId": "id",
                "brandName": "Live Retailer",
                "productReferenceDataBaseUri": "https://live/",
            }
        ]
    }
    session = _mock_session_for_url(200, body)
    endpoints, source = asyncio.run(get_registry(session))
    assert source == "live"
    assert any(e.brand_name == "Live Retailer" for e in endpoints)


def test_get_registry_falls_back_to_baked_in_on_failure():
    session = _mock_session_for_url(503, None)
    endpoints, source = asyncio.run(get_registry(session))
    assert source == "baked-in"
    assert len(endpoints) > 10  # baked-in has 78 at time of write


def test_get_registry_offline_mode_skips_network():
    session = MagicMock()
    # If prefer_live=False, session.get must NEVER be called.
    session.get = MagicMock(side_effect=AssertionError("network was hit"))
    endpoints, source = asyncio.run(get_registry(session, prefer_live=False))
    assert source == "baked-in"
    assert len(endpoints) > 10
