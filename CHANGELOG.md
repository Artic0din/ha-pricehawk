# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- Consolidated 9 PR-time workflows into one `ci.yml` (lint + types + tests + HACS + hassfest + gitleaks) so branch protection has a single status target.
- Replaced CodeRabbit-aware Claude assistant workflow with a Codex-aware one; `@claude` mention triggers the fix-loop and `--max-turns 30` is the load-bearing cap.
- Rewrote `CLAUDE.md` as a thin override that imports `@AGENTS.md` + `@ENGINEERING_CONSTITUTION.md` instead of duplicating `AGENTS.md` verbatim.
- SHA-pinned `home-assistant/actions/hassfest`, `hacs/action`, and `gitleaks/gitleaks-action` per repo action-pinning policy.

### Added

- `ENGINEERING_CONSTITUTION.md` at repo root — global engineering standards referenced by `CLAUDE.md`.
- `.github/CODEOWNERS` scoped to `.github/` + `manifest.json` only (irreversibility, not architectural sensitivity).
- `.github/pull_request_template.md` with Problem / Approach / Scope / Test plan / Risk / Reviewer focus / Constitution check.
- `.github/dependabot.yml` for weekly pip + monthly github-actions updates.
- `.claude/commands/{self-review,fix-review,ship}.md` slash commands for the Claude-Codex loop.
- `AGENTS.md` Review guidelines section with explicit P0/P1/P2/P3 severity rules so Codex review noise stays bounded.

### Removed

- `coderabbit-nitpicks.yml`, `dual-loop-review.yml`, `pr-checks.yml`, `lint.yml`, `python-ci.yml`, `security-scan.yml`, `validate.yaml`, `docs-check.yml` workflows (folded into `ci.yml` or made redundant by hassfest).
- Duplicated `CLAUDE.md` content that mirrored `AGENTS.md`.

### Deferred

- `wiki-update.yml` moved to `.github/workflows.disabled/` pending a deliberate decision to re-enable as post-merge-only.

### Tests

- **Direct coverage for `_resolve_service_target_entry` (Constitution P17).** The single point of entry routing for every PriceHawk service handler (`analyze_csv`, `backfill_history`, `rank_alternatives`, `reset_today`) was only exercised indirectly via handler invocations, leaving the explicit-vs-default and zero/one/many branches without isolated tests. Adds five tests in `tests/test_runtime_data.py` covering: explicit `entry_id` match, explicit unknown id → `ServiceValidationError`, implicit single loaded entry, implicit multi-entry ambiguity → `ServiceValidationError`, and zero-loaded-entries → `HomeAssistantError` (the error-class distinction matters: `SVE` is user-fixable, `HAE` is ops-level).

### Tests

- **Behavioural coverage for `handle_reset_today` service (Constitution P17).**
  The silver-checklist test only verified that the handler raised `HomeAssistantError` syntactically — none of the suite exercised the actual side effects of the service.
  Added four targeted tests in `tests/test_runtime_data.py`:
  `test_handle_reset_today_raises_home_assistant_error_when_no_entries` pins the exact user-visible message (`"no PriceHawk entries with active runtime data"`) so copy regressions can't slip past;
  `test_handle_reset_today_zeros_each_provider_daily_accumulators` registers two providers and asserts `reset_daily` fires on each;
  `test_handle_reset_today_persists_state_after_reset` asserts `async_persist_state` is awaited so cleared accumulators survive an HA restart;
  `test_handle_reset_today_continues_when_one_provider_reset_raises` covers the batch-resilience path (provider A raises, provider B still resets, persist still runs — pinning the `noqa: BLE001 — never sink the batch` contract).
  (`tests/test_runtime_data.py`)

### Tests

- **Retroactive P17 coverage for `_state_from_dwt_region` + `dwt_region` fallback path.** Engineering Constitution P17 ("Tests Are Part of the Fix") flagged that the `_state_from_dwt_region` helper, the `_AEMO_REGION_TO_STATE` map, and the `dwt_region` fallback inside `get_user_geography` shipped without direct unit tests. Added five tests in `tests/test_coordinator_ranking.py`: `test_state_from_dwt_region_NSW1_returns_NSW`, `test_state_from_dwt_region_VIC1_returns_VIC`, `test_state_from_dwt_region_unknown_returns_None`, `test_get_user_geography_uses_dwt_region_when_state_missing`, and `test_get_user_geography_explicit_state_overrides_dwt_region` (the last pins the current contract that no explicit `state` option is consumed; will fail-loudly if precedence ever changes). (`tests/test_coordinator_ranking.py`)

### Tests

### Refactored

- **Extracted per-tick rebuild gate into module-level `rebuild_per_tick_explanation` (Constitution P14 + P17).** The Linus audit on PR #168 flagged that the prior commit's tests duplicated the gate body via `_apply_per_tick_explanation_gate` and pinned it with a source-text grep (`test_gate_mirror_matches_production_source`). Mirroring production logic into tests violates P14 (Prefer Systemic Fixes Over Local Patches) — every future edit had to land in both places, and the grep guard merely papered over the duplication. The systemic fix: pull the gate into a pure module-level helper `rebuild_per_tick_explanation(providers, amber, providers_block) -> dict | None` and call it from both `_async_update_data` and the tests directly. The helper returns `None` when `providers` is empty (caller leaves `_last_explanation` untouched) and the explanation dict otherwise. Side benefit: `DataUpdateCoordinator` mocking in `conftest._MockModule` no longer matters for this coverage — the test target is a free function, not a coordinator method. Same five behavioural contracts (gate fires only when providers populated, `build_explanation` runs once per call, no memoisation across calls, `avg_amber_spot_c_kwh` recomputed each call, zero-kWh divide-by-zero guard) — rewritten under `tests/test_coordinator.py::TestRebuildPerTickExplanation` to invoke the real symbol. Source-grep guard and mirrored gate body deleted. (`custom_components/pricehawk/coordinator.py`, `tests/test_coordinator.py`)

### Fixed

- **`pricehawk.analyze_csv` now raises `ServiceValidationError` when called with empty rows.** The handler previously logged at ERROR and returned silently, which HA's service-call machinery surfaces to the caller as success — the dashboard would show no failure indicator and the user would expect updated comparison numbers, with only a buried log line as evidence. Raising SVE propagates a typed failure to the UI so the empty-input case is visible and recoverable. Adds a per-handler AST walker to `test_silver_checklist.py` that detects the `_LOGGER.error(...); return` anti-pattern (the prior walker only checked that SOME branch of each handler raised, masking this specific silent-return branch). Engineering Constitution P3 (No Silent Scope Reduction) + P5 (Production Standards Apply Universally), HA Silver action-exceptions rule. (`__init__.py:191-208`, `tests/test_silver_checklist.py`, `tests/test_runtime_data.py`)
- **`analyze_csv_data` now raises `ValueError` on empty rows** (Constitution-01 fix-up — Linus audit on PR #158). The initial fix raised SVE at the handler boundary only; the underlying function still returned a zeroed result on empty input, so any future caller (CLI script, alternative entry point, unit harness, refactor that bypasses the handler) would inherit the silent-success contract. Constitution P12 (Root-Cause First) + P14 (Prefer Systemic Fixes Over Local Patches) — fix at the function boundary where the defect originates. Three-layer contract documented in `DECISIONS.md` D-C01-1: `services.yaml` validates presence, handler raises SVE with user-actionable message, `analyze_csv_data` raises ValueError. Silver checklist AST walker now also catches `_LOGGER.exception/critical/fatal(...); return` patterns (initial walker only caught `.error` / `.warning`). Handler test pins the user-visible SVE string via `pytest.raises(..., match=...)`. (`csv_analyzer.py:444`, `tests/test_csv_analyzer.py`, `tests/test_silver_checklist.py`, `tests/test_runtime_data.py`, `DECISIONS.md`)

### Refactored

- **Coordinator: centralised `entry.options` → `entry.data` fallback in a module-level `_resolve()` helper.** Constitution P14 systemic fix. The options→data lookup pattern had been re-implemented six times across `coordinator.py` (one as a local closure `opt()`, two as inline ``or``-chain duplicates, three as data-only reads with no fallback). The data-only sites were silently broken for users who edited `CONF_API_KEY`, `CONF_CURRENT_PROVIDER`, or `CONF_FLOW_POWER_REGION` via the options flow — those edits never took effect until HA restart. New helpers: `_resolve(entry, key, default)` for `ConfigEntry`-bound call sites, and `_resolve_with_options(new_options, entry, key, default)` for `rebuild_engine` where the options dict arrives separately. Added 7 regression tests covering options-wins, data-fallback, default-when-neither, and the `options[key] is None` shadowing edge case. (`coordinator.py:171-208`, `tests/test_coordinator_helpers.py`)
- **PR #164 Linus audit follow-up: `_resolve_str` / `_resolve_str_with_options` variants resolve the `or ""` vs `None`-shadows-data semantic contradiction.** The three str-typed call sites (`__init__` lines 479-481 for grid_power_entity + api_key, `rebuild_engine` line 2079 for grid_power_entity re-resolve) previously used `_resolve(...) or ""` which contradicted the documented "options[key] is None shadows entry.data" precedence. New helpers coerce `None` → default at the seam between the tri-state resolver and the str-typed attributes, keeping the resolver's documented semantic unchanged for the call sites that genuinely need tri-state (boolean flags, pricing-mode resolvers). Added P13 regression-pin in `tests/test_reconfigure.py::TestOptionsFlowProviderEdit` covering options-flow edit to `CONF_CURRENT_PROVIDER` taking effect without HA restart, plus a deliberate-ignore comment on `_resolve_with_options` clarifying why `entry.options` is bypassed in favour of HA's fresh options-flow dict. (`coordinator.py:171-261`, `tests/test_coordinator_helpers.py`, `tests/test_reconfigure.py`)

### Fixed (observability)

- **`dashboard_config`: version-lookup failures now log a WARNING instead of being swallowed.**
The three panel/resource cache-buster sites (`setup_panel_iframe`, `setup_panel_custom_v2`, `register_lovelace_card_resource`) previously each ran their own `try: integration = await async_get_integration(...) except Exception: version = "unknown"` block.
A user-visible `?v=unknown` cache-buster surfaced with zero diagnostic trail in HA logs, so operators could not tell whether the integration manifest was malformed, the loader was unavailable, or the integration simply wasn't loaded yet.
Extracted a single `_get_manifest_version()` helper that logs `PriceHawk: version lookup failed (<exc>); using '<default>'` at WARNING on any failure (Constitution P20).
Constitution P14 systemic-fix: all three call sites now route through the helper, eliminating the duplicated try/except.
Tests in `tests/test_dashboard_config.py` cover the success path, both failure paths (loader raising + loader module missing), and source-level regression asserts that the silent-swallow pattern cannot be re-introduced.
(`custom_components/pricehawk/dashboard_config.py`, `tests/test_dashboard_config.py`)

### Fixed (observability)

- **Coordinator: every `except (...): return None` sentinel-swallow in `coordinator.py` now emits a `_LOGGER.debug(...)` line tagged with the helper + step name.** Five swallow sites were silently masking malformed CDR plan envelopes, non-numeric `unitPrice` / `dailySupplyCharge` / `perKwh` fields, and bad recorder state values — invisible at the default log level, undiagnosable in production. Switched to typed `as exc` capture so the swallow surfaces under `logger: custom_components.pricehawk: debug` without polluting INFO. Constitution P20 (Explain Architectural Consequences). (`coordinator.py:137`, `coordinator.py:164`, `coordinator.py:1433`, `coordinator.py:1732`, `coordinator.py:1743`)
- **Coordinator: extracted `_extract_cdr_daily_supply_aud_ex_gst` and `_find_perkwh_in_intervals` as module-level helpers.** Mirrors the existing `_extract_peak_rate_c_inc_gst` pattern so swallow paths are unit-testable in isolation (`tests/test_coordinator_helpers.py`) instead of buried inside `_build_data_dict` / `_replay_amber_today_from_api` closures. Constitution P4 (testability) + P14 (systemic over local fix).
- **Coordinator: Amber-replay hot loop aggregates swallow counts by exception type and emits one DEBUG line per category after the loop completes.** Replaces a per-row swallow (which would have been one DEBUG line per state row × N states) with two summary lines: `power_value_cast: swallowed N rows — {...}` and `_find_perkwh_in_intervals: swallowed N intervals — {...}`. Constitution P18 (performance: don't drown the log).
- **Coordinator: extracted `_tally(counter, exc)` helper.** Both swallow sites in `_replay_amber_today_from_api` (the `float(state.state)` cast at line 1810 and the rate-lookup tally inside `_find_perkwh_in_intervals`) used the same inline `counter[name] = counter.get(name, 0) + 1` pattern. One helper, two call sites, identical semantics. Constitution P4 (DRY) + P14 (systemic over local fix).
- **Coordinator: dropped the unreachable `KeyError` from `_extract_cdr_daily_supply_aud_ex_gst`'s swallow tuple.** Every dict access in the helper chain goes through `.get(...)` which returns the default on miss — `KeyError` cannot reach the except clause. Constitution P11 (Define "Done" Explicitly: dead branches are debt).

### Added (tests)

- **Integration coverage at the coordinator seam (`tests/test_coordinator_helpers.py`).** PR #167's whole point is the aggregated post-loop DEBUG lines fired from `PriceHawkCoordinator._replay_amber_today_from_api` — the prior commit only covered the helpers in isolation. New `TestReplayAmberAggregatedSwallowLogs` drives the method end-to-end with a mixed-quality recorder history + price stream and asserts BOTH aggregated DEBUG lines fire exactly once with the correct rolled-up counts (`swallowed 2 rows`, `swallowed 2 intervals`) plus a guard test that a clean stream stays log-silent. Closes the observability seam noted in the Linus audit (Constitution P11 + P17 — tests are part of the fix).
- **Direct unit coverage of `_tally(counter, exc)`.** Four-case contract: increments existing counts, initialises new exception types, no-ops on `counter=None`, handles mixed exception types independently. Lets the integration tests rely on the helper's semantics without re-asserting them.

### Fixed

- **Typing: widened `_*_won_bullets` helper signatures to `Mapping[str, ProviderSnapshot]`.**
  The Constitution P05 fix-up that tightened `build_explanation` to accept
  `ProviderBlock` (= `dict[str, ProviderSnapshot]`) left the five internal
  bullet builders declaring the looser `dict[str, dict[str, Any]]`.
  Python dict types are invariant in their value type, so the call sites
  failed pyright (5 errors at `explanation.py:140,148,150,153,160`).
  `Mapping` is covariant in its value type, so widening the helpers to
  `Mapping[str, ProviderSnapshot]` accepts the tighter `ProviderBlock`
  without sacrificing type safety inside the helper bodies.
  Restores Constitution P13 no-regression-by-design — the file was
  pyright-clean before the previous fix-up.
  (`custom_components/pricehawk/explanation.py`)

### Performance

- **Benchmarked per-tick `build_explanation` cost (Constitution P18).**
  The beta.4 per-tick rebuild in `coordinator._async_update_data` carried a
  comment estimating the cost as "a handful of dict comprehensions" — an
  estimate, not a measurement.
  Replaced the hand-wave with `tests/test_explanation.py::TestPerTickPerformance`,
  which builds a realistic 4-provider block (Amber + GloBird + Flow Power +
  DWT, all extras populated) from typed `ProviderSnapshot` factories and
  pins both percentiles by assertion: median < 200us and p95 < 500us.
  Measured median on Apple Silicon / Python 3.13: ~4us (p95 ~5us) — well
  inside both ceilings.
  The 200us / 500us ceilings give ~50x / ~100x headroom; GitHub Actions
  runners (3-5x slower than Apple Silicon) still clear by ~10-30x, so the
  test absorbs CI jitter without flaking, while a regression that adds
  accidental I/O, deep copies, or O(n^2) loops trips immediately.
  Coupling the producer (`_build_providers_block`) and consumer
  (`build_explanation`) via a shared `ProviderSnapshot` TypedDict means
  schema drift now breaks the type check rather than hiding behind raw
  dicts.
  (`coordinator.py:1167-1188`, `custom_components/pricehawk/explanation.py`,
  `tests/test_explanation.py`)

### Fixed

- **`_apply_options_to_state` no longer orphans `_current_plan_provider` when a bad-options rebuild bails through the strict-mode guard.**
Linus PR #170 audit finding: the projector cleared `self._dwt_provider = None` BEFORE deciding whether to early-return.
A non-strict (options-flow) rebuild with neither `cdr_plan` nor a DWT flag would null `_dwt_provider`, then return, leaving `_current_plan_provider` pointing at the stale DWT instance — a half-rebuilt half-orphaned coordinator state.
Fix moves the reset INSIDE the CDR branch, past the early-return, so a bad rebuild keeps every provider slot intact (P13 no-regression).
Added regression test `test_nonstrict_rebuild_preserves_dwt_provider_on_bad_options` that pre-seeds a DWT provider, fires a bad rebuild, and asserts all three slots survive.
(`custom_components/pricehawk/coordinator.py`, `tests/test_coordinator.py`)

### Tests

- **DWT branch parametrised cases added to `TestApplyOptionsToStateEquivalence`.**
Linus PR #170 audit: the original four `_EQUIVALENCE_CASES` were all CDR, leaving `_build_dwt_provider` equivalence between init and rebuild untested.
Added `dwt_oe_enabled_options_only` (OpenElectricity path, every setting in `options`) and `dwt_aemo_enabled_data_fallback` (AEMO Direct, every setting in `data` so the `opt(key) = options.get(key, data.get(key))` fallback inside `_build_dwt_provider` is what makes the test pass).
Init-time and rebuild-time projector states must remain identical for both — P17 tests are part of the fix.
(`tests/test_coordinator.py`)

### Changed (refactor)

- **`coordinator.PriceHawkCoordinator.__init__` and `rebuild_engine` now flow through a single `_apply_options_to_state(options, data, *, strict)` projector.**
The two paths used to duplicate grid-sensor resolution, the DWT vs CDR current-plan slot, every comparator's pricing-mode resolution, the named-comparator wiring, and the providers-dict reset.
That duplication had already drifted twice in production (retro-review #150 grid-sensor; #153 grid-sensor double-assign).
Constitution P14 — "If the same issue appears in multiple places, fix the underlying abstraction."
Strict mode (init-time) raises `ConfigEntryNotReady` on missing required config; non-strict mode (options-flow rebuild) degrades gracefully — preserves existing behaviour for both call sites.
`_build_dwt_provider` now accepts `(options, data)` mappings instead of a `ConfigEntry` so the same builder serves both paths.
New parametrised test class `TestApplyOptionsToStateEquivalence` in `tests/test_coordinator.py` asserts both call sites produce identical observable state across four scenario fixtures + the strict/non-strict gate semantics + the grid-sensor fallback retro-#150 regression case.
`tests/conftest.py` now stubs `DataUpdateCoordinator` with a real subclassable class (was a `_MockModule` MagicMock) so `PriceHawkCoordinator` resolves as an actual type for unit tests.
(`custom_components/pricehawk/coordinator.py`, `tests/test_coordinator.py`, `tests/test_review_improvements.py`, `tests/conftest.py`)

## [1.6.0-beta.9] - 2026-05-24

Four findings from gemini-code-assist reviews of beta.4-beta.8 PRs. Ryan caught that I'd been merging without reading reviews — these are the legitimate issues that surfaced.

### Fixed (retro-review batch from gemini)

- **Coordinator: removed duplicate `build_explanation` call in the daily rollover branch.** Beta.4 added a per-tick rebuild in `_async_update_data` but didn't remove the rollover branch's rebuild. The midnight rollover tick would build the explanation against the just-finalised "previous day" snapshot, then a few seconds later the per-tick rebuild would clobber it with the post-reset (all-zeros) snapshot. The per-tick rebuild also runs ON the midnight tick, so the rollover branch's duplicate was both redundant and actively harmful. Retro of PR #148. (`coordinator.py:1089-1100`)
- **AEMO file picker: sort case-insensitively to match the case-insensitive regex.** `_FILE_RE` matches with `re.IGNORECASE` but `sorted(matches)[-1]` compared Unicode codepoints — uppercase sorts before lowercase. A hypothetical mixed-case listing would put an older uppercase file last after sort. NEMWeb today serves uppercase only, but the contract should match the regex's tolerance. `key=str.upper` normalises before compare. Retro of PR #147. (`aemo_api.py:138`)
- **`rebuild_engine` now re-resolves `_grid_power_entity` from new options.** The `entry.options` → `entry.data` fallback was applied once at `__init__` and never refreshed. Users updating the grid power sensor via options-flow saw the integration silently keep pointing at the prior entity until an HA restart. `rebuild_engine` is called from the options-update listener; re-reading the entity there makes options changes take effect immediately. Retro of PR #150. (`coordinator.py:2018-2032`)
- **`pricehawk.reset_today` now raises `HomeAssistantError` when no active entries.** Silver action-exceptions rule: handlers should raise rather than silently succeed when they can't perform the requested action. Previously, a user with all entries in failed-load state would see the service "succeed" but observe no change. Now an explicit error tells them to reload the integration. Retro of PR #152. (`__init__.py:264-295`)

## [1.6.0-beta.8] - 2026-05-24

Recovery service for the beta.7 cost-math fix. The beta.7 AEMO RRP-parser fix corrected the rate going forward, but the DWT provider's `_import_cost_today_c` accumulator carried the inflated value built up under the bug (the user's screenshot still showed `current_plan_cost_today=$66.78` on a real spend < $2). Adds a manual reset so users don't have to wait until midnight rollover.

### Added

- **`pricehawk.reset_today` service.** Zeros every registered provider's daily accumulators (`import_kwh_today`, `import_cost_today_c`, `export_kwh_today`, `export_earnings_today_c`) on every PriceHawk entry and persists the cleared state to the JSON Store. Use after any cost-math fix lands mid-day. Takes no parameters; resets all entries. Replaces the prior "wait until midnight" workaround. (`__init__.py:264-294`, `services.yaml`)

## [1.6.0-beta.7] - 2026-05-24

🔴 **Critical cost-math bug fix.** AEMO dispatch parser was reading the wrong CSV row type — surfaced by live UAT 2026-05-24 when the user noticed today_cost claiming ~$66 on real consumption of ~12 kWh.

### Fixed

- **AEMO parser now reads RRP from `D,DISPATCH,PRICE` rows (was: `D,DISPATCH,REGIONSUM`).** REGIONSUM index 9 is **TOTALDEMAND** (MW), not RRP. Real VIC1 dispatch at 15:40 today carried TOTALDEMAND=5738.11 MW; the parser divided that by 10 and reported the result as 573.811 c/kWh. The DWT provider then accumulated cost at that inflated rate (~60x reality) — the user's actual 12 kWh of consumption was billed as $66 when the real VIC1 RRP at the same dispatch interval was $96.16/MWh = 9.62 c/kWh (≈$1.15 cost). Switched the row filter to `PRICE` (the correct AEMO record type where index 9 IS the RRP per the `I,DISPATCH,PRICE,5,...` schema). Also rebuilt the test fixture to emit `PRICE` rows — the prior fixture used `REGIONSUM` and so the parser bug had been silently passing tests. 2 new regression tests: `test_does_not_pick_up_regionsum_totaldemand_as_rrp` (synthetic CSV with BOTH record types, verifies parser picks RRP) and `test_real_nemweb_dispatch_csv_shape` (mirrors the actual NEMWeb file shape: REGIONSUM rows precede PRICE rows). (`aemo_api.py:166-225`, `aemo_api.py:250-285`, `tests/test_aemo_api.py:140-225`)

## [1.6.0-beta.6] - 2026-05-24

Fourth post-deploy UAT hotpatch. Two bugs for DWT-only users (configured DWT but never ran the CDR wizard).

### Fixed (live UAT 2026-05-24)

- **`grid_power_sensor` config now falls back to `entry.data` when `entry.options` is empty.** The config-flow wizard writes `CONF_GRID_POWER_SENSOR` to `entry.data` at initial setup; only the options-flow dialog mirrors it into `entry.options`. The coordinator read `entry.options.get(CONF_GRID_POWER_SENSOR, "")` exclusively, so users who completed initial setup but never opened options saw `_grid_power_entity = ""`. Consequences: backfill silently no-op'd (`backfill.py:317` early-returns on empty entity → `days_loaded=0`), and `_read_grid_power` returned `None` for every tick. The new pattern (`entry.options.get(...) or entry.data.get(..., "")`) matches the existing `_get_opt` helper and the API-key reads two lines below. (`coordinator.py:426-441`)
- **Ranking now filters to the user's state via DWT region fallback.** `get_user_geography` previously returned `state=None` always — the comment said "derived later in the registry filter" but the registry filter is `matches_geography(state=None)` which treats it as wildcard. Result: a VIC DWT user's top-K included AGL/Origin plans flagged for other states they can't purchase. New `_state_from_dwt_region(options)` helper derives state from `dwt_region` (`VIC1` → `VIC`, etc.) and feeds it through `get_user_geography`. CDR-wizard users with explicit `cdr_postcode` are unaffected; the state is additive, not exclusive. 4 new regression tests in `tests/test_coordinator_ranking.py::TestGetUserGeography`. (`cdr/ranking_job.py:30-90`)

## [1.6.0-beta.5] - 2026-05-24

Third post-deploy UAT hotpatch. With beta.4's per-tick explanation rebuild + AEMO data flowing, the alternatives sensor was producing top-K results — but every entry was a marketing-channel variant of one underlying plan (5 Origin "Affinity Variable" channels, or 20 Red Energy demand plans with identical headline rates). The user-facing value was "switch to X" — but X was the same plan listed 5 times.

### Fixed

- **`cheap_rank` now dedupes by economic fingerprint before top-K.** Plans sharing identical `(peak_cents, supply_cents)` collapse to a single representative (first-seen wins, deterministic given `fetch_plans_for_retailer` ordering). Retailers ship the same offer under multiple CDR planIds for marketing channels (e.g. Origin Affinity Variable - Comparable, - One Click Switch, - Electricity Wizard all carry identical headline economics); without dedupe the user saw the same offer 5 times instead of 5 different offers. Subtle rate-shape differences (TOU windows, step thresholds, demand charges) intentionally are NOT in the fingerprint — those are differentiated downstream by `deep_rank` against the user's actual consumption. 3 new regression tests in `tests/test_cdr_ranking.py::TestCheapRank`: `test_identical_economic_fingerprint_collapses_to_one_representative`, `test_first_seen_wins_at_each_fingerprint`, `test_unscorable_plan_does_not_pollute_fingerprint_set`. (`cdr/ranking.py:244-295`)

## [1.6.0-beta.4] - 2026-05-24

Second post-deploy UAT hotpatch. Once beta.3 fixed the NEMWeb regex and the AEMO spot rate started flowing, the Best Provider winner_explanation **still** showed `bullets=[]`. Root cause: `build_explanation` only runs inside the midnight-rollover branch. At midnight today NEMWeb was still broken (beta.2 deployed late morning), so the explanation cached at midnight had no wholesale price and returned empty bullets — and stayed that way for the rest of the day even after the AEMO fetch started succeeding.

### Fixed

- **`build_explanation` now runs every coordinator tick.** Moved the call out of the daily-rollover branch into `_async_update_data` after the per-provider tick, gated on `self._providers` being non-empty. The explanation now reflects the *current* provider snapshot every 30s, not whatever the rollover branch saw at midnight. Cost is a handful of dict comprehensions per tick — pays for itself the moment a user opens the dashboard before the next midnight. (`coordinator.py:1145-1166`)

## [1.6.0-beta.3] - 2026-05-24

Hot-patch following the post-deploy UAT of beta.2. The earlier `_LEGACY` fix (#107) silenced one cause of NEMWeb listing parse failures but not the actual one — the real NEMWeb directory serves filenames with the full server path prefix inside an UPPERCASE `HREF=` attribute, e.g. `HREF="/Reports/CURRENT/DispatchIS_Reports/PUBLIC_DISPATCHIS_..._.zip"`. The prior regex required `PUBLIC_DISPATCHIS_` to sit immediately after the opening quote, so it matched zero files for two days. **AEMO-Direct DWT and Flow Power's AEMO poll were both silently broken in production since 2026-05-22.**

### Fixed (live UAT 2026-05-24, beta.2 deploy verification)

- **NEMWeb regex now tolerates the directory's path prefix.** Inserted a non-greedy `[^"]*?` between the opening quote and the filename capture group, keeping group 1 limited to the bare filename (so the rest of `_pick_latest_dispatch_file` — lexical sort on the `YYYYMMDDHHMM` prefix — works unchanged). Two new regression tests in `tests/test_aemo_api.py::TestPickLatestFile`: `test_matches_real_nemweb_uppercase_href_with_path_prefix` (the exact HTML shape pulled from `https://nemweb.com.au/Reports/Current/DispatchIS_Reports/` on 2026-05-24) and `test_matches_real_nemweb_mixed_case_and_path_prefix`. (`aemo_api.py:42-52`)

## [1.6.0-beta.2] - 2026-05-24

First HACS-beta tag carrying the full Phase 7-11 work landed since `1.6.0-beta.1`. Tagged off the `dev` branch tip for the beta channel — main is not yet promoted; pre-release only. Triggered by live UAT on 2026-05-24 confirming the pre-#107 NEMWeb regex, statistic_id, and bootstrap-block bugs are still firing in production because HACS was pinned to v1.4.0-beta.1.

### Fixed (live UAT 2026-05-24)

- **`winner_explanation.bullets = []` for DWT winners.** `build_explanation` matched winner_id on literal provider IDs (``"amber"``, ``"globird"``, etc.) but Dynamic Wholesale Tariff providers carry IDs like ``"dwt_aemo_direct"`` / ``"dwt_openelectricity"``, so the bullet-builder fell through with an empty list every time a DWT plan won. Added a ``winner_id.startswith("dwt_")`` branch + ``_dwt_won_bullets`` builder that surfaces wholesale spot rate, today's import volume, daily supply, and a stale-price warning (>10 min). 5 new regression tests in ``tests/test_explanation.py::TestDwtWinnerBullets``. (`explanation.py:128-133, 238+`)

### Fixed (Copilot retro-review batch — PRs #93, #95, #99, #100)

Two real bugs surfaced by a Copilot-CLI retro-review of the 22 merged PRs the prior `@claude` batch couldn't reach (OIDC workflow-validation gate against stale `main`):

- **External statistics: hyphens in CDR-derived provider_ids now sanitized.** `external_statistic_id` lowercased `entry_id` (#107 fix) and `provider_id` (#114 fix) but didn't strip non-`[a-z0-9_]` characters. CDR-derived provider_ids carry the plan-id verbatim (e.g. `agl_AGL-CDR-N0001` — hyphens), so the recorder's `[a-z0-9_]+` regex silently rejected dual-write for every CDR user, and the Energy Dashboard never received their cost data. Added a regex sanitizer that coerces any non-conforming character to underscore. New regression test `test_cdr_plan_id_with_hyphens_is_sanitized`. (`statistics.py:36-58`)
- **Blueprints: `!input` no longer used inside Jinja `{{ }}` expressions.** `daily_7pm_summary.yaml` and `wholesale_spike_alert.yaml` had `{{ states(!input today_cost_sensor) }}` — `!input` is a YAML tag that resolves at parse time, NOT a Jinja construct. Jinja parses the `!` as an invalid operator and the template fails to render at runtime. Replaced with a `variables:` block at the action level that binds inputs as Jinja identifiers (HA-recommended pattern). (`blueprints/automation/pricehawk/daily_7pm_summary.yaml`, `wholesale_spike_alert.yaml`)

22 reviews ran (PRs #85, #87-#101, #104, #105, #108-#111). Most surfaced false positives — Copilot flagged HA APIs (`ServiceValidationError`, `async_items`) as non-existent or mis-used, but they're correct per current HA. Findings library + triage notes archived in `.planning/copilot-retro/`.

### Fixed (retro-review batch — PRs #86, #102, #103)

Four findings from a 2026-05-23 batch @claude retro-review of merged PRs:

- **OpenElectricity: `"forbidden"` substring no longer mis-classifies TLS/proxy errors as auth failures.** `_is_auth_error` previously matched the bare word `"forbidden"` anywhere in an exception message, so a corporate proxy rejecting outbound HTTPS with text like "connections forbidden by policy" would raise `ConfigEntryAuthFailed` and prompt the user to re-enter their API key — when the real problem is connectivity. The message check now keys on auth-specific tokens only (`401`, `unauthor`, `invalid api key`); "forbidden" still matches via the class-name check (only when the exception class is literally named `*Forbidden*`). New regression test `test_tls_error_with_forbidden_word_is_not_auth_failure`. Surfaced by retro-review of PR #86. (`providers/openelectricity.py:194-208`)
- **Hypothesis fuzz tests now use `assume()` instead of `return` for out-of-range draws.** Three property tests previously did `if k <= threshold: return` to early-exit, marking the example as PASSED — so Hypothesis didn't replace the discard, and the configured `max_examples=200` actually exercised only ~50 constrained examples. Switched to `from hypothesis import assume; assume(k > threshold)` so the 200-example budget is spent in the constrained region as intended. Surfaced by retro-review of PR #102. (`tests/test_tariff_engine_hypothesis.py:109-117, 142-145, 152-158`)
- **Hypothesis tolerance aligned to `1e-9`.** Invariant 3 used `1e-6` while the related invariant 2 used `1e-9`; single multiplications of bounded floats (≤ 200 × 200) have IEEE-754 round-trip error ≤ a few ULPs (~4e-12), so the tight tolerance is safe everywhere. Same review.
- **CI: dropped the legacy `develop` branch from the dual-loop-review PR-target gate.** `develop` never existed as a real branch (confirmed via `gh api repos/.../branches`); it was a stale alias that silently never matched. Surfaced by retro-review of PR #103. (`.github/workflows/dual-loop-review.yml:9`)

### Fixed (issue #115 — background-task cancel race)

- **Bootstrap background tasks now cancelled AND awaited before platform unload.** The Phase 7 + Codex P1-6 work routed the initial ranking + backfill via `hass.async_create_background_task` and registered `task.cancel` via `entry.async_on_unload` — but those callbacks fire and forget without awaiting. If the integration unloaded (reload, removal, options flow) inside the first ~30s of startup, the cancelled tasks could still be mid-flight when the coordinator tore down, racing `_ranking_lock` reads and recorder writes against a dying `hass`. `PriceHawkData` now tracks both task handles; `async_unload_entry` cancels + `asyncio.gather`-awaits them BEFORE `async_unload_platforms`, closing the race window. (`__init__.py:265+`, `data.py:14-26`)

### Fixed (retro-review of #107)

Two follow-ups from a Claude retro-review of the live-UAT bug-fix PR:

- **Stale inline comment in `_pick_latest_dispatch_file`.** Comment still claimed `_LEGACY` was part of every filename — the very assumption #107 corrected. Updated to describe both shapes plus why lexical sort still works. (`aemo_api.py:119-122`)
- **`provider_id` not lowercased in `external_statistic_id`.** All current provider IDs (`amber`, `globird`, `dwt_aemo_direct`) are lowercase so the gap was latent, but a future provider with mixed/upper case would re-trigger the same silent `Invalid statistic_id` failure #107 fixed for `entry_id`. Belt-and-suspenders `.lower()` on the `provider_id` segment + regression test (`DWT_AEMO_Direct` form). (`statistics.py:46`)

### CI

- Codecov upload: bump `codecov/codecov-action` v4 → v5 (per Context7 sweep), rename `file:` → `files:` for the v5 input contract, add explicit `token: ${{ secrets.CODECOV_TOKEN }}` reference, and set `fail_ci_if_error: false` so a codecov flake never breaks CI. Token itself is configured via GitHub repo secret `CODECOV_TOKEN`, never in the codebase.

### Fixed (live UAT 2026-05-23)

Three real-world bugs caught while running PriceHawk on the live HA box for the first time after the v3.0 stack landed. None were in the codex review — they only surface against real HA + real AEMO.

- **NEMWeb `_LEGACY` regex matches zero files.** AEMO retired the `_LEGACY` suffix from `PUBLIC_DISPATCHIS_*` filenames in May 2026. The old regex required it, so the directory listing returned no matches and every DWT-AEMO entry (plus Flow Power's AEMO poll, which shares `aemo_api.py`) failed every 30 seconds with `AEMO directory listing contained no dispatch files`. Made the suffix optional via `(?:_LEGACY)?` so we accept both old and new shapes during any future re-introduction. (`aemo_api.py:39-42`)
- **PriceHawk blocked HA startup.** The initial ranking job and 30-day history backfill were scheduled with `hass.async_create_task`, which puts them on HA's bootstrap-wait list. Live HA logged `Something is blocking Home Assistant from wrapping up the start up phase` listing every other integration as collateral, and `Setup timed out for bootstrap waiting on Task-831/833`. Switched both to `hass.async_create_background_task` with explicit names so HA's bootstrap doesn't wait on them. (`__init__.py:51-67`)
- **External-statistics backfill rejected with `Invalid statistic_id`.** `external_statistic_id()` used the raw first 8 chars of HA's ULID entry_id, which is uppercase (e.g. `01KS83AK`). HA's recorder validates `statistic_id` as `<domain>:<object_id>` with `object_id` lowercase only, so every backfill silently failed and the Energy Dashboard never received historical PriceHawk cost data. Lowercased the entry-id slice. (`statistics.py:36-38`)

3 regression tests added pinning the new no-`_LEGACY` filename shape, the cross-format directory pick, and the lowercase-statistic_id contract. 1067 → 1070 passing.

### Fixed

- **Stack-wide regressions caught by `codex review`.** Five functional bugs spanning the Phase 7 / Phase 8 PRs:
  - **DWT entry creation failed at first refresh.** The `dashboard_token` entry builder did not copy `CONF_DWT_OE_*` / `CONF_DWT_AEMO_*` / `CONF_DWT_REGION` from the in-progress flow `self._data` into the final entry's `data` + `options`. `_build_dwt_provider()` then raised `ConfigEntryNotReady` (AC-10c) on every new DWT install. (Codex P1, config_flow.py `async_step_dashboard_token`)
  - **`static_prd` exposed without a stored static plan.** The Comparators options form rendered all of `ALL_PRICING_MODES` for every comparator, but no flow writes `CONF_AMBER_STATIC_PLAN` / `CONF_LOCALVOLTS_STATIC_PLAN`. Selecting `static_prd` bricked the next reload with `ConfigEntryNotReady`. The form now gates `static_prd` visibility per comparator on whether a static plan is stored. (Codex P2#2, config_flow.py `async_step_comparators`)
  - **Reauth dispatcher could not route during startup.** The dispatcher read `entry.runtime_data.coordinator._reauth_provider_id`, but `runtime_data` is only assigned after `async_config_entry_first_refresh()` completes. Auth failures during startup or the first refresh (common after HA restart with an expired Amber / LocalVolts / OpenElectricity key) therefore got `provider_id = None` and aborted with `reauth_provider_unknown`. The dispatcher now falls back to `entry.data[CONF_CURRENT_PROVIDER]` when the coordinator tag is absent. (Codex P2#3, config_flow.py `async_step_reauth`)
  - **Reconfigure unreachable for CDR-backed Amber/LV entries.** `_current_plan_provider.id` is `{brand}_{plan_id}` (e.g. `amber_brokerage-xyz`) for CDR users — the install base — never the literal `PROVIDER_AMBER` / `PROVIDER_LOCALVOLTS` slug. Routing on it sent every CDR user to `reconfigure_unsupported`. Dispatcher now reads `entry.data[CONF_CURRENT_PROVIDER]`. (Codex P2#4, config_flow.py `async_step_reconfigure`)
  - **Amber schedule endpoint polled every 30s on static/off entries.** `_maybe_poll_amber()` returns early without updating `_last_amber_poll` when Amber mode is static/off, leaving it at `0.0` forever. `_async_update_data()` then re-triggered `_fetch_today_price_schedule()` every coordinator tick on the `_last_amber_poll == 0.0` first-run sentinel — hammering Amber's API with stale or missing credentials for DWT and static-Amber users. Guard now combines the sentinel check with `self._amber_mode == PRICING_MODE_LIVE_API`. (Codex P2#5, coordinator.py `_async_update_data`)

  13 regression tests added in `tests/test_codex_regression_fixes.py` (source-level + behavioural where the conftest stubs allow). `tests/test_reconfigure.py` updated for the new dispatcher contract.

### Added

- Hypothesis property-based tests of `tariff_engine` pure functions. Five invariants per v2 research § 7.3: (1) `calc_stepped_cost` is monotonic-non-decreasing in kWh; (2) at threshold it equals `threshold * step1_rate` exactly; (3) above threshold it composes as `step1_cost + (k - threshold) * step2_rate`; (4) `get_stepped_import_rate` returns exactly one of `step1_rate` / `step2_rate`; (5) `get_current_tou_period` returns a known period name or `"unknown"`, with rate matching the period. 9 Hypothesis test classes; ≥200 fuzzed examples per invariant. Final plank toward v3.0 GA. (Phase 11 / PR-18)

- HACS validation job in CI. The existing `Validation` workflow now also runs `hacs/action@main` with `category: integration` on every push + PR. Hassfest job stays as-is — both validators run side-by-side. Catches HACS distribution issues (manifest schema drift, brands gaps, version bump misses) before merge. (Phase 11 / PR-17)

- HA test-harness fixture prototypes (`tests/ha_fixtures.py`). Drop-in mocks for `OpenElectricityPriceSource`, `NEMWebPriceSource`, `async_add_external_statistics`, plus a `mock_config_entry_data` factory for DWT-OE entries. NOT auto-applied — the existing 1028 stub-conftest tests stay HA-free per D-P11-1 (dual-mode test strategy). New tests opt in by importing. `pytest-homeassistant-custom-component>=0.13.0` + `hypothesis>=6.100.0` added to `requirements.txt` for the new harness + Hypothesis fuzzing tests. 10 smoke tests cover the fixture shapes. (Phase 11 / PR-16)

- Blueprints library. Five HA automation blueprints under `custom_components/pricehawk/blueprints/automation/pricehawk/`: `cheapest_plan_alert.yaml` (notify when a retailer would have saved > threshold over 7d), `cheapest_30min_window.yaml` (trigger flexible loads at the lowest-price window), `pause_ev_on_spike.yaml` (suspend EV charger above threshold; hysteresis-aware), `daily_7pm_summary.yaml` (daily cost + savings + best-provider notification), `wholesale_spike_alert.yaml` (early warning when spot price crosses threshold). Users import via the HA "Blueprints" UI with the file URL or by dropping into `<config>/blueprints/automation/pricehawk/`. Ninth and final plank toward v3.0 GA. (Phase 10 / PR-15)

- Lovelace custom card `pricehawk-cost-card`. Compact card showing today's chosen-plan cost + optional savings line. Auto-registered as a Lovelace resource on entry setup — appears in the "Add Card" picker, no manual "Resources" step required. Best-effort: storage-mode Lovelace gets the auto-register; YAML-mode users see a log-line hint with the resource URL. Eighth plank toward v3.0 GA. (Phase 10 / PR-14)

- Lit `panel_custom` foundation for the v2 panel. New sidebar entry "PriceHawk v2" at `/pricehawk` registered via HA's `panel_custom` mechanism — auth flows through the host page's WebSocket session, no LLAT in URL (contract per v2 research § Wave 4). The `pricehawk-panel.js` ESM module imports Lit from the unpkg CDN (no build step). Initial content surfaces the Phase 9 PR-11 `sensor.pricehawk_today_cost` + savings + best provider; full UI port from the legacy iframe dashboard deferred to a dedicated Playwright UAT follow-up. The legacy iframe panel at `/pricehawk-dashboard` continues to work during the migration. Module URL carries a `?v={manifest}.{epoch}` cache buster so HACS upgrades invalidate the browser cache cleanly. Seventh plank toward v3.0 GA. (Phase 10 / PR-13)

- Energy-Dashboard-pickable chosen-plan cost sensor (`sensor.pricehawk_today_cost`). `device_class=MONETARY` + `unit_of_measurement="AUD"` + `state_class=TOTAL` + `last_reset` at midnight together qualify it for HA's Energy Dashboard cost picker. The `unique_id` is provider-INDEPENDENT (`{entry_id}_chosen_plan_today_cost`) so the entity id stays stable across plan swaps — the user's dashboard pick survives migrations between CDR plans and DWT entries. Sixth plank toward v3.0 GA. (Phase 9 / PR-11)

- External statistics dual-write. The coordinator now writes daily provider costs to BOTH the existing JSON Store AND HA's external statistics on every midnight rollover. One-shot backfill on first setup converts the existing `daily_cost_history` into stats entries (one per provider, batched). Each stat carries `unit_of_measurement="AUD"` + `has_sum=True` with a monotonic cumulative sum so the Energy Dashboard can pick it up as a cost source (PR-11 / 09-02). Negative-cost days (export-heavy with high FiT) produce a small dip in the cumulative sum — HA tolerates this for cost-style stats per docs. JSON Store remains the source of truth until PR-12 / 09-03 (stats-only flip; gated on ≥4w + ≥10 tester reports per ROADMAP). Fifth plank toward v3.0 GA. (Phase 9 / PR-10)

- HACS Silver compliance tickbox. `manifest.json` declares `quality_scale: "silver"`. New `quality_scale.yaml` documents every Bronze/Silver/Gold/Platinum rule's status (done / exempt / todo) — honest tickbox. Sensor platform declares `PARALLEL_UPDATES = 0` (CoordinatorEntity-backed → unlimited concurrent reads safe). Service handlers (`analyze_csv`, `backfill_history`, `rank_alternatives`) now raise `HomeAssistantError` on missing coordinator and `ServiceValidationError` on malformed input (was: warn + default-fallback). Version bumped to `1.6.0-beta.1`. Closes Phase 8 (Wave 2). (Phase 8 / PR-9)

- Repairs platform (persistent notifications). PriceHawk now raises HA issue-registry entries when the integration is in a degraded state: `grid_sensor_unavailable` after 10 consecutive None reads (5 min @ 30s coordinator interval) of the configured grid power sensor; `ranking_stale` when the nightly CDR plan ranking job hasn't completed in over 36 hours. Each issue auto-clears on recovery. Multi-entry safe: issue ids prefixed with the entry_id so two PriceHawk entries don't collide on the same issue. Fourth plank of HACS Silver compliance. (Phase 8 / PR-8)

- Diagnostics platform. The "Download diagnostics" button on the integration page now returns a JSON snapshot of entry data + options + selected coordinator runtime state. Every API key (Amber, OpenElectricity, LocalVolts) and HA token field is replaced with `**REDACTED**` via `async_redact_data`. The CDR plan envelope + per-comparator static-PRD envelopes are also redacted — not for secrecy but to keep the diagnostics output to a usable size (~15 KB per plan envelope adds up fast). A `_redaction_count` integer in the output gives reviewers immediate confidence the redaction list is hitting the targets. Third plank of HACS Silver compliance. (Phase 8 / PR-7)

- Per-provider reconfigure flow. HA 2024.10+ "Reconfigure" button now opens a per-provider settings page that lets users adjust Amber network/subscription fees, LocalVolts daily supply / buy ceiling / sell floor guard rails, or DWT (OE/AEMO) daily supply charge — without losing accumulated cost history. Narrow scope: credential rotation stays in PR-5 reauth; region swap is deferred (PriceHawk's entry unique_id is region-derived from Phase 7, swapping would invalidate the unique_id contract). Unsupported entry types (CDR-plan entries) abort cleanly with a clear message pointing at the Configure menu. Second plank of HACS Silver compliance. (Phase 8 / PR-6)

- Per-provider reauth flow. When Amber, LocalVolts, or OpenElectricity (DWT-OE) rejects an API key (HTTP 401/403), PriceHawk now raises `ConfigEntryAuthFailed` and HA prompts the user to re-enter credentials via the integration UI. A single `async_step_reauth` dispatcher routes to per-provider sub-steps (`reauth_amber`, `reauth_localvolts`, `reauth_dwt_oe`) based on a `_reauth_provider_id` tag set by the coordinator at the failure site. Accumulated daily cost history is preserved through the reauth — only the rotated field changes. API keys NEVER appear in log messages, UI errors, or exception strings. First plank of HACS Silver compliance. (Phase 8 / PR-5)

- Per-comparator pricing-mode opt-in. Each of Amber/Flow Power/LocalVolts gains a three-state mode (`off` / `live_api` / `static_prd`). `live_api` preserves the existing behaviour (REST/WebSocket polling using the user's API key). `static_prd` derives rates from a stored CDR PRD `tariffPeriod` for that retailer — no API hit, no API key required. The new `custom_components/pricehawk/static_pricing.py` module wraps `cdr/evaluator.py`'s window-evaluation helpers so there's a single source of truth for TOU window matching. Legacy `CONF_<P>_ENABLED` continues to map cleanly to the new mode (back-compat without migration). OptionsFlow's Comparators page now shows three mode selectors instead of three booleans. Flow Power `static_prd` is deferred (falls back to `live_api`) — Flow Power's internal margin math reads `set_wholesale_rate`, not `set_current_rates`; bridging that needs `flow_power.py` changes scheduled for a follow-up PR. Closes Phase 7 / Wave 1. (Phase 7 / PR-4)

- Dynamic Wholesale Tariff retailer choice — TWO new options in the config-flow retailer picker: "Dynamic Wholesale Tariff — OpenElectricity" (API key required) and "Dynamic Wholesale Tariff — AEMO Direct" (no key). Both backed by a single `DynamicWholesaleTariffProvider` implementation that consumes a `WholesalePriceSource` (PR-2's `OpenElectricityPriceSource` or PR-3's `NEMWebPriceSource`). Users can model their cost as if on a wholesale-pass-through plan. Self-priced provider: `update()` stays sync (Provider Protocol contract); a coordinator-driven async refresh coroutine fetches new prices every 4 minutes (5-min dispatch cadence minus 1-min slack) and pushes them in via `set_live_price`. Negative wholesale prices are honoured (exporter pays during curtailment) with sign discipline matching AmberProvider. AEMO Direct is NEM-only; WEM is only selectable on the OpenElectricity flavour. (Phase 7 / PR-2b)

- NEMWeb DISPATCH wholesale-price fallback at `custom_components/pricehawk/providers/nemweb.py` — no-API-key public-AEMO alternative to OpenElectricity. NEM-only (WEM rejected with a clear message pointing to OpenElectricity). Shares the `WholesalePrice` contract surface from PR-2. Settlement-date parsing anchored to `Australia/Brisbane` (no DST) because NEM dispatch publishes AEST year-round per AEMO docs — `Australia/Sydney` would silently 1-hour-error during AEDT. 45s `asyncio.timeout` bound. Not yet wired into the coordinator or config flow (Plan 07-02b). (Phase 7 / PR-3)

- OpenElectricity v4 wholesale-price client module at `custom_components/pricehawk/providers/openelectricity.py`. Standalone — not yet wired into the coordinator or config flow; that's PR-2 part 2 (Plan 07-02b). Pinned `openelectricity>=0.10.1,<0.11` in manifest. Includes CC BY-NC 4.0 attribution on every result, 30s `asyncio.timeout` bound, `ConfigEntryAuthFailed` mapping for 401, distinct 429 rate-limit handling that preserves the last-good cache, and `ConfigEntryNotReady` fallback for missing-SDK installs. API key never appears in `__repr__` or log messages (scrubber). (Phase 7 / PR-2)

### Changed

- Internal: typed runtime data via `PriceHawkData` dataclass + `PriceHawkConfigEntry` alias; `entry.runtime_data` replaces `hass.data[DOMAIN][entry_id]` for coordinator storage. Service handlers (`analyze_csv`, `backfill_history`, `rank_alternatives`) re-resolve the coordinator on every invocation, eliminating a stale-closure bug latent across `OptionsFlowWithReload` cycles. Unload re-ordered: `async_unload_platforms` runs first, coordinator teardown only on success. Multi-entry service deregistration now sourced from `hass.config_entries.async_entries(DOMAIN)`. No user-facing change. (Phase 7 / PR-1)

### Documentation

- Refreshed `README.md` for Phase 3: per-window dashboard tabs, ranked-alternatives table, CDR `Other (no API)` retailer path, partial-window mode, services FAQ.
- Added `docs/` reference set:
  - `docs/architecture.md` — module map, data flow, coordinator pattern.
  - `docs/dashboard.md` — feature reference for the WebSocket dashboard.
  - `docs/configuration.md` — setup wizard, GloBird tariff editor, OptionsFlow.
  - `docs/sensors.md` — every entity PriceHawk exposes.
  - `docs/services.md` — `rank_alternatives`, `backfill_history`, `analyze_csv`.
  - `docs/troubleshooting.md` — Disconnected dashboard, stuck consumption, slug mismatches, UAT recovery.
  - `docs/development.md` — local checks, branch model, UAT deploy recipe, conventions.

### CI / Discipline stack v1.1.0

Adopt three-agent workflow (Claude Code build → Codex local review →
Copilot inline → CI → manual merge) via `Artic0din/dev-templates@v1`.

- Add `.github/workflows/ci.yml` caller invoking `ci-core.yml@v1`
  (Python toolchain: uv, pyright, ruff, pytest, mdformat, zensical).
- Add 4 managed workflows: `pr-title-check.yml`, `version-drift-guard.yml`,
  `docs-check.yml`, `security-scan.yml` (overwrites existing).
- Add `.github/instructions/*.instructions.md` (meta, code-review, docs,
  tests, python) for AI reviewers.
- Add `.github/prompts/pr-review.prompt.md` (optional Claude review).
- Add `.github/copilot-instructions.md` (steers Copilot inline review).
- Add `CONTRIBUTING.md`, `codecov.yml`, `.gitleaks.toml`, `zensical.toml`.
- Remove obsolete CR/Claude integration shims: `.coderabbit.yaml`,
  `.sourcery.yaml`, `claude-assistant.yml`, `coderabbit-nitpicks.yml`.
  (`dual-loop-review.yml` is preserved per PR #103 — gates Claude AI
  review on the `dev` branch; the scaffold's blanket-delete rule for
  this file is stale.)
- `ai-review-override` label provisioned on the repo.

Existing `lint.yml`, `pr-checks.yml`, `python-ci.yml`, `validate.yaml`,
`wiki-update.yml` retained as repo-specific (may overlap with `ci-core`
jobs; consolidation is a follow-up, not blocking).

### Phase 3.5 — Dashboard rewrite (multi-plan ranked view)

Throws away the Amber-vs-current-plan two-comparator dashboard
(2447 LOC) and rebuilds it as a multi-plan ranked-alternatives view
keyed off the Phase 3.2 / 3.3 / 3.4 sensors. Visual seed lifted from
`assets/dashboard-v3-apple.html` — dark default, Outfit + IBM Plex
Mono, ambient radial bg, semantic accent tokens (no per-provider
colours).

#### Added

- **Full rewrite of `custom_components/pricehawk/www/dashboard.html`**
  (~1250 LOC, down from 2447). New card hierarchy per plan section 5.1:
  - NAV bar (brand + connection status pill + clock + theme toggle).
  - HERO row: current-cost card + savings-vs-best-alt card (with
    projected-annual extrapolation).
  - PERIOD TABS: `[Today][Week][Month][3 Month][Year]` — clicking a
    tab swaps the entity binding for every rollup card to the matching
    `_today` / `_week` / `_month` / `_3month` / `_year` sensor in
    one tick. Active tab persists to `localStorage['pricehawk-window']`
    so re-opens land on the user's last view.
  - RANKED ALTERNATIVES table rendered from
    `sensor.pricehawk_ranked_alternatives.attributes.alternatives[]`
    (already sorted by cheap-rank score in `summarize_for_sensor`).
    Click a row → drill-in card slides up below.
  - DRILL-IN CARD: peak rate / daily supply / customer type / plan ID
    / cheap-rank score, plus a "Pin as Named Comparator" button that
    deep-links to `/config/integrations/integration/pricehawk` (HA
    doesn't support per-step deep-linking; locked in plan section 9
    REVISIT 4).
  - DATA HEALTH FOOTER: `sensor.pricehawk_backfill_status` state
    (state-coloured: green=complete, amber=running, red=failed) +
    `days_loaded` + `ranked_alternatives.last_run` as relative +
    absolute time + alternatives count.
- **Empty-state UI for first-run users** (plan section 5.3 surprise #3):
  when `backfill_status.days_loaded < 7`, hero rollup values are
  replaced with an "Accruing… [n/365]" pill instead of showing a
  misleading `$0.00`. Surfaces clearly that we don't have enough
  history yet.
- **CSP `connect-src` extended** to include `ws://*.local:*` +
  `wss://*.local:*` so the dashboard works on Ryan's HA Green at
  `homeassistant.local` (plan section 5.3 surprise #1). Existing
  `localhost` + `*.ui.nabu.casa` entries preserved.
- **`assets/DESIGN.claude.md` — new PriceHawk Dashboard section**
  noting divergence: PriceHawk is a dark data-dashboard inside HA's
  sidebar, not a warm-canvas editorial site. Inherits typographic
  rationale (humanist sans + mono numerics) and the card-as-surface
  model + accent-discipline rule, but uses its own token palette.
  The rest of the Claude marketing-site spec stays intact.

#### Changed

- **WebSocket auth + URL detection preserved verbatim** from the prior
  dashboard:
  - `location.protocol === 'https:' ? 'wss://' : 'ws://'` for the WS
    URL (AEGIS rule: never hardcode `ws://`).
  - Token sourced from URL params first, then `window.parent
    .hassConnection`, then `localStorage.hassTokens`, then
    `window.parent.localStorage.hassTokens` (AEGIS rule: never
    hardcode the token).
- **Per-provider colour tokens deleted** (`--amber-primary`,
  `--globird-primary`). Replaced with `--accent-positive` /
  `--accent-negative` / `--accent-neutral` / `--accent-warn` — matches
  the Phase 3.0 pivot away from provider-specific branding.
- **`dashboard_config.setup_panel_iframe` cache-busting unchanged** —
  the existing `?v=<version>.<epoch>` query param survives the
  rewrite (it's appended to the URL, doesn't touch dashboard.html
  itself). Verified by smoke test; no code change.

#### Removed

- CSV import card, backfill-trigger button, Amber-API winner card,
  GloBird TOU strip, Amber forecast strip, sparkline chart, grid-power
  gauge, two-provider rate chart, ZeroHero status card — all replaced
  by the ranked-alternatives + rollup-sensor model.

#### Notes

- **No new JS framework, no build step.** Vanilla JS only, same
  constraint as the prior dashboard. All CSS + JS inlined; no CDN
  fetches beyond the Google Fonts stylesheet that the prior dashboard
  already used.
- **30s setInterval re-render** for the ranked + footer cards so
  relative timestamps ("ran 27s ago / 3h ago") tick forward without
  waiting on a state_changed event. Cheap (<1ms per tick on HA Green).
- **XSS hardening**: all CDR-sourced strings (plan_id, display_name,
  brand, customer_type) pass through `escapeHtml()` before innerHTML
  insertion. Defensive — current registry payloads don't contain
  HTML-ish characters, but future ones might.
- **Manual UAT only** for this commit (per plan section 6.3 table —
  `3.5 | none | manual on Ryan's HA + JS console`). Local smoke test:
  HTML parses cleanly via `html.parser`; JS extracted + run under
  Node `--check` + mock-DOM render harness exercising all 5 period
  windows + accruing branch + empty-ranked branch + drill render
  without throwing.

### Phase 3.4 — Named comparator drill-in

Lets the user pin ONE CDR plan from the ranked alternatives list as a
"named comparator" that runs tick-by-tick (every 30s) alongside the
current plan, instead of only refreshing at the daily rollover.
Surfaces as 5 new rolling-window cost sensors plus a new OptionsFlow
step.

#### Added

- **OptionsFlow `named_comparator` step** — dropdown of the current
  ranked alternatives (sourced from `coordinator.data["ranked_alternatives"]`
  + the per-day `_ranking_plan_cache`) plus a "(clear pin)" sentinel.
  Aborts with `no_ranked_alternatives` when either the ranked list or
  the plan cache is empty (covers the post-install + post-midnight-
  cache-reset edge cases). Aborts with `plan_not_in_cache` when the
  user's selection no longer maps to a cached body (concurrent eviction).
- **`CONF_NAMED_COMPARATOR_PLAN_ID` + `CONF_NAMED_COMPARATOR_PLAN`** —
  new option keys. We persist the FULL `PlanDetailV2` body (not the
  summarised form from `cdr.ranking.summarize_for_sensor`) because the
  evaluator needs the `tariffPeriod` data the summary deliberately omits.
- **`coordinator.build_named_comparator_provider`** — module-level pure
  helper extracted for unit-testability (same justification as
  `build_backfill_plan_set`). Called from both `__init__` AND
  `rebuild_engine` so a fresh pin lands on the next
  `OptionsFlowWithReload` cycle without an HA restart.
- **Coordinator registers the named provider under the literal
  `"named"` key** in `_providers`. The existing tick loop ticks it
  every 30s, and the daily rollover writes a `"named"` column into
  `daily_cost_history` — no new tick path, no new locks.
- **`NamedComparatorRollupSensor` × 5** — `today | week | month |
  3month | year` rolling-window cost sensors that read the `"named"`
  key from `daily_cost_history`. Registered only when the user has
  pinned a plan, so users who haven't opted in don't see five
  permanently-unavailable entities. Subclass of the Phase 3.3
  `PeriodRollupSensor` base.
- **`strings.json` + `translations/en.json`** — new step + menu entry +
  2 abort reasons + 5 entity name/description blocks.
- 14 new tests: 10 in `tests/test_config_flow_phase_3.py` exercising
  the new pure-logic `plan_named_comparator_step` decision tree
  (full-body persistence guard, plan_not_in_cache branch, dedupe,
  default fallback when prior pin evicted), 4 in
  `tests/test_coordinator_helpers.py` pinning the
  `build_named_comparator_provider` lifecycle.

#### Notes

- **Lock interaction with ranking lock: none.** The named comparator
  joins the existing tick loop unchanged. The OptionsFlow step reads
  `_ranking_plan_cache` without holding the ranking lock — safe
  because the worst-case torn read resolves to the existing abort
  path and re-prompts the user.
- **Persistent through ranking churn**: if the pinned plan drops out
  of the cheap-rank top-K two weeks later (rate changes), the named
  pin keeps showing — it's stored in options, not derived from
  ranking. Backfill includes it via `build_backfill_plan_set` so
  historical reads are continuous.

### Phase 3.2 — Universal HA-history backfill

Replaces the Amber-API-only backfill with a multi-plan replay over the
HA recorder. Reads N days of grid-power state changes, converts them to
half-hour evaluator slots, replays each day through the user's current
CDR plan + top-K ranked alternatives, and writes per-day cost rows into
`daily_cost_history` for the rollup sensors (Phase 3.3) and dashboard
(Phase 3.5) to consume.

#### Added

- **`cdr/history_replay.py`** — pure-logic fan-out (`states_to_half_hour_slots`,
  `replay_day_through_plan`, `fan_out_replay` generator). No HA imports;
  unit-testable in isolation (~25 tests).
- **`backfill.py` (rewrite)** — thin HA-side adapter pulling recorder
  history day-by-day (NOT one big query), delegating to `fan_out_replay`,
  merging into `daily_cost_history` (cap 180 entries).
- **`coordinator.async_run_backfill`** — status-tracked entry point
  (`_backfill_status` machine: `idle | running | complete | failed`)
  reusing the ranking lock for serialisation against the daily ranking
  job.
- **`coordinator.build_backfill_plan_set`** — module-level pure helper
  composing `{plan_key: plan_body}` from current plan + top-K
  alternatives + ranking plan cache. Keys ranked alts as
  `alt_<planId>` so Phase 3.3 rollup sensors can filter on the prefix.
- **Auto-kickoff** — `async_setup_entry` schedules one backfill after
  the first ranking job releases the lock (so the alternatives list is
  populated when the first replay runs).
- **`sensor.pricehawk_backfill_status`** — state machine read-through.
  Attributes: `last_run` (ISO), `days_loaded`, `plans_replayed`, `error`.
- **`tests/conftest.py`** — `homeassistant.components.recorder` +
  `.history` mocks so the backfill module's lazy recorder import
  resolves under the test harness.

#### Changed

- **`pricehawk.backfill_history` service** shrunk to a one-line delegate
  through `coordinator.async_run_backfill(days_back=...)`. Status now
  surfaces on the new sensor instead of being lost to log lines.
- **`services.yaml`** description updated — Amber API removed,
  replay-through-CDR-plan flow documented.

#### Removed

- `backfill.backfill_from_history` (Amber-API-coupled), along with
  `_build_amber_price_index`, `_find_amber_rate`, `_parse_history_states`,
  `_format_date`. Amber's role narrowed to a *truth overlay* written
  once daily by the live coordinator — the multi-plan backfill replays
  the user's CDR plan(s) through the evaluator instead.
- 14 legacy `tests/test_backfill.py` tests (covered the deleted Amber
  helpers); replaced by 14 new tests for the rewritten module.

### Phase 3.3 — Period rollup sensors

15 rolling-window cost rollup sensors covering
(`current_cost` | `best_alternative_cost` | `savings`) ×
(`today` | `week` | `month` | `3month` | `year`). All read from
`daily_cost_history` (populated by Phase 3.2's universal backfill and
by the live coordinator's daily rollover) and recompute on every
coordinator tick — pure-logic windowing keeps the per-tick cost
negligible (at most 15 × 365-row list scans).

#### Added

- **`cdr/rollup.py`** — pure-logic module (no HA imports) exposing
  `WINDOW_DAYS`, `filter_window`, `sum_window`,
  `best_alternative_for_window`, `savings`. Floats throughout (no
  Decimal mixing). Ties between alternatives broken lexicographically
  by plan_id so the choice is deterministic across coordinator ticks
  (no dashboard flicker). 27 stdlib-only tests.
- **`PeriodRollupSensor`** base class + three subclasses
  (`CurrentCostRollupSensor`, `BestAlternativeRollupSensor`,
  `SavingsRollupSensor`) with inline kind-dispatch (no Strategy
  interface). 15 new entities registered:
  - `sensor.pricehawk_current_cost_{today,week,month,3month,year}`
  - `sensor.pricehawk_best_alt_cost_{today,week,month,3month,year}`
  - `sensor.pricehawk_savings_cost_{today,week,month,3month,year}`
- **`strings.json` + `translations/en.json`** — `entity.sensor.*` block
  with friendly names and descriptions for all 15 sensors.
- 3 sensor smoke tests in `tests/test_review_improvements.py`
  (property-body mirror, same pattern as Phase 3.2's
  `BackfillStatusSensor` tests).

#### Notes

- **`last_reset` semantics**: only the `today` window sets `last_reset`
  to midnight. Rolling week/month/3month/year leave it unset — HA's
  TOTAL state-class tolerates this for monotonic-with-occasional-
  corrections series, and an artificial midnight reset on rolling
  windows would falsely re-attribute the prior day's value as today's
  spend.
- **Distinct from existing `sensor.pricehawk_saving_today`**: the
  legacy sensor tracks intraday delta vs Amber in real time; the new
  `sensor.pricehawk_savings_cost_today` is an end-of-day rollup from
  `daily_cost_history`. Both are valid, different math.
- **Sparse data**: rollups for `month`/`3month`/`year` return `None`
  (entity state `unknown`) until enough history accrues — distinct
  from `$0.00`, which would falsely imply zero spend.

## [1.5.0-beta.2] - 2026-05-17

Phase 3.1 — Multi-plan ranking engine. Cheap-rank heuristic across user's current
retailer + the big-4 competitors (AGL, Origin, EnergyAustralia, Red Energy),
ranked by `peak_rate * 0.7 + daily_supply * 0.3` (cents). Stored on the
coordinator and exposed via a new sensor + manual-trigger HA service.

### Added

- **Cheap-rank pipeline** (`cdr/ranking.py`): geography filter (state +
  distributor), eligibility filter (residential plans only), heuristic scoring,
  CDR fetcher with per-retailer plan cache, top-K aggregation across multiple
  retailers.
- **Deep-rank pipeline** (`cdr/ranking.py`): re-scores the cheap-rank top-K via
  the streaming evaluator so the final ordering reflects the full TOU + stepped
  + 8-retailer-incentive math, not just headline rates.
- **Pure-logic orchestrator** (`cdr/ranking_job.py`): geography extraction
  from `cdr_plan`, competitor retailer composition, top-level `run_ranking_job`.
  Type-guarded against malformed payloads (non-dict `cdr_plan`, non-list
  `distributors`, non-string first distributor entry).
- **Coordinator hook** (`coordinator.py`): `schedule_daily_ranking` /
  `cancel_ranking` / `async_run_ranking_job` (asyncio.Lock-serialized,
  date-rollover plan-cache reset). Daily fire at 00:30 local via
  `async_track_time_change`.
- **HA service** `pricehawk.rank_alternatives` — manual trigger with optional
  `top_k` (1-100, default 20). Defensive coercion on malformed payloads.
- **Sensor** `sensor.pricehawk_ranked_alternatives` — state = count,
  attributes = top-K list (`display_name`, `peak_c_per_kwh`,
  `supply_c_per_day`, `score`) + `last_run` ISO timestamp.

### Notes

- Cheap-rank only on the daily schedule; deep-rank exposed via direct call.
- Real HA-history backfill arrives in Phase 3.2.
- 22 new orchestrator tests + 54+ ranking tests. Full suite 724/724.

## [1.5.0-beta.1] - 2026-05-16

CDR-native release. Replaces the manual GloBird-specific tariff wizard with a
universal Consumer Data Right (CDR) flow that works for any AU retailer
published on the AER. Sensor cost math is now driven by structured CDR
PlanDetailV2 data rather than user-entered rates.

### Added

- **Universal CDR wizard.** New 4-step flow: state → distributor → retailer
  (from the AER registry) → CDR plan. Replaces the bespoke GloBird-only
  rate-entry form.
- **117 retailers via EME refdata2** registry (Phase 3.1 prep). Wizard
  sources retailer endpoints from `api.energymadeeasy.gov.au/refdata2` with
  the baked-in EME snapshot as the offline fallback.
- `RetailerEndpoint.cdr_brand` field carries the CDR-PlanDetail `brand`
  discriminator. Disambiguates the 14 brands that share a base URI
  (Energy Locals hosts ARCLINE / RAA / Cooperative / Indigo / Sonnen /
  iO; OVO hosts MYOB + CTM; Radian hosts iO; Future X hosts Sunswitch).
- `fetch_plan_list` / `fetch_plan_detail` accept optional `brand=`
  parameter and append `?brand=<cdrBrand>` so shared-base-URI plans are
  correctly disambiguated.
- Baked-in EME refdata2 snapshot at
  `custom_components/pricehawk/cdr/data/eme_refdata.json`.
- **8 retailer incentive parsers.** GloBird (ZEROHERO + Super Export + 3-for-Free),
  AGL (Solar Savers bonus FIT + Three for Free), Origin (tiered FIT), Alinta
  (stepped FiT), EnergyAustralia (Solar Max + PowerResponse VPP), Engie (free
  windows), OVO (free windows + EV off-peak + interest-on-balance), Red Energy
  (weekend-only free window).
- **Shared incentive helpers.** `tiered_fit.py` (multi-tier FIT for Sumo / Red
  / Origin patterns), `bonus_fit.py` (Super Export + Peak FIT overlap-aware),
  `free_window.py` (free-import-window engine across 315 published plans),
  `ev_offpeak.py` (midnight-6am EV rate override), `ovo_interest.py` (3% on
  credit balances), `vpp_rebate.py` (per-battery monthly credit).
- **Opt-in fields.** OVO interest balance + VPP batteries enrolled fed through
  the parser dispatcher so other-user-on-OVO/ENGIE/EA gets correct credits.
- **Streaming CDR evaluator.** Per-30-min slot pricing with full structural
  tariff support (TOU, stepped, controlled load) + per-retailer incentive
  application. Daily / period accumulators persist across HA restarts with
  storage version validation.
- **CDR HTTP client** (`cdr/cdr_client.py`) — paginated plan list + detail
  fetching with retry/backoff + 5xx + 429 handling.
- **Phase 3.0 evaluator unification.** Single coordinator path for any CDR
  plan; `CdrPlanProvider` replaces the GloBird-specific provider class.

### Changed

- **Manual tariff entry removed.** Phase 3.0f deleted the 4-step manual
  GloBird wizard (plan picker / rates / export / incentives) and the 4
  matching options-flow steps. Users must use a CDR plan. The Skip-CDR
  sentinel that previously routed to manual entry is gone.
- **`cdr_plan` is required** for setup. Coordinator raises
  `ConfigEntryNotReady` when missing — prevents broken half-configured
  entries.
- **Daily wins map** generalised from `{amber, globird}` to
  `{<any-provider-id>}`.
- **Storage version** validated on restore; loads from unknown schema
  versions are skipped.
- **Sensor labels** read provider display name from coordinator instead of
  hardcoded "GloBird Energy".

### Fixed

- **Dashboard token leak in logs** — `dashboard_url` no longer logs the
  raw JWT; appears as `&token=<REDACTED>`.
- **Multi-day under-credit** in `vpp_rebate.apply_rule` and
  `ovo_interest.apply_rule` — daily credits now scale by distinct days
  in the slot window instead of being subtracted once.
- **VPP regex** no longer matches `/month per kWh` plans (those need
  `critical_peak.py`, deferred).
- **Plan list deduplication** — `fetch_plan_list` now dedups by
  `planId` so republish-boundary repeats don't double-count.
- **404 mapping** — list endpoint 404s raise `CdrAPIError` (bad URL),
  not `CdrPlanNotFound` (reserved for stale planId on detail).
- **`saving_month_aud` pollution** when Amber not configured —
  accumulation skipped entirely instead of computing fake savings vs.
  $0.
- **`_last_update` restore** in `CdrStreamingEngine` — only restored
  when stored state belongs to today; previously synthetic deltas on
  the first tick of a new day over-counted energy/cost.
- **Unguarded `float()` on `dailySupplyCharge`** in `CdrPlanProvider` —
  malformed CDR values now default to $0/day supply rather than
  crashing provider setup.
- **`batteries_enrolled` parser crash** — uses `safe_int` defensive
  helper so garbage option values no-op the VPP credit instead of
  aborting the whole parser dispatch.
- **PERIOD-cap over-credit** in `tiered_fit` — cap no longer multiplied
  by # days in slots (proper billing-period proration deferred; under-credit
  preferred over the 30× over-credit it replaces).

### Removed

- Manual GloBird tariff wizard + options-flow steps (4 + 4 step methods).
- `async_step_cdr_override` JSON override path (was never wired into the
  install flow). The override step, its strings, and `CONF_CDR_OVERRIDE_JSON`
  are gone.
- Skip-CDR sentinel and "enter rates manually" copy from the retailer + plan
  pickers (with manual entry deleted, the affordance dead-ended on itself).
- `cdr/data/cdr_endpoints.json` (legacy jxeeno snapshot) — superseded by
  the EME baked-in copy.

### Breaking Changes

- Setup requires a CDR plan. Existing config entries created against
  1.4.x with manual-only tariffs need to re-run the wizard.

## [1.4.0-beta.2] - 2026-05-02

### Fixed

- **Dashboard cache stuck across upgrades** — iframe URL now appends an epoch
  suffix to the version cache-buster, so every HA restart / integration reload
  yields a unique URL. HA serves `/local/` static files with `max-age=2678400`
  (31 days), which previously caused browsers and the HA companion app to pin a
  stale `dashboard.html` for weeks even after a HACS upgrade.
- **Sensor unique_id collision warnings** — removed legacy import/export entries
  from `RATE_SENSORS`. These duplicated the generic per-provider rate sensors
  registered in the providers loop, producing four `Platform pricehawk does not
  generate unique IDs` errors at every startup. Functionally a no-op (the
  generic sensors won the race), but the log spam is gone.

## [1.4.0-beta.1] - 2026-05-02

### Added

- **Provider abstraction** (`custom_components/pricehawk/providers/`) — common Protocol with thin Amber and GloBird adapters, plus new Flow Power and LocalVolts implementations
- **Flow Power provider** — wholesale-pass-through with Happy Hour FiT (5:30–7:30pm: 45c NSW/QLD/SA, 35c VIC, 0c TAS) and PEA (Price Efficiency Adjustment). Logic adapted from `bolagnaise/Flow-Power-HA` (MIT)
- **LocalVolts provider** — P2P matching engine with buy ceiling / sell floor; fresh `aiohttp` client; 5-min API intervals aggregated to 30-min volume-weighted average (no GPL contamination)
- **AEMO NEMWeb client** (`aemo_api.py`) — pulls wholesale RRP from public dispatch reports (no API key, no Amber account required); used as the wholesale source for Flow Power
- **"Why X won" explanation engine** (`explanation.py`) — deterministic per-day winner breakdown with good/bad/neu bullets, ported from VoltCompare's `buildExplanation`
- **Generic per-provider sensors** (`sensor.pricehawk_<id>_import_rate`, `_export_rate`, `_cost_today`) registered automatically for every active provider
- **Winner explanation sensor** (`sensor.pricehawk_winner_explanation`) — section label as state, bullets as attributes
- **Setup flow rework** — first step asks which retailer the user is currently with, then conditionally collects credentials (Amber API key only if primary is Amber, LocalVolts credentials only if primary is LocalVolts)
- **V3 dashboard mockup** at `assets/dashboard-v3-mockup.html`

### Changed

- Setup no longer requires an Amber API key for non-Amber customers — Flow Power and GloBird work standalone
- Coordinator persistence now serialises every active provider; daily winner tracking generalised from `{amber, globird}` to any registered provider id
- Daily cost history records one entry per active provider per day

### Architectural notes

- **Wholesale source for Flow Power is AEMO direct, not Amber's `spotPerKwh`.** Amber's "spot" field bundles network charges, and an Amber API token requires being or having been an Amber customer — neither acceptable for a non-Amber comparator.
- **Provider availability is asymmetric**: GloBird and Flow Power are universally available comparators (no credentials), while Amber and LocalVolts are only enabled when they are the user's primary (since their APIs require a customer account).

## [1.3.0] - 2026-04-17

### Added

- Brand directory for HA 2026.3+ icon display (`custom_components/pricehawk/brand/`)
- TOU 24-hour coverage validation warning in config flow
- Config flow tests (27 tests for window parsing, overlap, tariff building)
- Tariff engine edge case tests (midnight crossing, negative rates, empty windows)
- Accuracy validation test suite (17 tests against real Amber billing data)
- Content Security Policy meta tag on deployed dashboard
- AEGIS-derived guardrails in CLAUDE.md
- Pre-commit Gitleaks hook configuration
- `requirements.txt` for CI pip cache

### Changed

- Extracted shared form builders from ConfigFlow and OptionsFlow (reduced duplication)
- Gap protection now clamps to 6 min instead of discarding (captures partial energy after restarts)
- `from_dict()` requires explicit HA-timezone date parameter (no `date.today()` fallback)
- State persisted immediately after daily rollover (prevents crash data loss)
- Amber API Retry-After delay capped at 30 seconds (was 300)
- Retry-After handles HTTP-date format with ValueError fallback

### Fixed

- CI shell injection in wiki-update.yml and claude-assistant.yml
- CI write-all permissions restricted in validate.yaml and coderabbit-nitpicks.yml
- Removed hardcoded `sensor.sandhurst_*` entity IDs from dashboard
- Fixed unused imports flagged by ruff (F401, F841)
- Fixed pre-existing test_constructor_creates_engines assertion (supply charge)

### Removed

- Stale `energy-dashboard.html` (hardcoded JWT token, wrong entity IDs)

### Security

- Deleted hardcoded HA Long-Lived Access Token from repo-root dashboard
- Added CSP headers to deployed dashboard (default-src 'none', connect-src 'self')
- CI workflows hardened against shell injection and permission escalation

## [1.2.0] - 2026-04-12

### Added

- V2 dashboard with glass card design, IBM Plex Mono, dark/light mode
- Amber price forecast on rate comparison chart
- 14-day savings history with daily winner streaks
- GloBird incentive tracker (ZEROHERO, Super Export)
- Mobile responsive layout (1200/768/480px breakpoints)
- WebSocket real-time updates via HA API

### Fixed

- Dashboard entity IDs corrected for PriceHawk sensors
- Forecast display conversion from dollars to cents
- Rate chart label and X-axis timeline

## [1.1.2] - 2026-03-31

### Changed

- 7-day price history buffer (was 48h)

### Fixed

- Yesterday/weekly chart tab display

## [1.1.0] - 2026-03-31

### Added

- Daily cost history (180-day buffer)
- kW unit auto-detection from sensor attributes

### Fixed

- Stats layout formatting

## [1.0.0] - 2026-03-30

### Added

- Initial release: Amber vs GloBird energy cost comparison
- Real-time rate comparison (Amber wholesale vs GloBird TOU/flat)
- 5 GloBird plans: ZEROHERO, FOUR4FREE, BOOST, GLOSAVE, Custom
- Editable TOU time windows
- Demand charge support
- ZEROHERO credit tracking ($1/day)
- Super Export tracking (15c/kWh cap)
- Directional savings calculation
- 21 sensor entities
- Sidebar dashboard panel
- HACS custom repository installation
