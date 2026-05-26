"""PriceHawk Store subclass with migration support.

Constitution P16 (Data Integrity). Implements the HA-standard
``Store`` migration pattern so future bumps of :data:`STORAGE_VERSION`
or :data:`STORAGE_MINOR_VERSION` transform the persisted payload
in-place rather than discarding it.

The previous implementation pinned ``STORAGE_VERSION = 1`` since
Phase 1.x. Many additive fields shipped between Phase 2 and Phase 9
(``_external_stats_cumulative``, ``_backfill_*``, ``_cheap_ranked_alternatives``,
``_ranking_plan_cache``) without ever bumping the version. The coordinator
restore path discarded mismatched payloads outright — meaning the FIRST
deliberate bump would have wiped every user's accumulated history.

This module makes bumps safe:

* Subclass :class:`homeassistant.helpers.storage.Store` and override
  :meth:`_async_migrate_func`.
* Per-version migrators registered in :data:`_MAJOR_MIGRATORS` /
  :data:`_MINOR_MIGRATORS`; each takes the old data dict and returns
  the migrated dict. Missing minors fill defaults; missing majors
  must be coded when bumped.
* Never discard data inside the migrator — raise on unrecoverable
  schema drift so a Repair issue can be opened and the user notified.

See ``docs/architecture.md`` § "Storage migration policy" for the
full procedure.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_MINOR_VERSION, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)


# Migrator signature: ``async def(old_data: dict) -> dict`` — returns
# the data shape for the NEXT version up. The Store engine chains
# them: a v1→v3 payload runs v1→v2 then v2→v3.
MigratorT = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


async def _passthrough(old: dict[str, Any]) -> dict[str, Any]:
    """Identity migrator — used for minors that only ADD fields with
    safe defaults supplied at read-time by the consumer."""
    return dict(old)


# Major-version migrators. Keys are the OLD major version; the
# function returns data shaped for ``old_major + 1``. Add an entry
# here BEFORE bumping :data:`STORAGE_VERSION` in const.py.
_MAJOR_MIGRATORS: dict[int, MigratorT] = {}

# Minor-version migrators. Keys are the OLD minor version (within the
# CURRENT major). For additive-only changes, ``_passthrough`` is fine —
# the consumer fills defaults via ``.get(key, default)``. Add an entry
# here BEFORE bumping :data:`STORAGE_MINOR_VERSION` in const.py.
_MINOR_MIGRATORS: dict[int, MigratorT] = {}


class PriceHawkStore(Store[dict[str, Any]]):
    """Store with versioned migration for PriceHawk persisted state.

    Constitution P16: migration is non-discarding. On unknown future
    versions we raise so the caller (coordinator) can degrade to a
    fresh-install state instead of silently writing back an empty
    payload that would mask the schema mismatch.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            STORAGE_VERSION,
            STORAGE_KEY,
            minor_version=STORAGE_MINOR_VERSION,
        )

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Walk migrators from (old_major, old_minor) → current.

        Behaviour matrix:

        * ``old_major > STORAGE_VERSION``: downgrade — refuse. The
          user must roll forward or wipe ``pricehawk_state``.
        * ``old_major < STORAGE_VERSION``: run major migrators in
          sequence. Each must exist in :data:`_MAJOR_MIGRATORS`.
        * ``old_minor < STORAGE_MINOR_VERSION`` (same major): run
          minor migrators.
        * Equal versions: should never reach this hook (HA gates on
          mismatch) — return as-is defensively.
        """
        if old_major_version > STORAGE_VERSION:
            raise ValueError(
                f"PriceHawk storage version {old_major_version}."
                f"{old_minor_version} is newer than this integration "
                f"({STORAGE_VERSION}.{STORAGE_MINOR_VERSION}). Refusing "
                "to downgrade — upgrade the integration or remove "
                f"{STORAGE_KEY} from .storage."
            )

        data = dict(old_data) if isinstance(old_data, dict) else {}
        current_major = old_major_version
        current_minor = old_minor_version

        # Walk major versions: every step from N → N+1 must have a
        # registered migrator. Missing migrators are programmer error
        # and must fail loudly so they're caught before release.
        while current_major < STORAGE_VERSION:
            migrator = _MAJOR_MIGRATORS.get(current_major)
            if migrator is None:
                raise ValueError(
                    f"No migrator registered for PriceHawk storage major "
                    f"version {current_major} → {current_major + 1}. "
                    "Add one to storage._MAJOR_MIGRATORS before bumping "
                    "STORAGE_VERSION."
                )
            _LOGGER.info(
                "Migrating PriceHawk storage major %s → %s",
                current_major, current_major + 1,
            )
            data = await migrator(data)
            current_major += 1
            # A major bump resets minor to 1.
            current_minor = 1

        # Walk minor versions within the current major.
        while current_minor < STORAGE_MINOR_VERSION:
            migrator = _MINOR_MIGRATORS.get(current_minor, _passthrough)
            _LOGGER.info(
                "Migrating PriceHawk storage minor %s.%s → %s.%s",
                current_major, current_minor,
                current_major, current_minor + 1,
            )
            data = await migrator(data)
            current_minor += 1

        # Stamp the app-level sentinel so the coordinator's existing
        # in-payload version check (added in CR PR #28) stays
        # consistent with the Store envelope.
        data["_storage_version"] = STORAGE_VERSION
        return data
