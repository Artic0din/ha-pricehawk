# Phase 0 Ground-Truth Spec — v1.5.0 CDR Evaluator Gate

**Authority:** Design doc §C/§H/§I.6/§I.7 + CEO plan + checkpoint
`20260514-213014-cdr-tariff-refactor-phase-0-ready.md`.
**Hard gate:** all 6 cases within ±5% of hand-calc. 1% aspirational. Plan C2 fail = GloBird migration dead, fall back to Approach A or re-scope.

---

## 1. Oracle: hand-calc from plan PDF

- **Canonical:** hand-calculated cost per period from plan PDF rates × consumption fixture.
- **Sanity check:** AGL bill estimator at `agl.com.au/usage/savings` (annual estimator, not 7-day, treat as smoke test only).
- Why hand-calc wins: unambiguous, traceable to source-of-truth, no third-party drift.
- Spreadsheet lives at `scripts/phase_0_handcalc.xlsx` (created Day 0.5 end-of-day).

## 2. GST convention (lock)

- CDR `unitPrice` field = **ex-GST**.
- HA sensor outputs = **inc-GST** (per §I.7) to match user's actual bill.
- **Hand-calc spreadsheet column must apply `× 1.10` at end** before comparing to evaluator output.
- Single conversion point in evaluator: `total_aud_inc_gst = total_aud_ex_gst * Decimal("1.10")`.
- `tests/test_cdr_evaluator.py::test_gst_inclusion` will guard regression in Phase 1.

## 3. Time zone convention (lock)

- CDR TOU thresholds use **AEST internally** (per §A.6).
- HA timezone = `Australia/Melbourne` for own use; sensor display = local.
- For non-DST plans (A/B/C1/C2): hand-calc spreadsheet rows in AEST. Consumption fixture timestamps in AEST.
- DST plans (D/E): see §6 — naive local time for the date in question, then explicit timezone-aware calc.

## 4. Plan selection (6 fixtures)

| ID | Retailer | Type | pricingModel | What it exercises |
|----|----------|------|--------------|-------------------|
| **A** | AGL | Flat residential | `SINGLE_RATE` | Simplest case. Single rate × kWh + daily supply × days. GST. |
| **B** | **Red Energy** | TOU residential + TOU FIT | `TIME_OF_USE` | Multi-window import (`timeOfUse`) **and** TOU FIT (`timeVariations` — opposite key). Red Energy is the only retailer using `timeVaryingTariffs` FIT properly at scale per CDR audit line 42. Cleaner TOU FIT gate than AGL's text-encoded singleTariff approach. |
| **C1** | **Hand-constructed minimal FLEXIBLE fixture** | `FLEXIBLE` structural | `FLEXIBLE` | Structural semantics of `FLEXIBLE` rate block (audit example lines 287-291 as fixture seed). Gate = evaluator walks rate-block structure correctly, NOT "found one in wild". C2 covers parser path; C1 covers structural path. Orthogonal. |
| **C2** | GloBird ZEROHERO Residential (Flexible Rate) United Energy | Load-bearing | `FLEXIBLE` + free-text incentive | ZEROHERO ($1/day credit) + FOUR4FREE (free-power window) parser end-to-end. **Hard fail = GloBird migration dead.** |
| **D** | Red Taronga Flex Ausgrid `RED552831MRE15@EME` (same as Plan B) | DST backward | `TIME_OF_USE` | **2026-04-05 (Sun)** AEDT→AEST. 25-hour day. 02:00-03:00 occurs twice. (Design doc said Apr 6 — corrected, that's Monday after; transition is first Sunday.) |
| **E** | Same as D | DST forward | `TIME_OF_USE` | **2026-10-04 (Sun)** AEST→AEDT. 23-hour day. 02:00-03:00 skipped. (Design doc said Oct 5 — corrected.) |

### Plan ID capture (Day 1)

Endpoints (`x-v: 1` for list, `x-v: 3` for detail):
- AGL list: `https://cdr.energymadeeasy.gov.au/agl/cds-au/v1/energy/plans?type=ALL&fuelType=ELECTRICITY`
- Red Energy list: `https://cdr.energymadeeasy.gov.au/red-energy/cds-au/v1/energy/plans?type=ALL&fuelType=ELECTRICITY`
- GloBird list: `https://cdr.energymadeeasy.gov.au/globird/cds-au/v1/energy/plans?type=ALL&fuelType=ELECTRICITY` (verify base URL via jxeeno registry)
- Detail: `{base}/cds-au/v1/energy/plans/{planId}` with `x-v: 3`.

Hardcode opaque plan IDs into `scripts/cdr_evaluator_proto.py`. Do NOT commit real plan-detail JSON to git until `effectiveFrom <= today <= effectiveTo` check passes and PII (none expected in plan data, but verify) is absent.

### C1 fixture (locked)

Hand-constructed minimal `FLEXIBLE` fixture. Seed from CDR audit lines 287-291:
```json
{"type":"PEAK","displayName":"Flexible","period":"P1D",
 "rates":[{"volume":15,"unitPrice":"0.246"},{"unitPrice":"0.301"}]}
```
Stepped pricing: first 15 kWh/day @ 24.6c, remainder @ 30.1c. Daily supply $1.20/day (typical VIC value). No incentives, no FIT. **Gate = evaluator correctly walks the rate-block structure including stepped pricing volume threshold.** If a real non-GloBird FLEXIBLE plan surfaces during Day 1 list scan, switch to it; if not, fixture stands.

## 5. Consumption fixture

### A / B / C1 / C2 — 7-day window (LOCKED)

- **Window:** 2026-05-07 00:00 AEST → 2026-05-14 00:00 AEST (last 7 full AEST days as of Day 0.5).
- Source: own HA grid-power history over that window.
- Method: HA recorder API export `sensor.grid_power` + `sensor.solar_export` at 5-min granularity → resample to half-hourly.
- Existing PriceHawk code to reuse: `custom_components/pricehawk/csv_analyzer.py` for NEM12 path OR direct HA recorder export.
  - Note: design doc references `nem12_*.py` which does NOT exist. NEM12 ingestion currently lives in `csv_analyzer.py` + `backfill.py`. Treat design doc reference as stale; use actual files.
- Output: `tests/fixtures/phase0/consumption_7d.json` — shape `[{ts_aest, grid_kwh, solar_kwh}]`.
- Use the SAME 7-day window for A/B/C1/C2 so cost deltas are pure plan-shape deltas.

### D / E — 24h synthetic each (LOCKED + generated)

- **Plan D fixture:** `tests/fixtures/phase0/consumption_dst_april_2026-04-05.json` — 50 slots = 25 wall-clock hours (gain 1h, 02:00-03:00 occurs twice).
- **Plan E fixture:** `tests/fixtures/phase0/consumption_dst_october_2026-10-04.json` — 46 slots = 23 wall-clock hours (lose 1h, 02:00-03:00 skipped).
- Both fixtures generated by `scripts/gen_dst_fixtures.py` using `zoneinfo.ZoneInfo("Australia/Sydney")` to handle transitions. UTC timestamps are canonical; local clock is annotation.
- Plan TOU windows from Red Taronga Flex: off-peak **22:00-06:59 every day** straddles midnight and the 02:00 DST transition. Perfect gate.
- Hand-calc spreadsheet rows use UTC timestamps to remove ambiguity. Half-hour slots × local-rate-at-that-clock-time.

## 6. Pass/fail thresholds

- **Per plan:** `|evaluator_total - handcalc_total| / handcalc_total <= 0.05` (5%).
- **Plan D / E:** `|evaluator_total - handcalc_total| <= $0.05` absolute (24h window, low dollar value).
- **Aspirational:** 1% on A/B (no incentives, no DST). Anything >1% on A/B = silent unit conversion bug; investigate before C/D/E.
- **C2 load-bearing:** if C2 fails after one fix attempt → escalate per §7.

## 7. Escalation path

| Failure | First action | Escalation |
|---------|--------------|------------|
| A or B >5% | Check GST × 1.10. Check c/kWh vs $/kWh unit. Check daily supply unit. | One fix attempt → if still >5%, log gate failure, hold Phase 1. |
| C1 >5% | Re-read `FLEXIBLE` spec (`rateBlockUType` semantics, stepped pricing). | Hand-construct simpler FLEXIBLE fixture to isolate structural-vs-data error. |
| C2 >5% | Check ZEROHERO + FOUR4FREE parser regex against current CDR `description` text. | **HARD ESCALATION** — fall back to Approach A (translation layer + bespoke GloBird schema). v1.5.0 scope renegotiated. |
| D or E >$0.05 | Check `zoneinfo` import, naive vs aware datetime, hour-by-hour iteration loop. | One fix → if still off, defer DST handling to v1.5.1 with explicit user warning. |

## 8. Deliverables checklist

- [ ] Day 0.5 end-of-day: this doc + `scripts/phase_0_handcalc.xlsx` skeleton + plan-list pull script.
- [ ] Day 1: 6 plan-detail JSON fixtures captured + consumption fixtures generated.
- [ ] Day 2: `scripts/cdr_evaluator_proto.py` implementing `evaluate(plan, consumption, period) -> CostBreakdown`.
- [ ] Day 3: comparison table evaluator vs hand-calc for all 6 cases. Gate decision logged in `DECISIONS.md`.
- [ ] If gate passes: snapshot `tariff_engine.py` outputs to `tests/fixtures/legacy_engine_outputs/*.json` BEFORE Phase 1 work starts.

## 9. Reference URLs

- AGL plans (list): `https://cdr.energymadeeasy.gov.au/agl/cds-au/v1/energy/plans` (`x-v: 1`)
- AGL plan detail: `https://cdr.energymadeeasy.gov.au/agl/cds-au/v1/energy/plans/{planId}` (`x-v: 3`)
- GloBird plans: `https://cdr.globirdenergy.com.au/cds-au/v1/energy/plans` (verify endpoint via jxeeno registry)
- CDR audit (load-bearing reference): `/Users/ryanfoyle/Downloads/aer-cdr-energy-api-reference.md`
- jxeeno registry (planned dep, v1.5.0): `https://jxeeno.github.io/energy-cdr-prd-endpoints/`

## 10. Locked decisions (Day 0.5 + Day 1 resolution log)

- **D-P0-1 (consumption window):** 2026-05-07 00:00 AEST → 2026-05-14 00:00 AEST. Locked.
- **D-P0-2 (Plan B retailer):** Red Energy `RED552831MRE15@EME` "Red Taronga Flex" (Ausgrid NSW). Single plan serves Plan B + Plans D/E (NSW, clean TOU+TOU-FIT via `timeVaryingTariffs`, off-peak 22:00-06:59 straddles DST). Replaces earlier QLD pick that had flat FIT.
- **D-P0-3 (C1 sourcing):** hand-constructed minimal FLEXIBLE fixture at `tests/fixtures/phase0/plan_c1_flexible_synthetic.json`. Day 1 scan of Red Energy plan list found zero non-GloBird FLEXIBLE plans — confirms audit gap. Fixture stands.
- **D-P0-4 (DST date correction):** transitions are first Sunday of April/October, not the Monday after. Plan D = **2026-04-05** (not 04-06). Plan E = **2026-10-04** (not 10-05). Verified via `zoneinfo.ZoneInfo("Australia/Sydney")` offset walk. Design doc + checkpoint dates were off by one day.
- **D-P0-5 (C2 incentive text gap):** EME proxy (`cdr.energymadeeasy.gov.au/globird`) returns STUB descriptions for GloBird incentives — `description` field = displayName, no rate text. GloBird's own DH (`cdr.globirdenergy.com.au`) is not publicly resolvable. **Workaround:** Day 2 hand-transcribe ZEROHERO + FOUR4FREE + Super Export + Critical Peak text from 4 PDFs already in repo root (`Victorian_Energy_Fact_Sheet_GLO*.pdf`) into `incentives[].description` of the C2 fixture. Mark transcription source in fixture metadata.

---

**Next step:** Day 1 — write `scripts/cdr_pull_plans.py`. Outputs:
- 4 real plan-detail JSON fixtures (A=AGL flat, B=Red TOU+FIT, C2=GloBird ZEROHERO, D/E share one Red NSW TOU plan).
- 1 hand-constructed FLEXIBLE fixture (C1).
- 1 consumption fixture (7d shared across A/B/C1/C2) from HA recorder export.
- 2 DST 24h synthetic consumption fixtures (April + October).
