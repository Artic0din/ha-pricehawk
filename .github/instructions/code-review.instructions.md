---
applyTo: "**"
---

# Code review rules (for AI reviewers and human reviewers)

## Tiered findings
- **Tier 1 (block merge)** — logic bugs, security, breakage, missing tests
  for new public APIs.
- **Tier 2 (should consider)** — performance pitfalls, API design,
  documentation drift, concurrency issues.
- **Tier 3 (strong justification only)** — architectural concerns, naming.

## What NOT to flag
- Code formatting, whitespace, quotes, line length — linters own these.
- Import ordering, unused imports — linters own.
- Refactoring preferences — author's call.
- Missing docstrings on internal helpers — only flag on public APIs.
- Test style — author's call.

## Asymmetric merge gate
Only Tier 1 findings block merge. Tier 2 and Tier 3 record concerns but
don't fail CI. Reviewer can request changes manually for high-impact
Tier 2/3 issues.

## Override label
`ai-review-override` label bypasses the gate. Use only for:
- Anthropic API outage (Claude review didn't run)
- Trailer malformed but actual review fine
- Production incident requiring immediate merge

Include "why" in PR body when applying the label.

## Draft PRs
Draft PRs skip Claude review; the merge gate passes open until you mark
the PR "Ready for review" — at which point claude-review fires and the
gate evaluates its verdict.

## Required check
Branch protection requires only the literal `CI passed` check (rollup in
caller `ci.yml`). Individual job results feed into it via `needs:`.

## Never auto-merge
Even if all checks pass. Human merge button always.
