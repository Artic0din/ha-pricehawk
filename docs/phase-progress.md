# Flow Power provider — phase progress log

Running log per handoff §11. One entry per PR.

---

## PR 1 — Docs scaffold + Amber surface audit (this PR)

**Date:** 2026-05-27
**Branch:** `claude/flow-power-provider-phase-1-J596D` (rebased onto `a20084c` after `#175` consolidated CI workflows mid-session)
**Scope:** documentation only; no code.

### Files added

- `docs/web-interface-guidelines.md` — verbatim from the user-supplied
  WIG document. Reformatted from the pasted plaintext into structured
  markdown with proper headings, code fences, tables, and inline
  code styling. Content preserved unchanged; only presentation
  improved (e.g. the ASCII clear-space diagram is inside a fenced
  code block). Unblocks PR 6 and the dashboard adoption pattern.
- `docs/AMBER_SURFACE.md` — the parity contract. Enumerates every
  Amber-coupled artefact in the integration: API endpoints, coordinator
  data dict keys, sensor entities, services, config flow steps,
  calculation entry points, dashboard references, and existing test
  coverage. Every subsequent PR is checked against this document.
- `docs/phase-progress.md` — this file.

### Files removed during rebase

- `docs/engineering-constitution.md` — superseded by `ENGINEERING_CONSTITUTION.md`
  at repo root (added by PR `#175`). Dropped to avoid duplication.

### Files this PR no longer adds (now provided by main)

- `AGENTS.md` — `#175` landed the canonical version at root before this
  PR opened. Rebase took main's copy; my staged version was discarded.
- `ENGINEERING_CONSTITUTION.md` — same, at root.

### Tasks closed

- Handoff Phase 0 (governance docs in repo) — **complete**.
  Constitution, AGENTS, and WIG all present (constitution + AGENTS
  via `#175`; WIG via this PR). No prerequisites outstanding for any
  PR in the Phase 1 plan.
- Handoff Phase 1 (Amber surface audit) — complete.

### Tests added

None. Pure documentation PR. Per constitution rule 17, no logic
changed → no tests required.

### Regression check

No code changed. `git diff --stat` shows additions only under `docs/`
and `AGENTS.md`. Existing test suite untouched.

### Performance check

n/a.

### Build / lint / type status

- `ruff check .` — no new violations (docs not in lint scope).
- `mypy custom_components/pricehawk` — unchanged.
- `pytest -q` — unchanged.

### Deltas from handoff worth flagging

Documented in detail inside the plan file and `docs/AMBER_SURFACE.md`.
Summary:

- D1: Amber uses REST polling, not WebSocket streaming.
- D2: Dashboard YAML lives at package root, not in a `dashboard/`
  subdir (PR 6 introduces the subdir).
- D3: No CDR ranker / `EXPECTED_KEYS` / "settlement primitives" exist.
  `daily_wins` is a simple two-element counter.
  ZEROHERO/FOUR4FREE are GloBird plan-type identifiers.
- D4: Existing comparison thesis is Amber + GloBird. FP replaces the
  Amber slot; GloBird remains the comparator. Decided by user.
- D5: Dashboard SPA is iframe-served static HTML, not a storage-mode
  Lovelace dashboard.
- D6: Branch is `claude/flow-power-provider-phase-1-J596D` per the task
  instructions, not `feat/flow-power-provider`.
- D7: Codex review wiring confirmed via AGENTS.md severity table.
  `.coderabbit.yaml` present in repo; auto-merge on CI-green is
  acceptable per constitution closing note.

### Architectural notes for follow-up

- `tariffs/` subdir referenced in AGENTS.md integration layout does
  not exist yet. Current static-plan tariff code (`tariff_engine.py`,
  `amber_calculator.py`) lives at package root. This is a pre-existing
  refactor opportunity, OUT OF SCOPE for the Flow Power feature
  branch. PR 2 will move `amber_calculator.py` into
  `wholesale/amber/calculator.py` (live-wholesale namespace); the
  GloBird side stays put.
- `graphify-out/` referenced in AGENTS.md does not exist. Not
  blocking; flagged for separate work.

### Deferred

- AGENTS.md `tariffs/` directory creation — out of scope; pre-existing
  refactor not driven by Flow Power work.
- `graphify-out/` initialisation — out of scope; flagged for separate
  work.

### Next phase

PR 2 — `WholesaleProvider` protocol + Amber move (`amber_calculator.py`
→ `wholesale/amber/calculator.py`, plus `AmberProvider` wrapper).
Zero behaviour change. Tests: `test_wholesale_protocol.py` (contract)
and `test_amber_unchanged.py` (regression snapshot of coordinator data
dict shape pre/post move).
