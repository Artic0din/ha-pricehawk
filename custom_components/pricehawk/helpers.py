"""Shared helper functions for pricehawk integration."""

from __future__ import annotations

from datetime import date, datetime


def compute_delta_h(now: datetime, last_update: datetime | None) -> float | None:
    """Return hours elapsed since last_update, or None if invalid.

    Returns None if last_update is None or delta <= 0.
    Clamps large gaps to 0.1 hours (6 min) to limit estimation error
    after HA restarts while still capturing some energy.
    """
    if last_update is None:
        return None
    delta_s = (now - last_update).total_seconds()
    delta_h = delta_s / 3600
    if delta_h <= 0:
        return None
    return min(delta_h, 0.1)


def split_grid_power(grid_power_w: float) -> tuple[float, float]:
    """Split grid power into (import_kw, export_kw).

    Positive grid_power_w = importing from grid.
    Negative grid_power_w = exporting to grid.
    """
    kw = grid_power_w / 1000
    return (max(0.0, kw), max(0.0, -kw))


def should_reset_daily(now_date: date, last_reset_date: date | None) -> bool:
    """Return True if daily accumulators should be reset."""
    if last_reset_date is None:
        return True
    return now_date != last_reset_date
