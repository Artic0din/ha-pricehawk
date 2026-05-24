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

    def test_every_service_handler_raises_home_assistant_error(self):
        """Silver action-exceptions rule: every service handler must raise
        HomeAssistantError on unrecoverable conditions (missing coordinator,
        no entries, etc).

        Prior versions of this test counted total occurrences with a
        hard-coded threshold (``>= 3``) — when handle_reset_today was added
        in beta.8 without a raise, the count was still 3 and the test
        stayed green. Gemini caught the compliance gap on PR #152.

        First rewrite used regex-sliced "function bodies", which gemini
        flagged on PR #154 as fragile: the last handler's "body" extended
        to EOF, swallowing any post-handler ``raise HomeAssistantError(``
        text (e.g. in registration calls or docstrings) into a false
        positive.

        Final version uses ``ast`` to walk real function bodies. Per
        handler: find every ``raise HomeAssistantError(...)`` node inside
        the function definition. Threshold auto-scales with handler count.
        """
        import ast
        src = (
            REPO / "custom_components" / "pricehawk" / "__init__.py"
        ).read_text()
        tree = ast.parse(src)

        def _iter_async_funcs(node):
            """Yield AsyncFunctionDef nodes recursively (handlers may be
            nested inside async_setup_entry, etc)."""
            for child in ast.walk(node):
                if isinstance(child, ast.AsyncFunctionDef):
                    yield child

        def _body_has_raise_hae(func: ast.AsyncFunctionDef) -> bool:
            """Walk only this function's body (not nested defs) and check
            for ``raise HomeAssistantError(...)``.

            Gemini caught on PR #154 that ``ast.walk`` is a flat generator
            — it yields every node in the subtree regardless. ``continue``
            on an encountered nested FunctionDef/AsyncFunctionDef only
            skipped THAT node, not its children, so a ``raise
            HomeAssistantError(...)`` inside a nested helper would still
            satisfy the check for the outer handler. Manual DFS with
            ``ast.iter_child_nodes`` properly prunes the subtree.
            """
            for stmt in func.body:
                todo: list[ast.AST] = [stmt]
                while todo:
                    node = todo.pop()
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        # Skip the entire nested-scope subtree — they're
                        # validated when they're top-level themselves.
                        continue
                    if isinstance(node, ast.Raise):
                        exc = node.exc
                        if (
                            isinstance(exc, ast.Call)
                            and isinstance(exc.func, ast.Name)
                            and exc.func.id == "HomeAssistantError"
                        ):
                            return True
                    todo.extend(ast.iter_child_nodes(node))
            return False

        handlers = [
            f for f in _iter_async_funcs(tree)
            if f.name.startswith("handle_")
        ]
        assert len(handlers) >= 4, (
            f"Expected at least 4 service handlers in __init__.py, "
            f"found {len(handlers)}: {[h.name for h in handlers]}."
        )

        missing = [
            h.name for h in handlers
            if not _body_has_raise_hae(h)
        ]
        assert not missing, (
            f"Silver action-exceptions: these handlers don't raise "
            f"HomeAssistantError anywhere in their body: {missing}. "
            f"Every service handler must raise on unrecoverable conditions."
        )

    def test_handlers_raise_service_validation_error_on_bad_input(self):
        src = (
            REPO / "custom_components" / "pricehawk" / "__init__.py"
        ).read_text()
        # backfill_history + rank_alternatives each raise on bad input.
        assert src.count("raise ServiceValidationError(") >= 2
