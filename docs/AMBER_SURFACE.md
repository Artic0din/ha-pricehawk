# Amber Surface Audit

**Purpose:** parity contract for the Flow Power provider port (issue
[#178](https://github.com/Artic0din/ha-pricehawk/issues/178)). Every
Amber-coupled artefact below must have a Flow Power counterpart before
that work is declared complete. Every later PR on the Flow Power feature
branch is checked against this document.

**Scope:** the *wholesale-provider* surface. GloBird-side artefacts
(`TariffEngine`, plan defaults, incentive trackers) are noted only
where they appear in the same code path; they are NOT part of the
provider abstraction and will remain unchanged.

**Method:** static read of every file under
`custom_components/pricehawk/` and `tests/` at branch HEAD.

**Audit version:** 1 (PR 1 — initial scaffold).

---

## 1. Amber data sources & I/O

Amber I/O is REST. There is no WebSocket. Two transport mechanisms are
in use — `aiohttp` for the live coordinator path, blocking `urllib` via
the executor for the backfill path. The Flow Power port must mirror both.

| Endpoint | Caller | Transport | Frequency | Retry | Notes |
|---|---|---|---|---|---|
| `GET /v1/sites` | `config_flow.fetch_amber_sites` | `aiohttp` (single call, `timeout=...`) | once per setup / options-edit | none | Returns user's Amber sites |
| `GET /v1/sites/{site_id}/prices/current` | `coordinator._poll_amber_prices` (`coordinator.py:101`) | `aiohttp` via `_fetch_amber_with_retry` | every 300s (`AMBER_API_POLL_INTERVAL`, `const.py:224`) | **3 attempts, exp-backoff, `Retry-After` honoured on 429/5xx, fail-fast on other 4xx** (`coordinator.py:139-200`) | Returns current `general` (import) and `feedIn` (export) channel prices in `c/kWh` (incl GST) |
| `GET /v1/sites/{site_id}/prices?startDate=&endDate=` | `coordinator._fetch_today_price_schedule` (`coordinator.py:202`) | `aiohttp` (single call, `timeout=15`) | once on first coordinator run | none | Populates today's 48 half-hour intervals for the dashboard chart |
| `GET /v1/sites/{site_id}/prices?startDate=&endDate=` | `backfill.fetch_amber_price_history` (`backfill.py:70-90`) | **blocking `urllib.request.urlopen` via `hass.async_add_executor_job`** (`timeout=30`) | invoked only by the `backfill_history` service | `urllib.error.URLError` / `HTTPError` / `TimeoutError` returns `None`; no backoff | Historical recovery. Different transport from the coordinator path — Flow Power port must implement an executor-based fallback OR migrate this caller to async at the same time |

Retry contract is **scoped to `/prices/current` only**. The other three
callers are single-shot and rely on caller-side gap tolerance (the
coordinator polls again 5 minutes later; setup/backfill failures
surface to the user). Flow Power's AEMO client should match this
contract per-endpoint, not blanket-apply retry everywhere.

Base URL: `AMBER_API_BASE_URL = "https://api.amber.com.au/v1"`
(`const.py:227`).

---

## 2. Coordinator data dict shape

The coordinator builds a single flat dict per update cycle
(`coordinator._build_data_dict`, lines 421–507). Sensor entities read
from this dict via `self.coordinator.data.get(key)`.

### Amber-specific keys (must gain a Flow Power counterpart)

| Key | Source | Unit |
|---|---|---|
| `amber_import_rate` | `self._amber_import_c` | c/kWh |
| `amber_export_rate` | `self._amber_export_c` | c/kWh |
| `amber_peak_rate` | mirrors `_amber_import_c` (Amber is dynamic) | c/kWh |
| `amber_daily_cost` | `self._amber_calc.net_daily_cost_aud` | AUD |
| `amber_daily_fixed_charges` | `self._amber_calc.daily_fixed_charges_aud` | AUD |
| `amber_import_cost_aud` | `self._amber_calc.import_cost_today_c / 100` | AUD |
| `amber_export_credit_aud` | `self._amber_calc.export_earnings_today_c / 100` | AUD |
| `amber_import_kwh` | `self._amber_calc.import_kwh_today` | kWh |
| `amber_export_kwh` | `self._amber_calc.export_kwh_today` | kWh |

### GloBird-specific keys (UNCHANGED — out of scope)

`globird_import_rate`, `globird_export_rate`, `globird_peak_rate`,
`globird_daily_cost`, `globird_daily_supply_aud`,
`globird_import_cost_aud`, `globird_export_credit_aud`,
`globird_import_kwh`, `globird_export_kwh`,
`globird_zerohero_status`, `globird_super_export_kwh`.

### Shared / provider-agnostic keys

| Key | Notes |
|---|---|
| `saving_today` | Directional, sign depends on `CONF_CURRENT_PROVIDER` |
| `saving_month_aud` | Monthly accumulator |
| `metrics_won` | `"N/3"` — Amber wins import-rate, export-rate, daily-cost (must generalise to "wholesale provider vs GloBird") |
| `last_updated` | timestamp |
| `daily_wins` | `{"amber": int, "globird": int}` — must accept `flow_power` key |
| `daily_cost_history` | list of `{date, amber, globird}` rows (key must become provider-agnostic) |
| `price_history` | list of `{t, ai, ae, gi, ge}` 5-min points (keys `ai`/`ae` must remain stable for chart compat — provider-agnostic semantically) |
| `today_schedule` | same shape as `price_history`, populated from `/prices?startDate=` |
| `csv_comparison` | added by `analyze_csv` service handler |

---

## 3. Sensor inventory

22 entities total (`sensor.async_setup_entry`, lines 332–381). Entity
unique-ids are `{entry.entry_id}_{key}` (`sensor.py:44`). All sensors
belong to a single device named "PriceHawk".

### Amber-dependent rate sensors (RATE_SENSORS, `sensor.py:27`)

| Entity ID | Coordinator key | Unit | State class | Notes |
|---|---|---|---|---|
| `sensor.amber_import_rate` | `amber_import_rate` | c/kWh | MEASUREMENT | `amber_dependent=True` (only `available` when `amber_import_rate is not None`) |
| `sensor.amber_feed_in_tariff` | `amber_export_rate` | c/kWh | MEASUREMENT | `_attr_name = "Amber Feed In Tariff"`; entity ID derived |
| `sensor.amber_peak_rate` | `amber_peak_rate` | c/kWh | MEASUREMENT | Mirrors import for Amber |

### GloBird rate sensors (UNCHANGED)

`sensor.globird_import_rate`, `sensor.globird_feed_in_tariff`,
`sensor.globird_peak_rate`.

### Comparison sensors (provider-agnostic but reference Amber in logic)

| Class | Entity ID | Coordinator key(s) read | Notes |
|---|---|---|---|
| `BestProviderSensor` (`sensor.py:91`) | `sensor.pricehawk_best_provider` | `amber_import_rate`, `globird_import_rate` | Returns string `"Amber Electric"` or `"GloBird Energy"` — must generalise to active wholesale provider name |
| `BestRateSensor` (`sensor.py:129`) | `sensor.pricehawk_best_rate` | `amber_import_rate`, `globird_import_rate` | `min()` of the two |
| `CheapestTodaySensor` (`sensor.py:110`) | `sensor.pricehawk_cheapest_today` | `amber_daily_cost`, `globird_daily_cost` | Same provider-name issue |
| `SavingTodaySensor` (`sensor.py:152`) | `sensor.pricehawk_saving_today` | `saving_today` | AUD, MONETARY, TOTAL |
| `SavingMonthSensor` (`sensor.py:174`) | `sensor.pricehawk_saving_month` | `saving_month_aud` | AUD, MONETARY, TOTAL |
| `MetricsWonSensor` (`sensor.py:196`) | `sensor.pricehawk_metrics_won` | `metrics_won` (+ fallback computation from amber/globird keys) | `"N/3"` string |

### Cost sensors

| Class | Entity ID | Coordinator key | Notes |
|---|---|---|---|
| `AmberDailyChargesSensor` (`sensor.py:228`) | `sensor.pricehawk_amber_daily_charges` | `amber_daily_fixed_charges` | AUD, MONETARY |
| `ProviderDailyCostSensor` ×2 totals (`sensor.py:362-363`) | `pricehawk_amber_cost_today`, `pricehawk_globird_cost_today` | `amber_daily_cost`, `globird_daily_cost` | AUD, MONETARY, TOTAL |
| `ProviderDailyCostSensor` ×4 breakdowns (`sensor.py:366-369`) | `pricehawk_{amber,globird}_{import_cost,export_credit}` | `{amber,globird}_{import_cost_aud,export_credit_aud}` | AUD, MONETARY, TOTAL |
| `GloBirdDailySupplySensor` (`sensor.py:302`) | `sensor.pricehawk_globird_daily_supply` | `globird_daily_supply_aud` | AUD, MONETARY |
| `LastUpdatedSensor` (`sensor.py:266`) | `sensor.pricehawk_last_updated` | `last_updated` + extras | TIMESTAMP. `extra_state_attributes` carry: `price_history`, `today_schedule`, `{amber,globird}_{import,export}_kwh`, `daily_wins`, `daily_cost_history`, `csv_comparison`. `_unrecorded_attributes` whitelist at `sensor.py:271` |
| `ZeroHeroStatusSensor` (`sensor.py:318`) | `sensor.pricehawk_zerohero_status` | `globird_zerohero_status` | GloBird-side, unchanged |

---

## 4. Services

Registered in `__init__.async_setup_entry` (lines 52–183), schemas in
`services.yaml`.

| Service | Inputs | Side effects | Wholesale-coupling |
|---|---|---|---|
| `pricehawk.analyze_csv` | `rows` (list of pre-parsed CSV row dicts from the dashboard) | Runs CSV through user's GloBird tariff via `csv_analyzer.analyze_csv_data`; result stored at `coordinator.data["csv_comparison"]` | CSV is Amber-issued. Replacement service for Flow Power must accept Flow Power CSV format OR a normalised intermediate shape — TBD when FP CSV format is known |
| `pricehawk.backfill_history` | `days` (1–90, default 30) | Reads HA recorder history for `CONF_GRID_POWER_SENSOR`, fetches Amber prices, computes daily costs both sides, merges into `daily_cost_history` (capped 180 days) | Hard-coded to Amber API (`backfill.fetch_amber_price_history`). FP equivalent uses AEMO NEMWEB |

---

## 5. Config flow surface

`ConfigFlow.VERSION = 1`, `MINOR_VERSION = 1` (`config_flow.py:418-419`). No
`async_migrate_entry` exists in `__init__.py`.

### Init steps (9, executed in order)

1. `async_step_user` — Amber API key → `fetch_amber_sites`
2. `async_step_site_select` — pick Amber site (if >1)
3. `async_step_amber_fees` — `CONF_AMBER_NETWORK_DAILY_CHARGE`, `CONF_AMBER_SUBSCRIPTION_FEE`
4. `async_step_globird_plan` — pick GloBird plan type
5. `async_step_globird_rates` — TOU/flat-stepped import rates
6. `async_step_globird_export` — export TOU rates
7. `async_step_incentives` — ZEROHERO + Super Export toggles
8. `async_step_sensor_select` — grid-power sensor entity
9. `async_step_dashboard_token` — `CONF_CURRENT_PROVIDER` + optional `CONF_HA_TOKEN`

### Options flow (`OptionsFlow`, `config_flow.py:726`)

Menu options: `amber_api_key`, `globird_plan`, `amber_fees`,
`sensor_select`. Selecting any re-runs the matching sub-flow.

### Stored config

`entry.data`: `CONF_API_KEY`, `CONF_SITE_ID`, `CONF_HA_TOKEN`,
`CONF_CURRENT_PROVIDER`.

`entry.options`: `CONF_PLAN_TYPE`, `CONF_DAILY_SUPPLY_CHARGE`,
`CONF_DEMAND_CHARGE`, `CONF_IMPORT_TARIFF`, `CONF_EXPORT_TARIFF`,
`CONF_INCENTIVES`, `CONF_GRID_POWER_SENSOR`,
`CONF_AMBER_NETWORK_DAILY_CHARGE`, `CONF_AMBER_SUBSCRIPTION_FEE`.

---

## 6. Calculation entry points

| Class / function | File | Responsibility | FP equivalent must provide |
|---|---|---|---|
| `AmberCalculator.update(grid_power_w, import_c, export_c, now_local)` | `amber_calculator.py:15` | Integrates 30-second grid-power samples against current Amber rates; midnight reset | `WholesaleProvider.cost_for_interval` |
| `AmberCalculator.{import_kwh_today, export_kwh_today, import_cost_today_c, export_earnings_today_c, daily_fixed_charges_aud, net_daily_cost_aud}` | `amber_calculator.py` | Properties consumed by `coordinator._build_data_dict` | Same property surface or equivalent dict keys |
| `AmberCalculator.{to_dict, from_dict}` | `amber_calculator.py:119+` | State persistence; **`from_dict` takes explicit HA-tz `today` arg (P0 rule)** | FP calculator must follow same `from_dict(data, today=...)` contract |
| `TariffEngine` (GloBird) | `tariff_engine.py` | UNCHANGED | n/a |
| `csv_analyzer.analyze_csv_data` | `csv_analyzer.py:324` | CSV replay through TariffEngine | n/a — GloBird side only |

---

## 7. CDR ranker integration

**Status: not present.** Despite handoff §4 / §5 / §6 referencing a
"CDR ranker (Phase 3.1)" and "Phase-3 work", no such code exists in
the repo. There is no `EXPECTED_KEYS` symbol, no `cost_for_interval`
ranker hook, no Phase 3.1 module.

`daily_wins` is a simple two-element counter
(`{"amber": int, "globird": int}`) tracking which provider had the
lowest `net_daily_cost_aud` at the day rollover
(`coordinator._async_update_data`, lines 340–344).

**Implication for FP:** `daily_wins` must accept a `flow_power` key
when the user is on Flow Power. The `globird` key remains because
GloBird is always the comparator.

ZEROHERO and FOUR4FREE are GloBird plan-type identifiers
(`const.py:27-28`), not "settlement primitives". The associated
trackers (`ZeroHeroTracker`, `SuperExportTracker` in
`tariff_engine.py`) operate purely on GloBird-side rates and
timestamps. Parity test: confirm they fire correctly when the
wholesale provider is Flow Power — i.e. no Amber-specific coupling
has crept into GloBird-side cost computation.

---

## 8. Dashboard surface

### Native YAML Lovelace dashboard

`custom_components/pricehawk/dashboard.yaml` (single file, package
root). Documented as the "fallback" dashboard in `README.md:118`.

Sensor references (verbatim from current YAML — entity naming is
**inconsistent**: cost and meta sensors carry the `pricehawk_` prefix,
but the four rate-card tiles still use the unprefixed entity IDs that
HA derives from the sensor `_attr_name`):

Cost & meta tiles (prefixed):
`sensor.pricehawk_amber_cost_today`, `sensor.pricehawk_globird_cost_today`,
`sensor.pricehawk_saving_today`, `sensor.pricehawk_saving_month`,
`sensor.pricehawk_best_provider`, `sensor.pricehawk_metrics_won`,
`sensor.pricehawk_amber_import_cost`, `sensor.pricehawk_amber_export_credit`,
`sensor.pricehawk_amber_daily_charges`,
`sensor.pricehawk_globird_import_cost`, `sensor.pricehawk_globird_export_credit`,
`sensor.pricehawk_globird_daily_supply`,
`sensor.pricehawk_last_updated`, `sensor.pricehawk_zerohero_status`.

Rate-card tiles (**unprefixed** — see `dashboard.yaml:57-72`):
`sensor.amber_import_rate`, `sensor.globird_import_rate`,
`sensor.amber_feed_in_tariff`, `sensor.globird_feed_in_tariff`.

**Implication for PR 6:** the dashboard generator must remap both
naming styles. The four unprefixed rate IDs are also the ones an FP
install would need re-pointed at `sensor.flow_power_import_rate` /
`sensor.flow_power_feed_in_tariff` (or equivalent). If the FP pass
only touches the SPA + prefixed names, the YAML fallback ships with
stale Amber-only references. The AGENTS.md P1 rule "Entity ID not
prefixed with `pricehawk_`" applies — but this inconsistency
**predates this PR** and is out of scope for PR 1 to fix; tracking
in PR 6's scope.

### HTML SPA dashboard

`custom_components/pricehawk/www/dashboard.html` is registered as a
sidebar panel via `dashboard_config.setup_panel_iframe` →
`async_register_built_in_panel(component_name="iframe")`. The
generator path is iframe-served static HTML, not a storage-mode
Lovelace dashboard.

Asset copy: `dashboard_config.copy_www_assets` copies
`www/dashboard.html` and icons to `/config/www/pricehawk/` on entry
setup.

The SPA fetches sensor data via the HA WebSocket API. Sensors it
references include the full Amber and comparison sensor list above
plus the `LastUpdatedSensor.extra_state_attributes` payload for
charts and history.

Provider-name hardcoding: the SPA UI text refers to "Amber" in
labels; PR 6 must replace user-visible strings with a provider-name
read from the active wholesale provider.

---

## 9. HA Energy Dashboard integration

**Status: not present.** PriceHawk creates its own
`ProviderDailyCostSensor` entities with
`device_class=MONETARY, state_class=TOTAL, last_reset=midnight` but
does NOT call `async_import_statistics` to feed HA's native Energy
Dashboard. Users can add the per-provider cost sensors to the Energy
Dashboard manually.

FP equivalent sensors must register with the same
`device_class`/`state_class`/`last_reset` schema so they're usable
the same way.

---

## 10. Tests covering Amber

Located under `tests/`:

| File | Touches Amber? | What it asserts |
|---|---|---|
| `tests/test_amber_calculator.py` | direct | `AmberCalculator.update`, midnight reset, `to_dict`/`from_dict` round-trip |
| `tests/test_coordinator.py` | direct | `_build_data_dict` shape, `_compute_saving` direction, retry logic |
| `tests/test_config_flow.py` | partial | Helper functions (`str_to_windows`, `windows_to_str`, `time_to_minutes`, overlap detection); no full HA config-flow integration tests |
| `tests/test_backfill.py` | direct | `backfill_from_history` merge logic |
| `tests/test_csv_analyzer.py` | direct | CSV replay correctness |
| `tests/test_tariff_engine.py` | n/a | GloBird-side only |
| `tests/test_helpers.py` | n/a | Generic helpers |
| `tests/test_accuracy_validation.py` | direct | End-to-end accuracy assertions against fixture data |

Fixtures: `tests/fixtures/`.

---

## Migration constraints (PR 4 onwards)

Existing Amber installs must:

1. Continue working with zero user action — `wholesale_provider` defaults to `"amber"`.
2. Preserve all `amber_*` coordinator keys for at least one release cycle (sensor entity IDs are stable; coordinator keys are internal but referenced by tests).
3. Continue to serve the existing dashboard until PR 6 lands.
4. Preserve all stored state — `from_dict` migration is additive (`flow_power_*` fields are absent on legacy entries and default to `None`).

`async_migrate_entry` (added in PR 4) sets
`entry.data["wholesale_provider"] = "amber"` if absent, bumps
`VERSION` to 2, returns `True`.

---

## Parity contract

Updated at the bottom of every audit revision. PR 4 introduces the
first full FP↔Amber parity table; PR 7 closes the loop with end-to-
end verification.
