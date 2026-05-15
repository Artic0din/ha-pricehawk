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
    "homeassistant.helpers.event": _MockModule(),
    "homeassistant.helpers.storage": _MockModule(),
    "homeassistant.helpers.update_coordinator": _MockModule(),
    "homeassistant.util": _MockModule(),
    "homeassistant.util.dt": _MockModule(),
}

# Wire parent -> child so attribute access also works
_mods["homeassistant"].helpers = _mods["homeassistant.helpers"]
_mods["homeassistant"].util = _mods["homeassistant.util"]
_mods["homeassistant"].config_entries = _mods["homeassistant.config_entries"]
_mods["homeassistant"].core = _mods["homeassistant.core"]
_mods["homeassistant.helpers"].event = _mods["homeassistant.helpers.event"]
_mods["homeassistant.helpers"].storage = _mods["homeassistant.helpers.storage"]
_mods["homeassistant.helpers"].update_coordinator = _mods["homeassistant.helpers.update_coordinator"]
_mods["homeassistant.util"].dt = _mods["homeassistant.util.dt"]

# Provide a CALLBACK_TYPE that's usable as a type annotation
_mods["homeassistant.core"].CALLBACK_TYPE = type(None)

# Phase 3.0c: real ConfigEntryNotReady class so `raise` statements work
_mods["homeassistant.exceptions"].ConfigEntryNotReady = type(
    "ConfigEntryNotReady", (Exception,), {}
)
_mods["homeassistant"].exceptions = _mods["homeassistant.exceptions"]

for name, mod in _mods.items():
    sys.modules[name] = mod

# Ensure the custom_components package is importable
root = Path(__file__).resolve().parents[3]  # /Users/.../HA
if str(root) not in sys.path:
    sys.path.insert(0, str(root))
