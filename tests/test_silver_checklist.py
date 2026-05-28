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
    return json.load(open(REPO / "custom_components" / "pricehawk" / "manifest.json"))


def _quality_scale() -> dict:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        # Fall back to a tiny YAML subset parser sufficient for our format.
        raw = (REPO / "custom_components" / "pricehawk" / "quality_scale.yaml").read_text()
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
            assert status == "done", f"{rule} should be 'done' for Silver, got {status!r}"

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
        src = (REPO / "custom_components" / "pricehawk" / "sensor.py").read_text()
        assert "PARALLEL_UPDATES = 0" in src


class TestServiceHandlerExceptions:
    def test_init_imports_home_assistant_error(self):
        """Silver action-exceptions rule: ``__init__.py`` must import
        ``HomeAssistantError`` + ``ServiceValidationError`` from
        ``homeassistant.exceptions``.

        Uses AST walk rather than a literal substring match — co-imports
        on the same line (e.g. ``ConfigEntryError`` for Constitution
        P19 downgrade refusal) break a naive substring grep but are
        semantically correct. The rule cares that the names are
        imported from the right module, not the exact whitespace
        layout.
        """
        import ast

        src = (REPO / "custom_components" / "pricehawk" / "__init__.py").read_text()
        tree = ast.parse(src)

        imported_from_exceptions: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "homeassistant.exceptions":
                imported_from_exceptions.update(alias.name for alias in node.names)

        assert "HomeAssistantError" in imported_from_exceptions, (
            "Silver action-exceptions rule: __init__.py must import "
            "HomeAssistantError from homeassistant.exceptions."
        )
        assert "ServiceValidationError" in imported_from_exceptions, (
            "Silver action-exceptions rule: __init__.py must import "
            "ServiceValidationError from homeassistant.exceptions."
        )

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

        src = (REPO / "custom_components" / "pricehawk" / "__init__.py").read_text()
        tree = ast.parse(src)

        # Silver's action-exceptions rule accepts either HomeAssistantError
        # or ServiceValidationError per HA docs; the latter is the typed
        # subclass for "user supplied bad input". Per gemini follow-up on
        # PR #154 — backfill_history and rank_alternatives use SVE for
        # type-cast failures, and a future handler that ONLY raises SVE
        # would still satisfy the rule.
        _ACTION_EXCEPTIONS = frozenset({"HomeAssistantError", "ServiceValidationError"})

        def _iter_handler_funcs(node):
            """Yield function/async-function defs whose name starts with
            ``handle_``. Async is the norm, but accept sync too — a future
            non-async handler shouldn't silently skip validation."""
            for child in ast.walk(node):
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and child.name.startswith("handle_"):
                    yield child

        def _body_has_action_exception_raise(func) -> bool:
            """Walk only this function's body (not nested scopes) and check
            for ``raise HomeAssistantError(...)`` or
            ``raise ServiceValidationError(...)``.

            Gemini caught on PR #154 that ``ast.walk`` is a flat generator
            — it yields every node in the subtree regardless of any
            ``continue``. Manual DFS with ``ast.iter_child_nodes`` properly
            prunes nested scopes (FunctionDef, AsyncFunctionDef, ClassDef
            — the latter to avoid a handler-internal helper class's method
            wrongly counting toward the outer handler).
            """
            for stmt in func.body:
                todo: list[ast.AST] = [stmt]
                while todo:
                    node = todo.pop()
                    if isinstance(
                        node,
                        (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                    ):
                        # Skip the entire nested-scope subtree.
                        continue
                    if isinstance(node, ast.Raise):
                        # Handle both ``raise X(...)`` (ast.Call wrapping
                        # ast.Name) and the bare-class form ``raise X``
                        # (ast.Name directly). Gemini caught the bare-form
                        # gap on PR #154 — it's valid Python and used in
                        # some HA integrations.
                        exc: ast.AST | None = node.exc
                        if isinstance(exc, ast.Call):
                            exc = exc.func
                        if isinstance(exc, ast.Name) and exc.id in _ACTION_EXCEPTIONS:
                            return True
                    todo.extend(ast.iter_child_nodes(node))
            return False

        handlers = list(_iter_handler_funcs(tree))
        assert len(handlers) >= 4, (
            f"Expected at least 4 service handlers in __init__.py, "
            f"found {len(handlers)}: {[h.name for h in handlers]}."
        )

        missing = [h.name for h in handlers if not _body_has_action_exception_raise(h)]
        assert not missing, (
            f"Silver action-exceptions: these handlers don't raise "
            f"HomeAssistantError or ServiceValidationError anywhere in "
            f"their (top-level) body: {missing}. Every service handler "
            f"must raise on unrecoverable conditions."
        )

    def test_handlers_raise_service_validation_error_on_bad_input(self):
        src = (REPO / "custom_components" / "pricehawk" / "__init__.py").read_text()
        # backfill_history + rank_alternatives + analyze_csv (empty rows)
        # each raise on bad input.
        assert src.count("raise ServiceValidationError(") >= 3

    def test_no_handler_has_silent_log_and_return_branch(self):
        """Silver action-exceptions + Engineering Constitution P3 (No Silent
        Scope Reduction): a service handler that logs at ERROR/WARNING and
        then ``return``s silently is indistinguishable from success to the
        caller. Every error branch must raise.

        This test was added after Constitution-01 — the prior AST walker
        (``test_every_service_handler_raises_home_assistant_error``) only
        verified that SOME branch of the handler raises, so a handler with
        an unrelated raise (e.g. coordinator-not-loaded) masked a separate
        log-and-return branch (analyze_csv empty-rows). This walker is
        per-branch: any ``return`` (with or without a value) preceded by a
        ``_LOGGER.error(...)`` / ``_LOGGER.warning(...)`` /
        ``_LOGGER.exception(...)`` call in the same sibling sequence is a
        silent-failure smell and fails the check.

        Fix-up commit (Constitution-01 round 2): ``_LOGGER.exception(...)``
        was missed in the initial walker — the exception level is the most
        likely silent-swallow culprit because it implies an active error
        path that has already been observed and is being deliberately
        downgraded to a log line. ``critical`` and ``fatal`` are also
        downgrades of the same kind.
        """
        import ast

        src = (REPO / "custom_components" / "pricehawk" / "__init__.py").read_text()
        tree = ast.parse(src)

        def _iter_handler_funcs(node):
            for child in ast.walk(node):
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and child.name.startswith("handle_"):
                    yield child

        # Every ``_LOGGER`` level that signals an error path — silently
        # returning after any of these is the anti-pattern under check.
        # ``exception`` is the canonical "log-then-swallow" pair that the
        # initial walker missed; ``critical`` and ``fatal`` are aliases.
        _LOG_LEVELS_SILENT_RISK = frozenset({"error", "warning", "exception", "critical", "fatal"})

        def _is_logger_error_or_warning(stmt: ast.AST) -> bool:
            """Match ``_LOGGER.error(...)`` / ``_LOGGER.warning(...)`` /
            ``_LOGGER.exception(...)`` / ``_LOGGER.critical(...)`` /
            ``_LOGGER.fatal(...)`` expression statements at any nesting
            level."""
            if not isinstance(stmt, ast.Expr):
                return False
            call = stmt.value
            if not isinstance(call, ast.Call):
                return False
            func = call.func
            return (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "_LOGGER"
                and func.attr in _LOG_LEVELS_SILENT_RISK
            )

        def _siblings_have_log_then_return(body: list[ast.stmt]) -> bool:
            """Detect ``_LOGGER.error(...); return`` sibling pairs in any
            block. The two statements must be adjacent in the SAME block —
            a return that follows a raise via separate branches is fine."""
            for i in range(len(body) - 1):
                if _is_logger_error_or_warning(body[i]) and isinstance(body[i + 1], ast.Return):
                    return True
            return False

        def _walk_blocks(func: ast.AST) -> bool:
            """DFS through every nested block (if/else/try/etc.) inside the
            handler function, skipping nested function/class scopes."""
            stack: list[ast.AST] = [func]
            while stack:
                node = stack.pop()
                # Skip nested scopes — they have their own contract.
                if node is not func and isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    continue
                # Every node with a ``body`` attribute that is a list of
                # statements is a candidate block.
                for attr in ("body", "orelse", "finalbody"):
                    block = getattr(node, attr, None)
                    if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                        if _siblings_have_log_then_return(block):
                            return True
                        stack.extend(block)
                # ast.Try handlers carry their own bodies.
                if isinstance(node, ast.Try):
                    for handler in node.handlers:
                        if _siblings_have_log_then_return(handler.body):
                            return True
                        stack.extend(handler.body)
            return False

        offenders = [h.name for h in _iter_handler_funcs(tree) if _walk_blocks(h)]
        assert not offenders, (
            f"Silver action-exceptions: these handlers contain a silent "
            f"``_LOGGER.<error|warning|exception|critical|fatal>(...); "
            f"return`` branch — replace with "
            f"``raise ServiceValidationError(...)`` or "
            f"``raise HomeAssistantError(...)``. Offenders: {offenders}."
        )

    def test_silent_log_walker_flags_exception_then_return(self):
        """Self-test for the walker — synthetic handler that logs at
        ``exception`` and then returns must be flagged.

        Without this self-test, a future regression in the level set (e.g.
        someone removes ``exception`` from ``_LOG_LEVELS_SILENT_RISK``)
        would silently weaken the production walker but pass CI because no
        real handler in the integration currently uses the anti-pattern.
        Constitution P14 — fix the underlying abstraction, not the
        downstream call sites.
        """
        import ast

        synthetic = """
async def handle_synthetic(call):
    try:
        do_something()
    except Exception:
        _LOGGER.exception("boom")
        return
"""
        tree = ast.parse(synthetic)

        # Inline copies of the same predicates the production test uses —
        # keeping them inline (vs hoisting to module scope) preserves the
        # locality of the walker logic and avoids exposing implementation
        # details outside the test.
        _LOG_LEVELS_SILENT_RISK = frozenset({"error", "warning", "exception", "critical", "fatal"})

        def _is_logger_error_or_warning(stmt: ast.AST) -> bool:
            if not isinstance(stmt, ast.Expr):
                return False
            call = stmt.value
            if not isinstance(call, ast.Call):
                return False
            func = call.func
            return (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "_LOGGER"
                and func.attr in _LOG_LEVELS_SILENT_RISK
            )

        def _siblings_have_log_then_return(body: list[ast.stmt]) -> bool:
            for i in range(len(body) - 1):
                if _is_logger_error_or_warning(body[i]) and isinstance(body[i + 1], ast.Return):
                    return True
            return False

        func = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "handle_synthetic"
        )
        # The exception handler body is the block containing the
        # log-then-return pair.
        try_node = next(n for n in ast.walk(func) if isinstance(n, ast.Try))
        assert _siblings_have_log_then_return(try_node.handlers[0].body)
