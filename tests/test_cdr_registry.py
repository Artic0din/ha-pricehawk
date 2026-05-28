"""Tests for cdr.registry — EME refdata2 retailer endpoint registry.

Covers:
- Pure-Python envelope parsing against the EME refdata2 shape.
- ``cdr_brand`` discriminator preserved for shared base URIs.
- Baked-in EME JSON loadable, well-formed, contains the big-4 retailers.
- ``fetch_live`` happy path returns parsed entries.
- ``fetch_live`` failure modes (HTTP, network, timeout, malformed body)
  raise ``CdrUnavailable``.
- ``get_registry`` falls back to baked-in when live fetch fails.
"""

from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest
from aioresponses import aioresponses

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
# EME refdata2 envelope parsing (pure Python — no HTTP, leave as-is)
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
        assert e.logo_uri == ("https://energymadeeasy.gov.au/static/organisations/logos/cova_u.png")

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

    def test_logo_uri_normalised_to_str_or_none(self):
        """``RetailerEndpoint.logo_uri`` is typed ``str | None``. EME has
        been observed dropping odd shapes into the ``logo`` field
        (dicts, ints, empty strings); coerce to ``None`` so downstream
        consumers can rely on the declared type."""
        raw = {
            "data": {
                "organisations": {
                    "1": {
                        "orgName": "Dict Logo",
                        "cdrCode": "dict",
                        "cdrBrand": "dict",
                        "logo": {"url": "/foo.png"},  # dict, not str
                    },
                    "2": {
                        "orgName": "Empty Logo",
                        "cdrCode": "empty",
                        "cdrBrand": "empty",
                        "logo": "",
                    },
                    "3": {
                        "orgName": "None Logo",
                        "cdrCode": "none-logo",
                        "cdrBrand": "none-logo",
                        "logo": None,
                    },
                    "4": {
                        "orgName": "Absolute Logo",
                        "cdrCode": "abs",
                        "cdrBrand": "abs",
                        "logo": "https://cdn.example.com/x.png",
                    },
                    "5": {
                        "orgName": "Relative Logo",
                        "cdrCode": "rel",
                        "cdrBrand": "rel",
                        "logo": "/static/x.png",
                    },
                }
            }
        }
        by_name = {e.brand_name: e.logo_uri for e in parse_eme_for_test(raw)}
        assert by_name["Dict Logo"] is None
        assert by_name["Empty Logo"] is None
        assert by_name["None Logo"] is None
        assert by_name["Absolute Logo"] == "https://cdn.example.com/x.png"
        assert by_name["Relative Logo"] == ("https://energymadeeasy.gov.au/static/x.png")

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
        assert {e.base_uri for e in result} == {"https://cdr.energymadeeasy.gov.au/energy-locals"}
        assert {e.cdr_brand for e in result} == {
            "energy-locals",
            "arcline",
            "cooperative",
        }

    def test_invalid_root_raises(self):
        # TRY004: wrong-type input raises TypeError (was ValueError).
        with pytest.raises(TypeError):
            parse_eme_for_test([])  # type: ignore[arg-type]

    def test_missing_organisations_raises(self):
        # TRY004: organisations of wrong type raises TypeError.
        with pytest.raises(TypeError):
            parse_eme_for_test({"data": {"thirdParties": {}}})

    def test_organisations_not_dict_raises(self):
        # TRY004: organisations of wrong type raises TypeError.
        with pytest.raises(TypeError):
            parse_eme_for_test({"data": {"organisations": "garbage"}})

    def test_slug_normalises_brand_name(self):
        e = RetailerEndpoint(brand_id="x", brand_name="Red Energy", base_uri="https://x")
        assert e.slug == "red_energy"
        e2 = RetailerEndpoint(brand_id="y", brand_name="Energy Locals", base_uri="https://y")
        assert e2.slug == "energy_locals"


# ---------------------------------------------------------------------------
# Baked-in registry health (no HTTP)
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
# Shared EME response body for fetch_live / get_registry tests
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Async fetch_live — converted from hand-rolled MagicMock to aioresponses
# ---------------------------------------------------------------------------


def test_fetch_live_happy_path():
    # ARRANGE
    with aioresponses() as m:
        m.get(LIVE_REGISTRY_URL, status=200, payload=_EME_BODY)

        async def run():
            async with aiohttp.ClientSession() as session:
                return await fetch_live(session)

        # ACT
        result = asyncio.run(run())

    # ASSERT — correct endpoint called, result parsed
    assert len(result) == 1
    assert result[0].brand_name == "Test Retailer"
    assert result[0].cdr_brand == "test"


def test_fetch_live_non_200_raises_unavailable():
    # ARRANGE — 503 must raise CdrUnavailable (no retry in fetch_live)
    with aioresponses() as m:
        m.get(LIVE_REGISTRY_URL, status=503)

        async def run():
            async with aiohttp.ClientSession() as session:
                await fetch_live(session)

        # ACT + ASSERT
        with pytest.raises(CdrUnavailable):
            asyncio.run(run())


def test_fetch_live_network_error_raises_unavailable():
    # ARRANGE — aiohttp.ClientConnectorError wraps an OS-level failure
    with aioresponses() as m:
        import unittest.mock

        m.get(
            LIVE_REGISTRY_URL,
            exception=aiohttp.ClientConnectorError(unittest.mock.MagicMock(), OSError("nx")),
        )

        async def run():
            async with aiohttp.ClientSession() as session:
                await fetch_live(session)

        # ACT + ASSERT
        with pytest.raises(CdrUnavailable):
            asyncio.run(run())


def test_fetch_live_timeout_raises_unavailable():
    # ARRANGE — asyncio.TimeoutError from the network call
    with aioresponses() as m:
        m.get(LIVE_REGISTRY_URL, exception=asyncio.TimeoutError())

        async def run():
            async with aiohttp.ClientSession() as session:
                await fetch_live(session)

        # ACT + ASSERT — TimeoutError caught by BLE001 handler → CdrUnavailable
        with pytest.raises(CdrUnavailable):
            asyncio.run(run())


def test_fetch_live_malformed_body_raises_unavailable():
    """Schema drift / partial outage at EME should surface as
    ``CdrUnavailable`` so ``get_registry`` falls through to baked-in
    rather than crashing the wizard."""
    # ARRANGE
    with aioresponses() as m:
        m.get(LIVE_REGISTRY_URL, status=200, payload={"data": {"organisations": "garbage"}})

        async def run():
            async with aiohttp.ClientSession() as session:
                await fetch_live(session)

        # ACT + ASSERT
        with pytest.raises(CdrUnavailable):
            asyncio.run(run())


def test_fetch_live_uses_eme_url():
    """The request must hit the EME refdata2 host + path, not any other endpoint."""
    # ARRANGE — exact URL match; aioresponses raises ConnectionError if the
    # URL doesn't satisfy the registered route, so just reaching this assertion
    # already proves the correct host was contacted.
    with aioresponses() as m:
        m.get(LIVE_REGISTRY_URL, status=200, payload=_EME_BODY)

        async def run():
            async with aiohttp.ClientSession() as session:
                return await fetch_live(session)

        # ACT
        asyncio.run(run())

    # ASSERT — yarl percent-encodes commas; check host + path instead of full URL
    called = list(m.requests.keys())
    assert len(called) == 1
    _, called_url = called[0]
    assert called_url.host == "api.energymadeeasy.gov.au"
    assert called_url.path == "/refdata2"


# ---------------------------------------------------------------------------
# get_registry fallback logic
# ---------------------------------------------------------------------------


def test_get_registry_prefers_live_when_available():
    # ARRANGE
    with aioresponses() as m:
        m.get(LIVE_REGISTRY_URL, status=200, payload=_EME_BODY)

        async def run():
            async with aiohttp.ClientSession() as session:
                return await get_registry(session)

        # ACT
        endpoints, source = asyncio.run(run())

    # ASSERT — live source used, test retailer present
    assert source == "live"
    assert any(e.brand_name == "Test Retailer" for e in endpoints)


def test_get_registry_falls_back_to_baked_in_on_5xx():
    # ARRANGE — live fetch fails with 503
    with aioresponses() as m:
        m.get(LIVE_REGISTRY_URL, status=503)

        async def run():
            async with aiohttp.ClientSession() as session:
                return await get_registry(session)

        # ACT
        endpoints, source = asyncio.run(run())

    # ASSERT — baked-in fallback used
    assert source == "baked-in"
    assert len(endpoints) > 50  # baked-in EME has 117 at time of write


def test_get_registry_falls_back_on_malformed_live_body():
    # ARRANGE — 200 but body is malformed
    with aioresponses() as m:
        m.get(LIVE_REGISTRY_URL, status=200, payload={"data": "not-a-dict"})

        async def run():
            async with aiohttp.ClientSession() as session:
                return await get_registry(session)

        # ACT
        endpoints, source = asyncio.run(run())

    # ASSERT
    assert source == "baked-in"
    assert len(endpoints) > 50


def test_get_registry_falls_back_on_timeout():
    # ARRANGE — live fetch times out
    with aioresponses() as m:
        m.get(LIVE_REGISTRY_URL, exception=asyncio.TimeoutError())

        async def run():
            async with aiohttp.ClientSession() as session:
                return await get_registry(session)

        # ACT
        endpoints, source = asyncio.run(run())

    # ASSERT — baked-in used, not an unhandled exception
    assert source == "baked-in"
    assert len(endpoints) > 50


def test_get_registry_offline_mode_skips_network():
    # ARRANGE — prefer_live=False; any network call is a test failure
    async def run():
        async with aiohttp.ClientSession() as session:
            return await get_registry(session, prefer_live=False)

    # ACT — no aioresponses context; any real network hit raises ConnectionError
    endpoints, source = asyncio.run(run())

    # ASSERT
    assert source == "baked-in"
    assert len(endpoints) > 50
