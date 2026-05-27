"""CI workflow validation.

Asserts ci.yml ships the hassfest + HACS jobs (consolidated from the prior
separate validate.yaml workflow) and respects the silver-rule permissions
floor.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CI_YAML = REPO / ".github" / "workflows" / "ci.yml"


def _ci_yaml() -> str:
    return CI_YAML.read_text()


class TestCiWorkflow:
    def test_file_exists(self):
        assert CI_YAML.exists()

    def test_triggers_on_pull_request(self):
        assert "pull_request:" in _ci_yaml()

    def test_triggers_on_push_to_main(self):
        src = _ci_yaml()
        assert "push:" in src
        assert "branches: [main]" in src

    def test_validate_job_runs_hassfest(self):
        src = _ci_yaml()
        assert "validate:" in src
        assert "home-assistant/actions/hassfest" in src

    def test_validate_job_runs_hacs(self):
        src = _ci_yaml()
        assert "hacs/action" in src

    def test_hacs_category_integration(self):
        src = _ci_yaml()
        assert "category: integration" in src

    def test_permissions_minimum_required(self):
        """Silver rule: minimum permissions per job."""
        src = _ci_yaml()
        assert "contents: read" in src
        assert "write-all" not in src
