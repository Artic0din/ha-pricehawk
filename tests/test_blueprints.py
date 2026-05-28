"""Phase 10 PR-15 — blueprint library tests.

Sanity-checks the 5 shipped blueprints: YAML parses, has the required
blueprint metadata block, declares domain=automation, names a
source_url, and exposes at least one `input` plus a `trigger`.
"""

from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
BP_DIR = REPO / "custom_components" / "pricehawk" / "blueprints" / "automation" / "pricehawk"

EXPECTED = [
    "cheapest_plan_alert.yaml",
    "cheapest_30min_window.yaml",
    "pause_ev_on_spike.yaml",
    "daily_7pm_summary.yaml",
    "wholesale_spike_alert.yaml",
]


def _parse_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore[import-not-found]

        # HA's !input tag is custom; register a passthrough constructor
        # so safe_load can parse blueprint files without choking.
        class _SafeLoader(yaml.SafeLoader):
            pass

        _SafeLoader.add_constructor("!input", lambda loader, node: f"!input {node.value}")
        return yaml.load(path.read_text(), Loader=_SafeLoader)
    except ImportError:
        # No yaml lib at runtime — fall back to a tiny scanner that
        # only verifies the blueprint header block exists.
        raw = path.read_text()
        return {
            "blueprint": {
                "name": (
                    [
                        line.split("name:", 1)[1].strip()
                        for line in raw.splitlines()
                        if line.strip().startswith("name:")
                    ]
                    or ["?"]
                )[0],
                "domain": (
                    [
                        line.split("domain:", 1)[1].strip()
                        for line in raw.splitlines()
                        if line.strip().startswith("domain:")
                    ]
                    or [None]
                )[0],
                "source_url": (
                    [
                        line.split("source_url:", 1)[1].strip()
                        for line in raw.splitlines()
                        if line.strip().startswith("source_url:")
                    ]
                    or [None]
                )[0],
                "input": "input:" in raw,
            },
            "trigger": "trigger:" in raw,
        }


class TestBlueprintLibrary:
    def test_all_5_blueprints_present(self):
        for name in EXPECTED:
            assert (BP_DIR / name).exists(), f"missing blueprint: {name}"

    def test_blueprints_parse_yaml(self):
        for name in EXPECTED:
            data = _parse_yaml(BP_DIR / name)
            assert "blueprint" in data, f"{name} missing blueprint block"

    def test_blueprints_declare_domain_automation(self):
        for name in EXPECTED:
            data = _parse_yaml(BP_DIR / name)
            assert data["blueprint"]["domain"] == "automation"

    def test_blueprints_name_source_url(self):
        for name in EXPECTED:
            data = _parse_yaml(BP_DIR / name)
            src = data["blueprint"]["source_url"]
            assert src, f"{name} missing source_url"
            assert "Artic0din/ha-pricehawk" in src

    def test_blueprints_expose_inputs(self):
        for name in EXPECTED:
            data = _parse_yaml(BP_DIR / name)
            inp = data["blueprint"].get("input")
            assert inp, f"{name} has no input block"

    def test_blueprints_have_trigger(self):
        for name in EXPECTED:
            data = _parse_yaml(BP_DIR / name)
            assert data.get("trigger"), f"{name} missing trigger"

    def test_cheapest_plan_alert_uses_ranked_alternatives_sensor(self):
        raw = (BP_DIR / "cheapest_plan_alert.yaml").read_text()
        assert "sensor.pricehawk_ranked_alternatives" in raw

    def test_daily_7pm_summary_uses_today_cost_sensor(self):
        raw = (BP_DIR / "daily_7pm_summary.yaml").read_text()
        assert "sensor.pricehawk_today_cost" in raw

    def test_pause_ev_blueprint_uses_hysteresis(self):
        raw = (BP_DIR / "pause_ev_on_spike.yaml").read_text()
        assert "hysteresis_c_kwh" in raw
        # Numeric trigger has both above + below to implement hysteresis.
        assert "above:" in raw
        assert "below:" in raw
