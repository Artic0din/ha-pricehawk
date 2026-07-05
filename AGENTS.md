# ha-pricehawk

Home Assistant custom integration (HACS) comparing real energy costs between [Amber Electric](https://www.amber.com.au) (wholesale spot pricing) and [GloBird Energy](https://www.globirdenergy.com.au) (time-of-use tariffs) using actual HA consumption data.

## Stack

- Python 3.12+ (CI floor; language target 3.13)
- Home Assistant 2025.2+ (min in `hacs.json` — options flow uses `OptionsFlowWithReload`)
- uv for dependency management (single source: `pyproject.toml [dependency-groups].dev`)
- pytest + pytest-homeassistant-custom-component; aioresponses for aiohttp HTTP mocking
- ruff (lint + format), ty (types — Astral, replaced mypy)
- HACS distribution

## Integration layout

```
custom_components/pricehawk/
├── __init__.py
├── manifest.json
├── config_flow.py       # Amber API key + GloBird tariff builder
├── const.py
├── sensor.py            # Cost calculation sensors
├── strings.json
├── tariffs/             # tariff calculation logic, NEVER in entity classes
├── translations/en.json
└── www/dashboard.html   # canonical dashboard, no repo-root copy
```

## Build, test, lint

All via uv (no pip, no requirements.txt):

- Install: `uv sync --group dev`
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Types: `uv run ty check`
- Tests: `uv run pytest --cov=custom_components/pricehawk --cov-fail-under=70`
- HACS validate: runs in CI via hacs/action
- HA validate: runs in CI via home-assistant/actions/hassfest

### ruff rule set

`select`: E, F, ASYNC, BLE, TRY, B, S, C901, RUF006.
`ignore`: E501 (line-length handles), TRY003 (HA raise-idiom noise).
`tests/**` ignores S + BLE (asserts + deliberate catch-all are by design).
`scripts/**` ignores S101/S310 (dev tooling).
C901 `max-complexity = 25` — a no-regression gate, NOT the target. Six
critical-logic functions (GloBird tariff `apply`, coordinator
`_async_update_data` / `async_restore_state` / `_apply_options_to_state`)
exceed 15; refactor them one-at-a-time with their own test focus and
ratchet 25 → 20 → 15 → 12. Never bundle a complexity refactor into a lint PR.

### types (ty)

ty resolves the real HA installed by pytest-homeassistant-custom-component,
so it type-checks against actual HA APIs (mypy couldn't on 3.12). Config in
`pyproject.toml [tool.ty]`. For HA stub-version gaps (a symbol the pinned
HA stub lacks but the runtime has), use `# ty: ignore[<rule>]` with a
one-line reason — never a blanket ignore. ty is pre-1.0; if a release goes
noisy, pin `ty==<version>` in the dev group.

### HTTP mocking (tests)

Clients use aiohttp (not httpx) — mock with `aioresponses`, not respx:
```python
from aioresponses import aioresponses
with aioresponses() as m:
    m.get(url, status=200, payload={...})  # or exception=asyncio.TimeoutError()
    async with aiohttp.ClientSession() as session:
        result = await fetch_fn(session, ...)
assert m.requests  # assert the route was called
```
Every API client needs failure-path tests: HTTP 500, timeout, malformed body.

## Engineering principles

This repo applies the standards defined in `ENGINEERING_CONSTITUTION.md`. The principles inform *why* the rules below exist; the rules below are what Codex enforces mechanically. When principles and pragmatism conflict, production standards apply to **code**; process rigor scales with **blast radius**.

## Code conventions

- All I/O is async. No `requests` library — use `aiohttp` via HA's `async_get_clientsession`.
- Config flow follows HA's modern selector pattern with full validation.
- All user-facing strings live in `strings.json`. Never hardcoded.
- Sensor entities use `SensorEntityDescription` dataclasses.
- Tariff calculations live in `pricehawk/tariffs/`, never in entity classes.
- Type hints on all public functions; `ty` gates them in CI.
- No bare `except:`. Always catch specifically, log with context, re-raise or handle deliberately.
- Use HA's `DataUpdateCoordinator` for all polling.

## GloBird tariff complexity

The config flow must support:
- Flat vs TOU import rates
- Stepped pricing (first X kWh at one rate, remainder at another)
- Multiple time windows per period (e.g. Shoulder = 9pm–12am + 12am–10am + 2pm–4pm)
- Separate import and export TOU schedules
- Optional incentives: ZEROHERO ($1/day credit), Super Export (15c/kWh), Critical Peak, free power windows
- Daily supply charge per plan

Sample plans in repo root as PDFs. Treat these as the contract.

## Review guidelines

Codex should surface findings at **all** priority levels (P0, P1, P2, and P3) —
including low-priority P3 nits. Pure formatting owned by `ruff` (line length,
import order, quotes, whitespace) is out of scope and must not be flagged.

### P0 — drop everything to fix

- Blocking I/O (`requests`, `time.sleep`, file ops without `aiofiles`) inside async code
- Missing `await` on a coroutine call
- Hardcoded token, API key, secret, or credential in any file
- Token, API key, or PII appearing in a log statement
- New `try/except` that suppresses an exception without addressing the cause AND without a `# noqa: reason` comment
- Tariff calculation change without a corresponding edge-case test (negative rates, midnight boundaries, empty windows)
- State `from_dict()` method missing explicit HA-timezone date (no `date.today()` fallback)
- State restore loading without validating storage version

### P1 — urgent, fix this cycle

- New external HTTP call without timeout
- HA deprecation warning introduced by patch
- New public function without test
- Config flow change without corresponding `test_config_flow.py` update
- User-facing string added outside `strings.json`
- Dashboard hardcoding `ws://` or `wss://` (must use `location.protocol`)
- Dashboard hardcoding entity prefix other than `pricehawk_`
- Workflow file interpolating `${{ }}` directly in `run:` blocks (use `env:` intermediates)
- Workflow file using `permissions: write-all`
- Entity ID not prefixed with `pricehawk_`

### P2 — fix eventually, do not block merge

- Missing docstring on private helper
- Test missing assertion message
- Magic number that would be clearer as a named constant

### P3 — low priority (surface, never block)

- Docstring style nits
- Typo in comment / docstring
- Naming or minor readability

Not flagged (owned by `ruff`): line length, import ordering, quotes, whitespace.

## High-risk paths

Almost none in this repo. No auth, payments, PII, or migrations beyond config entry version bumps. Cost calculation accuracy matters but is enforced via P0 review rules above (tariff edge-case tests), not via human gating — bugs cause "wrong dollar number in dashboard," not data loss.

Auto-merge is acceptable once CI is green.

CODEOWNERS gates only:
- `.github/` — workflow changes can lock the repo out of CI
- `manifest.json` — version bumps affect all HACS users on update

## graphify

This repo has a graphify knowledge graph at `graphify-out/`.

- Before answering architecture questions, read `graphify-out/GRAPH_REPORT.md`
- If `graphify-out/wiki/index.md` exists, navigate it instead of reading raw files
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost)
