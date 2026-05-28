"""Tests for cdr.cdr_client — Phase 2.0 async CDR HTTP client.

Pure-Python helper coverage + aioresponses-driven coverage of the
retry/error-mapping logic in `_get_json`.
"""

from __future__ import annotations

import asyncio
import re

import aiohttp
import pytest
from aioresponses import aioresponses
from unittest.mock import AsyncMock

from custom_components.pricehawk.cdr.cdr_client import (
    CdrAPIError,
    CdrPlanNotFound,
    CdrUnavailable,
    build_detail_envelope_for_test,
    build_list_envelope_for_test,
    fetch_plan_detail,
    fetch_plan_list,
    filter_residential_electricity_for_test,
)

# ---------------------------------------------------------------------------
# URL patterns used by cdr_client — match any CDR plan endpoint
# ---------------------------------------------------------------------------

_ANY_PLANS_URL = re.compile(r"https://test/cds-au/v1/energy/plans.*")
_ANY_PLAN_DETAIL_URL = re.compile(r"https://test/cds-au/v1/energy/plans/.*")

# ---------------------------------------------------------------------------
# Pure-Python helpers (no HTTP — leave as-is)
# ---------------------------------------------------------------------------


class TestEnvelopeBuilders:
    def test_list_envelope_shape(self):
        env = build_list_envelope_for_test([{"planId": "A", "displayName": "Plan A"}])
        assert env["data"]["plans"][0]["planId"] == "A"
        assert env["meta"]["totalPages"] == 1
        assert env["meta"]["totalRecords"] == 1

    def test_detail_envelope_shape(self):
        env = build_detail_envelope_for_test({"planId": "X", "displayName": "X"})
        assert env["data"]["planId"] == "X"
        assert "links" in env


class TestResidentialFilter:
    def test_keeps_residential_electricity_market(self):
        plans = [
            {"customerType": "RESIDENTIAL", "fuelType": "ELECTRICITY", "planId": "A"},
            {"customerType": "BUSINESS", "fuelType": "ELECTRICITY", "planId": "B"},
            {"customerType": "RESIDENTIAL", "fuelType": "GAS", "planId": "C"},
        ]
        result = filter_residential_electricity_for_test(plans)
        assert [p["planId"] for p in result] == ["A"]

    def test_empty_list_is_empty(self):
        assert filter_residential_electricity_for_test([]) == []


# ---------------------------------------------------------------------------
# Async retry / error-mapping behaviour via aioresponses
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Replace cdr_client's asyncio.sleep with a no-op so retry backoffs do
    not block tests."""
    from custom_components.pricehawk.cdr import cdr_client as _mod

    monkeypatch.setattr(_mod.asyncio, "sleep", AsyncMock())


def test_fetch_plan_list_happy_path():
    # ARRANGE
    plans = [
        {"planId": "A", "customerType": "RESIDENTIAL", "fuelType": "ELECTRICITY"},
        {"planId": "B", "customerType": "BUSINESS", "fuelType": "ELECTRICITY"},
    ]
    envelope = build_list_envelope_for_test(plans)

    with aioresponses() as m:
        m.get(_ANY_PLANS_URL, status=200, payload=envelope)

        # ACT
        async def run():
            async with aiohttp.ClientSession() as session:
                return await fetch_plan_list(session, "https://test")

        result = asyncio.run(run())

    # ASSERT — non-residential plan filtered out
    assert [p["planId"] for p in result] == ["A"]


def test_fetch_plan_list_paginates():
    # ARRANGE — two pages; both returned
    page1 = {
        "data": {
            "plans": [
                {"planId": "A", "customerType": "RESIDENTIAL", "fuelType": "ELECTRICITY"},
            ]
        },
        "meta": {"totalPages": 2},
    }
    page2 = {
        "data": {
            "plans": [
                {"planId": "B", "customerType": "RESIDENTIAL", "fuelType": "ELECTRICITY"},
            ]
        },
        "meta": {"totalPages": 2},
    }

    with aioresponses() as m:
        m.get(_ANY_PLANS_URL, status=200, payload=page1)
        m.get(_ANY_PLANS_URL, status=200, payload=page2)

        async def run():
            async with aiohttp.ClientSession() as session:
                return await fetch_plan_list(session, "https://test")

        # ACT + ASSERT
        result = asyncio.run(run())

    assert [p["planId"] for p in result] == ["A", "B"]


def test_fetch_plan_detail_happy_path():
    # ARRANGE
    detail = build_detail_envelope_for_test({"planId": "Z", "displayName": "Z"})

    with aioresponses() as m:
        m.get(_ANY_PLAN_DETAIL_URL, status=200, payload=detail)

        # ACT
        async def run():
            async with aiohttp.ClientSession() as session:
                return await fetch_plan_detail(session, "https://test", "Z")

        result = asyncio.run(run())

    # ASSERT — endpoint called, planId preserved
    assert result["data"]["planId"] == "Z"


def test_fetch_plan_detail_404_raises_plan_not_found():
    # ARRANGE
    with aioresponses() as m:
        m.get(_ANY_PLAN_DETAIL_URL, status=404)

        async def run():
            async with aiohttp.ClientSession() as session:
                await fetch_plan_detail(session, "https://test", "stale")

        # ACT + ASSERT
        with pytest.raises(CdrPlanNotFound):
            asyncio.run(run())


def test_5xx_retries_then_succeeds():
    # ARRANGE — first attempt 503, second succeeds
    detail = build_detail_envelope_for_test({"planId": "Z"})

    with aioresponses() as m:
        m.get(_ANY_PLAN_DETAIL_URL, status=503)
        m.get(_ANY_PLAN_DETAIL_URL, status=200, payload=detail)

        async def run():
            async with aiohttp.ClientSession() as session:
                return await fetch_plan_detail(session, "https://test", "Z")

        # ACT + ASSERT
        result = asyncio.run(run())

    assert result["data"]["planId"] == "Z"


def test_5xx_retries_exhausted_raises_unavailable():
    # ARRANGE — all three retries return 503
    with aioresponses() as m:
        m.get(_ANY_PLAN_DETAIL_URL, status=503)
        m.get(_ANY_PLAN_DETAIL_URL, status=503)
        m.get(_ANY_PLAN_DETAIL_URL, status=503)

        async def run():
            async with aiohttp.ClientSession() as session:
                await fetch_plan_detail(session, "https://test", "Z")

        # ACT + ASSERT
        with pytest.raises(CdrUnavailable):
            asyncio.run(run())


def test_429_retries_then_succeeds():
    # ARRANGE — rate-limited on first attempt, succeeds on second
    detail = build_detail_envelope_for_test({"planId": "Z"})

    with aioresponses() as m:
        m.get(_ANY_PLAN_DETAIL_URL, status=429)
        m.get(_ANY_PLAN_DETAIL_URL, status=200, payload=detail)

        async def run():
            async with aiohttp.ClientSession() as session:
                return await fetch_plan_detail(session, "https://test", "Z")

        # ACT + ASSERT
        result = asyncio.run(run())

    assert result["data"]["planId"] == "Z"


def test_unexpected_4xx_raises_api_error():
    # ARRANGE — 400 Bad Request should not retry, raises CdrAPIError immediately
    with aioresponses() as m:
        m.get(_ANY_PLAN_DETAIL_URL, status=400)

        async def run():
            async with aiohttp.ClientSession() as session:
                await fetch_plan_detail(session, "https://test", "Z")

        # ACT + ASSERT
        with pytest.raises(CdrAPIError):
            asyncio.run(run())


def test_timeout_all_retries_exhausted_raises_unavailable():
    # ARRANGE — every attempt times out; _get_json must raise CdrUnavailable
    with aioresponses() as m:
        m.get(_ANY_PLAN_DETAIL_URL, exception=asyncio.TimeoutError())
        m.get(_ANY_PLAN_DETAIL_URL, exception=asyncio.TimeoutError())
        m.get(_ANY_PLAN_DETAIL_URL, exception=asyncio.TimeoutError())

        async def run():
            async with aiohttp.ClientSession() as session:
                await fetch_plan_detail(session, "https://test", "Z")

        # ACT + ASSERT — TimeoutError is a subtype of OSError, caught by
        # the aiohttp.ClientError / TimeoutError branch → CdrUnavailable
        with pytest.raises(CdrUnavailable):
            asyncio.run(run())


def test_network_error_all_retries_exhausted_raises_unavailable():
    # ARRANGE — aiohttp.ClientConnectionError on every attempt
    with aioresponses() as m:
        m.get(_ANY_PLAN_DETAIL_URL, exception=aiohttp.ClientConnectionError("nx"))
        m.get(_ANY_PLAN_DETAIL_URL, exception=aiohttp.ClientConnectionError("nx"))
        m.get(_ANY_PLAN_DETAIL_URL, exception=aiohttp.ClientConnectionError("nx"))

        async def run():
            async with aiohttp.ClientSession() as session:
                await fetch_plan_detail(session, "https://test", "Z")

        # ACT + ASSERT
        with pytest.raises(CdrUnavailable):
            asyncio.run(run())


# ---------------------------------------------------------------------------
# Brand query-param composition — assert ?brand= appended correctly
# ---------------------------------------------------------------------------


def test_fetch_plan_list_appends_brand_when_set():
    # ARRANGE
    envelope = build_list_envelope_for_test([])

    with aioresponses() as m:
        m.get(_ANY_PLANS_URL, status=200, payload=envelope)

        async def run():
            async with aiohttp.ClientSession() as session:
                await fetch_plan_list(session, "https://test", brand="arcline")

        asyncio.run(run())

    # ASSERT — the one registered route was matched (aioresponses raises if
    # the URL regex didn't match, so if we got here the brand param was present)
    called_url = str(list(m.requests.keys())[0][1])
    assert "brand=arcline" in called_url


def test_fetch_plan_list_omits_brand_param_when_none():
    # ARRANGE
    envelope = build_list_envelope_for_test([])

    with aioresponses() as m:
        m.get(_ANY_PLANS_URL, status=200, payload=envelope)

        async def run():
            async with aiohttp.ClientSession() as session:
                await fetch_plan_list(session, "https://test")

        asyncio.run(run())

    # ASSERT — brand= must not appear in the request URL
    called_url = str(list(m.requests.keys())[0][1])
    assert "brand=" not in called_url


def test_fetch_plan_detail_appends_brand_when_set():
    # ARRANGE
    detail = build_detail_envelope_for_test({"planId": "Z"})
    detail_url_with_brand = re.compile(r"https://test/cds-au/v1/energy/plans/Z\?brand=cooperative")

    with aioresponses() as m:
        m.get(detail_url_with_brand, status=200, payload=detail)

        async def run():
            async with aiohttp.ClientSession() as session:
                return await fetch_plan_detail(session, "https://test", "Z", brand="cooperative")

        # ACT + ASSERT — aioresponses raises ConnectionError if URL doesn't match
        result = asyncio.run(run())

    assert result["data"]["planId"] == "Z"


def test_fetch_plan_detail_omits_brand_when_none():
    # ARRANGE — exact URL without query string
    detail = build_detail_envelope_for_test({"planId": "Z"})
    exact_url = "https://test/cds-au/v1/energy/plans/Z"

    with aioresponses() as m:
        m.get(exact_url, status=200, payload=detail)

        async def run():
            async with aiohttp.ClientSession() as session:
                return await fetch_plan_detail(session, "https://test", "Z")

        # ACT + ASSERT — no ? in URL means no brand param
        result = asyncio.run(run())

    assert result["data"]["planId"] == "Z"
