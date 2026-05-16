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
