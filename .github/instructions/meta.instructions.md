---
applyTo: "**"
---

# Meta instructions

Universal rules for AI reviewers across every file in this repo.

## Type discipline
- Strict typing always. No `any` in TypeScript, no `Any` in Python, no
  implicit unwraps (`!`) in Swift, no untyped function signatures.
- Validate at system boundaries (user input, external APIs, file reads,
  parsed JSON, env vars). Don't pepper internal code with null checks
  for things the type system has proved can't be null.

## Date and time
- Never `toISOString().split('T')[0]` for local dates in JavaScript — in
  AEST, midnight local becomes yesterday in UTC.
- Server-side Python: use `datetime.now(tz=ZoneInfo("Australia/Melbourne"))`,
  not `datetime.now()`.
- Swift: `Calendar.current` not UTC components for user-facing dates.

## Secrets
- No `.env`, `.pem`, `.key`, `credentials.json`, tokens, or API keys in git.
- Verify diff with `git diff --staged | grep -iE "key|secret|token|password"`
  before push.

## Comments
- Default to no comments. Add one only when the WHY is non-obvious
  (hidden constraint, subtle invariant, surprising behaviour).
- Don't explain WHAT — well-named identifiers do that.
- Don't reference current task / fix / callers — that belongs in PR body.
