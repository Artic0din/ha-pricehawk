"""Phase 11 PR-17 — CI workflow validation.

Verifies the validate.yaml workflow ships both hassfest + HACS jobs +
triggers on PR + push + schedule.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VALIDATE_YAML = REPO / ".github" / "workflows" / "validate.yaml"


def _validate_yaml() -> str:
    return VALIDATE_YAML.read_text()


class TestValidateWorkflow:
    def test_file_exists(self):
        assert VALIDATE_YAML.exists()

    def test_triggers_on_pull_request(self):
        src = _validate_yaml()
        assert "pull_request:" in src

    def test_triggers_on_push(self):
        src = _validate_yaml()
        assert "push:" in src

    def test_runs_hassfest_job(self):
        src = _validate_yaml()
        assert "validate-hassfest:" in src
        assert "home-assistant/actions/hassfest" in src

    def test_runs_hacs_job(self):
        src = _validate_yaml()
        assert "validate-hacs:" in src
        assert "hacs/action" in src

    def test_hacs_category_integration(self):
        src = _validate_yaml()
        assert 'category: "integration"' in src

    def test_permissions_minimum_required(self):
        """Silver rule: minimum permissions per job."""
        src = _validate_yaml()
        # Top-level permissions block scoped to contents:read; no
        # write-all anti-pattern.
        assert "contents: read" in src
        assert "write-all" not in src
