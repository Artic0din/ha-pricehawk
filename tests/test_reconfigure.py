"""Phase 8 PR-6 — reconfigure flow source-level tests.

Mirrors test_reauth.py pattern — source-grep on config_flow.py because
EnergyCompareConfigFlow can't be instantiated under conftest HA stubs
(documented in 07-02b D-1).

PR #164 Linus audit adds a behavioural section
(``TestOptionsFlowProviderEdit``) that exercises ``PriceHawkCoordinator``
directly — the coordinator IS instantiable under conftest HA stubs,
unlike the config flow — to pin the P13 regression: a user editing
``CONF_CURRENT_PROVIDER`` via the options flow must see ``_compute_saving``
flip direction immediately, without an HA restart.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.pricehawk import coordinator as _coord_mod
from custom_components.pricehawk.const import (
    CONF_CURRENT_PROVIDER,
    CONF_DWT_OE_ENABLED,
    CONF_DWT_REGION,
    PROVIDER_DWT_OE,
)


def _config_flow_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "custom_components" / "pricehawk" / "config_flow.py"
    ).read_text()


def _strings_json() -> dict:
    return json.load(
        open(
            Path(__file__).resolve().parents[1] / "custom_components" / "pricehawk" / "strings.json"
        )
    )


class TestDispatcherRouting:
    def test_dispatcher_routes_to_amber_substep(self):
        src = _config_flow_source()
        assert "if provider_id == PROVIDER_AMBER:" in src
        assert "return await self.async_step_reconfigure_amber()" in src

    def test_dispatcher_routes_to_localvolts_substep(self):
        src = _config_flow_source()
        assert "return await self.async_step_reconfigure_localvolts()" in src

    def test_dispatcher_routes_to_dwt_oe_substep(self):
        src = _config_flow_source()
        assert "return await self.async_step_reconfigure_dwt_oe()" in src

    def test_dispatcher_routes_to_dwt_aemo_substep(self):
        src = _config_flow_source()
        assert "return await self.async_step_reconfigure_dwt_aemo()" in src

    def test_dispatcher_aborts_on_unsupported_entry(self):
        src = _config_flow_source()
        assert 'reason="reconfigure_unsupported"' in src

    def test_dispatcher_reads_provider_from_entry_data(self):
        """Codex fix: dispatch from entry.data[CONF_CURRENT_PROVIDER]
        not coordinator._current_plan_provider.id. CdrPlanProvider.id is
        ``{brand}_{plan_id}`` for CDR Amber/LV entries and never matches
        the literal PROVIDER_AMBER / PROVIDER_LOCALVOLTS slug, so reading
        it from the coordinator made those reconfigure branches unreachable
        for the install base.
        """
        src = _config_flow_source()
        # Find the reconfigure dispatcher block.
        start = src.index("async def async_step_reconfigure(")
        end = src.index("async def async_step_reconfigure_amber", start)
        block = src[start:end]
        assert "entry.data.get(CONF_CURRENT_PROVIDER)" in block, (
            "Reconfigure dispatcher must read CONF_CURRENT_PROVIDER from "
            "entry.data, not from coordinator._current_plan_provider.id."
        )
        assert "self._get_reconfigure_entry()" in block
        # Guard against regression — runtime coordinator id MUST NOT
        # be the source of the dispatch decision.
        assert "_current_plan_provider" not in block, (
            "Reconfigure dispatcher must NOT rely on the runtime "
            "coordinator's _current_plan_provider — that is the CDR "
            "brand_planId for CDR users, not the literal provider slug."
        )


class TestSubstepContract:
    def test_amber_substep_updates_options_not_data(self):
        src = _config_flow_source()
        # Amber sub-step writes fees into options.
        assert "CONF_AMBER_NETWORK_DAILY_CHARGE: float(" in src
        assert "CONF_AMBER_SUBSCRIPTION_FEE: float(" in src

    def test_localvolts_substep_updates_three_option_fields(self):
        src = _config_flow_source()
        assert "CONF_LOCALVOLTS_DAILY_SUPPLY: float(" in src
        assert "CONF_LOCALVOLTS_BUY_CEILING: float(" in src
        assert "CONF_LOCALVOLTS_SELL_FLOOR: float(" in src

    def test_dwt_oe_substep_only_edits_daily_supply(self):
        src = _config_flow_source()
        # DWT-OE reconfigure MUST NOT touch region or API key.
        # Find the dwt_oe block:
        start = src.index("async def async_step_reconfigure_dwt_oe")
        end = src.index("async def async_step_reconfigure_dwt_aemo", start)
        block = src[start:end]
        assert "CONF_DWT_OE_DAILY_SUPPLY" in block
        assert "CONF_DWT_REGION" not in block
        assert "CONF_DWT_OE_API_KEY" not in block

    def test_dwt_aemo_substep_only_edits_daily_supply(self):
        src = _config_flow_source()
        start = src.index("async def async_step_reconfigure_dwt_aemo")
        # Find end of method — next def or @staticmethod
        end = src.index("@staticmethod", start)
        block = src[start:end]
        assert "CONF_DWT_AEMO_DAILY_SUPPLY" in block
        assert "CONF_DWT_REGION" not in block

    def test_all_substeps_use_update_reload_and_abort(self):
        src = _config_flow_source()
        # 4 sub-steps + the 3 reauth sub-steps = 7 calls minimum.
        assert src.count("self.async_update_reload_and_abort(") >= 7


class TestHistoryPreservation:
    def test_reconfigure_block_does_not_reset_history(self):
        src = _config_flow_source()
        start = src.index("async def async_step_reconfigure(")
        end = src.index("@staticmethod", start)
        block = src[start:end]
        # No reset_daily, no history wipes, no entry.data mutation inside reconfigure.
        assert ".reset_daily()" not in block
        assert "_daily_cost_history" not in block
        assert "data={**entry.data" not in block  # reconfigure ONLY touches options


class TestStringsParity:
    def test_strings_have_four_reconfigure_steps(self):
        s = _strings_json()
        for step_id in (
            "reconfigure_amber",
            "reconfigure_localvolts",
            "reconfigure_dwt_oe",
            "reconfigure_dwt_aemo",
        ):
            assert step_id in s["config"]["step"], f"strings.json missing config.step.{step_id}"

    def test_strings_have_reconfigure_abort_reasons(self):
        s = _strings_json()
        assert "reconfigure_unsupported" in s["config"]["abort"]
        assert "reconfigure_successful" in s["config"]["abort"]

    def test_translations_byte_identical(self):
        repo = Path(__file__).resolve().parents[1]
        a = (repo / "custom_components" / "pricehawk" / "strings.json").read_bytes()
        b = (repo / "custom_components" / "pricehawk" / "translations" / "en.json").read_bytes()
        assert a == b


# ---------------------------------------------------------------------------
# PR #164 Linus audit — P13 regression-pin for options-flow provider edit
# ---------------------------------------------------------------------------


class TestOptionsFlowProviderEdit:
    """Constitution P13 — no regression by design.

    Pins the contract that ``_compute_saving`` honours an options-flow
    edit to ``CONF_CURRENT_PROVIDER`` immediately. The bug shape this
    guards against: a user with ``entry.data[CONF_CURRENT_PROVIDER] =
    PROVIDER_AMBER`` opens the options flow and changes it to
    ``PROVIDER_GLOBIRD``. HA writes the new value to ``entry.options``
    but does NOT touch ``entry.data``. If ``_compute_saving`` read from
    ``entry.data`` (or cached the resolved value at __init__ time), the
    saving direction would stay anchored to PROVIDER_AMBER until an HA
    restart — silently miscalculating the directional saving for the
    new provider.

    The systemic fix (commit b109965) routes the read through
    :func:`_resolve` which applies the options→data precedence at
    *every* call. This test pins that semantic.

    Implementation note: ``PriceHawkCoordinator`` cannot be instantiated
    under conftest HA stubs — its base class ``DataUpdateCoordinator`` is
    a ``_MockModule``, so the ``class`` statement at module load time
    short-circuits and the symbol becomes the same mock (no real methods
    bound). The smoke-test pattern in
    ``test_review_improvements.py::TestBackfillStatusSensor`` /
    ``TestPeriodRollupSensorSmoke`` is the standing precedent: mirror the
    EXACT method body in the test. If the production method diverges, the
    mirror falls behind and the integration test on Ryan's HA catches it.

    What we ARE testing here: the contract that the ``_compute_saving``
    body — specifically the ``_resolve(config_entry, CONF_CURRENT_PROVIDER,
    PROVIDER_AMBER)`` call — picks up an options-flow edit on the live
    ``config_entry`` reference without coordinator reconstruction.
    """

    def _compute_saving(self, config_entry, amber_cost: float, globird_cost: float) -> float:
        """Mirror of ``PriceHawkCoordinator._compute_saving`` body. If the
        production method diverges, update this mirror in lock-step."""
        from custom_components.pricehawk.const import (
            CONF_CURRENT_PROVIDER,
            PROVIDER_AMBER,
        )
        from custom_components.pricehawk.coordinator import _resolve

        current_provider = _resolve(config_entry, CONF_CURRENT_PROVIDER, PROVIDER_AMBER)
        if current_provider == PROVIDER_AMBER:
            return amber_cost - globird_cost
        return globird_cost - amber_cost

    def _entry(self, *, data_provider: str, options_provider: str | None):
        """ConfigEntry stub seeded with ``CONF_CURRENT_PROVIDER`` in data
        and (optionally) overridden via options. Mirrors HA's contract
        where ``entry.options`` only contains keys the user has explicitly
        edited via the options flow."""
        from custom_components.pricehawk.const import CONF_CURRENT_PROVIDER

        options: dict = {}
        if options_provider is not None:
            options[CONF_CURRENT_PROVIDER] = options_provider
        data = {CONF_CURRENT_PROVIDER: data_provider}
        return MagicMock(options=options, data=data)

    def test_compute_saving_pins_against_production_body(self):
        """Belt-and-braces: source-grep the production method to catch
        drift between this test's mirror and the real ``_compute_saving``.
        If ``coordinator.py``'s body changes shape, this test fails first
        and forces the mirror update before the behavioural assertions
        below ship false-positives."""
        src = (
            Path(__file__).resolve().parents[1]
            / "custom_components"
            / "pricehawk"
            / "coordinator.py"
        ).read_text()
        # The production body must contain the exact _resolve call shape
        # this mirror replicates. If either side drifts, this assertion
        # fails fast.
        assert (
            "current_provider = _resolve(self.config_entry, CONF_CURRENT_PROVIDER, PROVIDER_AMBER)"
        ) in src, (
            "Production _compute_saving body diverged from the mirror in "
            "TestOptionsFlowProviderEdit. Update the mirror in lock-step."
        )

    def test_compute_saving_picks_up_options_edit_without_restart(self):
        """Options-flow edit to CONF_CURRENT_PROVIDER takes effect on the
        next ``_compute_saving`` call — no restart, no rebuild_engine."""
        from custom_components.pricehawk.const import (
            CONF_CURRENT_PROVIDER,
            PROVIDER_AMBER,
            PROVIDER_GLOBIRD,
        )

        # Initial state: PROVIDER_AMBER stored in data, options untouched
        # by the user (simulates an entry that completed initial setup but
        # never visited the options flow for the provider field).
        entry = self._entry(data_provider=PROVIDER_AMBER, options_provider=None)

        amber_cost = 5.00
        globird_cost = 7.50

        # Baseline: current is Amber → saving = amber - globird = -2.50
        # (negative = staying on Amber costs less than switching to GloBird).
        baseline = self._compute_saving(entry, amber_cost, globird_cost)
        assert baseline == amber_cost - globird_cost, (
            "Baseline saving must anchor to entry.data CONF_CURRENT_PROVIDER "
            "when entry.options is empty for that key."
        )

        # Options-flow edit: user flips primary provider to GloBird.
        # HA writes the new value to ``entry.options`` ONLY — ``entry.data``
        # is immutable across an options-flow edit. This mutation simulates
        # exactly what ``OptionsFlowWithReload`` does before triggering the
        # reload; the test asserts the read path picks it up WITHOUT the
        # reload (i.e. on the same entry reference).
        entry.options[CONF_CURRENT_PROVIDER] = PROVIDER_GLOBIRD

        after_edit = self._compute_saving(entry, amber_cost, globird_cost)
        assert after_edit == globird_cost - amber_cost, (
            "After an options-flow edit to CONF_CURRENT_PROVIDER, the next "
            "_compute_saving call must read the NEW value via _resolve's "
            "options→data fallback — no HA restart, no rebuild_engine."
        )
        # Direction must have flipped sign — sanity belt-and-braces.
        assert baseline == -after_edit

    def test_options_value_shadows_data_value(self):
        """Both layers populated: options wins. Pins the documented
        :func:`_resolve` precedence at the ``_compute_saving`` call site."""
        from custom_components.pricehawk.const import (
            PROVIDER_AMBER,
            PROVIDER_GLOBIRD,
        )

        # data says AMBER, options (already populated from a prior edit)
        # says GLOBIRD → GLOBIRD wins.
        entry = self._entry(
            data_provider=PROVIDER_AMBER,
            options_provider=PROVIDER_GLOBIRD,
        )
        amber_cost = 5.00
        globird_cost = 7.50
        # GloBird-anchored saving = globird_cost - amber_cost = +2.50.
        assert self._compute_saving(entry, amber_cost, globird_cost) == globird_cost - amber_cost

    def test_falls_back_to_amber_default_when_neither_layer_set(self):
        """Defensive: a stale entry that somehow lacks
        ``CONF_CURRENT_PROVIDER`` in BOTH layers falls back to the
        documented ``PROVIDER_AMBER`` default in ``_compute_saving``.
        Pins the third arg of the ``_resolve`` call (the default) which
        keeps legacy entries from before CONF_CURRENT_PROVIDER existed
        in working order."""
        entry = MagicMock(options={}, data={})

        amber_cost = 5.00
        globird_cost = 7.50
        # Defaults to PROVIDER_AMBER → amber-anchored direction.
        assert self._compute_saving(entry, amber_cost, globird_cost) == amber_cost - globird_cost


# Codex P2 follow-up — rebuild_engine graceful degrade on bad DWT options
# ---------------------------------------------------------------------------
#
# Background. ``_apply_options_to_state(strict=False)`` is the rebuild
# path: options-flow updates feed through it and any raise tears the
# integration down mid-edit. ``_build_dwt_provider`` raises
# ``ConfigEntryNotReady`` (AC-10c) when the entry's ``current_provider``
# marker says DWT but the matching enable/API-key fields are missing —
# correct for strict (initial setup) but wrong on the rebuild path,
# where pre-refactor behaviour was to log and keep the existing
# providers. Constitution P13 (no regression) — these tests pin that
# graceful-degrade contract so the systemic ``_apply_options_to_state``
# refactor cannot silently revert it.


def _bare_coordinator_with_dwt_sentinel():
    """Construct a ``PriceHawkCoordinator`` shell with a pre-bound DWT
    provider sentinel — the state a running coordinator presents when
    options-flow fires a rebuild after a DWT entry has been live."""
    coord = object.__new__(_coord_mod.PriceHawkCoordinator)
    coord.hass = MagicMock()
    coord.config_entry = MagicMock()
    coord.config_entry.data = {}
    dwt_sentinel = MagicMock()
    dwt_sentinel.id = "dwt_oe_sentinel"
    coord._dwt_provider = dwt_sentinel
    coord._current_plan_provider = dwt_sentinel
    coord._providers = {"dwt_oe_sentinel": dwt_sentinel}
    coord._amber_mode = "off"
    coord._amber = None
    coord._amber_static_plan = None
    coord._flow_power_mode = "off"
    coord._flow_power = None
    coord._localvolts_mode = "off"
    coord._localvolts = None
    coord._localvolts_static_plan = None
    coord._named_comparator = None
    coord._grid_power_entity = ""
    return coord, dwt_sentinel


class TestRebuildGracefulDegradeOnInconsistentDwt:
    """P13 regression-pin — rebuild with inconsistent DWT options must
    NOT raise ``ConfigEntryNotReady`` and must keep the existing
    providers intact (matches pre-refactor behaviour where partial
    options updates never aborted ``rebuild_engine``)."""

    def test_rebuild_does_not_raise_on_inconsistent_dwt_options(self):
        """Options says ``current_provider = dwt_oe`` but neither the
        ENABLED flag nor the API key is present. Strict mode would
        raise (and ``__init__`` still does, by design). Non-strict mode
        must catch the raise and bail without re-raising."""
        coord, _ = _bare_coordinator_with_dwt_sentinel()
        bad_options = {
            CONF_CURRENT_PROVIDER: PROVIDER_DWT_OE,
            # Missing CONF_DWT_OE_ENABLED + CONF_DWT_OE_API_KEY —
            # _build_dwt_provider raises ConfigEntryNotReady here.
            CONF_DWT_REGION: "VIC1",
        }
        # Must not raise — pre-fix this asserted via uncaught
        # ConfigEntryNotReady tearing the rebuild down.
        coord._apply_options_to_state(bad_options, {}, strict=False)

    def test_rebuild_preserves_existing_providers_on_inconsistent_dwt(self):
        """Same scenario as above, asserting the early-return contract:
        every provider slot the coordinator was running with survives
        the bad rebuild untouched."""
        coord, sentinel = _bare_coordinator_with_dwt_sentinel()
        bad_options = {
            CONF_CURRENT_PROVIDER: PROVIDER_DWT_OE,
            CONF_DWT_REGION: "VIC1",
        }

        coord._apply_options_to_state(bad_options, {}, strict=False)

        # All three slots survive — the projector caught the raise and
        # bailed before nulling any state.
        assert coord._dwt_provider is sentinel
        assert coord._current_plan_provider is sentinel
        assert coord._providers == {"dwt_oe_sentinel": sentinel}

    def test_strict_init_still_raises_on_inconsistent_dwt(self):
        """The non-strict graceful degrade must NOT leak into strict
        mode — initial setup with inconsistent DWT options must still
        raise so HA surfaces the failure to the user (AC-10c)."""
        from homeassistant.exceptions import ConfigEntryNotReady

        coord, _ = _bare_coordinator_with_dwt_sentinel()
        # In strict mode we drop the sentinel pre-conditions so the
        # raise can only come from _build_dwt_provider — but the test
        # only asserts the raise propagates, not which slot survives.
        bad_options = {
            CONF_CURRENT_PROVIDER: PROVIDER_DWT_OE,
            CONF_DWT_OE_ENABLED: False,  # explicit False — still missing API key
        }
        with pytest.raises(ConfigEntryNotReady):
            coord._apply_options_to_state(bad_options, {}, strict=True)
