"""Root conftest — mock homeassistant for pure-Python unit tests."""

import sys
from unittest.mock import MagicMock


class _MockModule(MagicMock):
    """A MagicMock that pretends to be a package (has __path__)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__path__ = []


_HA_MODULES = [
    "homeassistant",
    "homeassistant.config_entries",
    "homeassistant.core",
    "homeassistant.helpers",
    "homeassistant.helpers.aiohttp_client",
    "homeassistant.helpers.entity_platform",
    "homeassistant.helpers.event",
    "homeassistant.helpers.selector",
    "homeassistant.helpers.storage",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.components",
    "homeassistant.components.sensor",
    "homeassistant.util",
    "homeassistant.util.dt",
    "aiohttp",
    "voluptuous",
]

_mods: dict[str, _MockModule] = {}
for mod_name in _HA_MODULES:
    if mod_name not in sys.modules:
        _mods[mod_name] = _MockModule()
        sys.modules[mod_name] = _mods[mod_name]
    else:
        _mods[mod_name] = sys.modules[mod_name]  # type: ignore[assignment]

# Wire parent -> child attributes for `from X.Y import Z` to work
_mods["homeassistant"].helpers = _mods["homeassistant.helpers"]
_mods["homeassistant"].util = _mods["homeassistant.util"]
_mods["homeassistant"].config_entries = _mods["homeassistant.config_entries"]
_mods["homeassistant"].core = _mods["homeassistant.core"]
_mods["homeassistant"].components = _mods["homeassistant.components"]
_mods["homeassistant.helpers"].event = _mods["homeassistant.helpers.event"]
_mods["homeassistant.helpers"].storage = _mods["homeassistant.helpers.storage"]
_mods["homeassistant.helpers"].update_coordinator = _mods["homeassistant.helpers.update_coordinator"]
_mods["homeassistant.helpers"].aiohttp_client = _mods["homeassistant.helpers.aiohttp_client"]
_mods["homeassistant.helpers"].entity_platform = _mods["homeassistant.helpers.entity_platform"]
_mods["homeassistant.helpers"].selector = _mods["homeassistant.helpers.selector"]
_mods["homeassistant.util"].dt = _mods["homeassistant.util.dt"]
_mods["homeassistant.components"].sensor = _mods["homeassistant.components.sensor"]
_mods["homeassistant.core"].CALLBACK_TYPE = type(None)
