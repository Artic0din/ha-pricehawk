---
description: Squash, push, hand off to Ryan for merge
---

Final step before merge. All P0/P1 addressed, CI green locally.

## Steps

1. **Rebase autosquash to fold fixup commits**
   ```bash
   git rebase -i --autosquash main
   ```

2. **Push (force-with-lease, only if rebasing required it)**
   ```bash
   git push --force-with-lease
   ```

3. **Confirm to Ryan**
   - PR number and URL
   - Required checks status
   - Codex review status (P0/P1 clean)

4. **Stop. Ryan presses the merge button — never enable auto-merge (hub hard rule 3).**
