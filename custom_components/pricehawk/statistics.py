"""External statistics push for PriceHawk (Phase 9 / PR-10).

Dual-write helper. Coordinator continues writing daily_cost_history to
the JSON Store (the existing source of truth); this module adds the
parallel write to HA's external statistics so cost streams become
pickable in the Energy Dashboard.

Stats-only flip (remove JSON write) ships in PR-12 / 09-03 — gated on
≥4 weeks elapsed + ≥10 testers confirming clean dual-write ≥7 days
per the ROADMAP v2.0 GA criteria.

Statistic-id format: ``f"{DOMAIN}:cost_{entry_id[:8]}_{provider_id}"``.
The entry_id slice keeps the id under HA's practical 50-char limit
while staying unique across multi-entry installs (8 chars of
hex-uuid prefix = 2^32 collision space; fine for a single user).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from typing import Any

from homeassistant.components.recorder.statistics import (
    StatisticData,
    StatisticMetaData,
    async_add_external_statistics,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def external_statistic_id(entry_id: str, provider_id: str) -> str:
    """Return the stable HA external-statistic id for one entry+provider."""
    return f"{DOMAIN}:cost_{entry_id[:8]}_{provider_id}"


def _metadata_for(entry_id: str, provider_id: str) -> StatisticMetaData:
    return StatisticMetaData(
        has_mean=False,
        has_sum=True,
        name=f"PriceHawk {provider_id} cost",
        source=DOMAIN,
        statistic_id=external_statistic_id(entry_id, provider_id),
        unit_of_measurement="AUD",
    )


def _day_start_utc(day: date) -> datetime:
    """Anchor to midnight UTC. HA stat 'start' is hour-aligned."""
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


async def async_push_daily_cost_to_statistics(
    hass: HomeAssistant,
    entry_id: str,
    provider_id: str,
    day: date,
    cost_aud: float,
    cumulative_sum: float,
) -> None:
    """Push one day's cost for one provider to HA external statistics.

    Idempotent on ``(statistic_id, start)`` per HA upsert semantics —
    safe to call again for the same day (e.g. after a restart that
    re-runs the rollover branch).
    """
    metadata = _metadata_for(entry_id, provider_id)
    stats: list[StatisticData] = [
        StatisticData(
            start=_day_start_utc(day),
            state=float(cost_aud),
            sum=float(cumulative_sum),
        )
    ]
    async_add_external_statistics(hass, metadata, stats)
    _LOGGER.debug(
        "external stats push: %s day=%s cost=%.4f sum=%.4f",
        metadata["statistic_id"] if isinstance(metadata, dict)
        else getattr(metadata, "statistic_id", "?"),
        day.isoformat(), cost_aud, cumulative_sum,
    )


async def async_backfill_external_statistics(
    hass: HomeAssistant,
    entry_id: str,
    daily_cost_history: list[dict[str, Any]],
) -> int:
    """Backfill external statistics from the JSON-Store history.

    Walks the history in date order, computes a monotonic cumulative
    sum per provider, and pushes one batch per provider (more efficient
    than per-day-per-provider calls).

    Returns the total number of statistic data points written.
    """
    if not daily_cost_history:
        return 0

    # Group cost entries by provider id, in date order. The history
    # list is already chronological (coordinator appends at rollover).
    cumulative: dict[str, float] = {}
    per_provider_stats: dict[str, list[StatisticData]] = {}
    for entry in daily_cost_history:
        day_str = entry.get("date")
        if not day_str:
            continue
        try:
            day = date.fromisoformat(day_str)
        except (TypeError, ValueError):
            continue
        for key, value in entry.items():
            if key == "date" or not isinstance(value, (int, float)):
                continue
            cumulative[key] = cumulative.get(key, 0.0) + float(value)
            per_provider_stats.setdefault(key, []).append(
                StatisticData(
                    start=_day_start_utc(day),
                    state=float(value),
                    sum=cumulative[key],
                )
            )

    total = 0
    for provider_id, stats in per_provider_stats.items():
        if not stats:
            continue
        metadata = _metadata_for(entry_id, provider_id)
        async_add_external_statistics(hass, metadata, stats)
        total += len(stats)

    _LOGGER.info(
        "external stats backfill: %d entries across %d providers (entry %s)",
        total, len(per_provider_stats), entry_id[:8],
    )
    return total
