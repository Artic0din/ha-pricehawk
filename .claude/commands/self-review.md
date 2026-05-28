---
description: Local self-review before flipping PR to ready
---

Run this before flipping the draft PR to ready-for-review.

## Steps

1. **Lint and format**
   ```bash
   uv run ruff check . --fix
   uv run ruff format .
   ```

2. **Types (ty — resolves real HA; replaced mypy)**
   ```bash
   uv run ty check
   ```

3. **Tests with coverage gate**
   ```bash
   uv run pytest --cov=custom_components/pricehawk --cov-fail-under=70 --cov-report=term-missing
   ```

4. **Secret scan**
   ```bash
   gitleaks detect --source . --no-git
   ```

5. **Diff review against AGENTS.md Review guidelines**

   Read the diff with `git diff main...HEAD`. For each change:
   - Any blocking I/O in async code? → P0, fix before pushing
   - Any missing `await`? → P0, fix before pushing
   - Any hardcoded secret / token / API key? → P0, fix before pushing
   - Any new HTTP call without timeout? → P1, fix before pushing
   - Any user-facing string outside `strings.json`? → P1, fix before pushing
   - Any new public function without test? → P1, add test before pushing
   - Any HA deprecation warning introduced? → P1, fix before pushing

6. **Constitution check (principles 11, 12, 13)**
   - Is the change *done* (impl + tests + error handling + no new lint errors)?
   - Was the root cause identified, not just the symptom patched?
   - Does existing behavior remain preserved (no regression)?

7. **Report findings, do not push fixes automatically.** Surface what was found and let Ryan decide.
