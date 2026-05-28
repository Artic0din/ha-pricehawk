"""Test configuration — make pure-Python modules importable without HA."""

import sys
from pathlib import Path
from unittest.mock import MagicMock


class _MockModule(MagicMock):
    """A MagicMock that pretends to be a package (has __path__)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__path__ = []


# Register all HA modules that our code imports from
_mods = {
    "homeassistant": _MockModule(),
    "homeassistant.config_entries": _MockModule(),
    "homeassistant.core": _MockModule(),
    "homeassistant.exceptions": _MockModule(),
    "homeassistant.helpers": _MockModule(),
    "homeassistant.helpers.aiohttp_client": _MockModule(),
    "homeassistant.helpers.event": _MockModule(),
    "homeassistant.helpers.storage": _MockModule(),
    "homeassistant.helpers.update_coordinator": _MockModule(),
    "homeassistant.util": _MockModule(),
    "homeassistant.util.dt": _MockModule(),
    # Phase 3.2 — backfill.py imports from ``homeassistant.components
    # .recorder`` and ``.recorder.history``. Without these in sys.modules
    # the lazy ``from ... import ...`` inside ``async_run_backfill`` raises
    # ImportError under the test harness even though it's intentionally
    # lazy at runtime to avoid loading the recorder on HA startup.
    "homeassistant.components": _MockModule(),
    "homeassistant.components.recorder": _MockModule(),
    "homeassistant.components.recorder.history": _MockModule(),
    "homeassistant.components.recorder.statistics": _MockModule(),
    "homeassistant.components.diagnostics": _MockModule(),
}

# Wire parent -> child so attribute access also works
_mods["homeassistant"].helpers = _mods["homeassistant.helpers"]
_mods["homeassistant"].util = _mods["homeassistant.util"]
_mods["homeassistant"].config_entries = _mods["homeassistant.config_entries"]
_mods["homeassistant"].core = _mods["homeassistant.core"]
_mods["homeassistant.helpers"].aiohttp_client = _mods["homeassistant.helpers.aiohttp_client"]
_mods["homeassistant.helpers"].event = _mods["homeassistant.helpers.event"]
_mods["homeassistant.helpers"].storage = _mods["homeassistant.helpers.storage"]
_mods["homeassistant.helpers"].update_coordinator = _mods[
    "homeassistant.helpers.update_coordinator"
]
_mods["homeassistant.util"].dt = _mods["homeassistant.util.dt"]
# Phase 3.2 recorder mocks
_mods["homeassistant"].components = _mods["homeassistant.components"]
_mods["homeassistant.components"].recorder = _mods["homeassistant.components.recorder"]
_mods["homeassistant.components.recorder"].history = _mods[
    "homeassistant.components.recorder.history"
]
_mods["homeassistant.components.recorder"].statistics = _mods[
    "homeassistant.components.recorder.statistics"
]
_mods["homeassistant.components"].diagnostics = _mods["homeassistant.components.diagnostics"]

# Phase 9 PR-10: stub StatisticData / StatisticMetaData as plain dicts +
# async_add_external_statistics as an observable recorder.
_stats_mod = _mods["homeassistant.components.recorder.statistics"]


def _StatisticData(**kwargs):  # noqa: N802 — mirrors HA typed dict name
    return dict(kwargs)


def _StatisticMetaData(**kwargs):  # noqa: N802
    return dict(kwargs)


_stats_mod.StatisticData = _StatisticData
_stats_mod.StatisticMetaData = _StatisticMetaData
_stats_mod._calls = []  # (metadata, stats_list) tuples observable by tests


def _async_add_external_statistics(hass, metadata, stats):  # noqa: ARG001
    _stats_mod._calls.append((metadata, list(stats)))


_stats_mod.async_add_external_statistics = _async_add_external_statistics


# Phase 8 PR-7: async_redact_data behaviour needed at test time. Real
# HA impl walks the dict and replaces values for keys in TO_REDACT.
def _async_redact_data(data, to_redact):  # pragma: no cover — test helper
    if isinstance(data, dict):
        return {
            k: ("**REDACTED**" if k in to_redact else _async_redact_data(v, to_redact))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_async_redact_data(item, to_redact) for item in data]
    return data


_mods["homeassistant.components.diagnostics"].async_redact_data = _async_redact_data

# Phase 8 PR-8: stub homeassistant.helpers.issue_registry with create/delete
# recorders so tests can observe repair-issue toggles.
_issue_registry = _MockModule()
_issue_registry.IssueSeverity = type("IssueSeverity", (), {"WARNING": "warning", "ERROR": "error"})
_issue_registry._created = {}  # (domain, issue_id) → kwargs
_issue_registry._deleted = []


def _async_create_issue(hass, domain, issue_id, **kwargs):  # noqa: ARG001
    _issue_registry._created[(domain, issue_id)] = kwargs


def _async_delete_issue(hass, domain, issue_id):  # noqa: ARG001
    _issue_registry._deleted.append((domain, issue_id))
    _issue_registry._created.pop((domain, issue_id), None)


_issue_registry.async_create_issue = _async_create_issue
_issue_registry.async_delete_issue = _async_delete_issue
_mods["homeassistant.helpers"].issue_registry = _issue_registry
sys.modules["homeassistant.helpers.issue_registry"] = _issue_registry

# Provide a CALLBACK_TYPE that's usable as a type annotation
_mods["homeassistant.core"].CALLBACK_TYPE = type(None)


# Constitution P14 (#159) — real DataUpdateCoordinator base class so
# ``PriceHawkCoordinator`` resolves as an actual type (not a MagicMock).
# Without this stub ``class PriceHawkCoordinator(DataUpdateCoordinator
# [dict[str, Any]])`` evaluates ``MagicMock.__class_getitem__`` and the
# class ends up as a ``_MockModule``, so unit tests cannot call any
# coordinator method directly. Methods we never invoke in tests
# (super().__init__) remain MagicMock-bound; this stub only needs to
# satisfy the ``class X(...)`` syntax + subscript.
class _StubDataUpdateCoordinator:
    def __class_getitem__(cls, item):  # noqa: ARG004
        return cls

    def __init__(self, *args, **kwargs):  # noqa: D401, ARG002
        # Side-effect free — real HA implementation registers schedulers
        # we don't need in the unit test layer.
        return None


_mods["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = _StubDataUpdateCoordinator
# Constitution P16 (Data Integrity) — PriceHawk subclasses HA's Store to
# supply ``_async_migrate_func``. Tests need a REAL base class for that
# subclass to work (a MagicMock parent makes every method on the
# subclass return a Mock, breaking ``asyncio.run(coro)``). Mirror the
# minimal contract HA exposes: __init__ accepts hass/version/key plus
# optional ``private`` + ``minor_version`` kwargs; ``_async_migrate_func``
# raises NotImplementedError by default; ``async_load`` / ``async_save``
# are AsyncMocks so coordinator tests still work.
import asyncio  # noqa: E402


class _StubStore:  # generic via __class_getitem__ below
    """Minimal Store stand-in. Generic over the payload type.

    Mirrors the real ``homeassistant.helpers.storage.Store`` contract
    closely enough that subclass migrations are exercised end-to-end:

    * ``async_load`` checks the on-disk version (``_stored_version`` /
      ``_stored_minor``) against the in-code ``self.version`` /
      ``self.minor_version`` and dispatches through
      ``_async_migrate_func`` on mismatch. The migrated payload is
      then re-saved under the current version so the next load is
      cheap (matches HA behaviour).
    * Pre-seed legacy state for tests via ``seed_stored(data, major,
      minor)`` — the next ``async_load`` will trigger the migration
      path.
    """

    def __init__(
        self,
        hass,  # noqa: ANN001 — mock
        version,
        key,
        private=False,  # noqa: ARG002
        *,
        atomic_writes=False,  # noqa: ARG002
        encoder=None,  # noqa: ARG002
        minor_version=1,
    ):
        self.hass = hass
        self.version = version
        self.minor_version = minor_version
        self.key = key
        self._stored = None
        self._stored_version = version
        self._stored_minor = minor_version

    def __class_getitem__(cls, _item):
        # Support ``Store[dict[str, Any]]`` subscript at class-def time.
        return cls

    async def _async_migrate_func(  # noqa: PLR6301
        self,
        old_major_version,
        old_minor_version,
        old_data,
    ):
        raise NotImplementedError

    def seed_stored(self, data, *, major, minor=1):
        """Test-only: plant payload + version on disk so the next
        ``async_load`` exercises the migration path."""
        self._stored = data
        self._stored_version = major
        self._stored_minor = minor

    async def async_load(self):
        if self._stored is None:
            return None
        # Real HA Store dispatches through _async_migrate_func when the
        # on-disk version differs from the constructor-supplied one,
        # then re-saves with the new version. Mirror that behaviour so
        # tests can assert the envelope path runs subclass migrators.
        if self._stored_version != self.version or self._stored_minor != self.minor_version:
            migrated = await self._async_migrate_func(
                self._stored_version,
                self._stored_minor,
                self._stored,
            )
            self._stored = migrated
            self._stored_version = self.version
            self._stored_minor = self.minor_version
            return migrated
        return self._stored

    async def async_save(self, data):
        self._stored = data
        self._stored_version = self.version
        self._stored_minor = self.minor_version

    async def async_remove(self):
        self._stored = None

    # async_delay_save: noop helper used by coordinator for debounced
    # writes; return value is unused, so a simple sync stub suffices.
    def async_delay_save(self, *_args, **_kwargs):
        return None


_mods["homeassistant.helpers.storage"].Store = _StubStore
# Re-export under the attribute path too, for the
# ``from homeassistant.helpers.storage import Store`` style.
_mods["homeassistant.helpers"].storage.Store = _StubStore

# Keep linters quiet about the unused-import — asyncio is used by the
# class above. (Not strictly necessary but matches the file's style.)
_ = asyncio

# Phase 3.0c: real ConfigEntryNotReady class so `raise` statements work
_mods["homeassistant.exceptions"].ConfigEntryNotReady = type(
    "ConfigEntryNotReady", (Exception,), {}
)
# Phase 7 PR-2: ConfigEntryAuthFailed for OpenElectricity 401 mapping
_mods["homeassistant.exceptions"].ConfigEntryAuthFailed = type(
    "ConfigEntryAuthFailed", (Exception,), {}
)
# Phase 8 PR-9 (HA Silver) — action-exceptions rule.
_mods["homeassistant.exceptions"].HomeAssistantError = type("HomeAssistantError", (Exception,), {})
_mods["homeassistant.exceptions"].ServiceValidationError = type(
    "ServiceValidationError",
    (_mods["homeassistant.exceptions"].HomeAssistantError,),
    {},
)
# Constitution P19 — ``async_migrate_entry`` raises ConfigEntryError on
# downgrade/missing-migrator so the UI surfaces the diagnostic message.
_mods["homeassistant.exceptions"].ConfigEntryError = type(
    "ConfigEntryError",
    (_mods["homeassistant.exceptions"].HomeAssistantError,),
    {},
)
_mods["homeassistant"].exceptions = _mods["homeassistant.exceptions"]

for name, mod in _mods.items():
    sys.modules[name] = mod

# Ensure the custom_components package is importable. parents[1] is
# the repo root (the directory CONTAINING custom_components/). Phase
# 3.0g (CodeRabbit): legacy parents[3] pointed two levels above the
# repo root which only worked because pytest's auto-rootdir detection
# masked the bug. Fix so non-pytest invocations import cleanly.
root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))
