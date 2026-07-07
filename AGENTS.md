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

- Install: `pip install -r requirements.txt && pip install ruff mypy bandit pytest pytest-cov` (no pyproject.toml — this repo is requirements.txt based)
- Lint: `ruff check . && ruff format --check .`
- Types: `mypy custom_components/pricehawk`
- Tests: `pytest --cov=custom_components/pricehawk --cov-fail-under=70`
- HACS validate: runs in CI via hacs/action
- HA validate: runs in CI via home-assistant/actions/hassfest

## Engineering principles

This repo applies the standards defined in `ENGINEERING_CONSTITUTION.md`. The principles inform *why* the rules below exist; the rules below are what Codex enforces mechanically. When principles and pragmatism conflict, production standards apply to **code**; process rigor scales with **blast radius**.

When stuck between approaches, use the constitution's Priority Rules: correctness over speed, systemic fix over local fix, maintainability over convenience.
Before pushing, self-check against constitution principles 11 (Define Done), 12 (Root-Cause First), 13 (No Regression by Design).

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

No auto-merge. A human presses the merge button on every PR (hub hard rule).

CODEOWNERS gates only:
- `.github/` — workflow changes can lock the repo out of CI
- `manifest.json` — version bumps affect all HACS users on update

## Commit and branch conventions

- Commit format: `{type}({scope}): {description}`.
  Valid types: `feat`, `fix`, `test`, `refactor`, `perf`, `docs`, `style`, `chore`. Never `sync`, `wip`, `update`, or anything else.
- Valid scopes: `config-flow`, `tariffs`, `sensor`, `amber`, `globird`, `dashboard`, `ci`, `tests`, `deps`.
- Never commit directly to `main`. Branch naming: `{type}/{description}-{issue-number}` (e.g. `feat/super-export-incentive-42`). One feature per branch.

## PR workflow

1. Open PR ready for review (no drafts — hub rule); CI must be green locally first
2. Run `/self-review` before opening
3. Codex reviews on push
4. Address P0/P1 only via `/fix-review`
5. Reply to each thread: `Fixed in <sha>. <one-line rationale>`
6. Cap fix loop at 3 rounds; if same finding reappears, stop and surface
7. Squash on merge (no force-push during review — breaks line-anchored comments)
8. No auto-merge — human presses the button on every PR

### Review reply formats

- Fix applied: `Fixed in <sha>. <one-line rationale>`
- Disagreement: `Disagree: <reason>. Leaving as-is.` (do not resolve unilaterally)
- P2/P3 acknowledged: `Acknowledged — tracked for later.` (do not fix inline)

## Home Assistant guardrails

- Never run background processes via SSH to a live HA instance
- Never edit `/config/.storage/*.json` directly on a live HA instance
- Verify entity names via `/api/states` before referencing in code or tests

## Secrets

- Never commit `.env`, tokens, API keys, or credentials
- Run `gitleaks detect` before every push
- The `energy-dashboard.html` at repo root is DELETED — do not recreate

## Slash commands

- `/plan` — explore issue, propose design, no code
- `/implement` — execute against PLAN.md in fresh context
- `/self-review` — local lint, typecheck, tests, gitleaks, codex pre-review
- `/fix-review` — fetch latest Codex comments, apply P0/P1, push, reply
- `/ship` — rebase autosquash, push, flip to ready

## graphify

This repo has a graphify knowledge graph at `graphify-out/`.

- Before answering architecture questions, read `graphify-out/GRAPH_REPORT.md`
- If `graphify-out/wiki/index.md` exists, navigate it instead of reading raw files
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost)
