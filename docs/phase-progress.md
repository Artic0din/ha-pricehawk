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

---

## PR 2 — WholesaleProvider protocol + Amber namespace move

**Date:** 2026-05-27
**Branch:** `claude/flow-power-provider-phase-2-J596D`
**Base:** `22f466b` (PR 1 merge tip)
**Scope:** code refactor only — zero behaviour change. Establishes the
`WholesaleProvider` Protocol that PR 4's `FlowPowerProvider` and PR 5's
config-flow dispatch will both target.

### Files added

- `custom_components/pricehawk/wholesale/__init__.py` — re-exports
  `WholesaleProvider`.
- `custom_components/pricehawk/wholesale/protocol.py` — `@runtime_checkable`
  Protocol matching the current `AmberCalculator` surface (rates
  injected via `update()`, accumulator properties out, `to_dict`/`from_dict`
  for persistence). Provider-owns-rate-fetching deferred to PR 4.
- `custom_components/pricehawk/wholesale/amber/__init__.py` — re-exports
  `AmberCalculator` and `AmberProvider`.
- `custom_components/pricehawk/wholesale/amber/provider.py` —
  `AmberProvider` subclasses `AmberCalculator`, adds `name = "amber"`
  class attribute for PR 4's coordinator dispatch. No method overrides.
- `tests/test_wholesale_protocol.py` — 4 tests: `isinstance` check
  against `@runtime_checkable` Protocol, method/property reachability,
  `to_dict`/`from_dict` round-trip, `name` attribute.
- `tests/test_amber_unchanged.py` — 3 tests: 24-hour replay snapshot
  match between `AmberCalculator` and `AmberProvider`, persistence
  restore parity, midnight rollover parity. Documents the zero-behaviour-
  change invariant.

### Files moved

- `custom_components/pricehawk/amber_calculator.py` →
  `custom_components/pricehawk/wholesale/amber/calculator.py`
  (git-tracked rename; content unchanged except for the `helpers`
  import depth: `from .helpers` → `from ...helpers`).

### Files modified

- `custom_components/pricehawk/coordinator.py`:
  - Import path updated (`from .amber_calculator import AmberCalculator` →
    `from .wholesale.amber import AmberProvider`).
  - Class instantiation renamed (`AmberCalculator(...)` → `AmberProvider(...)`).
  - Internal attribute renamed (`self._amber_calc` → `self._amber_provider`)
    so naming matches the new abstraction. 25 references updated; mechanical.
- `tests/test_amber_calculator.py`, `tests/test_coordinator.py`,
  `tests/test_accuracy_validation.py` — import path updated. Class
  references still `AmberCalculator` (these tests target the calculator,
  not the provider wrapper).

### Tests added

- 7 new tests across the two new test files (see above).
- Total suite: 215 → 223 tests, all passing.

### Regression check

- `pytest -q`: 223 passed, zero failures, zero skipped.
- `ruff check .`: clean.
- `mypy`: clean (15 source files; was 11).
- `gitleaks detect`: no leaks across 51 commits.
- Coordinator data dict shape unchanged — same 22+ keys, same units,
  same nullability. Verified by `test_amber_unchanged.py`'s `to_dict`
  parity assertions.

### Performance check

n/a — same code paths, same allocations. The subclass wrapper adds no
overhead.

### Build / lint / type status

- Diff: +220 / −1 (excluding the file rename, which git counts as zero).
- New tests: ~150 lines.
- Production code added: ~70 lines (Protocol + provider wrapper + two `__init__.py`).
- Well under the 400-line cap.

### Deltas from handoff worth flagging

- D8: The Protocol mirrors today's calculator surface (rates injected by
  caller). Handoff §6 implied providers own their rate-fetching. Decision:
  move that responsibility in PR 4 where the coordinator gets rewired,
  not here, to preserve the zero-behaviour-change boundary.
- D9: `AmberProvider` is implemented as a subclass of `AmberCalculator`
  rather than a delegating wrapper. Subclassing avoids 50+ lines of
  trivial pass-through and inherits Protocol conformance for free.
  Future divergence (PR 4: provider owns rate-fetching) will likely
  refactor this — at that point the inheritance becomes composition,
  or `AmberCalculator` collapses into `AmberProvider`.

### Deferred

- Moving GloBird's `tariff_engine.py` into a parallel `tou/` namespace.
  Out of scope for the wholesale-provider abstraction; tracked for a
  separate refactor PR.
- Coordinator-level provider dispatch (selecting Amber vs Flow Power by
  config). Lands in PR 4.

### Next phase

PR 3 — Port Flow Power core modules from the user-supplied vendored
source. Verbatim copies with MIT license preservation under
`wholesale/flow_power/`. Sliced into 3a (pricing), 3b (tariff_utils),
3c (AEMOClient) per the ≤400 line cap, since the upstream is ~1800
lines of core code. No coordinator wiring yet — that's PR 4.

---

## PR 3a — Vendor Flow Power pricing module

**Date:** 2026-05-27
**Branch:** `claude/flow-power-provider-phase-3-J596D`
**Base:** `8ee684d` (PR 2 merge tip)
**Upstream:** `bolagnaise/Flow-Power-HA` @ `3c2a9bb`
**Scope:** vendor import only — pricing.py and the constants it requires.
No wiring, no provider, no behaviour change in the integration.

### Files added

- `custom_components/pricehawk/wholesale/flow_power/pricing.py` — vendored
  verbatim from upstream (295 lines, MIT). PEA + import + export +
  forecast price calculations.
- `custom_components/pricehawk/wholesale/flow_power/const.py` — PEA-related
  slice of upstream const.py (~30 lines). PR 3b appends tariff constants;
  PR 3c appends AEMO/portal URLs. When all three land the file matches
  upstream byte-for-byte.
- `custom_components/pricehawk/wholesale/flow_power/__init__.py` — package
  docstring + provenance pointers.
- `LICENSES/flow-power-ha.LICENSE` — upstream MIT text, preserved.
- `NOTICES.md` (repo root) — third-party provenance table + SHA bump
  procedure.
- `ruff.toml` — excludes the vendored package from lint so upstream nits
  don't bleed into our review surface.
- `tests/test_flow_power_pricing.py` — 8 smoke tests: PDS-anchored
  constants, PEA legacy + V2 formulas, Tesla-zero clamp, Happy Hour
  window, NEM-region export rates, forecast pipeline.

### Tests added

8 new (223 → 231 passing).

### Gates

- pytest: 231 passed, 0 failed.
- ruff: clean.
- mypy: clean (18 source files; was 15).
- gitleaks: no leaks across 53 commits.

### Deltas worth flagging

- D10: One pre-existing upstream lint nit (`F401 datetime.time imported
  but unused` in pricing.py) is invisible to our CI because the vendor
  directory is ruff-excluded. Documented intent: vendored files are
  third-party and not modified.
- D11: const.py is intentionally not vendored verbatim in this slice —
  only the 7 constants pricing.py imports. PR 3b/3c will append
  additional sections. This is the additive-slice strategy that keeps
  each PR under the 400-line cap while ending up byte-identical to
  upstream after PR 3c lands.

### Next phase

PR 3b — vendor `tariff_utils.py` (170 lines) + append network-tariff
constants to `const.py` (~50 lines). Wraps the `aemo_to_tariff` library.
No coordinator wiring.
