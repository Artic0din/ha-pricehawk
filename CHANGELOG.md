# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

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
