"""Phase 10 PR-13 — Lit panel_custom foundation tests.

Source-level + filesystem asserts. Frontend bundle compilation +
visual UAT live in a dedicated Playwright follow-up.
"""

from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _dashboard_config_source() -> str:
    return (
        REPO / "custom_components" / "pricehawk" / "dashboard_config.py"
    ).read_text()


def _init_source() -> str:
    return (
        REPO / "custom_components" / "pricehawk" / "__init__.py"
    ).read_text()


def _panel_js() -> str:
    return (
        REPO / "custom_components" / "pricehawk" / "www" / "pricehawk-panel.js"
    ).read_text()


class TestPanelJSAsset:
    def test_panel_js_file_exists(self):
        path = REPO / "custom_components" / "pricehawk" / "www" / "pricehawk-panel.js"
        assert path.exists(), (
            "pricehawk-panel.js must exist for copy_www_assets to publish it"
        )

    def test_panel_defines_custom_element_pricehawk_panel(self):
        src = _panel_js()
        assert "customElements.define(" in src
        assert '"pricehawk-panel"' in src

    def test_panel_reads_today_cost_sensor_from_phase_9_pr11(self):
        """Lit panel surfaces the Energy-Dashboard-pickable sensor."""
        assert "sensor.pricehawk_today_cost" in _panel_js()

    def test_panel_imports_lit_from_module_url(self):
        """ESM CDN import — no build step required."""
        src = _panel_js()
        assert "lit-element" in src or "lit-html" in src
        assert "?module" in src  # ESM hint to the CDN

    def test_panel_uses_hass_states_not_llat(self):
        """Auth-via-host-session contract: read hass.states, no token in code."""
        src = _panel_js()
        assert "this.hass" in src
        # No token / LLAT plumbing in the source. Strip JS comments
        # before the check so the docstring's reference to "LLAT" in
        # the rationale doesn't trip us.
        import re
        code_only = re.sub(r"/\*[\s\S]*?\*/", "", src)
        code_only = re.sub(r"//.*", "", code_only)
        assert "token=" not in code_only
        assert "longLivedAccessToken" not in code_only
        assert "long_lived" not in code_only.lower()


class TestPanelCustomRegistration:
    def test_setup_panel_custom_v2_defined(self):
        assert "async def setup_panel_custom_v2(" in _dashboard_config_source()

    def test_panel_uses_component_name_custom(self):
        src = _dashboard_config_source()
        # The v2 registration uses panel_custom (component_name="custom").
        assert 'component_name="custom"' in src

    def test_panel_uses_panel_custom_config_dict(self):
        src = _dashboard_config_source()
        # HA's _panel_custom key drives the JS module + element name.
        assert "_panel_custom" in src
        assert '"embed_iframe": False' in src
        assert '"trust_external": False' in src

    def test_module_url_carries_version_busting_query(self):
        src = _dashboard_config_source()
        assert (
            'f"/local/pricehawk/pricehawk-panel.js?v={cache_token}"' in src
        )

    def test_v2_url_path_distinct_from_legacy(self):
        src = _dashboard_config_source()
        assert 'PANEL_V2_URL_PATH = "pricehawk"' in src
        assert 'PANEL_URL_PATH = "pricehawk-dashboard"' in src

    def test_v2_panel_called_from_async_setup_entry(self):
        src = _init_source()
        assert "await setup_panel_custom_v2(hass)" in src
        # Legacy iframe path still wired for the migration window.
        assert "await setup_panel_iframe(hass, entry)" in src

    def test_remove_panel_handles_both_paths(self):
        """Unload must clean up BOTH the legacy and v2 panels."""
        src = _dashboard_config_source()
        assert "for path in (PANEL_URL_PATH, PANEL_V2_URL_PATH)" in src


class TestCopyAssets:
    def test_copy_www_assets_includes_panel_js(self):
        src = _dashboard_config_source()
        # The asset copier must copy the new JS file alongside the
        # legacy HTML dashboard.
        assert "pricehawk-panel.js" in src
        # And specifically copy it from the source www/ directory.
        assert 'src_dir / "www" / "pricehawk-panel.js"' in src
