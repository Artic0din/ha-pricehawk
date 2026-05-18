# Copilot Code Review instructions

Steer GitHub Copilot Code Review toward inline-only, actionable feedback.
Avoid overlapping with Claude Code review's walkthrough.

## Do
- Inline comments on specific lines with concrete fixes.
- Reference repo rules in `.github/instructions/`.
- Flag obvious bugs, security issues, type errors.

## Don't
- Write PR-level walkthroughs or summaries — Claude review owns that.
- Restate findings the linter already catches.
- Suggest stylistic refactors.
- Comment on test style preferences.

## Tier 1 priority
Treat these as must-fix:
- Logic bugs that change observable behaviour incorrectly
- Security issues (XSS, SQL injection, exposed secrets)
- Missing tests for new public APIs
- AEST date bug pattern (`.toISOString().split('T')[0]`)
- Type system bypasses (`any`, `as` casts, force unwraps)
