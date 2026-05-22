"""Phase 8 PR-6 — reconfigure flow source-level tests.

Mirrors test_reauth.py pattern — source-grep on config_flow.py because
EnergyCompareConfigFlow can't be instantiated under conftest HA stubs
(documented in 07-02b D-1).
"""

from __future__ import annotations

import json
from pathlib import Path


def _config_flow_source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "pricehawk"
        / "config_flow.py"
    ).read_text()


def _strings_json() -> dict:
    return json.load(
        open(
            Path(__file__).resolve().parents[1]
            / "custom_components"
            / "pricehawk"
            / "strings.json"
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
            assert step_id in s["config"]["step"], (
                f"strings.json missing config.step.{step_id}"
            )

    def test_strings_have_reconfigure_abort_reasons(self):
        s = _strings_json()
        assert "reconfigure_unsupported" in s["config"]["abort"]
        assert "reconfigure_successful" in s["config"]["abort"]

    def test_translations_byte_identical(self):
        repo = Path(__file__).resolve().parents[1]
        a = (
            repo / "custom_components" / "pricehawk" / "strings.json"
        ).read_bytes()
        b = (
            repo
            / "custom_components"
            / "pricehawk"
            / "translations"
            / "en.json"
        ).read_bytes()
        assert a == b
