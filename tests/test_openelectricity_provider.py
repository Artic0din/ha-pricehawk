"""Contract tests for OpenElectricityPriceSource (Phase 7 / PR-2).

Covers AC-1b, AC-2 through AC-7e per ``07-02-PLAN.md``. The openelectricity SDK
is mocked via sys.modules — no real network calls, no SDK install required.

Async pattern matches the rest of the suite: ``asyncio.run(...)`` inside sync
tests (no pytest-asyncio dependency).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# SDK stubs — install BEFORE importing the module under test so the lazy
# `from openelectricity import AsyncOEClient` inside fetch_current_price
# resolves to our stubs at test time.
# ---------------------------------------------------------------------------


def _install_sdk_stubs() -> None:
    """Idempotently install openelectricity stubs into sys.modules."""
    if "openelectricity" not in sys.modules:
        sys.modules["openelectricity"] = MagicMock()
    if "openelectricity.types" not in sys.modules:
        types_mod = MagicMock()
        # MarketMetric.PRICE just needs to be a truthy sentinel.
        types_mod.MarketMetric = MagicMock(PRICE="price")
        sys.modules["openelectricity.types"] = types_mod


_install_sdk_stubs()


# Defer module import until after stubs are in place.
from custom_components.pricehawk.providers.openelectricity import (  # noqa: E402
    _ATTRIBUTION,
    OpenElectricityPriceSource,
    WholesalePrice,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_response(
    region: str = "NSW1",
    price: float = 85.42,
    ts: datetime | None = None,
) -> SimpleNamespace:
    """Build a stub TimeSeriesResponse matching openelectricity==0.10.1 shape.

    Real shape (verified via venv introspection 2026-05-20):
        response.data: Sequence[NetworkTimeSeries]
        NetworkTimeSeries.results: list[TimeSeriesResult]
        TimeSeriesResult.columns.network_region: str
        TimeSeriesResult.data: list[<point with .timestamp + .value>]
    """
    if ts is None:
        ts = datetime(2026, 5, 20, 1, 30, 0, tzinfo=timezone.utc)
    point = SimpleNamespace(timestamp=ts, value=price)
    columns = SimpleNamespace(network_region=region)
    result = SimpleNamespace(columns=columns, data=[point])
    series = SimpleNamespace(results=[result])
    return SimpleNamespace(data=[series])


def _mock_async_client(get_market_result: Any) -> MagicMock:
    """Build an AsyncOEClient factory mock supporting `async with`.

    Returns a callable that, when invoked (i.e. `AsyncOEClient(api_key=...)`),
    yields an object with `__aenter__`/`__aexit__` and `.get_market`.
    """
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    if isinstance(get_market_result, Exception):
        client.get_market = AsyncMock(side_effect=get_market_result)
    elif callable(get_market_result) and not isinstance(
        get_market_result, AsyncMock
    ):
        client.get_market = AsyncMock(side_effect=get_market_result)
    else:
        client.get_market = AsyncMock(return_value=get_market_result)
    return MagicMock(return_value=client)


def _patch_sdk(client_factory: MagicMock):
    """Patch the SDK symbols the module imports lazily."""
    return patch.dict(
        sys.modules,
        {
            "openelectricity": MagicMock(AsyncOEClient=client_factory),
            "openelectricity.types": MagicMock(
                MarketMetric=MagicMock(PRICE="price")
            ),
        },
    )


# ---------------------------------------------------------------------------
# AC-1b / AC-2: happy-path NEM region + attribution
# ---------------------------------------------------------------------------


def test_happy_path_nem_region_returns_wholesale_price_with_attribution():
    factory = _mock_async_client(_build_response("NSW1", price=85.42))
    src = OpenElectricityPriceSource(api_key="sk-test-12345678")

    with _patch_sdk(factory):
        result = asyncio.run(src.fetch_current_price("NSW1"))

    assert isinstance(result, WholesalePrice)
    assert result.price_aud_per_mwh == 85.42
    assert result.region == "NSW1"
    assert result.interval_end_utc == datetime(
        2026, 5, 20, 1, 30, 0, tzinfo=timezone.utc
    )
    # AC-1b: verbatim attribution string (research §1.3).
    assert result.attribution == (
        "Wholesale price data: Open Electricity (Superpower Institute), "
        "CC BY-NC 4.0"
    )
    assert _ATTRIBUTION == result.attribution

    # Validate SDK was called with the right params.
    client_instance = factory.return_value
    client_instance.get_market.assert_awaited_once()
    kwargs = client_instance.get_market.await_args.kwargs
    assert kwargs["network_code"] == "NEM"
    assert kwargs["interval"] == "5m"
    assert kwargs["primary_grouping"] == "network_region"


# ---------------------------------------------------------------------------
# AC-3: WEM region resolves to network_code="WEM"
# ---------------------------------------------------------------------------


def test_happy_path_wem_region_uses_wem_network_code():
    factory = _mock_async_client(_build_response("WEM", price=72.10))
    src = OpenElectricityPriceSource(api_key="sk-test-12345678")

    with _patch_sdk(factory):
        result = asyncio.run(src.fetch_current_price("WEM"))

    assert result is not None
    assert result.region == "WEM"
    kwargs = factory.return_value.get_market.await_args.kwargs
    assert kwargs["network_code"] == "WEM"


# ---------------------------------------------------------------------------
# AC-4: 401 → ConfigEntryAuthFailed
# ---------------------------------------------------------------------------


def test_401_maps_to_config_entry_auth_failed():
    from homeassistant.exceptions import ConfigEntryAuthFailed

    factory = _mock_async_client(RuntimeError("HTTP 401 Unauthorized"))
    src = OpenElectricityPriceSource(api_key="sk-test-12345678")

    with _patch_sdk(factory):
        with pytest.raises(ConfigEntryAuthFailed) as exc_info:
            asyncio.run(src.fetch_current_price("VIC1"))

    assert "OpenElectricity" in str(exc_info.value)


# ---------------------------------------------------------------------------
# AC-5: non-auth/non-rate-limit errors → None, cache preserved
# ---------------------------------------------------------------------------


def test_non_auth_error_returns_none_and_preserves_cache():
    src = OpenElectricityPriceSource(api_key="sk-test-12345678")
    factory_ok = _mock_async_client(_build_response("NSW1", price=85.42))
    factory_fail = _mock_async_client(RuntimeError("connection refused"))
    factory_ok2 = _mock_async_client(_build_response("NSW1", price=92.50))

    with _patch_sdk(factory_ok):
        first = asyncio.run(src.fetch_current_price("NSW1"))
    assert first is not None
    assert src.last_good("NSW1") is first

    with _patch_sdk(factory_fail):
        second = asyncio.run(src.fetch_current_price("NSW1"))
    assert second is None
    # Cache survives failure.
    assert src.last_good("NSW1") is first

    with _patch_sdk(factory_ok2):
        third = asyncio.run(src.fetch_current_price("NSW1"))
    assert third is not None
    assert third.price_aud_per_mwh == 92.50
    # Cache updated on success.
    assert src.last_good("NSW1") is third


# ---------------------------------------------------------------------------
# AC-6: empty-data response → None + WARNING
# ---------------------------------------------------------------------------


def test_empty_data_returns_none_and_warns(caplog: pytest.LogCaptureFixture):
    # response.data is empty list — no series, no results.
    empty_response = SimpleNamespace(data=[])
    factory = _mock_async_client(empty_response)
    src = OpenElectricityPriceSource(api_key="sk-test-12345678")

    with _patch_sdk(factory), caplog.at_level(
        logging.WARNING,
        logger="custom_components.pricehawk.providers.openelectricity",
    ):
        result = asyncio.run(src.fetch_current_price("NSW1"))

    assert result is None
    warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("no data for region NSW1" in r.getMessage() for r in warning_records)


# ---------------------------------------------------------------------------
# AC-7: invalid region → ValueError before any network call
# ---------------------------------------------------------------------------


def test_invalid_region_raises_valueerror_before_network_call():
    factory = _mock_async_client(_build_response("NSW1"))
    src = OpenElectricityPriceSource(api_key="sk-test-12345678")

    with _patch_sdk(factory):
        with pytest.raises(ValueError) as exc_info:
            asyncio.run(src.fetch_current_price("INVALID1"))

    assert "INVALID1" in str(exc_info.value)
    factory.return_value.get_market.assert_not_called()


# ---------------------------------------------------------------------------
# Defensive: empty API key → ValueError at construction
# ---------------------------------------------------------------------------


def test_constructor_rejects_empty_api_key():
    with pytest.raises(ValueError):
        OpenElectricityPriceSource(api_key="")


# ---------------------------------------------------------------------------
# AC-7b: explicit timeout
# ---------------------------------------------------------------------------


def test_timeout_returns_none_and_warns(caplog: pytest.LogCaptureFixture):
    """Hung SDK call must return None within bounded time."""

    async def _hang(**_kwargs: Any) -> Any:
        # Sleep well past the test's patched timeout.
        await asyncio.sleep(60)

    factory = _mock_async_client(_hang)
    src = OpenElectricityPriceSource(api_key="sk-test-12345678")

    # Patch the constant to 0.05s for the test.
    patch_timeout = patch(
        "custom_components.pricehawk.providers.openelectricity._FETCH_TIMEOUT_SECONDS",
        0.05,
    )

    with _patch_sdk(factory), patch_timeout, caplog.at_level(
        logging.WARNING,
        logger="custom_components.pricehawk.providers.openelectricity",
    ):
        result = asyncio.run(src.fetch_current_price("NSW1"))

    assert result is None
    assert any("timed out" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# AC-7c: missing SDK → ConfigEntryNotReady (not ImportError)
# ---------------------------------------------------------------------------


def test_missing_sdk_raises_config_entry_not_ready():
    from homeassistant.exceptions import ConfigEntryNotReady

    src = OpenElectricityPriceSource(api_key="sk-test-12345678")

    # Force the lazy import to fail. Setting sys.modules[name]=None makes
    # Python's import machinery raise ImportError on `from name import ...`.
    with patch.dict(
        sys.modules,
        {"openelectricity": None, "openelectricity.types": None},
    ):
        with pytest.raises(ConfigEntryNotReady) as exc_info:
            asyncio.run(src.fetch_current_price("NSW1"))

    msg = str(exc_info.value)
    assert "openelectricity" in msg.lower()
    # Must NOT be a bare ImportError (HA wouldn't retry).
    assert not isinstance(exc_info.value, ImportError)


# ---------------------------------------------------------------------------
# AC-7d: 429 rate-limit → None, preserves cache, distinct WARNING, no AuthFailed
# ---------------------------------------------------------------------------


def test_429_rate_limit_returns_none_preserves_cache(
    caplog: pytest.LogCaptureFixture,
):
    from homeassistant.exceptions import ConfigEntryAuthFailed

    src = OpenElectricityPriceSource(api_key="sk-test-12345678")

    # First successful fetch — populate cache.
    factory_ok = _mock_async_client(_build_response("NSW1", price=85.42))
    with _patch_sdk(factory_ok):
        first = asyncio.run(src.fetch_current_price("NSW1"))
    assert first is not None

    # 429 hit.
    factory_429 = _mock_async_client(
        RuntimeError("HTTP 429 Too Many Requests")
    )
    second: WholesalePrice | None = None
    with _patch_sdk(factory_429), caplog.at_level(
        logging.WARNING,
        logger="custom_components.pricehawk.providers.openelectricity",
    ):
        try:
            second = asyncio.run(src.fetch_current_price("NSW1"))
        except ConfigEntryAuthFailed:
            pytest.fail("429 must NOT raise ConfigEntryAuthFailed")

    assert second is None
    assert src.last_good("NSW1") is first  # cache preserved
    rate_limit_logs = [
        r for r in caplog.records if "rate-limited" in r.getMessage()
    ]
    assert len(rate_limit_logs) >= 1


# ---------------------------------------------------------------------------
# AC-7e (part 1): API key redacted in repr
# ---------------------------------------------------------------------------


def test_repr_redacts_api_key():
    api_key = "sk-test-1234567890abcdef"
    src = OpenElectricityPriceSource(api_key=api_key)

    rendered = repr(src)
    assert api_key not in rendered, "FULL API KEY LEAKED IN REPR"
    # No 8-char prefix either.
    assert api_key[:8] not in rendered, "API KEY PREFIX LEAKED IN REPR"
    assert "<redacted" in rendered


# ---------------------------------------------------------------------------
# AC-7e (part 2): API key scrubbed from log messages
# ---------------------------------------------------------------------------


def test_log_scrubs_api_key_from_sdk_error(caplog: pytest.LogCaptureFixture):
    """SDK exceptions that contain the API key must NOT leak it to logs."""
    api_key = "sk-test-1234567890abcdef"
    # SDK raises a generic exception whose message embeds the key (e.g. via URL).
    leaky_exc = RuntimeError(
        f"connection refused to https://api.openelectricity.org.au/v4 "
        f"with token={api_key}"
    )
    factory = _mock_async_client(leaky_exc)
    src = OpenElectricityPriceSource(api_key=api_key)

    with _patch_sdk(factory), caplog.at_level(
        logging.WARNING,
        logger="custom_components.pricehawk.providers.openelectricity",
    ):
        result = asyncio.run(src.fetch_current_price("NSW1"))

    assert result is None
    captured = "\n".join(r.getMessage() for r in caplog.records)
    assert api_key not in captured, "API KEY LEAKED IN LOG"
    assert api_key[:8] not in captured, "API KEY PREFIX LEAKED IN LOG"
    assert "<redacted" in captured
