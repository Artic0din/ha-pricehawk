# Decisions Log

> Architectural and technical decisions for this project.
> Auto-appended by PAUL unify at session end.

<!-- Add new decisions at the top -->

## 2026-05-20 — Phase 7 Plan 01 (typed runtime data)

### D-P7-1 — Adopt `PriceHawkConfigEntry = ConfigEntry[PriceHawkData]` typed-entry alias
**Decision:** Introduce `custom_components/pricehawk/data.py` exporting `PriceHawkData` (`@dataclass(slots=True)`) and `PriceHawkConfigEntry: TypeAlias = ConfigEntry[PriceHawkData]`. All future `entry: PriceHawkConfigEntry` annotations use this alias. Coordinator storage moves from `hass.data[DOMAIN][entry_id]` to `entry.runtime_data`.
**Rationale:** PR-1 from `PriceHawk v2 — Deep Research Round 2 (Scope-Corrected).md`, Wave 1 Foundation. Required prerequisite for Phase 8 Silver-compliance handlers (reauth, reconfigure, diagnostics) which all need a single typed object to reach into. HA core convention since 2024.
**Alternatives:** Continue using `hass.data[DOMAIN]` — rejected as it blocks Silver compliance, leaks the multi-entry sentinel responsibility, and forces every consumer to know the entry-id-keyed indirection.
**Consequences:** Every Phase 8 PR consumes this alias. Dataclass kept mutable (`slots=True`, NOT `frozen`) so additive fields can land in later PRs without re-creating the object.

### D-P7-2 — Service handlers must re-resolve coordinator on every invocation
**Decision:** The three registered service handlers (`analyze_csv`, `backfill_history`, `rank_alternatives`) read the coordinator via a `_resolve_coordinator()` helper that reads `entry.runtime_data` on every call. No closure capture of `coordinator` in setup scope.
**Rationale:** Latent pre-existing bug surfaced (not introduced) by this PR: `OptionsFlowWithReload` (HA 2026.3+) triggers a setup→unload→setup cycle on options save. The entry object survives (same identity) but `entry.runtime_data.coordinator` is replaced with a fresh `PriceHawkCoordinator`. A handler that closed over `coordinator` from the original `async_setup_entry` scope would silently keep firing methods on the dead coordinator forever. The typed-runtime-data migration makes the failure mode more visible, so fixed in the same PR.
**Alternatives:** Re-register the services on every `async_setup_entry` — rejected because the multi-entry sentinel only deregisters when the LAST entry unloads. Multiple registrations of the same service name in HA throw.
**Consequences:** Sets the pattern for all future service handlers in this integration. `test_service_handlers_resolve_fresh_coordinator` enforces it.

### D-P7-3 — `async_unload_entry` reordered: platform-unload first, coordinator teardown only on success
**Decision:** `async_unload_platforms` runs FIRST in `async_unload_entry`. If it returns False, return False immediately with `entry.runtime_data` left intact so HA can retry the unload. Coordinator timer cancellation + state persistence happen ONLY after a successful platform unload.
**Rationale:** The previous order (cancel timers → persist state → unload platforms) left the entry in a half-unloaded state on platform-unload failure — coordinator was already torn down with no recovery path. Audit Gap #4.
**Alternatives:** Try/finally pattern with restore-on-failure — rejected as the simpler reorder produces equivalent correctness without restore complexity.
**Consequences:** HA can safely retry `async_unload_entry` after a failure. Documented in `<verification>` MANUAL SMOKE step (multi-entry add/remove cycle).

### D-P7-4 — Multi-entry singleton-service sentinel via `hass.config_entries.async_entries(DOMAIN)`
**Decision:** Singleton-service deregistration (the three services unregistered when the last PriceHawk entry leaves) now reads the config-entries registry, not `hass.data`. Filters out the entry being unloaded explicitly via `entry_id` comparison (whether HA includes or excludes it from `async_entries(DOMAIN)` at unload time varies by HA version — explicit filter is version-safe).
**Rationale:** PR removed `hass.data[DOMAIN]` entirely. Audit Gap #1: previous sentinel (`if not hass.data.get(DOMAIN)`) became unreachable garbage after the removal. Production-breaking for any HACS user with two PriceHawk entries (one per house) — either premature deregistration (services break) or services never unregistered (leak across HA restarts).
**Alternatives:** Module-level counter — rejected because it diverges from HA's authoritative source of truth (config-entries registry).
**Consequences:** `test_multi_entry_service_lifecycle` enforces the contract. Future entries (e.g. multi-NMI households) work correctly.



### D-P0-7 — Evaluator bug fixes (post-gate, during Phase 1 parity work)
**Decision:** Two bugs corrected in `scripts/cdr_evaluator_proto.py`. Phase 0 gate result stands — bugs were masked by Plan C2's specifics + your hand-calc presumably caught the right semantics. Re-verify with `phase_0_verify.py --markdown`.

**Bug 1: `_slot_in_window` endTime treated as INCLUSIVE.** CDR AER convention is start-INCLUSIVE, end-EXCLUSIVE. For retailers using `"HH:00"` endings (GloBird), consecutive windows share boundaries — first match wins. My code matched slot 14:00 as OFF_PEAK (11:00-14:00) instead of SHOULDER (14:00-16:00). Plan C2 ZEROHERO went from $60.28 → $65.42 (+$5.14, +8.5%). Other plans use `"HH:59"` endings (Red Energy) so no boundary collision — they were unaffected (still 0.000% diff). Fixed: `sm <= m < em`, with `endTime "00:00" + startTime > 0` treated as end-of-day (1440).

**Bug 2: ZEROHERO `$1/Day` credit applied × 1.10 GST.** PDF dollar amounts are inc-GST; legacy treats them as flat $1. Refactored `CostBreakdown` to track `incentive_aud_inc_gst` separately from rate-based ex-GST quantities. GST applied only to import/export/supply; incentive credit added after conversion. Same fix applied to Super Export credit (15 c/kWh is inc-GST per PDF).

**Phase 1 parity check** (`scripts/phase_1_parity.py`, `PARITY_REPORT.md`):
- TOTAL 7d: legacy $65.12 vs new $65.42 = 0.46% diff — **PASS** 0.5% gate per §H §3
- Per-day passes: 5/7 (May 7 1.63%, May 10 0.62% remaining)
- Remaining day-07 / day-10 gaps: super_export OVERRIDES FIT rate in legacy (15c instead of 3c TOU FIT in 18-20 window); new evaluator currently ADDs both. Net effect tiny because of near-zero exports in this household's fixture. Optional Phase 1 refinement: encode override semantics in parser to bring per-day pass to 7/7.

**Phase 0 GATE numbers refreshed in GATE_RESULTS.md** — C2 corrected to $65.42 (was $60.28). If your hand-calc agreed with $65.42 originally, no action needed; if it agreed with $60.28 you were unknowingly compensating for the bug.

## 2026-05-14 — Phase 0 GATE PASS

### D-P0-6 — Phase 0 evaluator gate PASSED on all 6 plans
**Decision:** v1.5.0 CDR-native engine refactor proceeds. Approach A fallback NOT triggered. Phase 1 entry approved.
**Evidence:**
- Software cross-check (`scripts/phase_0_verify.py`): evaluator vs independent bucket aggregator agree to 0.0000% diff across A/B/C1/C2/D/E.
- Hand-calc (canonical, user-performed): all 6 plans within ±5% / ±$0.05 gate.
- Plan C2 (GloBird ZEROHERO) — load-bearing — passed. CDR `PlanDetailV2` canonical-schema bet validated.
**Implications:**
- pydantic v2 + CDR-native engine refactor green-lit for Phase 1.
- Legacy `custom_components/pricehawk/tariff_engine.py` (496 lines) scheduled for deletion at end of Phase 1, AFTER fixture-based parity snapshot.
- EME proxy gaps (D-P0-5 incentive stubs + FIT stripping) confirmed as v1.5.1 concern; v1.5.0 ships with PDF-augmented fixture for ZEROHERO.
**Phase 1 entry tasks (sequencing per design doc):**
1. Snapshot existing `tariff_engine.py` outputs against current GloBird fixtures → `tests/fixtures/legacy_engine_outputs/*.json`. **BEFORE any refactor work.**
2. Create `custom_components/pricehawk/cdr/` package with pydantic v2 models.
3. Port `scripts/cdr_evaluator_proto.py` logic into `cdr/evaluator.py` typed module.
4. Migrate GloBird parser into `cdr/incentive_parsers/globird.py` registered via hardcoded dict.
5. New evaluator must reproduce legacy snapshots within 0.5% (parity gate per §H §3) before legacy deletion.

## 2026-05-14 — Phase 0 Day 1 decisions

### D-P0-5 — GloBird incentive text gap (EME proxy stubs)
**Decision:** Hand-transcribe ZEROHERO + FOUR4FREE + Super Export + Critical Peak rate text from in-repo PDFs (`Victorian_Energy_Fact_Sheet_GLO*.pdf`) into `incentives[].description` of the Plan C2 fixture. Mark transcription source in fixture metadata. Use real EME-pulled `tariffPeriod` data; only override the incentive descriptions.
**Rationale:** `cdr.energymadeeasy.gov.au/globird` returns stub descriptions for every incentive (description = displayName, no rate text). GloBird's own DH (`cdr.globirdenergy.com.au`) is not publicly resolvable. CDR audit's 763 free-text incentive observations must have come via retailer-direct DH access we don't have today. PDFs in repo are the available source-of-truth.
**Scope:** Day 2 task. Phase 0 unblocked.

### D-P0-4 — DST date correction
**Decision:** Plan D fixture date = **2026-04-05 (Sun)**, Plan E = **2026-10-04 (Sun)**. Not Apr 6 / Oct 5 as design doc + checkpoint stated.
**Rationale:** Australian DST transitions on the FIRST SUNDAY of April (end) and October (start). Apr 6 / Oct 5 are the Mondays after. Verified via `zoneinfo.ZoneInfo("Australia/Sydney")` offset walk: Apr 5 03:00 AEDT → AEST, Oct 4 02:00 AEST → AEDT. Fixtures regenerated.
**Scope:** Phase 0 fixtures + Phase 1 test names will use corrected dates.

### D-P0-2-refined — Plan B = Red Taronga Flex Ausgrid NSW
**Decision:** Plan B + Plans D/E share one fixture: `RED552831MRE15@EME` "Red Taronga Flex" (Ausgrid distributor, NSW postcodes 2xxx).
**Rationale:** Vanilla TOU plan, no demand/seasonal/CL modifiers. TOU-FIT via `timeVaryingTariffs` (covers the FIT-key quirk per design doc §A). Off-peak 22:00-06:59 straddles DST 02:00 — perfect gate for D/E too. NSW state required for DST relevance.
**Scope:** Replaces earlier short-lived QLD pick (Living Energy Saver Energex which had flat singleTariff FIT, wrong state).


