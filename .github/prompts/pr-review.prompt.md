# PR review prompt

Review this pull request as a senior engineer. Read the diff, the files it
touches, and the repo's `.github/instructions/` directory for context. Apply
the tiered findings framework below.

## Output structure

1. **Walkthrough** — 3-6 bullets summarising what changed and why.
2. **Findings**, grouped by tier:
   - **Tier 1 (block merge)** — logic bugs, security issues, breakage,
     missing tests for new public APIs.
   - **Tier 2 (should consider)** — performance pitfalls, API design,
     documentation drift, concurrency issues.
   - **Tier 3 (strong justification only)** — architectural concerns, naming.
3. **Trailer** at the very end, in this exact format:

```yaml
trailer:
  tier_1_count: <N>
  tier_2_count: <N>
  tier_3_count: <N>
  verdict: "ship_it" | "ship_with_notes" | "block_merge"
```

## Verdict rules

- `block_merge` — any Tier 1 finding.
- `ship_with_notes` — zero Tier 1, but Tier 2 or Tier 3 findings present.
- `ship_it` — zero findings across all tiers.

## What NOT to flag

- Code formatting, whitespace, quotes, line length — linters own these.
- Import ordering, unused imports — linters own.
- Refactoring preferences ("could be a list comprehension") — author's call.
- Missing docstrings on internal helpers — only flag on public APIs.
- Test style (parametrise vs separate) — author's call.

## Repo-specific instructions

Read every file in `.github/instructions/` and apply its rules to the
relevant paths. Apply `applyTo:` globs strictly.

## Always

- Cite file paths + line numbers for every finding.
- Prefer the minimal fix; never suggest unrelated refactors.
- If a finding overlaps with a linter rule that should have caught it, say so.
