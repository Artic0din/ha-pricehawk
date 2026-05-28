"""OpenElectricity v4 wholesale-price client for PriceHawk (Phase 7 / PR-2).

Standalone module — not yet wired into the coordinator or config flow; that's
Plan 07-02b. The openelectricity SDK is imported lazily inside
``fetch_current_price`` so a missing-SDK install raises ``ConfigEntryNotReady``
(HA retries setup) rather than crashing module import.

Audit invariants (07-02-AUDIT.md):
- API key NEVER appears in __repr__ output or log messages.
- SDK call bounded by _FETCH_TIMEOUT_SECONDS via asyncio.timeout.
- Missing SDK at runtime → ConfigEntryNotReady, not ImportError.
- 401-equivalent → ConfigEntryAuthFailed (caller must re-prompt for key).
- 429-equivalent → return None + WARNING (does NOT raise AuthFailed).
- Other errors → return None + WARNING; last-good cache preserved.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final, TypeAlias

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

_LOGGER = logging.getLogger(__name__)

_ATTRIBUTION: Final[str] = (
    "Wholesale price data: Open Electricity (Superpower Institute), CC BY-NC 4.0"
)

# Region → network_code mapping (research §1.2). The SDK accepts "NEM", "WEM", "AU".
_NEM_REGIONS: Final[frozenset[str]] = frozenset({"NSW1", "QLD1", "SA1", "TAS1", "VIC1"})
_WEM_REGIONS: Final[frozenset[str]] = frozenset({"WEM"})

# Audit M2: bounded SDK call. 30s is generous for a 5-min cadence; HA polling
# tolerates slow ticks but not hung ones.
_FETCH_TIMEOUT_SECONDS: Final[float] = 30.0

_REDACTED: Final[str] = "<redacted-api-key>"
_REDACTED_PREFIX: Final[str] = "<redacted-prefix>"


@dataclass(slots=True, frozen=True)
class WholesalePrice:
    """Result type for fetch_current_price().

    IMPORTANT — do NOT add fields without coordinated update of consumers
    in 07-02b and any subsequent PRs. frozen=True is part of the cross-PR
    contract (07-02-PLAN.md > CROSS-PR CONTRACTS).
    """

    price_aud_per_mwh: float
    interval_end_utc: datetime
    region: str
    attribution: str = _ATTRIBUTION


_LatestPoint: TypeAlias = tuple[float, datetime]


class OpenElectricityPriceSource:
    """Async client for OpenElectricity v4 wholesale-price queries.

    Owns auth (API key passed at construction, never logged), region→network_code
    mapping, last-good cache, and error→exception mapping. Does NOT own polling
    cadence — the caller decides when to invoke fetch_current_price.
    """

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("api_key must be a non-empty string")
        self._api_key = api_key
        self._last_good_by_region: dict[str, WholesalePrice] = {}

    def __repr__(self) -> str:
        # Audit M1: API key MUST NOT appear in repr.
        cached_regions = sorted(self._last_good_by_region.keys())
        return f"OpenElectricityPriceSource(api_key={_REDACTED}, cached_regions={cached_regions!r})"

    @staticmethod
    def _network_code_for(region: str) -> str:
        if region in _NEM_REGIONS:
            return "NEM"
        if region in _WEM_REGIONS:
            return "WEM"
        raise ValueError(
            f"Unknown OpenElectricity region {region!r}; "
            f"expected one of {sorted(_NEM_REGIONS | _WEM_REGIONS)}"
        )

    def _scrub(self, text: str) -> str:
        """Redact the API key from any string before logging it (audit M1)."""
        if not text:
            return text
        scrubbed = text.replace(self._api_key, _REDACTED)
        if len(self._api_key) >= 8:
            scrubbed = scrubbed.replace(self._api_key[:8], _REDACTED_PREFIX)
        return scrubbed

    async def fetch_current_price(self, region: str) -> WholesalePrice | None:
        """Fetch the latest 5-minute dispatch price for the region.

        Returns None on non-auth, non-rate-limit errors and empty-data responses;
        caller can fall back to last-good via ``last_good(region)``.

        Raises:
            ValueError: unknown region (caller bug — raised before any network call).
            ConfigEntryAuthFailed: HTTP 401 / auth-equivalent — API key needs renewal.
            ConfigEntryNotReady: openelectricity SDK is not importable at runtime.
        """
        # Validate region BEFORE any network call.
        network_code = self._network_code_for(region)

        # Audit M3: lazy import + ImportError → ConfigEntryNotReady.
        try:
            from openelectricity import AsyncOEClient  # noqa: PLC0415  # ty: ignore[unresolved-import]  # untyped optional HACS runtime dep (lazy import, ImportError-guarded)
            from openelectricity.types import MarketMetric  # noqa: PLC0415  # ty: ignore[unresolved-import]  # untyped optional HACS runtime dep (lazy import, ImportError-guarded)
        except ImportError as exc:
            raise ConfigEntryNotReady(
                "openelectricity SDK is not installed. The HA wheel resolver "
                "should pick it up from manifest.json:requirements; if this "
                "persists, install manually: "
                "pip install 'openelectricity>=0.10.1,<0.11'. "
                f"({exc})"
            ) from exc

        # Audit M2: bounded SDK call.
        try:
            async with asyncio.timeout(_FETCH_TIMEOUT_SECONDS):
                async with AsyncOEClient(api_key=self._api_key) as client:
                    response = await client.get_market(
                        network_code=network_code,  # type: ignore[arg-type]
                        metrics=[MarketMetric.PRICE],
                        interval="5m",
                        primary_grouping="network_region",
                    )
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "OpenElectricity fetch timed out after %.0fs for region %s",
                _FETCH_TIMEOUT_SECONDS,
                region,
            )
            return None
        except Exception as exc:  # noqa: BLE001 — classified below
            if _is_auth_error(exc):
                raise ConfigEntryAuthFailed(
                    f"OpenElectricity API rejected the key (HTTP 401): {self._scrub(str(exc))}"
                ) from exc
            if _is_rate_limit_error(exc):
                _LOGGER.warning(
                    "OpenElectricity rate-limited fetch for region %s; "
                    "preserving cached last-good. Detail: %s",
                    region,
                    self._scrub(str(exc)),
                )
                return None
            _LOGGER.warning(
                "OpenElectricity fetch failed for %s: %s",
                region,
                self._scrub(str(exc)),
            )
            return None

        extracted = _extract_latest_for_region(response, region)
        if extracted is None:
            _LOGGER.warning(
                "OpenElectricity returned no data for region %s (response had %d point(s))",
                region,
                _record_count(response),
            )
            return None

        price_value, ts_utc = extracted
        price = WholesalePrice(
            price_aud_per_mwh=price_value,
            interval_end_utc=ts_utc,
            region=region,
        )
        self._last_good_by_region[region] = price
        return price

    def last_good(self, region: str) -> WholesalePrice | None:
        """Return the cached last successful fetch for the region, if any."""
        return self._last_good_by_region.get(region)


def _is_auth_error(exc: Exception) -> bool:
    """Detect 401-equivalent errors. Belt-and-braces: message + class name (audit S1).

    Retro-review of #86 (claude, 2026-05-23): the prior message check used a
    bare ``"forbidden" in msg`` substring, which fires false positives on
    TLS/network errors that happen to contain the word "forbidden"
    (e.g. ``"SSL verify failed — connections forbidden by network policy"``,
    corporate proxy errors, DNS rebind protection). 403 Forbidden is also
    semantically distinct from 401 Unauthorized — a valid key on a plan
    tier without market access returns 403, and re-entering the key won't
    fix it. The message check now keys on auth-specific tokens only;
    "forbidden" matches survive only via the class-name check below, which
    requires the exception class to actually be named ``*Forbidden*``.
    """
    msg = str(exc).lower()
    if "401" in msg or "unauthor" in msg or "invalid api key" in msg:
        return True
    class_name = type(exc).__name__.lower()
    return any(token in class_name for token in ("auth", "unauthor", "forbidden", "credential"))


def _is_rate_limit_error(exc: Exception) -> bool:
    """Detect 429-equivalent errors (audit S2)."""
    msg = str(exc).lower()
    if "429" in msg or "rate limit" in msg or "rate-limit" in msg or "too many requests" in msg:
        return True
    class_name = type(exc).__name__.lower()
    return "ratelimit" in class_name or "throttle" in class_name


def _extract_latest_for_region(response: object, region: str) -> _LatestPoint | None:
    """Walk the nested TimeSeriesResponse and pull the latest (price, ts) for `region`.

    Verified against openelectricity==0.10.1 (2026-05-20). Real shape:
        response.data: Sequence[NetworkTimeSeries]
        NetworkTimeSeries.results: list[TimeSeriesResult]
        TimeSeriesResult.columns.network_region: str
        TimeSeriesResult.data: list[<point with .timestamp + .value>]

    Does NOT use response.to_records() — that flattens but emits naive-local
    timestamps; we need tz-aware UTC.
    """
    data = getattr(response, "data", None)
    if not data:
        return None

    candidates: list[_LatestPoint] = []
    for series in data:
        for result in getattr(series, "results", []):
            columns = getattr(result, "columns", None)
            row_region = getattr(columns, "network_region", None) if columns else None
            if row_region != region:
                continue
            for point in getattr(result, "data", []):
                ts = getattr(point, "timestamp", None)
                val = getattr(point, "value", None)
                if ts is None or val is None:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                candidates.append((float(val), ts))

    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[1])


def _record_count(response: object) -> int:
    """Helper for log messages — total data points across all series/results."""
    data = getattr(response, "data", None)
    if not data:
        return 0
    return sum(
        len(getattr(result, "data", []))
        for series in data
        for result in getattr(series, "results", [])
    )
