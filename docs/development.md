# Development

Local workflow, conventions, and pre-push checks.

## Setup

```bash
git clone https://github.com/Artic0din/ha-pricehawk.git
cd ha-pricehawk
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install ruff pyright pytest pytest-cov pytest-asyncio
```

## Pre-push checks

Never push with failing local checks.

```bash
ruff check .
pyright . --ignoremissing
pytest --tb=short -q
```

## Test layout

```
tests/
├── conftest.py
├── unit/
│   ├── test_amber_calculator.py
│   ├── test_tariff_engine.py
│   ├── test_coordinator.py
│   ├── test_config_flow.py
│   ├── test_backfill.py
│   ├── test_review_improvements.py
│   └── cdr/
│       ├── test_evaluator.py
│       ├── test_ranking.py
│       ├── test_rollup.py
│       └── incentive_parsers/
│           └── test_*.py
└── fixtures/
    ├── cdr/                  # Sample PlanDetailV2 payloads
    └── recorder/             # Sample HA recorder history
```

## Conventions

- **Strict typing.** No `Any`, no untyped function signatures. `pyright` in strict mode.
- **Async only for I/O.** Tariff math stays synchronous and pure.
- **AEST everywhere.** Use `datetime.now(tz=ZoneInfo("Australia/Melbourne"))` server-side; never bare `datetime.now()`. `from_dict()` constructors accept an explicit HA-timezone `date` — never fall back to `date.today()`.
- **One sentence per line in markdown.** Semantic line breaks (sembr.org).
- **Conventional commits.** `{type}({scope}): {description}` — `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `ci`, `perf`, `build`, `revert`, `style`.

## Branch model

- `main` — stable, protected. All changes via PR.
- `dev` — current development integration branch.
- Feature branches: `feat/<slug>`, `fix/<slug>`, `chore/<slug>`.

## Releasing

1. Bump `manifest.json`'s `version` (`X.Y.Z` for stable, `X.Y.Z-beta.N` for pre-releases).
2. Move `[Unreleased]` notes in `CHANGELOG.md` into a dated heading.
3. Open PR to `main`.
4. After merge, tag `vX.Y.Z` (or `vX.Y.Z-beta.N`).
5. HACS picks up the tag automatically.
   Beta versions appear only after users toggle **Show beta versions** in the HACS redownload dialog.

## UAT workflow (direct deploy)

**Never use HACS for UAT** — it adds a round-trip via GitHub Releases that masks fast iteration.

Deploy directly over SSH:

```bash
ssh root@homeassistant.local 'systemctl stop home-assistant.service' && \
tar -czf - custom_components/pricehawk \
  | ssh root@homeassistant.local \
    'tar -xzf - -C /config/' && \
ssh root@homeassistant.local 'systemctl start home-assistant.service'
```

The `core.config_entries` cache is in-memory; to change config-entry data without going through the UI, use **stop → edit `/config/.storage/core.config_entries` → start**.

## Code review

PRs are reviewed via:

1. **Claude Code GitHub Action** — structured walkthrough + tiered findings, posts as a single PR comment with a machine-readable trailer.
2. **CodeRabbit** — inline review of changed code.
3. Pre-merge: at least one human review.

The `CI passed` rollup is the only required check in branch protection.
Individual checks can fail without breaking protection if a tier-3 issue is acknowledged.

## graphify

The project maintains a knowledge graph at `graphify-out/`.
Before answering architecture questions about an unfamiliar area, read `graphify-out/GRAPH_REPORT.md` for god nodes and community structure.
After modifying code, run `graphify update .` to keep the graph current (AST-only, free).

## Tooling we don't use

PriceHawk does **not** use:

- CodeRabbit `.coderabbit.yaml` config-driven rules (we use defaults with `.coderabbit.yaml` for tone only)
- Sourcery
- Auto-merge on PRs

CI runs ruff + pyright + pytest + coverage + gitleaks; that's the full picture.

## Naming conventions

Source of truth for every name the integration emits. Drift here breaks the Energy Dashboard cost picker, HA recorder validation, and config-flow translations.

- **entity_id**: `sensor.pricehawk_<snake_case>`. No camelCase, no abbreviations (`amber_cost_today`, not `AmberCostT`). The `pricehawk_` prefix is mandatory — `sensor.py` matches AEGIS rule "Dashboard entity IDs MUST use the `pricehawk_` prefix matching `sensor.py`".
- **provider_id**: snake_case slug declared as a literal `PROVIDER_*` constant in `const.py` (`PROVIDER_AMBER = "amber"`, `PROVIDER_DWT_OE = "dwt_openelectricity"`). Provider classes set `self.id = PROVIDER_*` — never compute it from `__class__.__name__`.
- **statistic_id**: `pricehawk:cost_<entry_id[:8].lower()>_<provider_id>`. Must match `[a-z0-9_]+` per HA recorder contract. Lowercase the entry-id slice — HA's ULIDs are uppercase and recorder rejects raw slices (live UAT 2026-05-23).
- **config-flow step IDs**: snake_case verbs (`dwt_credentials`, `reauth_amber`, `reconfigure_dwt_oe`). Mirror to `strings.json` `config.step.<step_id>` entries; the byte-identical translations check in CI catches drift.
- **service IDs**: declared in `services.yaml` + `strings.json` `services.<name>`. snake_case verbs (`backfill_history`, `rank_alternatives`, `reset_today`).
- **CONF_\* constants**: `CONF_<PROVIDER>_<FIELD>` (`CONF_DWT_OE_API_KEY`, `CONF_AMBER_NETWORK_DAILY_CHARGE`). One per config key, no shared keys across providers.

## Reference implementations

When extending the integration, mirror these as the canonical implementations:

| Pattern | Reference file | Notes |
|---------|----------------|-------|
| External-SDK price source | `custom_components/pricehawk/providers/openelectricity.py` | API-key handling, 30s timeout, `ConfigEntryAuthFailed` mapping, attribution string, scrubbed `__repr__` |
| Public-endpoint price source | `custom_components/pricehawk/providers/nemweb.py` | No-API-key path; shared `WholesalePrice` contract from openelectricity.py |
| CDR-derived provider | `custom_components/pricehawk/providers/cdr_plan.py` | `from_dict` version check, `id = f"{brand}_{plan_id}"` |
| Composition / wrapper | `custom_components/pricehawk/providers/dynamic_wholesale_tariff.py` | Wraps a `WholesalePriceSource` (OE or NEMWeb) behind one Provider |
| Energy-Dashboard-pickable sensor | `sensor.py::PriceHawkTodayCostSensor` | `device_class=MONETARY`, `state_class=TOTAL`, `last_reset` at midnight, provider-INDEPENDENT `unique_id` |
| Reauth dispatcher | `config_flow.py::async_step_reauth` | Reads `coordinator._reauth_provider_id` then falls back to `entry.data[CONF_CURRENT_PROVIDER]` for startup-auth-fail case |
| External-statistics push | `statistics.py::async_push_daily_cost_to_statistics` | `statistic_id` contract, monotonic-`sum` discipline |

When adding a new retailer:
1. Add `PROVIDER_<NAME>` to `const.py`.
2. Implement against the Provider Protocol — pick the closest reference from the table above.
3. Wire into `coordinator._build_<name>_provider` (or `_current_plan_provider` if it's a primary).
4. Add config-flow step + `strings.json` entries.
5. Add a test mirror in `tests/` — copy the structure of the closest existing `test_<provider>.py`.

## Versioning

PriceHawk follows SemVer via `manifest.json["version"]`. HACS reads this field; users see it in the integration page.

- **Patch (`1.6.0` → `1.6.1`)**: bug fixes, no user-visible behaviour change.
- **Minor (`1.6.0` → `1.7.0`)**: new sensors, new comparator, new options. Backwards-compatible.
- **Major (`1.x` → `2.0`)**: breaking changes — entity_id rename, removed config keys, anything that requires user action on upgrade.
- **Beta tag** (`1.6.0-beta.1` → `1.6.0-beta.2`): pre-release for tester reports. Beta tags are NEVER promoted by drop-prefix; create a fresh `1.6.0` tag instead so HACS upgrades cleanly.

Version bumps happen in the PR that completes a phase — not per intermediate PR in a stack. The CI version-drift workflow (`version-drift-guard.yml`) enforces this.
