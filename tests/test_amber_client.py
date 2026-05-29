"""Failure-path tests for the Amber API client (``fetch_amber_sites``).

``fetch_amber_sites`` is the config-flow's API-key validator: it does a single
GET to the Amber ``/v1/sites`` endpoint and maps the outcome to one of three
domain exceptions (or returns the site list). It is the only Amber HTTP client
currently reachable in isolation — the coordinator's polling client
(``_fetch_amber_with_retry``) lives on ``PriceHawkCoordinator``, which subclasses
the conftest-mocked ``DataUpdateCoordinator`` and so cannot be instantiated
here. Testing that retry/backoff client requires extracting it into a
standalone module first (tracked separately).

Wiring note: these tests exercise the *real* ``aiohttp`` stack so aioresponses
can intercept the request. ``aiohttp`` is a real test dependency (pulled in by
aioresponses) and is no longer mocked in conftest, so the module-level
``import aiohttp`` in config_flow binds the genuine module. Only
``async_get_clientsession`` (normally HA-provided, mocked here) is patched, to
hand the function a real session aioresponses can see.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.pricehawk.config_flow import (
    CannotConnect,
    InvalidAuth,
    NoActiveSites,
    fetch_amber_sites,
)

SITES_URL = "https://api.amber.com.au/v1/sites"

# A representative two-site response shaped like the Amber /v1/sites payload.
SAMPLE_SITES = [
    {"id": "site-01", "nmi": "1234567890", "status": "active"},
    {"id": "site-02", "nmi": "0987654321", "status": "closed"},
]


async def _invoke(api_key: str = "test-key") -> list[dict]:
    """Call fetch_amber_sites against a real, interceptable aiohttp session.

    A genuine ClientSession is created inside the running loop and injected via
    async_get_clientsession so aioresponses (which patches real aiohttp) catches
    the GET. ``hass`` is irrelevant once the session is injected, so a sentinel
    object is passed.
    """
    async with aiohttp.ClientSession() as session:
        with patch(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession",
            return_value=session,
        ):
            return await fetch_amber_sites(hass=object(), api_key=api_key)


def _single_request_count(mocked: aioresponses) -> int:
    """Total number of HTTP requests aioresponses recorded across all routes."""
    return sum(len(calls) for calls in mocked.requests.values())


class TestFetchAmberSitesSuccess:
    """Happy-path behaviour."""

    def test_returns_site_list_on_200(self) -> None:
        with aioresponses() as mocked:
            mocked.get(SITES_URL, status=200, payload=SAMPLE_SITES)
            result = asyncio.run(_invoke())
        assert result == SAMPLE_SITES

    def test_issues_exactly_one_request(self) -> None:
        """No accidental retry/double-fetch on the single-shot validator."""
        with aioresponses() as mocked:
            mocked.get(SITES_URL, status=200, payload=SAMPLE_SITES)
            asyncio.run(_invoke())
            assert _single_request_count(mocked) == 1

    def test_sends_bearer_token(self) -> None:
        """The API key must be transmitted as a Bearer Authorization header."""
        with aioresponses() as mocked:
            mocked.get(SITES_URL, status=200, payload=SAMPLE_SITES)
            asyncio.run(_invoke(api_key="secret-123"))
            (_, calls), = mocked.requests.items()
            assert calls[0].kwargs["headers"]["Authorization"] == "Bearer secret-123"


class TestFetchAmberSitesFailures:
    """Each failure mode maps to its documented domain exception."""

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_error_raises_invalid_auth(self, status: int) -> None:
        with aioresponses() as mocked:
            mocked.get(SITES_URL, status=status)
            with pytest.raises(InvalidAuth):
                asyncio.run(_invoke())

    @pytest.mark.parametrize("status", [500, 502, 503])
    def test_server_error_raises_cannot_connect(self, status: int) -> None:
        with aioresponses() as mocked:
            mocked.get(SITES_URL, status=status)
            with pytest.raises(CannotConnect):
                asyncio.run(_invoke())

    def test_non_retryable_4xx_raises_cannot_connect(self) -> None:
        """A 404 (not an auth code) is a connection-class failure, not InvalidAuth."""
        with aioresponses() as mocked:
            mocked.get(SITES_URL, status=404)
            with pytest.raises(CannotConnect):
                asyncio.run(_invoke())

    def test_empty_site_list_raises_no_active_sites(self) -> None:
        with aioresponses() as mocked:
            mocked.get(SITES_URL, status=200, payload=[])
            with pytest.raises(NoActiveSites):
                asyncio.run(_invoke())

    def test_timeout_raises_cannot_connect(self) -> None:
        with aioresponses() as mocked:
            mocked.get(SITES_URL, exception=asyncio.TimeoutError())
            with pytest.raises(CannotConnect):
                asyncio.run(_invoke())

    def test_connection_error_raises_cannot_connect(self) -> None:
        with aioresponses() as mocked:
            mocked.get(SITES_URL, exception=aiohttp.ClientConnectionError("boom"))
            with pytest.raises(CannotConnect):
                asyncio.run(_invoke())

    def test_malformed_json_raises_cannot_connect(self) -> None:
        """A 200 with a non-JSON body fails in resp.json() and surfaces as CannotConnect."""
        with aioresponses() as mocked:
            mocked.get(
                SITES_URL,
                status=200,
                body="<html>not json</html>",
                content_type="text/html",
            )
            with pytest.raises(CannotConnect):
                asyncio.run(_invoke())
