"""Phase 11 PR-16 — HA test-harness fixture prototypes.

These are PROTOTYPE fixtures for the future migration to
``pytest-homeassistant-custom-component``. They're NOT auto-applied;
tests that want the real HA harness import them explicitly. The
existing 1028 stub-conftest tests stay HA-free per D-P11-1.

Migration path: as each existing test module gets touched in a future
PR, replace its imports + bring in these fixtures. Avoids a single
massive refactor PR that would block review.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock


def mock_openelectricity_client(
    *,
    price_aud_per_mwh: float = 85.42,
    region: str = "NSW1",
    interval_end_utc: datetime | None = None,
) -> MagicMock:
    """Drop-in mock for ``OpenElectricityPriceSource``.

    Returns a MagicMock with ``fetch_current_price`` + ``last_good`` shaped
    like the real class. Tests can override individual return values.
    """
    from custom_components.pricehawk.providers.openelectricity import (
        WholesalePrice,
    )

    if interval_end_utc is None:
        interval_end_utc = datetime.now(tz=timezone.utc)

    price = WholesalePrice(
        price_aud_per_mwh=price_aud_per_mwh,
        interval_end_utc=interval_end_utc,
        region=region,
    )
    client = MagicMock()
    client.fetch_current_price = AsyncMock(return_value=price)
    client.last_good = MagicMock(return_value=price)
    return client


def mock_nemweb_client(
    *,
    price_c_kwh: float = 8.5,
    region: str = "NSW1",
) -> MagicMock:
    """Drop-in mock for ``NEMWebPriceSource``.

    Mirrors the OE mock shape; price is in c/kWh per the real NEMWeb
    contract (gets multiplied by 10 internally to match WholesalePrice
    ``price_aud_per_mwh`` field).
    """
    from custom_components.pricehawk.providers.openelectricity import (
        WholesalePrice,
    )

    price = WholesalePrice(
        price_aud_per_mwh=price_c_kwh * 10.0,
        interval_end_utc=datetime.now(tz=timezone.utc),
        region=region,
        attribution="Wholesale price data: AEMO NEMWeb DISPATCH (public)",
    )
    client = MagicMock()
    client.fetch_current_price = AsyncMock(return_value=price)
    client.last_good = MagicMock(return_value=price)
    return client


def recorder_mock_external_statistics() -> tuple[MagicMock, list[Any]]:
    """Mock ``async_add_external_statistics`` for tests that need to
    observe stat-push calls without HA's recorder running.

    Returns a tuple of (mock, calls) where calls is appended every time
    the mock is invoked: ``calls.append((metadata, stats))``.
    """
    calls: list[Any] = []

    def _record(_hass, metadata, stats):
        calls.append((metadata, list(stats)))

    mock = MagicMock(side_effect=_record)
    return mock, calls


def mock_config_entry_data(
    *,
    entry_id: str = "test-entry-xyz",
    pricing_mode: str = "live_api",
) -> dict[str, Any]:
    """Build the minimum entry.data + entry.options needed for a basic
    DWT-OE coordinator construction.

    Useful for HA-harness tests that need a real ConfigEntry instance.
    """
    from custom_components.pricehawk.const import (
        CONF_AMBER_PRICING_MODE,
        CONF_API_KEY,
        CONF_CURRENT_PROVIDER,
        CONF_DWT_OE_API_KEY,
        CONF_DWT_OE_DAILY_SUPPLY,
        CONF_DWT_OE_ENABLED,
        CONF_DWT_REGION,
        CONF_GRID_POWER_SENSOR,
        PROVIDER_DWT_OE,
    )

    return {
        "entry_id": entry_id,
        "data": {
            CONF_API_KEY: "<test-key>",
            CONF_DWT_OE_API_KEY: "<test-oe-key>",
            CONF_DWT_REGION: "NSW1",
            CONF_CURRENT_PROVIDER: PROVIDER_DWT_OE,
        },
        "options": {
            CONF_DWT_OE_ENABLED: True,
            CONF_DWT_OE_DAILY_SUPPLY: 110.0,
            CONF_GRID_POWER_SENSOR: "sensor.test_grid_power",
            CONF_AMBER_PRICING_MODE: pricing_mode,
        },
    }
