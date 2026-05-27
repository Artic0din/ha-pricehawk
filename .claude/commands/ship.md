---
description: Squash, push, flip to ready, enable auto-merge
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

3. **Flip PR from draft to ready**
   ```bash
   gh pr ready
   ```

4. **Enable auto-merge with squash**
   ```bash
   gh pr merge --auto --squash
   ```

5. **Confirm to Ryan**
   - PR number and URL
   - Required checks status
   - Codex review status (P0/P1 clean)
   - Auto-merge enabled

6. **Stop. Do not poll for merge — GitHub will merge when checks pass.**
