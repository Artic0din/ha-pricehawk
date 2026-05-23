"""Typed runtime data for the PriceHawk integration (Phase 7 / PR-1)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeAlias

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import PriceHawkCoordinator


@dataclass(slots=True)
class PriceHawkData:
    """Runtime data attached to a PriceHawk ConfigEntry via entry.runtime_data."""

    coordinator: "PriceHawkCoordinator"
    # Issue #115 — task handles for the initial ranking + backfill background
    # work. Tracked here so async_unload_entry can cancel AND await them before
    # tearing down the coordinator, instead of relying on entry.async_on_unload
    # cancel callbacks that fire-and-forget without awaiting completion.
    background_tasks: list[asyncio.Task] = field(default_factory=list)


PriceHawkConfigEntry: TypeAlias = ConfigEntry[PriceHawkData]
