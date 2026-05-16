"""Tests for cdr.registry — EME refdata2 retailer endpoint registry.

Covers:
- Pure-Python envelope parsing against the EME refdata2 shape.
- ``cdr_brand`` discriminator preserved for shared base URIs.
- Baked-in EME JSON loadable, well-formed, contains the big-4 retailers.
- ``fetch_live`` happy path returns parsed entries.
- ``fetch_live`` failure modes (HTTP, network, malformed body) raise
  ``CdrUnavailable``.
- ``get_registry`` falls back to baked-in when live fetch fails.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.pricehawk.cdr.cdr_client import CdrUnavailable
from custom_components.pricehawk.cdr.registry import (
    LIVE_REGISTRY_URL,
    RetailerEndpoint,
    baked_in_path_for_test,
    fetch_live,
    find_by_brand,
    get_registry,
    load_baked_in,
    parse_eme_for_test,
)


# ---------------------------------------------------------------------------
# EME refdata2 envelope parsing
# ---------------------------------------------------------------------------


class TestParseEmeEntries:
    def test_parses_single_org(self):
        raw = {
            "data": {
                "organisations": {
                    "9611": {
                        "tradingName": "CovaU Pty Ltd",
                        "orgName": "CovaU",
                        "cdrCode": "covau",
                        "cdrBrand": "covau",
                        "abn": "54 090 117 730",
                        "logo": "/static/organisations/logos/cova_u.png",
                    }
                }
            }
        }
        result = parse_eme_for_test(raw)
        assert len(result) == 1
        e = result[0]
        assert e.brand_id == "9611"
        assert e.brand_name == "CovaU Pty Ltd"  # tradingName preferred
        assert e.base_uri == "https://cdr.energymadeeasy.gov.au/covau"
        assert e.cdr_brand == "covau"
        assert e.abn == "54 090 117 730"
        assert e.logo_uri == (
            "https://energymadeeasy.gov.au/static/organisations/logos/cova_u.png"
        )

    def test_falls_back_to_org_name_when_trading_name_missing(self):
        raw = {
            "data": {
                "organisations": {
                    "1": {
                        "orgName": "Foo Energy",
                        "cdrCode": "foo",
                        "cdrBrand": "foo",
                    }
                }
            }
        }
        assert parse_eme_for_test(raw)[0].brand_name == "Foo Energy"

    def test_skips_orgs_missing_cdr_code(self):
        raw = {
            "data": {
                "organisations": {
                    "1": {"orgName": "No Code", "cdrBrand": "x"},
                    "2": {
                        "orgName": "Has Code",
                        "cdrCode": "has-code",
                        "cdrBrand": "has-code",
                    },
                }
            }
        }
        assert [e.brand_name for e in parse_eme_for_test(raw)] == ["Has Code"]

    def test_strips_trailing_whitespace_in_cdr_brand(self):
        """Upstream EME has trailing-space bugs in several cdrBrand fields
        (Aurora, Brighte, Amber etc). Strip so ``?brand=amber+`` doesn't
        end up sent to the CDR endpoint."""
        raw = {
            "data": {
                "organisations": {
                    "1": {
                        "orgName": "Aurora Energy",
                        "cdrCode": "aurora",
                        "cdrBrand": "aurora ",  # bug in upstream
                    }
                }
            }
        }
        assert parse_eme_for_test(raw)[0].cdr_brand == "aurora"

    def test_strips_trailing_whitespace_in_display_name(self):
        """Same trailing-space bug appears in tradingName / orgName on
        some EME orgs. Trim so UI labels don't render with stray spaces."""
        raw = {
            "data": {
                "organisations": {
                    "1": {
                        "orgName": "Origin Energy ",  # trailing space
                        "cdrCode": "origin",
                        "cdrBrand": "origin",
                    }
                }
            }
        }
        assert parse_eme_for_test(raw)[0].brand_name == "Origin Energy"

    def test_non_string_fields_safely_skipped(self):
        """EME has been observed shipping non-string values in fields
        we expect to be strings (numeric cdrCode, None tradingName).
        Parser must not raise — affected orgs are silently dropped."""
        raw = {
            "data": {
                "organisations": {
                    "bad_code": {
                        "orgName": "Numeric cdrCode",
                        "cdrCode": 12345,  # int, not str
                        "cdrBrand": "x",
                    },
                    "bad_name": {
                        "orgName": None,
                        "tradingName": None,
                        "cdrCode": "no-name",
                        "cdrBrand": "no-name",
                    },
                    "good": {
                        "orgName": "Good Org",
                        "cdrCode": "good",
                        "cdrBrand": "good",
                    },
                }
            }
        }
        result = parse_eme_for_test(raw)
        assert [e.brand_name for e in result] == ["Good Org"]

    def test_preserves_brand_discriminator_for_shared_base_uris(self):
        """Energy Locals hosts seven brands. Each org gets the same base
        URI but a distinct ``cdr_brand`` so plan list/detail can be
        disambiguated via ``?brand=``."""
        raw = {
            "data": {
                "organisations": {
                    "1": {
                        "orgName": "Energy Locals",
                        "cdrCode": "energy-locals",
                        "cdrBrand": "energy-locals",
                    },
                    "2": {
                        "orgName": "ARCLINE by RACV",
                        "cdrCode": "energy-locals",
                        "cdrBrand": "arcline",
                    },
                    "3": {
                        "orgName": "Cooperative Power",
                        "cdrCode": "energy-locals",
                        "cdrBrand": "cooperative",
                    },
                }
            }
        }
        result = parse_eme_for_test(raw)
        assert {e.base_uri for e in result} == {
            "https://cdr.energymadeeasy.gov.au/energy-locals"
        }
        assert {e.cdr_brand for e in result} == {
            "energy-locals", "arcline", "cooperative",
        }

    def test_invalid_root_raises(self):
        with pytest.raises(ValueError):
            parse_eme_for_test([])  # type: ignore[arg-type]

    def test_missing_organisations_raises(self):
        with pytest.raises(ValueError):
            parse_eme_for_test({"data": {"thirdParties": {}}})

    def test_organisations_not_dict_raises(self):
        with pytest.raises(ValueError):
            parse_eme_for_test({"data": {"organisations": "garbage"}})

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

    def test_baked_in_has_organisations(self):
        raw = json.loads(baked_in_path_for_test().read_text())
        assert "data" in raw
        assert isinstance(raw["data"], dict)
        orgs = raw["data"].get("organisations")
        assert isinstance(orgs, dict)
        # EME shipped 117 orgs at time of bake; >50 is a generous floor.
        assert len(orgs) > 50

    def test_load_baked_in_contains_big_4(self):
        endpoints = load_baked_in()
        names = {e.brand_name.lower() for e in endpoints}
        for required in ["origin", "agl", "energyaustralia", "red energy"]:
            assert any(required in n for n in names), (
                f"baked-in registry missing required brand fragment '{required}'"
            )

    def test_load_baked_in_populates_cdr_brand(self):
        """EME exposes cdrBrand for every org; baked-in load must carry it
        through so shared-base-URI plans can be queried with ``?brand=``."""
        endpoints = load_baked_in()
        with_brand = [e for e in endpoints if e.cdr_brand]
        assert len(with_brand) > 50, "EME load lost cdr_brand on most entries"

    def test_find_by_brand_substring(self):
        endpoints = load_baked_in()
        agl = find_by_brand(endpoints, "AGL")
        assert agl is not None
        assert "AGL" in agl.brand_name
        assert agl.base_uri.startswith("https://")

    def test_find_by_brand_miss(self):
        endpoints = load_baked_in()
        assert find_by_brand(endpoints, "NotARealRetailer123") is None


# ---------------------------------------------------------------------------
# Async fetch + fallback
# ---------------------------------------------------------------------------


def _mock_session(status: int, body: dict | None) -> MagicMock:
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


_EME_BODY = {
    "data": {
        "organisations": {
            "1": {
                "orgName": "Test Retailer",
                "cdrCode": "test",
                "cdrBrand": "test",
            }
        }
    }
}


def test_fetch_live_happy_path():
    session = _mock_session(200, _EME_BODY)
    result = asyncio.run(fetch_live(session))
    assert len(result) == 1
    assert result[0].brand_name == "Test Retailer"
    assert result[0].cdr_brand == "test"


def test_fetch_live_non_200_raises_unavailable():
    session = _mock_session(503, None)
    with pytest.raises(CdrUnavailable):
        asyncio.run(fetch_live(session))


def test_fetch_live_network_error_raises_unavailable():
    session = MagicMock()

    def _get(_url, **_kwargs):
        import aiohttp
        raise aiohttp.ClientConnectorError(MagicMock(), OSError("nx"))

    session.get = MagicMock(side_effect=_get)
    with pytest.raises(CdrUnavailable):
        asyncio.run(fetch_live(session))


def test_fetch_live_malformed_body_raises_unavailable():
    """Schema drift / partial outage at EME should surface as
    ``CdrUnavailable`` so ``get_registry`` falls through to baked-in
    rather than crashing the wizard."""
    session = _mock_session(200, {"data": {"organisations": "garbage"}})
    with pytest.raises(CdrUnavailable):
        asyncio.run(fetch_live(session))


def test_fetch_live_uses_eme_url():
    """Smoke-check: the request hits the EME refdata2 URL, not any other."""
    seen: list[str] = []
    session = MagicMock()

    def _get(url, **_kwargs):
        seen.append(url)
        resp = MagicMock()
        resp.status = 200
        resp.json = AsyncMock(return_value=_EME_BODY)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    session.get = MagicMock(side_effect=_get)
    asyncio.run(fetch_live(session))
    assert seen == [LIVE_REGISTRY_URL]


def test_get_registry_prefers_live_when_available():
    session = _mock_session(200, _EME_BODY)
    endpoints, source = asyncio.run(get_registry(session))
    assert source == "live"
    assert any(e.brand_name == "Test Retailer" for e in endpoints)


def test_get_registry_falls_back_to_baked_in_on_failure():
    session = _mock_session(503, None)
    endpoints, source = asyncio.run(get_registry(session))
    assert source == "baked-in"
    assert len(endpoints) > 50  # baked-in EME has 117 at time of write


def test_get_registry_falls_back_on_malformed_live_body():
    session = _mock_session(200, {"data": "not-a-dict"})
    endpoints, source = asyncio.run(get_registry(session))
    assert source == "baked-in"
    assert len(endpoints) > 50


def test_get_registry_offline_mode_skips_network():
    session = MagicMock()
    session.get = MagicMock(side_effect=AssertionError("network was hit"))
    endpoints, source = asyncio.run(get_registry(session, prefer_live=False))
    assert source == "baked-in"
    assert len(endpoints) > 50
