---
applyTo: "**/*.py"
---

# Python rules

## Type discipline
- Type hints on every public function (params + return).
- No `Any`. Use `TypeAlias` for complex types.
- `from __future__ import annotations` for forward refs.
- `pyright` strict on changed files.

## Exception handling
- No bare `except:`. Always name the exception type.
- Don't catch `Exception` unless you re-raise or log + re-raise.
- Use `try/except/else/finally` correctly — `else` for success path.

## Security
- No `eval`, no `exec`, no `pickle.loads` on untrusted input.
- SQL via parameterised queries / ORM. No string formatting into SQL.
- Sanitise user input at boundaries (Flask request, file paths).
- `bandit -ll` clean.

## Flask
- Blueprints for grouping routes.
- `request.get_json(force=False, silent=False)` — fail loud on bad JSON.
- Set `Content-Security-Policy`, `X-Content-Type-Options` headers.

## Date and time
- `datetime.now(tz=ZoneInfo("Australia/Melbourne"))`, not `datetime.now()`.
- Store UTC in DB, convert to AEST for display.
- `datetime.fromisoformat()` for parsing ISO strings.

## APScheduler / background jobs
- Every job has try/except logging the error.
- Idempotent — safe to re-run.
- Don't depend on job execution order unless explicitly serialised.
