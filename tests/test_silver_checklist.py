"""Phase 8 PR-9 — HA Silver tickbox tests.

Verifies the load-bearing invariants of the Silver flip:
- manifest declares quality_scale=silver + version bumped.
- quality_scale.yaml parses + has all expected rules.
- sensor.py declares PARALLEL_UPDATES.
- Service handlers use action-exceptions discipline.
"""

from __future__ import annotations

import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _manifest() -> dict:
    return json.load(
        open(REPO / "custom_components" / "pricehawk" / "manifest.json")
    )


def _quality_scale() -> dict:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        # Fall back to a tiny YAML subset parser sufficient for our format.
        raw = (
            REPO / "custom_components" / "pricehawk" / "quality_scale.yaml"
        ).read_text()
        return _parse_quality_scale(raw)
    return yaml.safe_load(
        (REPO / "custom_components" / "pricehawk" / "quality_scale.yaml").read_text()
    )


def _parse_quality_scale(raw: str) -> dict:
    """Tiny YAML parser specific to quality_scale.yaml shape.

    Format:
        rules:
          rule_name:
            status: done|exempt|todo
            comment: >-
              text...
    """
    rules: dict[str, dict[str, str]] = {}
    current_rule: str | None = None
    current_key: str | None = None
    multiline_collect: list[str] = []
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # Rule heading "  rule-name:" at 2 spaces of indent.
        stripped = line.rstrip()
        indent = len(line) - len(line.lstrip())
        if indent == 2 and stripped.endswith(":") and ":" in stripped:
            current_rule = stripped.strip().rstrip(":")
            rules[current_rule] = {}
            current_key = None
            multiline_collect = []
            continue
        if indent == 4 and ":" in stripped:
            # End any pending multiline collect.
            if current_key and multiline_collect:
                rules[current_rule][current_key] = " ".join(multiline_collect).strip()
                multiline_collect = []
            key, _, value = stripped.strip().partition(":")
            value = value.strip()
            if value in ("", ">-", ">"):
                current_key = key
                multiline_collect = []
            else:
                rules[current_rule][key] = value
                current_key = None
            continue
        if indent >= 6 and current_key:
            multiline_collect.append(stripped.strip())
    if current_key and multiline_collect:
        rules[current_rule][current_key] = " ".join(multiline_collect).strip()
    return {"rules": rules}


class TestManifest:
    def test_quality_scale_silver(self):
        assert _manifest()["quality_scale"] == "silver"

    def test_version_bumped(self):
        m = _manifest()
        # Track the in-flight beta line. Bump this assertion when cutting
        # a new HACS-beta release. Hard-coded so a stray manifest edit
        # can't silently break HACS version pinning.
        assert m["version"] == "1.6.0-beta.9", (
            f"manifest version should be 1.6.0-beta.9, got {m['version']}"
        )

    def test_codeowner_present(self):
        assert "@Artic0din" in _manifest()["codeowners"]

    def test_requirements_pin_intact(self):
        # Phase 7 PR-2 pin must survive the Silver flip.
        reqs = _manifest()["requirements"]
        assert any("openelectricity" in r for r in reqs)


class TestQualityScaleYaml:
    def test_file_parses(self):
        qs = _quality_scale()
        assert "rules" in qs

    def test_silver_rules_marked_done(self):
        qs = _quality_scale()
        silver_done = (
            "reauthentication-flow",
            "reconfiguration-flow",
            "parallel-updates",
            "action-exceptions",
            "config-entry-unloading",
            "entity-unavailable",
            "integration-owner",
            "test-coverage",
        )
        for rule in silver_done:
            assert rule in qs["rules"], f"quality_scale.yaml missing {rule}"
            status = qs["rules"][rule]["status"]
            assert status == "done", (
                f"{rule} should be 'done' for Silver, got {status!r}"
            )

    def test_log_when_unavailable_documented_as_exempt(self):
        qs = _quality_scale()
        assert qs["rules"]["log-when-unavailable"]["status"] == "exempt"

    def test_diagnostics_marked_done(self):
        qs = _quality_scale()
        assert qs["rules"]["diagnostics"]["status"] == "done"

    def test_repairs_marked_done(self):
        qs = _quality_scale()
        assert qs["rules"]["repairs"]["status"] == "done"


class TestSensorParallelUpdates:
    def test_sensor_declares_parallel_updates(self):
        src = (
            REPO / "custom_components" / "pricehawk" / "sensor.py"
        ).read_text()
        assert "PARALLEL_UPDATES = 0" in src


class TestServiceHandlerExceptions:
    def test_init_imports_home_assistant_error(self):
        src = (
            REPO / "custom_components" / "pricehawk" / "__init__.py"
        ).read_text()
        assert (
            "from homeassistant.exceptions import HomeAssistantError"
            in src
        )
        assert "ServiceValidationError" in src

    def test_handlers_raise_home_assistant_error_on_missing_coordinator(self):
        src = (
            REPO / "custom_components" / "pricehawk" / "__init__.py"
        ).read_text()
        # At least one raise per handler — count must match the three handlers.
        assert src.count("raise HomeAssistantError(") >= 3

    def test_handlers_raise_service_validation_error_on_bad_input(self):
        src = (
            REPO / "custom_components" / "pricehawk" / "__init__.py"
        ).read_text()
        # backfill_history + rank_alternatives each raise on bad input.
        assert src.count("raise ServiceValidationError(") >= 2
