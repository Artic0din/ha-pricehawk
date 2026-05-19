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
