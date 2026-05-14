# Decisions Log

> Architectural and technical decisions for this project.
> Auto-appended by PAUL unify at session end.

<!-- Add new decisions at the top -->

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


