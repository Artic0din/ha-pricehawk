---
description: Address P0 and P1 Codex review comments
---

Apply P0 and P1 review findings from Codex on the current PR. Acknowledge P2/P3 without fixing.

## Steps

1. **Fetch latest review state**
   ```bash
   gh pr view --comments
   gh api repos/{owner}/{repo}/pulls/{n}/comments
   ```

2. **Triage by severity**
   - P0 findings → fix
   - P1 findings → fix
   - P2 findings → reply `Acknowledged — tracked for later.` and resolve
   - P3 findings → reply `Acknowledged — style/nit.` and resolve

3. **For each P0/P1, apply the minimal fix**
   - Locate exact file and line from the Codex comment
   - Apply the smallest change that resolves the finding
   - Do not refactor beyond what was flagged
   - One fixup commit per logical fix:
     ```
     fix({scope}): {one-line description}

     Resolves Codex P0 finding.
     ```

4. **Verify locally before pushing**
   ```bash
   ruff check . && ruff format --check .
   mypy custom_components/pricehawk
   pytest --cov=custom_components/pricehawk --cov-fail-under=70
   ```

5. **Push to PR branch (never to main)**

6. **Reply to each thread with fix SHA**
   - Fix applied: `Fixed in <sha>. <one-line rationale>`
   - Pushing back: `Disagree: <reason>. Leaving as-is.` (do not resolve)

7. **Resolve threads only after reply**

8. **Loop cap: 3 rounds maximum.** If the same finding reappears after 2 fix rounds, stop and surface to Ryan with the diff.

9. **Stop conditions — bail and surface to Ryan**
   - Same Codex finding reappears after 2 rounds
   - Codex flags P0 in a `config_flow.py` migration handler
   - Coverage on changed lines drops
   - Test count decreases between rounds
   - PR grows past 500 lines during fixes
