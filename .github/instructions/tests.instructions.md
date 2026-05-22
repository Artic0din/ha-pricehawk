---
applyTo: "**/{tests,test,__tests__,spec}/**"
---

# Test rules

## When tests are required (same PR)
- New public API
- New endpoint or route
- New database operation
- Significant behaviour change to existing code
- Bug fix — write the failing test first

## Test what matters
- Test behaviour, not implementation. Avoid asserting on internal state
  when an observable output would do.
- Bug-fix TDD: failing test reproduces the bug, verify it fails for the
  expected reason, then fix.
- No mocks for systems whose contracts you're trying to verify — use real
  fakes (in-memory DB, test fixtures).

## Test scope
- Unit tests fast. Integration tests slow but realistic.
- Coverage target: 90% on changed lines (Codecov patch).
- Coverage target: project auto with 1% threshold.

## Don't
- Don't test framework code.
- Don't test mocks (you'll just verify the mock matches itself).
- Don't write tests that mirror implementation 1:1.
