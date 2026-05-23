# PriceHawk — Deferred Work

Items deferred from `/plan-ceo-review` on 2026-05-14. Two milestones:
- **v1.5.1** — polish + broaden release, ~4 weeks after v1.5.0 stable
- **v1.6.0+** — strategic features requiring v1.5.x foundation

See `~/.gstack/projects/Artic0din-ha-pricehawk/ceo-plans/2026-05-14-cdr-tariff-refactor.md` and the design doc at `~/.gstack/projects/Artic0din-ha-pricehawk/ryanfoyle-dev-design-20260514-185807.md` for context.

---

## v1.5.1 — Polish & Broaden

### TODO-5: `demandCharges` as primary rate block

**What:** Extend evaluator to handle plans where `rateBlockUType: demandCharges` is the primary billing mechanism (1,883 plans per CDR audit). Includes `chargePeriod` (DAY/MONTH/TARIFF_PERIOD), `measurementPeriod`, `minDemand` floor, time-window restrictions.

**Why:** Sumo Power and Arcline/RACV plans are demand-charge-primary. v1.5.0 silently returns wrong cost numbers for these users. v1.5.1 fixes the gap.

**Pros:** Removes a class of "wrong cost numbers" for ~10% of AU plans. Required before cross-retailer shadow billing (v1.6.0+).
**Cons:** Demand charge math has nothing in common with TOU; ~3-4 days of evaluator work + new test fixtures.

**Effort:** human ~3-4 days / CC ~1 day.
**Priority:** P1 (blocks v1.6.0 cross-retailer).
**Depends on:** v1.5.0 ships, evaluator architecture proven on TOU + FLEXIBLE.

---

### TODO-6: OVO Energy incentive parser

**What:** Add `cdr/incentive_parsers/ovo.py` — text extractor for OVO's "Free 3" 3-hour-per-day free window credit. Pattern similar to GloBird FOUR4FREE.

**Why:** OVO is a sizable AU retailer (436 plans, EV-focused). v1.5.0 ships globird + agl parsers only; OVO users get partial cost math.

**Pros:** Modest LOC, high user value for OVO subscribers (EV households are a growing PriceHawk audience).
**Cons:** Each parser adds drift risk if OVO changes wording.

**Effort:** human ~0.5 day / CC ~30 min.
**Priority:** P1.
**Depends on:** Parser framework from v1.5.0.

---

### TODO-7: Flow Power Happy Hour FiT parser

**What:** Add `cdr/incentive_parsers/flow_power.py` — text extractor for Flow Power's 5:30-7:30pm Happy Hour FiT (45c NSW/QLD/SA, 35c VIC).

**Why:** Flow Power's hybrid model (wholesale rate per interval + Happy Hour FiT credit) is exactly the free-text incentive pattern. v1.5.0's CDR-native engine cannot represent it. Existing `providers/flow_power.py` (269 lines) hand-codes this; v1.5.1 ports it to a parser so Flow Power lives on the CDR-native path. **Outside voice's gap finding from CEO review.**

**Pros:** Removes the last special-cased provider from the CDR-native architecture. Cleans up tech debt.
**Cons:** Flow Power's FiT publishing isn't consistent — may need a hand-tuned regex per state.

**Effort:** human ~1 day / CC ~30 min.
**Priority:** P1 (clean architecture before v1.6.0).
**Depends on:** Parser framework from v1.5.0.

---

### TODO-8: Plan-change diff notifications

**What:** Daily CDR refresh hashes stored `PlanDetailV2`. On change, compute structured diff (which fields, old vs new), fire HA `persistent_notification` with diff summary.

**Why:** Delight feature surfacing information users genuinely care about (their rates changed). Leverages CDR refresh path already in v1.5.0.

**Pros:** Pure delight, ~no risk.
**Cons:** Diff-rendering template work; "how do we present this" decision needed.

**Effort:** human ~0.5 day / CC ~20 min.
**Priority:** P2.
**Depends on:** v1.5.0 CDR refresh + stored `PlanDetailV2` shape.

---

### TODO-9: Community plan-override YAML (field-level override on top of CDR)

**What:** Wizard "Custom" branch (in v1.5.0) becomes a full PlanDetailV2 builder. Field-level override on top of CDR (paste corrected field, keep the rest live) is v1.5.1 work.

**Why:** Escape valve for users whose actual bill terms differ from what CDR publishes (stale rates, missing fields). Manual-wizard path in v1.5.0 lets users build from scratch but doesn't offer "override one field on top of CDR."

**Pros:** Power-user feature; trust play with the audience that cares most about correctness.
**Cons:** "Where does YAML live" UX decision (config dir? config_entry options? both?). Slight state-management addition.

**Effort:** human ~0.5 day / CC ~20 min.
**Priority:** P2.
**Depends on:** v1.5.0 manual wizard.

---

## v1.6.0+ — Strategic Features

### TODO-1: Cross-retailer shadow billing

**What:** Extend nightly shadow-billing job to score plans from EVERY published AU retailer in the user's state.

**Why:** The 10x vision. Headline differentiator nothing else has: live-data cross-retailer comparison.

**Pros:**
- Foundation for "PriceHawk is the AU energy autopilot" narrative
- Unlocks affiliate revenue path (paired with TODO-2)

**Cons:**
- Requires evaluator hardened against full pricingModel matrix (demandCharges from v1.5.1, FLEXIBLE from v1.5.0)
- Requires per-retailer incentive parsers for top 10 retailers (v1.5.0 covers globird + agl; v1.5.1 adds ovo + flow_power; v1.6.0 needs ~6 more)
- ~25-29 plan details/sec budget; cross-retailer scoring 30 retailers × top 5 plans = 150 evaluations, ~5 sec compute. Manageable.

**Effort:** human ~1-2 weeks / CC ~1-2 days.
**Priority:** P1.
**Depends on:** v1.5.1 ships (demandCharges + flow_power parser).

---

### TODO-2: Affiliate-link plumbing + retailer referral programs

**What:** Replace plain "visit retailer" href (v1.5.0) with affiliate URLs. ACCC-compliant disclosure UX.

**Why:** Revenue path aligning with North Star (active projects with real users by Jan 2027).

**Pros:** Real revenue from a product users genuinely value. Self-funding.
**Cons:** ACCC disclosure rules strict. Retailer-program approval cycles 2-6 weeks each. Partial coverage awkward.

**Effort:** human ~5-7 working days (engineering) + 4-8 weeks calendar (retailer-program approval).
**Priority:** P2.
**Depends on:** Cross-retailer shadow billing (TODO-1). Business decision on commercial path.

---

### TODO-3: Controlled-load circuit accounting in evaluator

**What:** Support plans with separate hot-water / pool-pump controlled-load tariffs (up to 3 CL circuits per plan per CDR audit).

**Why:** Users with separate CL circuits get cost math wrong by 5-15%. v1.5.0 surfaces presence; v1.6.0 fixes the math.

**Pros:** Removes documented v1.5.0 limitation hitting a significant subset of users.
**Cons:** Requires user to expose CL-circuit sensor in HA (smart-meter dependent). UX for pairing main+CL sensors needs design.

**Effort:** human ~3-4 days / CC ~1 day.
**Priority:** P2.
**Depends on:** Decision on CL sensor selection UX. Test users with real CL configs.

---

### TODO-4: HA Energy Dashboard tariff-provider hook

**What:** Register PriceHawk as a tariff provider with HA's native energy dashboard.

**Why:** Cuts the "open PriceHawk dashboard separately" friction. Validates PriceHawk as "the AU energy integration."

**Pros:** Discoverability boost. Validates positioning.
**Cons:** Uncertain whether HA's energy-platform API supports custom tariff providers from integrations. Research needed.

**Effort:** human ~unknown (depends on HA API support); 1 day if hook exists, 1-2 months calendar if requires HA core PR.
**Priority:** P3.
**Depends on:** Research outcome. If hook exists: nothing blocking after v1.5.0. If not: HA core contribution.

---

## Codex full-repo review findings (2026-05-23) — open

These items came out of the `codex exec` full-repo review on 2026-05-23 (log at `/tmp/codex-fullrepo-review.log`). They are NOT yet fixed. Listed here so a future agent doesn't silently re-discover them as new bugs. Each links a file:line + the codex rationale.

### TODO-CODEX-P0-1: Legacy iframe puts `ha_token` in URL query

**Where:** `custom_components/pricehawk/dashboard_config.py:115`
**Why:** Tokens leak via browser history, referrers, screenshots, panel config. Redacting the log line doesn't fix exposure.
**Fix:** Remove the iframe token path. Use only `panel_custom`/HA-session auth or a postMessage/session bootstrap that never serialises credentials into URLs.
**Priority:** P0 (security).

### TODO-CODEX-P0-2: Daily rollover doesn't reset DWT provider

**Where:** `custom_components/pricehawk/coordinator.py:1021` + `providers/dynamic_wholesale_tariff.py:73`
**Why:** "Today" sensors and external statistics corrupt across midnight on DWT entries — yesterday's accumulators bleed into today's count.
**Fix:** Call `reset_daily()` on all providers during coordinator rollover, OR add provider-level date reset logic. Add a DWT midnight regression test.
**Priority:** P0 (correctness).

### TODO-CODEX-P1-1: Options-flow named-comparator reads legacy `hass.data`

**Where:** `custom_components/pricehawk/config_flow.py:2734`
**Why:** v3 stores coordinator on `entry.runtime_data`; the legacy `self.hass.data[DOMAIN][entry_id]` lookup returns None and named comparator setup aborts despite valid data.
**Fix:** Read `self.config_entry.runtime_data.coordinator`. Add a real OptionsFlow test with populated runtime data.
**Priority:** P1.

### TODO-CODEX-P1-2: Services captured per-entry instead of resolved at call time

**Where:** `custom_components/pricehawk/__init__.py:101`
**Why:** Closures capture one `entry`; multi-entry installs route every service call to the last-registered entry.
**Fix:** Register singleton services once; require/accept `entry_id` in the service call; resolve the target entry from active config entries at call time.
**Priority:** P1.

### TODO-CODEX-P1-3: External-stats `sum` adds net daily cost (can go negative)

**Where:** `custom_components/pricehawk/statistics.py:65` + `coordinator.py:1053`
**Status:** Research complete 2026-05-23 — decision deferred pending live-UAT eyeball test on Energy Dashboard.

**Codex's reading:** HA's `has_sum=True` statistics contract is monotonic; export-heavy days make the cumulative sum decrease, violating the contract.

**HA-docs read (Context7 query 2026-05-23):**
- The monotonic rule lives on **sensor-derived** stats with `state_class=TOTAL_INCREASING`: "the sum column is updated with the difference between the current and previous state, unless the difference is negative. In the case of a negative difference, nothing is added to the sum."
- For `state_class=TOTAL` (sensor-derived): sum is updated with the diff including negative.
- For **external statistics** via `async_add_external_statistics`, no `state_class` field exists on `StatisticMetaData`. The integration provides absolute `sum` values directly; HA stores them as given.
- Energy Dashboard's cost source computes "today's cost" as `sum[end_of_day] - sum[start_of_day]`. A decreasing sum produces a NEGATIVE today's-cost — which is actually correct for a net-export day where the user earned more than they spent.

So codex may have been applying the sensor-stats monotonic rule incorrectly to the external-stats path. The current single-net-cost stat is plausibly correct.

**Why not just close as won't-fix:**
- The HA docs don't EXPLICITLY bless non-monotonic external-stats sums.
- The Energy Dashboard chart UX with negative bars on export-heavy days may look broken to users.
- Switching to split import-cost + export-credit streams is the safer, more obviously-correct shape; but it's a schema change that breaks existing users' stat history.

**Decision needed:** eyeball the Energy Dashboard on a known export-heavy day (PriceHawk live entry will produce one in solar season) and judge whether the negative-bar UX is acceptable. If acceptable → close this TODO as won't-fix. If not → split streams in a separate PR with explicit migration plan.

**Priority:** P1 → P2 (now blocked on UX call, not engineering).

### TODO-CODEX-P1-4: Cheap-rank only reads `timeOfUseRates`

**Where:** `custom_components/pricehawk/cdr/ranking.py:153`
**Why:** Flat / `singleRate` CDR plans are silently excluded from ranking. Alternatives list biases against simpler plans.
**Fix:** Parse `singleRate.rates[].unitPrice` in `_extract_peak_rate_cents()`. Test against a genuine single-rate CDR plan.
**Priority:** P1.

### TODO-CODEX-P1-5: DWT `from_dict(today)` ignores the `today` arg

**Where:** `custom_components/pricehawk/providers/dynamic_wholesale_tariff.py:222`
**Why:** Validates version but restores daily counters unconditionally — yesterday's persisted DWT state becomes today's state after restart.
**Fix:** Persist a state date alongside the counters; restore daily accumulators only when stored date equals the supplied HA-local date.
**Priority:** P1.

### TODO-CODEX-P1-6: Setup background tasks not retained / cancelled

**Where:** `custom_components/pricehawk/__init__.py:57`
**Why:** PR #107's `async_create_background_task` calls don't store the handles. Reload/unload races leave tasks mutating unloaded coordinator state. Pytest already emits unawaited-coroutine warnings on these paths.
**Fix:** Store the task handles and register `entry.async_on_unload(task.cancel)` or a coordinator shutdown hook.
**Priority:** P1.

### TODO-CODEX-P2-1: MagicMock conftest + source-string Silver tests masking drift

**Where:** `tests/conftest.py:16` + `tests/test_silver_checklist.py:142`
**Why:** Broad `MagicMock` HA stubs let stale-`hass.data` regressions pass the Silver "no legacy hass.data" test (TODO-CODEX-P1-1 above).
**Fix:** Replace key checklist greps with behaviour tests or AST checks, especially for config/options flow and runtime-data access paths.
**Priority:** P2.

### TODO-CODEX-P2-2: `__pycache__` artefacts contain dummy-secret-shaped strings

**Where:** `custom_components/pricehawk/**/__pycache__/*` + `tests/**/__pycache__/*`
**Why:** Local audit/secret scans can flag compiled dummy strings used in fixtures, even though git doesn't track them.
**Fix:** Clean `__pycache__` before release/audit scans. Keep CI secret scans scoped to tracked files OR add an explicit clean step.
**Priority:** P2.
