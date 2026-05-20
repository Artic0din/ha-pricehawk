"""Contract tests for NEMWebPriceSource (Phase 7 / PR-3).

Covers AC-1 through AC-7 per ``07-03-PLAN.md``. The underlying ``aemo_api``
parsing is exercised separately in ``tests/test_aemo_api.py`` — these tests
focus on the new wrapper surface (validation, timezone parsing, cache,
timeout).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Reuse the openelectricity SDK stubs so the contract surface import resolves.
if "openelectricity" not in sys.modules:
    sys.modules["openelectricity"] = MagicMock()
if "openelectricity.types" not in sys.modules:
    types_mod = MagicMock()
    types_mod.MarketMetric = MagicMock(PRICE="price")
    sys.modules["openelectricity.types"] = types_mod


from custom_components.pricehawk.providers.nemweb import (  # noqa: E402
    _ATTRIBUTION,
    NEMWebPriceSource,
    _parse_settlement_date,
)
from custom_components.pricehawk.providers.openelectricity import (  # noqa: E402
    WholesalePrice as OE_WholesalePrice,
)
from custom_components.pricehawk.providers import nemweb as nemweb_mod  # noqa: E402


# ---------------------------------------------------------------------------
# AC-1: attribution constant + single contract surface
# ---------------------------------------------------------------------------


def test_attribution_constant_verbatim():
    assert _ATTRIBUTION == "Wholesale price data: AEMO NEMWeb DISPATCH (public)"


def test_module_imports_wholesale_price_from_openelectricity():
    # The NEMWeb module imports WholesalePrice from openelectricity to share
    # the single contract surface. Verify they're the same class object.
    assert nemweb_mod.WholesalePrice is OE_WholesalePrice


# ---------------------------------------------------------------------------
# AC-2: happy path NEM region
# ---------------------------------------------------------------------------


def test_happy_path_nem_region_returns_wholesale_price_with_nemweb_attribution():
    src = NEMWebPriceSource(session=MagicMock())
    with patch(
        "custom_components.pricehawk.providers.nemweb.fetch_current_rrp",
        new=AsyncMock(return_value=(8.542, "2026/05/20 01:30:00")),
    ):
        result = asyncio.run(src.fetch_current_price("NSW1"))

    assert isinstance(result, OE_WholesalePrice)
    assert result.price_aud_per_mwh == 85.42  # 8.542 c/kWh * 10
    assert result.region == "NSW1"
    assert result.attribution == _ATTRIBUTION
    assert result.attribution != (
        "Wholesale price data: Open Electricity (Superpower Institute), "
        "CC BY-NC 4.0"
    )
    # tz-aware UTC.
    assert result.interval_end_utc.tzinfo == timezone.utc
    # NEM time 01:30 → UTC 15:30 previous day (AEST -10).
    assert result.interval_end_utc == datetime(
        2026, 5, 19, 15, 30, 0, tzinfo=timezone.utc
    )


# ---------------------------------------------------------------------------
# AC-3: WEM region raises ValueError before any network call
# ---------------------------------------------------------------------------


def test_wem_region_raises_valueerror_before_network_call():
    src = NEMWebPriceSource(session=MagicMock())
    mock_fetch = AsyncMock()
    with patch(
        "custom_components.pricehawk.providers.nemweb.fetch_current_rrp",
        new=mock_fetch,
    ):
        with pytest.raises(ValueError) as exc_info:
            asyncio.run(src.fetch_current_price("WEM"))

    assert "WEM" in str(exc_info.value)
    assert "OpenElectricity" in str(exc_info.value)
    mock_fetch.assert_not_called()


# ---------------------------------------------------------------------------
# AC-4: aemo_api returns None → cache preserved
# ---------------------------------------------------------------------------


def test_aemo_returns_none_preserves_cache():
    src = NEMWebPriceSource(session=MagicMock())

    # First call: success, populates cache.
    with patch(
        "custom_components.pricehawk.providers.nemweb.fetch_current_rrp",
        new=AsyncMock(return_value=(8.542, "2026/05/20 01:30:00")),
    ):
        first = asyncio.run(src.fetch_current_price("NSW1"))
    assert first is not None
    assert src.last_good("NSW1") is first

    # Second call: aemo_api fails, returns None.
    with patch(
        "custom_components.pricehawk.providers.nemweb.fetch_current_rrp",
        new=AsyncMock(return_value=None),
    ):
        second = asyncio.run(src.fetch_current_price("NSW1"))
    assert second is None
    assert src.last_good("NSW1") is first

    # Third call: success again, cache updates.
    with patch(
        "custom_components.pricehawk.providers.nemweb.fetch_current_rrp",
        new=AsyncMock(return_value=(9.20, "2026/05/20 01:35:00")),
    ):
        third = asyncio.run(src.fetch_current_price("NSW1"))
    assert third is not None
    assert third.price_aud_per_mwh == 92.0
    assert src.last_good("NSW1") is third


# ---------------------------------------------------------------------------
# AC-5: invalid region → ValueError before network call
# ---------------------------------------------------------------------------


def test_invalid_region_raises_valueerror_before_network_call():
    src = NEMWebPriceSource(session=MagicMock())
    mock_fetch = AsyncMock()
    with patch(
        "custom_components.pricehawk.providers.nemweb.fetch_current_rrp",
        new=mock_fetch,
    ):
        with pytest.raises(ValueError) as exc_info:
            asyncio.run(src.fetch_current_price("INVALID1"))
    assert "INVALID1" in str(exc_info.value)
    mock_fetch.assert_not_called()


# ---------------------------------------------------------------------------
# AC-6: settlement-date parsing — NEM time is AEST year-round (no DST)
# ---------------------------------------------------------------------------


def test_settlement_date_parsing_winter_aest():
    # May = winter; Brisbane and Sydney both at +10:00.
    dt = _parse_settlement_date("2026/05/20 01:30:00")
    assert dt == datetime(2026, 5, 19, 15, 30, 0, tzinfo=timezone.utc)


def test_settlement_date_parsing_summer_no_dst():
    # January = summer; Sydney would be +11:00 (AEDT). Brisbane stays +10:00.
    # NEM dispatch publishes AEST year-round per AEMO docs.
    # Correct: 02:30 NEM-time → 16:30 UTC previous day (offset -10).
    # If we'd anchored to Sydney we'd get 15:30 UTC (offset -11) — WRONG.
    dt = _parse_settlement_date("2026/01/15 02:30:00")
    assert dt == datetime(2026, 1, 14, 16, 30, 0, tzinfo=timezone.utc), (
        "Settlement date must NOT shift by 1 hour during DST — NEM dispatch "
        "is AEST year-round. Anchor must be Australia/Brisbane (no DST), "
        "not Australia/Sydney."
    )


def test_settlement_date_parsing_empty_string():
    with pytest.raises(ValueError):
        _parse_settlement_date("")


def test_settlement_date_parsing_strips_quotes():
    dt = _parse_settlement_date('"2026/05/20 01:30:00"')
    assert dt == datetime(2026, 5, 19, 15, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Bonus: timeout returns None
# ---------------------------------------------------------------------------


def test_timeout_returns_none_and_warns(caplog: pytest.LogCaptureFixture):
    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(60)

    src = NEMWebPriceSource(session=MagicMock())
    with (
        patch(
            "custom_components.pricehawk.providers.nemweb.fetch_current_rrp",
            new=AsyncMock(side_effect=_hang),
        ),
        patch(
            "custom_components.pricehawk.providers.nemweb._FETCH_TIMEOUT_SECONDS",
            0.05,
        ),
        caplog.at_level(
            logging.WARNING,
            logger="custom_components.pricehawk.providers.nemweb",
        ),
    ):
        result = asyncio.run(src.fetch_current_price("NSW1"))

    assert result is None
    assert any("timed out" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Defensive: malformed settlement date → None + WARNING
# ---------------------------------------------------------------------------


def test_malformed_settlement_date_returns_none(
    caplog: pytest.LogCaptureFixture,
):
    src = NEMWebPriceSource(session=MagicMock())
    with (
        patch(
            "custom_components.pricehawk.providers.nemweb.fetch_current_rrp",
            new=AsyncMock(return_value=(8.542, "not-a-valid-date")),
        ),
        caplog.at_level(
            logging.WARNING,
            logger="custom_components.pricehawk.providers.nemweb",
        ),
    ):
        result = asyncio.run(src.fetch_current_price("NSW1"))

    assert result is None
    assert any(
        "settlement-date parse failed" in r.getMessage() for r in caplog.records
    )
