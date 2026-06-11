# ha-pricehawk

HA custom integration (HACS) comparing real energy costs across providers: Amber (wholesale spot), GloBird (TOU tariffs), Localvolts, Flow Power, plus CDR plan comparison — using actual HA consumption data. Standards: repo `ENGINEERING_CONSTITUTION.md`.

> Branch note: this file matches `dev` (v3 stack: uv/ty/`pyproject.toml`, `providers/` + `cdr/`, manifest 1.6.0-beta.9). `main` still runs pip + `requirements.txt` + `ruff.toml` + mypy at manifest 1.3.0 until `dev` merges. Verify which toolchain the checkout has before running commands.

## Stack
- Python 3.12 floor (CI), language target 3.13; HA min 2024.1.0 (`hacs.json`)
- uv deps — single source `pyproject.toml [dependency-groups].dev` (no `requirements.txt` on dev)
- pytest + pytest-homeassistant-custom-component; aioresponses for aiohttp mocking
- ruff (lint + format); ty (Astral) for types — replaced mypy 2026-05-28

## Build, test, lint (dev / uv)
- Install: `uv sync --group dev`
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Types: `uv run ty check`
- Tests: `uv run pytest --cov=custom_components/pricehawk --cov-fail-under=70` (real dev CI gate ~71%; `main` CI has no gate, ~48%)
- CI also runs hassfest + HACS validate + Version Drift Guard; run `gitleaks detect` before push

## Layout (custom_components/pricehawk/)
- `config_flow.py` — Amber API key + GloBird tariff builder
- `coordinator.py` — `DataUpdateCoordinator`; ALL polling routes through it
- `sensor.py` — cost sensors via `SensorEntityDescription` dataclasses
- `tariff_engine.py` — tariff calc logic (a module, not a package); NEVER in entity classes
- `providers/` — multi-provider core: `amber.py`, `flow_power.py`, `localvolts.py`, `nemweb.py`, `openelectricity.py`, `dynamic_wholesale_tariff.py`, `cdr_plan.py`, `base.py`
- `wholesale/` — `protocol.py` + vendored `amber/`, `flow_power/` (third-party — see Vendored code)
- `cdr/` — Consumer Data Right plan parsing + per-retailer `incentive_parsers/`
- `backfill.py`, `csv_analyzer.py`, `dashboard_config.py`, `static_pricing.py`, `storage.py`, `explanation.py`, `diagnostics.py`
- `www/dashboard.html` — canonical dashboard; repo-root `energy-dashboard.html` is DELETED, do not recreate

## Conventions
- All I/O async via `aiohttp` from HA `async_get_clientsession`; never `requests`/`time.sleep`/blocking file ops in async
- User-facing strings live in `strings.json` only. Entity IDs prefixed `pricehawk_`
- Dashboard derives ws/wss from `location.protocol`; never hardcoded
- Runtime deps go in `manifest.json:requirements` (HACS installs into HA venv) — NEVER `pyproject [project].dependencies`. `pyproject version` must mirror `manifest.json` byte-for-byte (Version Drift Guard)
- ty HA stub gaps: `# ty: ignore[<rule>]` + one-line reason, never blanket. ty is pre-1.0; pin `ty==<ver>` if a release goes noisy
- ruff `C901 max-complexity = 25` is a no-regression ceiling, NOT the target; ratchet one function per PR, never inside a lint PR
- API client tests mock with `aioresponses` (not respx); each needs failure paths: HTTP 500, timeout, malformed body

## Vendored code (wholesale/flow_power/)
Verbatim from bolagnaise/Flow-Power-HA EXCEPT documented `FORK(#PR)` patches in `NOTICES.md` (Evoenergy in REGION_NETWORKS, United→united API/module routing, NETWORK_TIMEZONE table — each fixes a real customer-facing bug). ruff-exempt. Change third-party files only via a labelled FORK recorded in `NOTICES.md`; re-apply forks on every SHA bump.

## Domain — GloBird tariffs
Config flow supports: flat vs TOU import, stepped pricing, multiple windows per period, separate import/export schedules, incentives (ZEROHERO, Super Export, Critical Peak, free-power windows), daily supply charge. Untracked GloBird Victorian Energy Fact Sheet PDFs at repo root are the contract. GloBird rates are user-specific — never hardcode anyone's rates as defaults.

## Review severities (Codex)
- P0: blocking I/O or missing `await` in async; secret/PII in code or logs; exception suppressed without fixing the cause + `# noqa: reason`; tariff change without edge-case tests (negative rates, midnight boundaries, empty windows); state restore using `date.today()` fallback or skipping storage-version validation
- P1: HTTP call without timeout; new public fn or config-flow change without tests; user-facing string outside `strings.json`; `${{ }}` interpolated in workflow `run:`; `permissions: write-all`; entity not `pricehawk_`-prefixed; HA deprecation introduced
- P2/P3 never block; ruff owns style

## PRs
- Commit scopes: `config-flow`, `tariffs`, `sensor`, `amber`, `globird`, `dashboard`, `ci`, `tests`, `deps`
- Squash on merge; no force-push during review (breaks line-anchored comments)
- CODEOWNERS gates: `.github/`, `manifest.json` (version bumps hit every HACS user on update)

## HA guardrails
- Verify entity names via `/api/states` (or ha-mcp) before referencing in code/tests
- Never edit `/config/.storage/*.json`; never run background processes over SSH on the live HA instance

## graphify
Architecture questions: read `graphify-out/GRAPH_REPORT.md` and navigate `graphify-out/wiki/` over raw files. After code changes: `graphify update .` (AST-only, no API cost). Untracked working-tree dir; absent on `dev`.