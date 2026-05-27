# ha-pricehawk

Home Assistant custom integration (HACS) comparing real energy costs between [Amber Electric](https://www.amber.com.au) (wholesale spot pricing) and [GloBird Energy](https://www.globirdenergy.com.au) (time-of-use tariffs) using actual HA consumption data.

## Stack

- Python 3.12+
- Home Assistant 2025.x+
- pytest + pytest-homeassistant-custom-component
- ruff (lint + format), mypy (types)
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

- Install: `pip install -e ".[dev]"`
- Lint: `ruff check . && ruff format --check .`
- Types: `mypy custom_components/pricehawk`
- Tests: `pytest --cov=custom_components/pricehawk --cov-fail-under=70`
- HACS validate: runs in CI via hacs/action
- HA validate: runs in CI via home-assistant/actions/hassfest

## Engineering principles

This repo applies the standards defined in `ENGINEERING_CONSTITUTION.md`. The principles inform *why* the rules below exist; the rules below are what Codex enforces mechanically. When principles and pragmatism conflict, production standards apply to **code**; process rigor scales with **blast radius**.

## Code conventions

- All I/O is async. No `requests` library — use `aiohttp` via HA's `async_get_clientsession`.
- Config flow follows HA's modern selector pattern with full validation.
- All user-facing strings live in `strings.json`. Never hardcoded.
- Sensor entities use `SensorEntityDescription` dataclasses.
- Tariff calculations live in `pricehawk/tariffs/`, never in entity classes.
- Type hints on all public functions. `mypy` strict where feasible.
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

Codex applies these severities. Do not list nitpick rules — `ruff` handles style.

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

### P3 — do not flag on GitHub

- Docstring style nits
- Typo in comment
- Line-length issues (`ruff` handles)
- Import ordering (`ruff` handles)

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
