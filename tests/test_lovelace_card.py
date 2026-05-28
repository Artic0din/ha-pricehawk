"""Phase 10 PR-14 — Lovelace card source + registration tests."""

from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _card_js() -> str:
    return (REPO / "custom_components" / "pricehawk" / "www" / "pricehawk-card.js").read_text()


def _dashboard_config_src() -> str:
    return (REPO / "custom_components" / "pricehawk" / "dashboard_config.py").read_text()


def _init_src() -> str:
    return (REPO / "custom_components" / "pricehawk" / "__init__.py").read_text()


class TestCardAsset:
    def test_card_js_exists(self):
        path = REPO / "custom_components" / "pricehawk" / "www" / "pricehawk-card.js"
        assert path.exists()

    def test_card_defines_pricehawk_cost_card(self):
        src = _card_js()
        assert "customElements.define(" in src
        assert '"pricehawk-cost-card"' in src

    def test_card_registers_in_customCards_catalogue(self):
        """HA's "Add Card" picker reads window.customCards."""
        src = _card_js()
        assert "window.customCards" in src
        assert '"pricehawk-cost-card"' in src

    def test_card_uses_setConfig_and_getCardSize(self):
        """Lovelace custom-card interface contract."""
        src = _card_js()
        assert "setConfig(config)" in src
        assert "getCardSize()" in src

    def test_card_default_entity_is_today_cost(self):
        """Phase 9 PR-11 sensor is the default."""
        src = _card_js()
        assert '"sensor.pricehawk_today_cost"' in src


class TestResourceRegistration:
    def test_register_function_defined(self):
        src = _dashboard_config_src()
        assert "async def register_lovelace_card_resource(" in src

    def test_resource_url_constant(self):
        src = _dashboard_config_src()
        assert 'LOVELACE_CARD_RESOURCE_URL = "/local/pricehawk/pricehawk-card.js"' in src

    def test_resource_type_module(self):
        src = _dashboard_config_src()
        assert '"res_type": "module"' in src

    def test_dedup_existing_resource(self):
        """Avoid duplicate registration on entry reload."""
        src = _dashboard_config_src()
        assert "existing = [" in src
        assert "LOVELACE_CARD_RESOURCE_URL" in src

    def test_called_from_async_setup_entry(self):
        src = _init_src()
        assert "await register_lovelace_card_resource(hass)" in src


class TestCopyAsset:
    def test_card_js_copied_alongside_panel_js(self):
        src = _dashboard_config_src()
        assert "shutil.copy2(str(src_card_js), card_js_path)" in src
