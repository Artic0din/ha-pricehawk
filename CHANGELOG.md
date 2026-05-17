# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
