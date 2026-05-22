"""Typed runtime data for the PriceHawk integration (Phase 7 / PR-1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import PriceHawkCoordinator


@dataclass(slots=True)
class PriceHawkData:
    """Runtime data attached to a PriceHawk ConfigEntry via entry.runtime_data."""

    coordinator: "PriceHawkCoordinator"


PriceHawkConfigEntry: TypeAlias = ConfigEntry[PriceHawkData]
