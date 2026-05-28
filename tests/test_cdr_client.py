"""Tests for cdr.cdr_client — Phase 2.0 async CDR HTTP client.

Pure-Python helper coverage + AsyncMock-driven coverage of the
retry/error-mapping logic in `_get_json`. We avoid spinning up an
aiohttp TestServer to keep the test suite import-free of CI deps and
match the lightweight style of `test_aemo_api.py`.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

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
# Pure-Python helpers
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
# Async retry / error-mapping behaviour (driven via AsyncMock)
# ---------------------------------------------------------------------------


def _mock_session_returning(
    *responses: tuple[int, dict | None],
) -> MagicMock:
    """Build a mock aiohttp.ClientSession whose .get() context-manager yields
    the queued (status, json_body) tuples in order."""
    session = MagicMock()
    queue = list(responses)

    def _get(url, **_kwargs):
        status, body = queue.pop(0)
        resp = MagicMock()
        resp.status = status
        resp.json = AsyncMock(return_value=body or {})
        resp.text = AsyncMock(return_value="")
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    session.get = MagicMock(side_effect=_get)
    return session


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Replace cdr_client's asyncio.sleep with a no-op so retry backoffs do
    not block tests. Only patches `sleep` on the module's asyncio reference;
    leaves the rest of the asyncio API intact."""
    from custom_components.pricehawk.cdr import cdr_client as _mod

    async def _instant_sleep(_secs):
        return None

    monkeypatch.setattr(_mod.asyncio, "sleep", _instant_sleep)


def test_fetch_plan_list_happy_path():
    plans = [
        {"planId": "A", "customerType": "RESIDENTIAL", "fuelType": "ELECTRICITY"},
        {"planId": "B", "customerType": "BUSINESS", "fuelType": "ELECTRICITY"},
    ]
    envelope = build_list_envelope_for_test(plans)
    session = _mock_session_returning((200, envelope))

    result = asyncio.run(fetch_plan_list(session, "https://test"))

    # Boundary filter strips non-residential.
    assert [p["planId"] for p in result] == ["A"]


def test_fetch_plan_list_paginates():
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
    session = _mock_session_returning((200, page1), (200, page2))

    result = asyncio.run(fetch_plan_list(session, "https://test"))

    assert [p["planId"] for p in result] == ["A", "B"]


def test_fetch_plan_detail_happy_path():
    detail = build_detail_envelope_for_test({"planId": "Z", "displayName": "Z"})
    session = _mock_session_returning((200, detail))

    result = asyncio.run(fetch_plan_detail(session, "https://test", "Z"))

    assert result["data"]["planId"] == "Z"


def test_fetch_plan_detail_404_raises_plan_not_found():
    session = _mock_session_returning((404, None))

    with pytest.raises(CdrPlanNotFound):
        asyncio.run(fetch_plan_detail(session, "https://test", "stale"))


def test_5xx_retries_then_succeeds():
    detail = build_detail_envelope_for_test({"planId": "Z"})
    session = _mock_session_returning((503, None), (200, detail))

    result = asyncio.run(fetch_plan_detail(session, "https://test", "Z"))

    assert result["data"]["planId"] == "Z"


def test_5xx_retries_exhausted_raises_unavailable():
    session = _mock_session_returning(
        (503, None),
        (503, None),
        (503, None),
    )

    with pytest.raises(CdrUnavailable):
        asyncio.run(fetch_plan_detail(session, "https://test", "Z"))


def test_429_retries_then_succeeds():
    detail = build_detail_envelope_for_test({"planId": "Z"})
    session = _mock_session_returning((429, None), (200, detail))

    result = asyncio.run(fetch_plan_detail(session, "https://test", "Z"))

    assert result["data"]["planId"] == "Z"


def test_unexpected_4xx_raises_api_error():
    session = _mock_session_returning((400, None))

    with pytest.raises(CdrAPIError):
        asyncio.run(fetch_plan_detail(session, "https://test", "Z"))


# ---------------------------------------------------------------------------
# Brand disambiguation (Phase 3.1 prep) — shared base URIs need ?brand=
# ---------------------------------------------------------------------------


def _mock_session_capturing(*responses: tuple[int, dict | None]):
    """Like _mock_session_returning but also records every URL requested
    so tests can assert query-string composition."""
    seen: list[str] = []
    queue = list(responses)
    session = MagicMock()

    def _get(url, **_kwargs):
        seen.append(url)
        status, body = queue.pop(0)
        resp = MagicMock()
        resp.status = status
        resp.json = AsyncMock(return_value=body or {})
        resp.text = AsyncMock(return_value="")
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    session.get = MagicMock(side_effect=_get)
    return session, seen


def test_fetch_plan_list_appends_brand_when_set():
    envelope = build_list_envelope_for_test([])
    session, seen = _mock_session_capturing((200, envelope))

    asyncio.run(fetch_plan_list(session, "https://test", brand="arcline"))

    assert len(seen) == 1
    assert "brand=arcline" in seen[0]


def test_fetch_plan_list_omits_brand_param_when_none():
    envelope = build_list_envelope_for_test([])
    session, seen = _mock_session_capturing((200, envelope))

    asyncio.run(fetch_plan_list(session, "https://test"))

    assert "brand=" not in seen[0]


def test_fetch_plan_detail_appends_brand_when_set():
    detail = build_detail_envelope_for_test({"planId": "Z"})
    session, seen = _mock_session_capturing((200, detail))

    asyncio.run(fetch_plan_detail(session, "https://test", "Z", brand="cooperative"))

    assert "?brand=cooperative" in seen[0]


def test_fetch_plan_detail_omits_brand_when_none():
    detail = build_detail_envelope_for_test({"planId": "Z"})
    session, seen = _mock_session_capturing((200, detail))

    asyncio.run(fetch_plan_detail(session, "https://test", "Z"))

    assert "?" not in seen[0]
