"""Regression tests for codex review findings on the v3.0 stack.

Five functional regressions surfaced by `codex review --base dev` against
the tip of the Phases 7-11 PR stack. Each test pins the fix in place so a
future refactor can't silently re-introduce the bug.

Source-level asserts mirror test_reauth.py / test_reconfigure.py because
``EnergyCompareConfigFlow`` and ``EnergyCompareCoordinator`` can't be
instantiated under the conftest HA stubs (per 07-02b D-1 deviation).
"""

from __future__ import annotations

from pathlib import Path


def _config_flow_source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "pricehawk"
        / "config_flow.py"
    ).read_text()


def _coordinator_source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "pricehawk"
        / "coordinator.py"
    ).read_text()


# ----------------------------------------------------------------------
# P1 — dashboard_token entry builder persists DWT setup fields
# ----------------------------------------------------------------------


class TestDwtFieldsPersistedAtEntryCreation:
    """Codex P1: dashboard_token built entry without DWT fields. New
    DWT installs landed in ConfigEntryNotReady at first refresh because
    _build_dwt_provider() reads CONF_DWT_OE_ENABLED / CONF_DWT_OE_API_KEY
    / CONF_DWT_REGION / CONF_DWT_OE_DAILY_SUPPLY (and AEMO variants)
    that were never copied from self._data.
    """

    def test_dashboard_token_copies_dwt_oe_fields(self):
        src = _config_flow_source()
        start = src.index("async def async_step_dashboard_token(")
        end = src.index("async def async_step_reauth(", start)
        block = src[start:end]
        # Credentials persisted to entry.data
        assert "data[CONF_DWT_OE_API_KEY]" in block
        assert "data[CONF_DWT_REGION]" in block
        # Runtime config persisted to entry.options
        assert "options[CONF_DWT_OE_ENABLED] = True" in block
        assert "options[CONF_DWT_OE_DAILY_SUPPLY]" in block

    def test_dashboard_token_copies_dwt_aemo_fields(self):
        src = _config_flow_source()
        start = src.index("async def async_step_dashboard_token(")
        end = src.index("async def async_step_reauth(", start)
        block = src[start:end]
        assert "options[CONF_DWT_AEMO_ENABLED] = True" in block
        assert "options[CONF_DWT_AEMO_DAILY_SUPPLY]" in block
        # AEMO has no API key — region is the only data field.
        assert "data[CONF_DWT_REGION]" in block

    def test_dwt_block_gated_on_setup_step_flag(self):
        """The DWT copy block must only fire when a DWT setup step ran.
        CDR-flow entries (Amber/LV/GloBird primary) must not gain DWT
        fields, since their dashboard_token render path is identical.
        """
        src = _config_flow_source()
        start = src.index("async def async_step_dashboard_token(")
        end = src.index("async def async_step_reauth(", start)
        block = src[start:end]
        assert (
            'self._data.get(CONF_DWT_OE_ENABLED)' in block
            and 'self._data.get(CONF_DWT_AEMO_ENABLED)' in block
        )


# ----------------------------------------------------------------------
# P2 — comparator form hides static_prd until a static plan is stored
# ----------------------------------------------------------------------


class TestStaticPrdHiddenWithoutStoredPlan:
    """Codex P2#2: ALL_PRICING_MODES contained static_prd and was fed
    directly to Amber/FP/LV selectors. No flow writes CONF_*_STATIC_PLAN,
    so selecting static_prd bricked the reload with ConfigEntryNotReady
    (Amber/LV) or a silent fallback-with-warning (Flow Power).
    """

    def test_modes_helper_filters_static_prd_when_plan_absent(self):
        src = _config_flow_source()
        # The fix introduces a per-comparator helper.
        assert "def _modes_for(" in src
        # It excludes PRICING_MODE_STATIC_PRD when the static plan key
        # is missing from options.
        assert "if m != PRICING_MODE_STATIC_PRD" in src

    def test_comparator_form_uses_per_provider_mode_options(self):
        """Each of the three comparator selectors must read its own
        gated options list, not share a single `_mode_options` list
        (the original bug)."""
        src = _config_flow_source()
        assert "_amber_mode_options" in src
        assert "_fp_mode_options" in src
        assert "_lv_mode_options" in src

    def test_static_plan_consts_imported(self):
        src = _config_flow_source()
        assert "CONF_AMBER_STATIC_PLAN" in src
        assert "CONF_LOCALVOLTS_STATIC_PLAN" in src
        assert "CONF_FLOW_POWER_STATIC_PLAN" in src

    def test_modes_helper_uses_correct_static_key_per_provider(self):
        """Each comparator gets its own static-plan key — Amber must
        not be gated on the LV static plan or vice versa."""
        src = _config_flow_source()
        assert "_modes_for(CONF_AMBER_STATIC_PLAN)" in src
        assert "_modes_for(CONF_FLOW_POWER_STATIC_PLAN)" in src
        assert "_modes_for(CONF_LOCALVOLTS_STATIC_PLAN)" in src


# ----------------------------------------------------------------------
# P3 — reauth dispatcher survives startup auth-failure without runtime_data
# ----------------------------------------------------------------------


class TestReauthDispatcherFallbackToEntryData:
    """Codex P2#3: dispatcher read coordinator._reauth_provider_id via
    entry.runtime_data, but runtime_data is set AFTER
    async_config_entry_first_refresh() succeeds. Auth failures during
    startup or first refresh therefore got no provider id and aborted
    with reauth_provider_unknown.
    """

    def test_dispatcher_falls_back_to_entry_data_provider(self):
        src = _config_flow_source()
        start = src.index("async def async_step_reauth(")
        end = src.index("async def async_step_reauth_amber(", start)
        block = src[start:end]
        # The fallback must read CONF_CURRENT_PROVIDER from entry.data.
        assert "entry.data.get(CONF_CURRENT_PROVIDER)" in block
        # The fallback must only fire when the coordinator tag is None.
        assert "if provider_id is None:" in block

    def test_dispatcher_still_prefers_coordinator_tag_when_present(self):
        """The coordinator tag is provider-specific (could be the
        comparator that failed, not the primary). Don't replace it —
        only fall back when missing."""
        src = _config_flow_source()
        start = src.index("async def async_step_reauth(")
        end = src.index("async def async_step_reauth_amber(", start)
        block = src[start:end]
        # The original read-from-runtime_data path stays.
        assert '"_reauth_provider_id"' in block
        # entry_data parameter is still discarded (the fix uses entry.data
        # via _get_reauth_entry, not the entry_data mapping argument).
        assert "del entry_data" in block


# ----------------------------------------------------------------------
# P4 — reconfigure dispatcher uses entry.data not CDR plan id
# ----------------------------------------------------------------------


class TestReconfigureDispatcherUsesEntryData:
    """Codex P2#4: dispatcher read coordinator._current_plan_provider.id,
    but CdrPlanProvider.id is ``{brand}_{plan_id}`` (e.g.
    ``amber_brokerage-xyz``), never the literal PROVIDER_AMBER /
    PROVIDER_LOCALVOLTS. CDR-backed Amber/LV entries — the install base —
    therefore fell through to reconfigure_unsupported.
    """

    def test_reconfigure_routes_via_entry_data_provider(self):
        src = _config_flow_source()
        start = src.index("async def async_step_reconfigure(")
        end = src.index("async def async_step_reconfigure_amber(", start)
        block = src[start:end]
        assert "entry.data.get(CONF_CURRENT_PROVIDER)" in block

    def test_reconfigure_does_not_read_coordinator_plan_id(self):
        """Regression guard: a future refactor must not re-introduce
        the coordinator-id lookup, which is the CDR brand_planId for
        the install base and never matches the comparison literals."""
        src = _config_flow_source()
        start = src.index("async def async_step_reconfigure(")
        end = src.index("async def async_step_reconfigure_amber(", start)
        block = src[start:end]
        assert "_current_plan_provider" not in block


# ----------------------------------------------------------------------
# P5 — Amber schedule fetch gated on live API mode
# ----------------------------------------------------------------------


class TestAmberScheduleFetchGatedOnLiveMode:
    """Codex P2#5: ``_maybe_poll_amber`` returns early in static/off
    mode without updating _last_amber_poll, leaving it at 0.0 forever.
    ``_async_update_data`` then re-triggers _fetch_today_price_schedule
    on every 30s tick because ``_last_amber_poll == 0.0`` is its
    first-run sentinel. Result: DWT and static-Amber entries hammered
    Amber's API every 30s with stale or missing credentials.
    """

    def test_schedule_fetch_guard_checks_live_api_mode(self):
        src = _coordinator_source()
        # Find the first-run guard at the top of _async_update_data.
        start = src.index("async def _async_update_data(")
        end = src.index("await self._maybe_poll_amber()", start)
        block = src[start:end]
        # The guard must check pricing mode in addition to the
        # _last_amber_poll == 0.0 sentinel.
        assert "self._amber_mode == PRICING_MODE_LIVE_API" in block
        assert "self._last_amber_poll == 0.0" in block

    def test_schedule_fetch_only_called_under_combined_guard(self):
        """The _fetch_today_price_schedule call must sit inside the
        combined `(== 0.0) and (LIVE_API)` guard, not before it or
        in a separate branch.
        """
        src = _coordinator_source()
        start = src.index("async def _async_update_data(")
        end = start + src[start:].index("await self._maybe_poll_amber()")
        block = src[start:end]
        # Single call site for the first-run schedule fetch.
        assert block.count("await self._fetch_today_price_schedule()") == 1
        # And it must follow the combined guard — find the `if` that
        # encloses it.
        idx = block.index("await self._fetch_today_price_schedule()")
        # Walk back to find the controlling `if` — must reference both
        # _last_amber_poll and PRICING_MODE_LIVE_API.
        preceding = block[:idx]
        last_if = preceding.rindex("if ")
        guard = preceding[last_if:idx]
        assert "_last_amber_poll" in guard
        assert "PRICING_MODE_LIVE_API" in guard
