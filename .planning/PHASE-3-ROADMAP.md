# Phase 3 — Multi-Plan Pivot Roadmap

Locked 2026-05-15 after Phase 2.12.1 ship + product-direction reset.

## Why we pivoted

Phase 2 architected PriceHawk as **one current retailer + one user-chosen comparison plan**, gated on "current retailer must have a live consumer API". User correction: that's the wrong shape.

The actual product: **PriceHawk evaluates every CDR plan eligible for the user's geography (state + postcode + distributor) against their real meter data, ranks them, and surfaces the best alternatives.** API providers (Amber, Flow Power, LocalVolts) are optional truth-source overlays for users who happen to have them — they're not a gate.

Phase 2 incentive parsers (the wedge feature — free-text math nobody else parses) are kept verbatim. Phase 2 orchestration (coordinator wiring, sensor schema, wizard) gets rewritten.

## No migration

Existing entries from Phase 2 are NOT migrated. User must remove + re-add. Justification: migration paths are bug surfaces; this is pre-1.0, expected disruption.

## Phase order

Sequence chosen to land foundation first, then layer features and polish.

### Phase 3.0 — Unify under one evaluator (foundation)

Every cost number flows through `evaluator.evaluate()`. API providers become optional truth-source overlays.

**Files touched:**
- `coordinator.py` — rip 4-provider dispatch; introduce `_current_plan_provider` (CdrPlanProvider) + optional `_truth_overlay`
- `config_flow.py` — wizard: state → distributor → retailer → plan → [optional API connect] → done
- `const.py` — drop `CONF_CURRENT_PROVIDER` enum semantics; keep PROVIDER_* only for truth-overlay identification
- `providers/{amber,flow_power,localvolts}.py` — repurpose as truth-source overlays (override computed cost when connected)
- `sensor.py` — drop per-provider sensor classes; introduce CurrentCostSensor, BestAlternativeSensor (placeholder), WinnerExplanation
- `__init__.py` — entry setup flow + clean async_migrate_entry returning False
- New: `cdr/ranking.py` skeleton (3.1 fills it)

**Commits:** 5-8 small, each independently testable. Expected ~500 LOC delta.

**Risk:** breaks ~50 existing tests (per-provider sensor tests, single-comparator coord tests). Replace as we go.

### Phase 3.1 — Multi-plan ranking engine

Daily job: filter CDR registry by user geography → cheap-heuristic top-K → deep-evaluate top-K → persist ranked list.

**Files:**
- `cdr/ranking.py` — eligibility + cheap-rank + deep-rank
- `cdr/registry.py` — extend `eligible_plans_for(state, postcode, distributor)` query
- `coordinator.py` — `async_track_time_change` at 00:30 local → ranking job
- `__init__.py` — `pricehawk.rank_alternatives` service

**Commits:** 4-6. ~400 LOC.

**Heuristic:** rank by `peak_rate * 0.7 + daily_supply * 0.3` (no incentives, no FIT). Top-K=20 default, user-configurable.

### Phase 3.2 — Universal HA-history backfill

At wizard completion, replay HA grid-sensor history through current + top-K plans → populate `daily_cost_history` for full available lookback (HA recorder default: 10 days; longer if user has `purge_keep_days` raised).

**Files:**
- Rewrite `backfill.py` — generic replay-through-evaluator
- New: `cdr/history_replay.py` — multi-plan wrapper
- `__init__.py` — kick off backfill post-setup; surface `sensor.pricehawk_backfill_status`

**Commits:** 3-4. ~300 LOC.

**UX note:** if HA recorder retention is default 10 days, dashboard's "year" rollup will be sparse until 365 days of live data accrues. Surface this in setup.

### Phase 3.3 — Period rollup sensors

Day / week / month / 3-month / 12-month sensors for current + best-alt + savings.

**Files:**
- `sensor.py` — new `PeriodRollupSensor` class
- New: `cdr/rollup.py` — rolling-window aggregate math

**Sensor names:**
- `sensor.pricehawk_current_cost_{today, week, month, 3month, year}`
- `sensor.pricehawk_best_alternative_cost_{today, week, month, 3month, year}`
- `sensor.pricehawk_savings_{today, week, month, 3month, year}`

**Commits:** 3-4. ~250 LOC.

### Phase 3.4 — Optional named comparator drill-in

User pins ONE specific CDR plan as primary comparator; gets tick-by-tick computation (vs daily for auto-ranked alternatives).

**Files:**
- `config_flow.py` OptionsFlow — "named_comparator" step, skippable
- `coordinator.py` — extends current_evaluator pattern with parallel `_named_comparator` evaluator running every tick
- `sensor.py` — `named_comparator_cost_{...}` sensors

**Commits:** 2-3. ~150 LOC.

### Phase 3.5 — Dashboard rewrite

HA Lovelace cards: current cost + ranked top-N alternatives + drill-in card.

**Files:**
- `www/dashboard.html` — rewrite
- `dashboard_config.py` — entity exposure
- `assets/DESIGN.claude.md` — design spec update

**Commits:** 2-3. UI-only.

## Cadence

- 3.0 lands first (foundation; everything else depends on it)
- 3.1 + 3.2 can develop in parallel after 3.0
- 3.3 / 3.4 / 3.5 are independent polish layers, ship in any order

## Totals

| | |
|---|---|
| Phases | 6 |
| Commits | 19-28 |
| LOC delta | ~1,600 net |
| Test count | 600 → ~750-800 |
| Wall-clock | 2-3 weeks focused |

## Held by user (not part of Phase 3)

- "Dynamic wholesale pricing (Amber-style) for CDR plans" — would require CDR plans to publish half-hourly variable rates, which they don't. Defer until AER pushes a CDR amendment, or until we add a wholesale-overlay feature.

## v1.5.1+ (post-Phase-3)

Per TODOS.md:
- TODO-5 demand charges (~10% AU plans currently silently wrong)
- TODO-7 Flow Power Happy Hour FiT parser
- TODO-8 plan-change diff notifications
- TODO-9 plan-override YAML
