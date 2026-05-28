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
  the migrated dict. Both registries fail loudly on missing entries —
  a bump without a paired migrator is programmer error and must not
  silently succeed.
* Same-major newer-minor payloads LOAD as-is with a debug log — HA's
  Store convention is that minor versions are backward-compatible
  within a major.
* Non-dict payloads degrade gracefully — log + return an empty
  migrated state. Raising aborts ``async_load`` and integration setup;
  HA's Store contract is "be resilient on the read path".
* Discard policy: only the non-dict fallback returns a stripped
  payload. Schema-drift errors at registered migrators raise so a
  Repair issue can be opened and the user notified.

The current major-version chain ships with a single ``v1 → v2``
identity migrator. The bump has no schema effect; its purpose is to
exercise the migrator chain end-to-end on every existing install so
the wiring is proven correct BEFORE a real schema change relies on it.

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


async def _v1_to_v2_no_op(old: dict[str, Any]) -> dict[str, Any]:
    """Identity migrator — v1 and v2 share the same payload shape.

    Purpose: prove the migration chain end-to-end on real user storage
    BEFORE a substantive schema bump needs it. Every install on disk
    today is v1; on first load post-upgrade they flow through this
    function, persist as v2, and any future v2→v3 migrator can rely on
    the envelope shape being correct.
    """
    return dict(old)


# Major-version migrators. Keys are the OLD major version; the
# function returns data shaped for ``old_major + 1``. Add an entry
# here BEFORE bumping :data:`STORAGE_VERSION` in const.py.
_MAJOR_MIGRATORS: dict[int, MigratorT] = {
    1: _v1_to_v2_no_op,
}

# Minor-version migrators. Keys are the OLD minor version (within the
# CURRENT major). For additive-only changes the consumer fills defaults
# via ``.get(key, default)`` at read-time, so most minor bumps will
# register an identity migrator (``async def(old): return dict(old)``).
# Add an entry here BEFORE bumping :data:`STORAGE_MINOR_VERSION` in
# const.py.
_MINOR_MIGRATORS: dict[int, MigratorT] = {}


class PriceHawkStore(Store[dict[str, Any]]):
    """Store with versioned migration for PriceHawk persisted state.

    Constitution P16: migration is non-discarding for known versions.
    Same-major newer-minor payloads load as-is (HA's documented
    forward-compatibility contract — Constitution P19). Major
    downgrades raise loudly. Non-dict corruption signals log + return
    an empty migrated state so integration setup is not held hostage
    to a single corrupt blob.
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

        * ``old_major > STORAGE_VERSION``: major downgrade — refuse.
          The user must roll forward or wipe ``pricehawk_state``.
        * ``old_major == STORAGE_VERSION and old_minor >
          STORAGE_MINOR_VERSION``: same-major newer-minor. Per HA's
          documented Store convention, minor versions are
          backward-compatible within a major — LOAD the payload as-is
          with a debug log; the older code paths use ``.get(key,
          default)`` reads for additive fields. Codex follow-up #3
          (2026-05-27) reversed the earlier loud-refusal stance.
        * ``old_major < STORAGE_VERSION``: run major migrators in
          sequence. Each must exist in :data:`_MAJOR_MIGRATORS`.
        * ``old_minor < STORAGE_MINOR_VERSION`` (same major): run
          minor migrators.
        * Equal versions: should never reach this hook (HA gates on
          mismatch) — return as-is defensively.
        * Non-dict payload: corruption signal (serializer bug, manual
          edit, truncated file). Log + return an empty migrated state
          rather than raising. Raising from ``_async_migrate_func``
          aborts ``async_load`` which in turn aborts integration setup
          — the user would lose access to a working integration to
          surface a single corrupt blob. Constitution P19: HA's Store
          contract is "be resilient on the read path"; the coordinator
          ``async_restore_state`` then treats the empty result as a
          fresh-install state and re-accumulates from live data.
        """
        if old_major_version > STORAGE_VERSION:
            raise ValueError(
                f"PriceHawk storage version {old_major_version}."
                f"{old_minor_version} is newer than this integration "
                f"({STORAGE_VERSION}.{STORAGE_MINOR_VERSION}). Refusing "
                "to downgrade — upgrade the integration or remove "
                f"{STORAGE_KEY} from .storage."
            )

        # Codex follow-up #3 (2026-05-27): same-major newer-minor is
        # backward-compatible by design. The previous follow-up
        # raised here; that contradicted HA's documented Store
        # convention (minor versions within a major are forward-
        # compatible). LOAD the payload as-is — older code reads
        # additive fields via ``.get(key, default)`` and ignores
        # unknown ones. Constitution P19 (Platform Conventions)
        # OVERRIDES the earlier loud-refusal guidance.
        if old_major_version == STORAGE_VERSION and old_minor_version > STORAGE_MINOR_VERSION:
            _LOGGER.debug(
                "PriceHawk storage payload carries a newer minor "
                "version (%s.%s) than this integration build (%s.%s); "
                "loading as-is — minor versions are backward-compatible "
                "within a major per HA convention.",
                old_major_version,
                old_minor_version,
                STORAGE_VERSION,
                STORAGE_MINOR_VERSION,
            )
            if isinstance(old_data, dict):
                data = dict(old_data)
                data["_storage_version"] = STORAGE_VERSION
                return data
            # Fall through to the non-dict branch below if the
            # newer-minor payload is also malformed.

        # Codex follow-up #3 (2026-05-27): non-dict payloads are a
        # corruption signal (serializer bug, hand-edit, truncated
        # file). The previous follow-up raised RuntimeError here;
        # that aborts ``async_load`` and in turn aborts integration
        # setup — the user loses a working integration to surface a
        # single corrupt blob, which violates HA's "be resilient on
        # the read path" Store contract (Constitution P19). Log loudly
        # with the payload type for diagnostics and return an empty
        # migrated state so async_load completes; the coordinator's
        # restore path will treat the empty payload as a fresh-install
        # state and re-accumulate from live data. The user does not
        # silently lose history without a log trail.
        if not isinstance(old_data, dict):
            _LOGGER.error(
                "PriceHawk Store payload at %s is not a dict (got %s); "
                "returning empty migrated state so integration setup "
                "can proceed. Inspect %s in .storage/ if you want to "
                "recover the original payload — once setup succeeds "
                "the next save will overwrite it with a fresh state.",
                STORAGE_KEY,
                type(old_data).__name__,
                STORAGE_KEY,
            )
            return {
                "_storage_version_major": STORAGE_VERSION,
                "_storage_version_minor": STORAGE_MINOR_VERSION,
                "_storage_version": STORAGE_VERSION,
            }

        data = dict(old_data)
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
                current_major,
                current_major + 1,
            )
            data = await migrator(data)
            current_major += 1
            # A major bump resets minor to 1.
            current_minor = 1

        # Walk minor versions within the current major. Missing minor
        # migrators are programmer error (same contract as majors) —
        # raise loudly so a release can't ship a minor bump without the
        # paired migrator entry.
        while current_minor < STORAGE_MINOR_VERSION:
            migrator = _MINOR_MIGRATORS.get(current_minor)
            if migrator is None:
                raise ValueError(
                    f"No migrator registered for PriceHawk storage minor "
                    f"version {current_major}.{current_minor} → "
                    f"{current_major}.{current_minor + 1}. Add one to "
                    "storage._MINOR_MIGRATORS before bumping "
                    "STORAGE_MINOR_VERSION."
                )
            _LOGGER.info(
                "Migrating PriceHawk storage minor %s.%s → %s.%s",
                current_major,
                current_minor,
                current_major,
                current_minor + 1,
            )
            data = await migrator(data)
            current_minor += 1

        # Stamp the app-level sentinel so the coordinator's existing
        # in-payload version check (added in CR PR #28) stays
        # consistent with the Store envelope.
        data["_storage_version"] = STORAGE_VERSION
        return data
