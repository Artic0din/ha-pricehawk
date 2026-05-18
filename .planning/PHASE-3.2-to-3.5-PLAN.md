# PriceHawk — Phase 3.2 → 3.5 Implementation Plan

Locked 2026-05-17. Source-of-truth roadmap: `.planning/PHASE-3-ROADMAP.md`.
Branch base: `dev` @ `2155148` (1.5.0-beta.2). Tests at HEAD: ~724 passing.

This doc is the executable plan. It assumes Phase 3.0 + 3.1 have shipped and
the architecture summarised at the top of the prompt is in place. Anything
marked **REVISIT** is a deliberate 50/50 call we lock now and reopen only if
something blocks it.

---

## 0. Inter-phase dependency graph

```text
Phase 3.2  (history_replay + backfill rewrite + status sensor)
   │
   ├──> Phase 3.3  (rollup sensors read daily_cost_history populated by 3.2)
   │       ├──> Phase 3.4 (named comparator sensors reuse PeriodRollupSensor)
   │       └──> Phase 3.5 (dashboard renders rollups + ranked list)
   │
   └──> Phase 3.4 (named comparator participates in same daily_cost_history)
```

**Strict ordering:** 3.2 must merge before 3.3 / 3.4 / 3.5 can ship anything
they depend on. 3.3 must merge before 3.5 can wire its rollup cards.

**Parallel-safe:** Once 3.2 is merged, 3.3 and 3.4 can be developed in
parallel branches (they touch disjoint modules — `cdr/rollup.py` +
`PeriodRollupSensor` vs `coordinator.py` named-comparator path +
OptionsFlow). They merge in either order.

**3.5 ships last** — it's the consumer of everything else's entities.

**Wall-clock estimate:** 3.2 = 2 days, 3.3 = 1.5 days, 3.4 = 1 day,
3.5 = 1.5 days. ~6 days focused.

---

## 1. Architectural decisions locked up front

Read these before opening any file. They prevent re-deriving rationale at
each commit.

### 1.1 `daily_cost_history` becomes the single source of truth
Today, `_daily_cost_history` is appended one row/day by the live coordinator
loop (`coordinator.py:636-642`). After 3.2, the SAME list is also written by
the multi-plan backfill. Schema migration:

**Before (today):** `{"date": "2026-05-16", "globird_<planid>": 9.21, "amber": 8.40}`
— keys are provider IDs from `self._providers`.

**After 3.2:** Same shape, but with NEW keys for ranked alternatives. Top-K
alternative costs land under `alt_<planId>` keys. Current plan still under
its provider-id key (unchanged so existing rollups don't break).

```python
{
    "date": "2026-05-16",
    "globird_GLO731031MR@VEC": 9.21,   # current plan provider id (unchanged)
    "amber": 8.40,                     # truth overlay (unchanged)
    "alt_AGL900123": 7.85,             # NEW: ranked alternative #1
    "alt_ORIG456789": 8.10,            # NEW: ranked alternative #2
    "named": 8.00,                     # NEW (3.4): named comparator
}
```

This keeps the existing daily-rollover write path untouched and additive.
Rollup sensors (3.3) read these keys; dashboard (3.5) reads them too.

**REVISIT:** if `alt_*` key explosion hurts recorder attribute size, we
compact to a flat `"alts": {"AGL900123": 7.85, ...}` nested dict. Not doing
this now because flat keys serialise smaller and the cap is 180 entries.

### 1.2 History replay is a pure function over (slots, plan) → CostBreakdown
The existing evaluator (`cdr/evaluator.py:evaluate`) and streaming
adapter (`cdr/streaming.py`) already replay slots through one plan. Phase
3.2 wraps this in `cdr/history_replay.py` to fan-out across N plans, with
HA-recorder streaming chunks day-by-day so memory stays O(1 day) regardless
of lookback length.

### 1.3 Rollup is computed-on-demand, not stored
`cdr/rollup.py` returns `(sum_aud, day_count, oldest_date)` from a
`daily_cost_history` list filtered by date window. No new storage. Sensors
recompute on every coordinator tick (cheap — at most 365 list iterations).

### 1.4 Named comparator is the SAME class as ranked alternatives
A pinned plan is just one more plan in the multi-plan provider set. We
register it as a long-lived `CdrPlanProvider` instance keyed `named`,
ticked in the same `for provider in self._providers.values()` loop
(`coordinator.py:682-683`). No new tick code path; no new lock.

### 1.5 Backfill status is a single string sensor
`sensor.pricehawk_backfill_status` carries `idle | running | complete | failed`
as state, with `last_run`, `days_loaded`, `plans_replayed`, `error`
as attributes. State machine lives on the coordinator (`_backfill_status`
attr). One sensor, no new entity class hierarchy.

### 1.6 Dashboard is rewritten, not augmented
Existing `dashboard.html` (2447 LOC) is keyed off Amber-vs-current-plan
two-comparator world view. 3.5 throws it away and starts from
`assets/dashboard-v3-apple.html` (1478 LOC mockup) as the seed, retaining
the dark-theme Outfit/IBM Plex Mono visual language + the noise/ambient
treatment, but replacing all Amber-specific cards with the multi-plan
ranked top-N list.

---

## 2. Phase 3.2 — Universal HA-history backfill

**Goal:** Populate `daily_cost_history` for last N days (where N = HA
recorder retention) across current plan + top-K ranked alternatives + named
comparator. Kicked off automatically at wizard completion and on integration
reload; user-triggerable via existing `backfill_history` service.

**Files touched (3 commits):**
- New: `custom_components/pricehawk/cdr/history_replay.py` (~180 LOC)
- Rewritten: `custom_components/pricehawk/backfill.py` (~120 LOC after rewrite, down from current 369)
- Modified: `custom_components/pricehawk/coordinator.py` (~40 LOC delta — backfill hook + status state)
- Modified: `custom_components/pricehawk/__init__.py` (~20 LOC delta — kick-off + service rewire)
- Modified: `custom_components/pricehawk/sensor.py` (~30 LOC — `BackfillStatusSensor` class + registration)
- New: `tests/test_history_replay.py` (~180 LOC, ~14 tests)
- Rewritten: `tests/test_backfill.py` (~250 LOC — Amber-specific tests deleted, generic ones replace)

**Total LOC delta:** ~+300 net (file-count net-zero; one new module, two rewritten).

### 2.1 Commit breakdown

#### Commit 3.2/1 — `cdr/history_replay.py` skeleton + pure-logic tests
**Touches:**
- `custom_components/pricehawk/cdr/history_replay.py` (new)
- `tests/test_history_replay.py` (new)

**What it does:**
Adds the module with three pure functions, no HA imports. Establishes the
public API surface that commit 3.2/2 wires the coordinator to.

**Public API:**
```python
# cdr/history_replay.py
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Iterator

from .evaluator import CostBreakdown, evaluate


def states_to_half_hour_slots(
    states: Iterable[tuple[datetime, float, str]],
    *,
    gap_protection_h: float = 0.1,
) -> list[dict[str, Any]]:
    """Convert (ts, power_w, unit) tuples to evaluator slot dicts.

    Each slot: {"ts_local": iso, "import_kwh": float, "export_kwh": float}.
    Slots align to 30-min boundaries (matching streaming engine semantics).
    Partial trailing slot is included (evaluator handles short slots).

    Unit string: "W" or "kW" — kW values multiplied by 1000.
    Gap protection caps any single (ts[i] - ts[i-1]) at gap_protection_h
    hours to prevent runaway energy from HA recorder gaps.
    """
    ...


def replay_day_through_plan(
    slots: list[dict[str, Any]],
    plan: dict[str, Any],
    *,
    entry_options: dict[str, Any] | None = None,
) -> CostBreakdown | None:
    """Single-day replay of slots through one plan. Returns None on failure.

    Wraps evaluate() with the standard exception-swallow pattern (matches
    deep_rank in cdr/ranking.py). Returns None when evaluator raises OR
    when slot_count == 0 (consistent with deep_rank semantics).
    """
    ...


def fan_out_replay(
    daily_slots: dict[str, list[dict[str, Any]]],
    plans: dict[str, dict[str, Any]],
    *,
    entry_options: dict[str, Any] | None = None,
) -> Iterator[tuple[str, dict[str, float]]]:
    """Generator: yields (date_str, {plan_key: aud_inc_gst, ...}) per day.

    daily_slots: {"YYYY-MM-DD": [slot, slot, ...]}  one day's worth of slots
    plans:       {"plan_key": plan_body}            CDR PlanDetailV2 data shape

    Generator (not list-return) so the caller (coordinator) can write each
    day's row to daily_cost_history without holding all-days × all-plans
    breakdowns in memory simultaneously. For 365 days × 25 plans, the
    full materialised list is ~50 MB of CostBreakdown objects + traces.
    Streaming keeps peak RAM at ~one day × 25 plans = ~1 MB.
    """
    ...
```

**Tests (TestStatesToHalfHourSlots, TestReplayDayThroughPlan, TestFanOutReplay):**
- `test_states_aligned_to_30min_boundary` — 11:17 reading lands in 11:00-11:30 slot
- `test_kw_unit_multiplied_by_1000` — unit="kW" multiplies import_kwh by 1000 (kW→W conversion)
- `test_gap_protection_caps_long_delta` — 1-hour gap clamped to 0.1h
- `test_negative_power_lands_in_export` — power_w=-2000 fills export_kwh
- `test_zero_power_skipped` — no-import-no-export readings don't create slots
- `test_replay_returns_none_on_evaluator_exception` — patches evaluate to raise
- `test_replay_returns_none_on_zero_slot_count` — empty input → None
- `test_replay_passes_entry_options_through` — opt-in fields reach evaluator
- `test_fan_out_yields_one_tuple_per_day` — 3 days in → 3 yields
- `test_fan_out_excludes_failed_plans_from_day_dict` — plan that returns None is absent from that day's dict
- `test_fan_out_empty_plans_dict_yields_empty_dicts` — fan_out({date:slots}, {}) yields (date, {})
- `test_fan_out_empty_daily_slots_yields_nothing` — no days = no iterations
- `test_fan_out_preserves_date_ordering` — iterates daily_slots in sort order
- `test_states_handles_string_power_values` — float("2000") works (HA serialises numerics as strings)

**Mocking:**
- No HA mocks needed. Tests import only `cdr.history_replay` + `cdr.evaluator`.
- Use real fixtures from `tests/fixtures/` if any plans already exist; otherwise build minimal `_flat_plan()` helper in the test file (single-rate, $0.30/kWh, $1/day supply).

**CR-anticipation:**
- `path: "**/*.py"` recipe will flag missing type hints on public functions → use the typed signatures above verbatim.
- `path: "**/*.py"` recipe will flag bare `except:` → wrap only the evaluator call in `try/except Exception` with a logger.exception (mirrors `deep_rank` at `cdr/ranking.py:392`).

#### Commit 3.2/2 — Rewrite `backfill.py` as thin HA-side adapter + delete Amber-specific code
**Touches:**
- `custom_components/pricehawk/backfill.py` (rewrite)
- `tests/test_backfill.py` (rewrite)

**What it does:**
`backfill.py` becomes the HA-side coordination layer: pulls recorder history,
groups by date, delegates to `cdr.history_replay.fan_out_replay`, returns
merged `daily_cost_history` rows. Amber-API price fetching deletes entirely
(replaced by plan-body evaluation; Amber as a TRUTH OVERLAY only writes its
side once daily via the live coordinator — backfill doesn't need it).

**Public API after rewrite:**
```python
async def backfill_daily_cost_history(
    hass: HomeAssistant,
    grid_sensor_entity: str,
    plans: dict[str, dict[str, Any]],   # {plan_key: cdr_plan_data}
    *,
    days_back: int = 30,
    entry_options: dict[str, Any] | None = None,
    existing_history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """End-to-end backfill. Returns merged daily_cost_history (max 180 entries).

    Internals:
      1. Pull recorder history day-by-day (NOT one big query): 30 separate
         state_changes_during_period calls, each scoped to one local day.
         Keeps recorder query footprint bounded — single 30-day query
         on a 1Hz sensor returns ~2.5M rows in worst case.
      2. For each day: parse states → half-hour slots via history_replay.
      3. Pass to fan_out_replay; consume generator one day at a time.
      4. Merge into existing_history (overwrite same-day rows).
    """
```

**Why day-by-day recorder queries:** A single 30-day `state_changes_during_period`
on a heavily-recording grid sensor can return 100K+ State objects. Per-day
queries keep peak memory bounded and let us surface progress through the
status sensor (commit 3.2/4). The recorder's SQLite query is indexed on
last_changed so 30 small queries are not meaningfully slower than 1 big one.

**Tests:**
- All existing test_backfill.py tests get DELETED (they assert Amber-API behavior).
- New tests: 6 functional, all using mocked recorder (`mock_state_changes_during_period`):
  - `test_backfill_returns_one_row_per_day_in_window`
  - `test_backfill_merges_with_existing_history_overwriting_same_dates`
  - `test_backfill_caps_at_180_entries`
  - `test_backfill_skips_days_with_no_history`
  - `test_backfill_handles_evaluator_failure_gracefully` (plan that throws is absent from row)
  - `test_backfill_returns_existing_history_when_grid_sensor_unconfigured`

**Mocking:**
- HA recorder calls are mocked via `unittest.mock.patch` on
  `homeassistant.components.recorder.get_instance` and `state_changes_during_period`
  (no real HA app context needed since `tests/conftest.py` already mocks
  the HA module tree).
- Need to extend `conftest.py` to add `homeassistant.components.recorder`
  + `homeassistant.components.recorder.history` to `_mods` dict.

**CR-anticipation:**
- `path: "**/*.py"` will flag the deletion of `fetch_amber_price_history`
  → confirm in commit message it's intentional (Amber backfill is now
  the live coordinator's job, not the backfill's).
- `path: "**/*.py"` may flag the loop of N async recorder calls as
  "could be parallel" — DON'T parallelise. HA's recorder uses a single
  executor pool; concurrent queries serialise anyway and just bloat task
  count. Comment this in source.

#### Commit 3.2/3 — Wire coordinator hook + auto-kickoff at first refresh
**Touches:**
- `custom_components/pricehawk/coordinator.py`
- `custom_components/pricehawk/__init__.py`

**What it does:**
- Adds `async_run_backfill()` method to coordinator (mirrors
  `async_run_ranking_job` pattern at `coordinator.py:1232-1282`).
- Reuses the existing `_ranking_lock` REUSE (REVISIT below) to serialise
  backfill with ranking — both write to `_daily_cost_history` so we can't
  let them race.
- Adds `_backfill_status: str` attribute initialised to `"idle"`,
  `_backfill_last_run_at`, `_backfill_days_loaded`, `_backfill_error`.
- `async_setup_entry` in `__init__.py:48` kicks off backfill ONCE after
  `async_run_ranking_job` completes its first run (so we have the top-K
  alternatives to replay against). Uses `hass.async_create_task` so setup
  doesn't block.

**Coordinator shape:**
```python
# coordinator.py — new section after Phase 3.1 ranking job
async def async_run_backfill(
    self, *, days_back: int = 30
) -> int:
    """Run universal HA-history backfill. Returns # days loaded."""
    async with self._ranking_lock:   # REVISIT: separate _backfill_lock?
        if self._backfill_status == "running":
            return 0
        self._backfill_status = "running"
        try:
            plans = self._build_backfill_plan_set()
            from .backfill import backfill_daily_cost_history  # local import
            result = await backfill_daily_cost_history(
                self.hass,
                self._grid_power_entity,
                plans,
                days_back=days_back,
                entry_options=dict(self.config_entry.options),
                existing_history=list(self._daily_cost_history),
            )
            self._daily_cost_history = result
            self._backfill_days_loaded = len(result)
            self._backfill_status = "complete"
            self._backfill_last_run_at = dt_util.now()
            await self.async_persist_state()
            return len(result)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("backfill: failed")
            self._backfill_status = "failed"
            self._backfill_error = str(err)
            return 0

def _build_backfill_plan_set(self) -> dict[str, dict[str, Any]]:
    """Compose {plan_key: plan_body} for backfill replay.

    Includes current plan + top-K ranked alternatives + named (3.4).
    Keys match daily_cost_history schema (section 1.1).
    """
    plans: dict[str, dict[str, Any]] = {}
    current_plan = self.config_entry.options.get("cdr_plan", {}).get("data")
    if isinstance(current_plan, dict):
        plans[self._current_plan_provider.id] = current_plan
    for alt in self._cheap_ranked_alternatives:
        plan_id = alt.get("planId")
        if plan_id:
            plans[f"alt_{plan_id}"] = alt
    # Named comparator (3.4) — uncomment when 3.4 lands:
    # if self._named_comparator_plan:
    #     plans["named"] = self._named_comparator_plan
    return plans
```

**REVISIT — lock choice:** Use shared `_ranking_lock` for now. Backfill
runs once at setup + on user-triggered service; ranking runs daily at 00:30
+ on user trigger. Overlap is unlikely. If we observe contention in
production, split to dedicated `_backfill_lock`. Cost of being wrong: brief
serialisation of two rare operations.

**`__init__.py` kickoff (around line 48):**
```python
# Phase 3.1 — schedule daily multi-plan ranking job
coordinator.schedule_daily_ranking()
hass.async_create_task(coordinator.async_run_ranking_job())

# Phase 3.2 — kick off backfill after ranking populates alternatives
async def _backfill_after_ranking() -> None:
    # Wait for first ranking run to finish so plan set includes alts
    await coordinator._ranking_lock.acquire()
    coordinator._ranking_lock.release()
    await coordinator.async_run_backfill(days_back=30)

hass.async_create_task(_backfill_after_ranking())
```

**REVISIT — days_back default:** 30 days. HA recorder default is 10 days,
so most users get only the last 10. Power users with `purge_keep_days: 365`
get the full 30 we ask for. Pulling 365 by default would be slower and
mostly empty for new users. The `backfill_history` service already accepts
a `days` parameter so users can over-ride.

**Tests:** Coordinator wrappers are 1-line delegates; verify via
`tests/test_coordinator_helpers.py` smoke test for `_build_backfill_plan_set`
producing the right dict shape from a mocked provider + ranked list.

#### Commit 3.2/4 — `BackfillStatusSensor` + service signature update
**Touches:**
- `custom_components/pricehawk/sensor.py` (add class + register)
- `custom_components/pricehawk/__init__.py` (update `handle_backfill` service to use new coordinator method)
- `custom_components/pricehawk/services.yaml` (update description)
- `tests/test_review_improvements.py` (add `BackfillStatusSensor` smoke test)

**What it does:**
- New `BackfillStatusSensor` class in `sensor.py` (mirrors
  `RankedAlternativesSensor` shape at sensor.py:492-528).
- State = `self.coordinator._backfill_status` (string).
- Attributes = `{"last_run", "days_loaded", "plans_replayed", "error"}`.
- The existing `backfill_history` service (currently `__init__.py:92-186`)
  shrinks to a one-liner that delegates to `coordinator.async_run_backfill()`.

**Sensor shape:**
```python
class BackfillStatusSensor(PriceHawkBaseSensor):
    """Phase 3.2 — universal HA-history backfill status.

    State: idle | running | complete | failed.
    Attributes: last_run, days_loaded, plans_replayed, error.
    """
    _attr_name = "PriceHawk Backfill Status"
    _attr_icon = "mdi:database-refresh"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "backfill_status")

    @property
    def native_value(self) -> str:
        return getattr(self.coordinator, "_backfill_status", "idle")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        return {
            "last_run": c._backfill_last_run_at.isoformat()
                if c._backfill_last_run_at else None,
            "days_loaded": c._backfill_days_loaded,
            "error": c._backfill_error,
        }
```

**Surprise risk:** if the user's HA recorder is at default 10 days BUT
their integration was installed yesterday, backfill loads only yesterday.
That's correct behaviour — surface this clearly. Add a `data_age_days`
attribute showing oldest date in `_daily_cost_history` so the dashboard
can warn.

### 2.2 Phase 3.2 risk assessment

**Most likely surprises during integration:**
1. **HA recorder `state_changes_during_period` returns States with
   `last_changed` as `datetime` objects, NOT strings.** The current
   `backfill.py:_parse_history_states` expects strings. Easy fix during
   rewrite — accept both.
2. **Some users have grid sensors that report energy (kWh) not power (W).**
   `_read_grid_power` (`coordinator.py:698-721`) only handles W/kW. Backfill
   should reuse the same path → if it fails, fail loudly to backfill_status.
   Don't silently misinterpret kWh as W.
3. **Day-boundary spanning in AEST.** `replay_day_through_plan` slots have
   `ts_local` as ISO strings; the evaluator parses them with
   `ZoneInfo("Australia/Sydney")` (`cdr/evaluator.py:26`). When grouping
   states into daily buckets, MUST use AEST local date, not UTC.
   `_format_date` in current `backfill.py:200-205` is AEST-safe; preserve
   this pattern.
4. **Top-K alternatives can change between ranking runs** — backfill replays
   yesterday's history through today's alternatives, so a plan that was
   ranked #1 last week and dropped to #25 this week disappears from
   recent backfill rows. This is acceptable (rollups read keys present
   in each row independently). Document in module docstring.
5. **EME proxy throttling during backfill** is a non-issue because backfill
   doesn't fetch plan bodies — they come from the cached ranking results.

**Rollback plan:** revert all four commits in reverse order. The old
`backfill.py` is preserved in git history. Existing
`sensor.pricehawk_backfill_status` doesn't exist yet so no entity-removal
side effects.

**Success metrics:**
- `pytest tests/test_history_replay.py tests/test_backfill.py -v` passes
  with ≥20 new tests.
- After fresh install on Ryan's HA: `sensor.pricehawk_backfill_status`
  transitions `idle → running → complete` within 60s of setup, with
  `days_loaded` between 1 and 30 (depends on his `purge_keep_days`).
- `coordinator.data["daily_cost_history"]` contains rows with `alt_*` keys
  matching cheap-ranked plan IDs.

---

## 3. Phase 3.3 — Period rollup sensors

**Goal:** 15 new sensors covering (current_cost | best_alternative_cost |
savings) × (today | week | month | 3month | year), all reading from
`daily_cost_history`.

**Files touched (3 commits):**
- New: `custom_components/pricehawk/cdr/rollup.py` (~140 LOC)
- Modified: `custom_components/pricehawk/sensor.py` (~80 LOC delta)
- New: `tests/test_rollup.py` (~200 LOC, ~18 tests)

**Total LOC delta:** ~+250.

### 3.1 Commit breakdown

#### Commit 3.3/1 — `cdr/rollup.py` pure-logic module
**Touches:**
- `custom_components/pricehawk/cdr/rollup.py` (new)
- `tests/test_rollup.py` (new)

**Public API:**
```python
# cdr/rollup.py
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

WindowName = Literal["today", "week", "month", "3month", "year"]

# Window sizes in DAYS (rolling, end-inclusive at today).
# Calendar month vs rolling 30 days: chose rolling for simplicity +
# consistency. "month" = last 30 days, "3month" = last 90, "year" = 365.
# REVISIT if users want calendar-month accounting; trivial to add.
WINDOW_DAYS: dict[str, int] = {
    "today": 1,
    "week": 7,
    "month": 30,
    "3month": 90,
    "year": 365,
}


def filter_window(
    history: list[dict[str, Any]],
    window: WindowName,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return rows whose date falls inside the rolling window ending today.

    history rows shape: {"date": "YYYY-MM-DD", "<plan_key>": float, ...}
    `now` defaults to dt_util.now() (AEST). Reads `.date()` only.
    """
    ...


def sum_window(
    rows: list[dict[str, Any]],
    plan_key: str,
) -> tuple[float | None, int]:
    """Sum rows[plan_key] across rows. Returns (sum_aud, day_count).

    Rows lacking plan_key are skipped (alt that wasn't in the top-K that
    day, named comparator missing on pre-3.4 rows, etc).
    Returns (None, 0) when no rows contain plan_key — sensor displays as
    'unknown' rather than '$0.00' so the user knows it's a missing-data
    state, not a real zero spend.
    """
    ...


def best_alternative_for_window(
    rows: list[dict[str, Any]],
    *,
    alt_key_prefix: str = "alt_",
) -> tuple[str | None, float | None, int]:
    """Pick the alt with the LOWEST sum across the window.

    Returns (best_plan_id, sum_aud, day_count). Returns (None, None, 0) if
    no alt keys present in any row. Ties broken by lexicographic plan_id
    so the choice is deterministic across reads.
    """
    ...


def savings(
    current_sum: float | None,
    best_alt_sum: float | None,
) -> float | None:
    """current - best_alt. Returns None if either side is None.

    Positive = you'd save by switching to the best alternative.
    Negative = your current plan is already cheaper than every alt
    (legitimate outcome — surface honestly).
    """
    ...
```

**Tests (TestFilterWindow, TestSumWindow, TestBestAlternativeForWindow, TestSavings):**
- `test_filter_window_today_returns_only_today` — 1 row out of 30
- `test_filter_window_week_returns_7_most_recent` — 7 rows out of 30
- `test_filter_window_year_returns_all_when_history_shorter` — 30 rows out of 30
- `test_filter_window_excludes_future_dated_rows` — defensive: skip ts > now
- `test_filter_window_handles_empty_history` — returns []
- `test_filter_window_handles_malformed_dates_silently` — bad date string skipped, not raised
- `test_sum_window_skips_rows_missing_plan_key` — sparse alt presence
- `test_sum_window_returns_none_when_no_rows_have_key` — never present
- `test_sum_window_handles_string_values_defensively` — coerces float()
- `test_sum_window_counts_only_rows_actually_summed` — day_count accurate
- `test_best_alt_picks_lowest_sum_across_alts` — 3 alts, one wins
- `test_best_alt_tie_broken_lexicographically` — deterministic
- `test_best_alt_handles_no_alt_keys` — returns (None, None, 0)
- `test_best_alt_ignores_non_alt_prefix_keys` — current plan key excluded
- `test_savings_positive_when_alt_cheaper` — current=$10, alt=$8 → +$2
- `test_savings_negative_when_current_cheaper` — current=$8, alt=$10 → -$2
- `test_savings_none_when_either_side_none` — incomplete data
- `test_window_days_constants_present` — smoke test on WINDOW_DAYS

**Mocking:** None. All pure functions over dicts.

**CR-anticipation:**
- `path: "**/*.py"` will demand type hints; the API above is already typed.
- Watch for the `Literal["today",...]` type — may need `from __future__ import
  annotations` (already present in the template above).

#### Commit 3.3/2 — `PeriodRollupSensor` class + 15 registrations
**Touches:**
- `custom_components/pricehawk/sensor.py`

**What it does:**
- New base class `PeriodRollupSensor(PriceHawkBaseSensor)`.
- Three subclasses sharing window-handling code: `CurrentCostRollupSensor`,
  `BestAlternativeRollupSensor`, `SavingsRollupSensor`.
- `async_setup_entry` registers 5 of each (today/week/month/3month/year).

**Sensor shape (DRY via base class):**
```python
class PeriodRollupSensor(PriceHawkBaseSensor):
    """Phase 3.3 — rolling-window cost rollup sensor.

    Subclasses set:
      - _ROLLUP_KIND: "current" | "best_alt" | "savings"
      - _METRIC_LABEL: human-readable infix for the entity name
    """

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "AUD"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    _ROLLUP_KIND: str = ""           # overridden
    _METRIC_LABEL: str = ""          # overridden

    def __init__(self, coordinator, entry, window: str) -> None:
        super().__init__(
            coordinator, entry,
            f"{self._ROLLUP_KIND}_cost_{window}",
        )
        self._window = window
        self._attr_name = (
            f"PriceHawk {self._METRIC_LABEL} "
            f"{window.replace('3month', '3 Month').title()}"
        )

    @property
    def native_value(self) -> float | None:
        from .cdr.rollup import (
            best_alternative_for_window,
            filter_window,
            savings,
            sum_window,
        )
        history = self.coordinator.data.get("daily_cost_history") or []
        rows = filter_window(history, self._window)
        if not rows:
            return None
        current_key = self.coordinator._current_plan_provider.id
        if self._ROLLUP_KIND == "current":
            value, _ = sum_window(rows, current_key)
            return value
        if self._ROLLUP_KIND == "best_alt":
            _, value, _ = best_alternative_for_window(rows)
            return value
        if self._ROLLUP_KIND == "savings":
            current_sum, _ = sum_window(rows, current_key)
            _, alt_sum, _ = best_alternative_for_window(rows)
            return savings(current_sum, alt_sum)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose data_age + best_alt plan_id for the dashboard."""
        from .cdr.rollup import best_alternative_for_window, filter_window
        history = self.coordinator.data.get("daily_cost_history") or []
        rows = filter_window(history, self._window)
        attrs: dict[str, Any] = {
            "window": self._window,
            "days_in_window": len(rows),
        }
        if self._ROLLUP_KIND in ("best_alt", "savings"):
            best_plan_id, _, _ = best_alternative_for_window(rows)
            attrs["best_alternative_plan_id"] = best_plan_id
        return attrs


class CurrentCostRollupSensor(PeriodRollupSensor):
    _ROLLUP_KIND = "current"
    _METRIC_LABEL = "Current Cost"


class BestAlternativeRollupSensor(PeriodRollupSensor):
    _ROLLUP_KIND = "best_alt"
    _METRIC_LABEL = "Best Alternative Cost"


class SavingsRollupSensor(PeriodRollupSensor):
    _ROLLUP_KIND = "savings"
    _METRIC_LABEL = "Savings"
```

**Registration (in `async_setup_entry`, after the Phase 3.1 sensor block):**
```python
# Phase 3.3 — rolling-window rollups
for window in ("today", "week", "month", "3month", "year"):
    entities.append(CurrentCostRollupSensor(coordinator, entry, window))
    entities.append(BestAlternativeRollupSensor(coordinator, entry, window))
    entities.append(SavingsRollupSensor(coordinator, entry, window))
```

**Tests:** 3 smoke tests in `tests/test_review_improvements.py`:
- `test_rollup_sensor_native_value_uses_filter_and_sum`
- `test_rollup_sensor_returns_none_when_history_empty`
- `test_savings_sensor_returns_none_when_alt_data_missing`

**Surprise risk — `last_reset`:** the existing `SavingTodaySensor`
(`sensor.py:163-183`) sets `last_reset = midnight`. For ROLLING windows
(7-day, 30-day), `last_reset` is not meaningful — leave UNSET (HA tolerates
this on `TOTAL` state class). Only the `today` rollups should set `last_reset`
to midnight; DON'T inherit a global `last_reset` from the base class.

**REVISIT — `today` vs existing `saving_today`:** the new
`sensor.pricehawk_savings_today` overlaps semantically with the existing
`sensor.pricehawk_saving_today` (`saving_today` = realtime intraday delta,
`savings_today` = end-of-day rollup from history). Different sensors,
different math, both valid. Document the distinction in the strings.json
description so users don't think one is a typo of the other.

#### Commit 3.3/3 — strings.json + tests/test_rollup.py wiring + dashboard exposure stub
**Touches:**
- `custom_components/pricehawk/strings.json` (add 15 sensor names)
- `custom_components/pricehawk/translations/en.json` (same 15)
- `tests/test_rollup.py` (already created in 3.3/1 — this commit just
  finalises any integration tests that need the sensor classes registered)

### 3.2 Phase 3.3 risk assessment

**Most likely surprises:**
1. **HA recorder warning on monetary sensors with `state_class=TOTAL`
   that change without a `last_reset`.** Rolling windows fluctuate
   downward as old days drop out — HA may flag this in logs. Solution:
   if logs get noisy, switch state_class to `MEASUREMENT` for non-today
   rollups. Test on Ryan's HA first.
2. **15 sensors × 30s tick × `filter_window` over 365 rows = 450 list
   walks per tick.** Trivial (<5ms), but if `daily_cost_history` grows
   beyond the 180-cap (it shouldn't, but the cap is enforced in two
   different spots and could drift), we'd see slowdown. Audit cap
   enforcement at `coordinator.py:642` AND in `backfill_daily_cost_history`.
3. **Sparse data** — most users will have <30 days of history at first
   run. Sensors return `None` (unavailable) for `month`, `3month`, `year`
   until they accrue. Dashboard (3.5) should detect this and show
   "accruing... [n/365]" rather than blank.

**Rollback:** revert the 3 commits; 15 sensor entities disappear cleanly.
No persisted state changes.

**Success metrics:**
- After 7 days of operation on Ryan's HA: `sensor.pricehawk_savings_week`
  shows a non-null value matching manual `sum(daily savings)` calc.
- `pytest tests/test_rollup.py -v` ≥18 passing tests.

---

## 4. Phase 3.4 — Optional named comparator drill-in

**Goal:** Let the user pin ONE specific CDR plan as their "primary comparator"
that gets tick-by-tick streaming cost (not just daily rollup). Surfaces as
`sensor.pricehawk_named_comparator_cost_{today,week,month,3month,year}`.

**Files touched (2 commits):**
- Modified: `custom_components/pricehawk/config_flow.py` (~80 LOC delta — new OptionsFlow step)
- Modified: `custom_components/pricehawk/coordinator.py` (~50 LOC delta — register named provider + persistence)
- Modified: `custom_components/pricehawk/const.py` (~5 LOC — CONF_NAMED_COMPARATOR_PLAN_ID const)
- Modified: `custom_components/pricehawk/sensor.py` (~30 LOC — 5 rollup sensor registrations under new key)
- Modified: `tests/test_config_flow_phase_3.py` (~80 LOC — new step tests)
- Modified: `tests/test_coordinator_helpers.py` (~50 LOC — named provider lifecycle)

**Total LOC delta:** ~+150.

### 4.1 Commit breakdown

#### Commit 3.4/1 — OptionsFlow `named_comparator` step + coordinator wiring
**Touches:**
- `custom_components/pricehawk/config_flow.py`
- `custom_components/pricehawk/coordinator.py`
- `custom_components/pricehawk/const.py`

**OptionsFlow step (added to `EnergyCompareOptionsFlow` menu at
`config_flow.py:1879`):**
```python
return self.async_show_menu(
    step_id="init",
    menu_options=[
        "comparators",
        "named_comparator",   # NEW
        "amber_api_key",
        # ...
    ],
)


async def async_step_named_comparator(
    self, user_input: dict[str, Any] | None = None
) -> config_entries.ConfigFlowResult:
    """Pin one CDR plan from the current ranked list as a primary comparator.

    Pulls the user's current ranked alternatives from the coordinator
    (no fresh registry fetch — that's what daily ranking is for) and
    presents them as a dropdown. Choosing 'None' clears any pin.

    The chosen plan body is stored in options as CONF_NAMED_COMPARATOR_PLAN
    (full PlanDetailV2 data — needed by CdrPlanProvider). Storage size
    is bounded (~15 KB per plan) — acceptable for one pinned plan.
    """
    # Pull ranked alternatives from coordinator data
    coordinator = self.hass.data[DOMAIN].get(self.config_entry.entry_id)
    alternatives = []
    if coordinator and coordinator.data:
        alternatives = coordinator.data.get("ranked_alternatives", [])
    if not alternatives:
        # No ranking results yet — surface a friendly message
        return self.async_abort(reason="no_ranked_alternatives")

    if user_input is not None:
        plan_id = user_input.get(CONF_NAMED_COMPARATOR_PLAN_ID)
        new_opts = dict(self.config_entry.options)
        if plan_id == "__none__":
            new_opts.pop(CONF_NAMED_COMPARATOR_PLAN_ID, None)
            new_opts.pop(CONF_NAMED_COMPARATOR_PLAN, None)
        else:
            # Find the FULL plan body (alternatives are summarized;
            # we need the full body for evaluator). Pull from coord cache.
            full_plan = coordinator._ranking_plan_cache.get(plan_id)
            if not full_plan:
                return self.async_abort(reason="plan_not_in_cache")
            new_opts[CONF_NAMED_COMPARATOR_PLAN_ID] = plan_id
            new_opts[CONF_NAMED_COMPARATOR_PLAN] = full_plan
        return self.async_create_entry(title="", data=new_opts)

    options = [{"value": "__none__", "label": "(clear pin)"}]
    for alt in alternatives:
        options.append({
            "value": alt["plan_id"],
            "label": f"{alt['brand']} — {alt['display_name']}",
        })
    return self.async_show_form(
        step_id="named_comparator",
        data_schema=vol.Schema({
            vol.Required(
                CONF_NAMED_COMPARATOR_PLAN_ID,
                default=self.config_entry.options.get(
                    CONF_NAMED_COMPARATOR_PLAN_ID, "__none__",
                ),
            ): SelectSelector(SelectSelectorConfig(options=options)),
        }),
    )
```

**Coordinator wiring (additions to `__init__` and `rebuild_engine`):**
```python
# coordinator.py __init__ — after existing provider construction
self._named_comparator: CdrPlanProvider | None = None
named_plan = entry.options.get(CONF_NAMED_COMPARATOR_PLAN)
if named_plan:
    self._named_comparator = CdrPlanProvider(
        named_plan, entry_options=dict(entry.options),
    )
    self._providers["named"] = self._named_comparator
```

**Same addition in `rebuild_engine` (`coordinator.py:1288-1328`).**

**No new tick path** — the named comparator is already in `self._providers`
so the existing tick loop (`coordinator.py:682-683`) updates it. The
existing daily rollover (`coordinator.py:617-665`) already iterates
`self._providers` to populate `daily_cost_history` rows, so the `"named"`
key appears automatically.

**Lock interaction with ranking lock:** None. The named comparator updates
on every 30s coordinator tick (same path as other providers). The ranking
lock only serialises ranking-job runs. No new contention.

**REVISIT — what if the user-pinned plan disappears from the ranked list
two weeks later (rate changes pushed it out of top-K)?** Named pin
persists independently — it's stored in options, not derived from
ranking. Backfill still includes it via `_build_backfill_plan_set`.
This is correct: the user pinned it deliberately, so we keep showing it
even if it's no longer "cheap".

#### Commit 3.4/2 — Named-comparator rollup sensors
**Touches:**
- `custom_components/pricehawk/sensor.py`

**What it does:**
Adds 5 sensors using a new subclass that reads the `"named"` key from
`daily_cost_history`:

```python
class NamedComparatorRollupSensor(PeriodRollupSensor):
    _ROLLUP_KIND = "named"
    _METRIC_LABEL = "Named Comparator Cost"

    @property
    def native_value(self) -> float | None:
        from .cdr.rollup import filter_window, sum_window
        history = self.coordinator.data.get("daily_cost_history") or []
        rows = filter_window(history, self._window)
        if not rows:
            return None
        value, _ = sum_window(rows, "named")
        return value


# In async_setup_entry, AFTER existing named-provider check:
named_present = "named" in getattr(coordinator, "_providers", {})
if named_present:
    for window in ("today", "week", "month", "3month", "year"):
        entities.append(NamedComparatorRollupSensor(coordinator, entry, window))
```

**Tests:**
- Add `test_named_comparator_options_step_lists_ranked_alternatives` to
  `test_config_flow_phase_3.py`.
- Add `test_named_comparator_options_step_aborts_without_alternatives`.
- Add `test_named_comparator_clear_pin_removes_option`.
- Add `test_named_comparator_persists_full_plan_body_for_evaluator` —
  asserts CONF_NAMED_COMPARATOR_PLAN gets the full body, not a summary.
- Add coordinator test: `test_named_comparator_provider_registered_when_plan_present`.

### 4.2 Phase 3.4 risk assessment

**Most likely surprises:**
1. **The dropdown options come from `coordinator.data` which is
   populated on the FIRST tick after `async_run_ranking_job` completes.**
   If the user enters the OptionsFlow seconds after first install before
   ranking finishes, `alternatives` is empty → `async_abort(reason=
   "no_ranked_alternatives")` fires. UX: surface a helpful message
   "Wait for the daily ranking job to complete or run it manually via
   the `pricehawk.rank_alternatives` service".
2. **Full plan body must come from `_ranking_plan_cache`, not from the
   ranked-alternatives sensor attributes.** The sensor attrs are
   summarised (`summarize_for_sensor` at `cdr/ranking.py:413-444`) and
   lack tariffPeriod data the evaluator needs. The OptionsFlow MUST
   reach into `coordinator._ranking_plan_cache[plan_id]` for the full
   PlanDetailV2 body.
3. **Plan cache is cleared on date rollover** (`coordinator.py:1255-1258`).
   If the user opens OptionsFlow at 00:30:01 right after the daily reset,
   the cache is empty until the morning's ranking run completes. Same
   abort path as #1.
4. **Persisted full plan body bloats config entry** — ~15 KB per pinned
   plan vs ~0.5 KB for summaries. One pin = fine. If we later add
   multi-pin, switch to storing only `plan_id` + refetching detail at
   coordinator construction.

**Rollback:** revert both commits. The `"named"` key disappears from
daily_cost_history (rollups display None / unavailable cleanly). No
state migration needed.

**Success metrics:**
- After pinning a plan on Ryan's HA: 5 new sensors appear within one
  reload; `sensor.pricehawk_named_comparator_cost_today` updates
  alongside `sensor.pricehawk_current_plan_cost_today` on the same
  30s tick cadence.
- `pytest tests/test_config_flow_phase_3.py -v` includes the 4 new step tests.

---

## 5. Phase 3.5 — Dashboard rewrite

**Goal:** Replace the 2447-LOC Amber-centric dashboard with a multi-plan
ranked view. Use `assets/dashboard-v3-apple.html` (1478 LOC) as the visual
seed.

**Files touched (3 commits):**
- Rewritten: `custom_components/pricehawk/www/dashboard.html`
- Modified: `custom_components/pricehawk/dashboard_config.py` (entity exposure metadata only)
- Modified: `assets/DESIGN.claude.md` (spec update for PriceHawk vs Claude marketing site)
- Modified: `CHANGELOG.md`

**Total LOC delta:** UI-only, net ~-500 (smaller dashboard, cleaner state).

### 5.1 Layout (the executing model should follow this card hierarchy)

```text
┌────────────────────────────────────────────────────────────────┐
│ NAV BAR  [PriceHawk logo]                          [theme tog] │
├────────────────────────────────────────────────────────────────┤
│ HERO ROW                                                       │
│  ┌─────────────────┐  ┌──────────────────────────────────────┐ │
│  │ CURRENT COST    │  │ SAVINGS THIS MONTH                   │ │
│  │ This month      │  │ $XX.XX  (vs best alternative)        │ │
│  │ $XX.XX          │  │ alt: <Brand> <Plan Name>             │ │
│  │ on <Plan Name>  │  │ projected annual: $XXX               │ │
│  └─────────────────┘  └──────────────────────────────────────┘ │
├────────────────────────────────────────────────────────────────┤
│ PERIOD TABS  [Today][Week][Month*][3 Month][Year]              │
│  (* = active; switches data binding for all rollup cards)      │
├────────────────────────────────────────────────────────────────┤
│ RANKED ALTERNATIVES (top 5 visible, expand for 6-20)           │
│  ┌───┬───────────────────┬───────────┬──────────┬───────────┐  │
│  │ # │ Plan              │ Peak rate │ Supply   │ Saving    │  │
│  ├───┼───────────────────┼───────────┼──────────┼───────────┤  │
│  │ 1 │ AGL Value Saver   │ 28.2c     │ 110c/d   │ +$45/mo   │  │
│  │ 2 │ Origin Predictable│ 29.1c     │ 105c/d   │ +$38/mo   │  │
│  │ … │                   │           │          │           │  │
│  └───┴───────────────────┴───────────┴──────────┴───────────┘  │
│  [Click row → drill-in card slides up below]                   │
├────────────────────────────────────────────────────────────────┤
│ DRILL-IN CARD (collapsed by default; shows when row clicked)   │
│  Plan: AGL Value Saver — full breakdown                        │
│  - Peak / Off-peak / Shoulder windows                          │
│  - Incentives detected: ZEROHERO, Super Export                 │
│  - Daily supply + connection fees                              │
│  - Pin as Named Comparator [button → opens HA options flow]    │
├────────────────────────────────────────────────────────────────┤
│ DATA HEALTH FOOTER                                             │
│  Backfill: complete, 28 days loaded (HA recorder retention 30d)│
│  Last ranking: 2026-05-17 00:30, 18 alts evaluated             │
│  Next ranking: 2026-05-18 00:30                                │
└────────────────────────────────────────────────────────────────┘
```

### 5.2 Commit breakdown

#### Commit 3.5/1 — Strip Amber-centric chrome + scaffold new layout
**Touches:**
- `custom_components/pricehawk/www/dashboard.html` (full rewrite, ~1200 LOC)

**What it does:**
- Carries over from `dashboard-v3-apple.html`: noise + ambient bg,
  `--bg-base/--bg-card/--text-*` token system, Outfit + IBM Plex Mono
  type, dark-default theme with light-toggle.
- Replaces Amber-specific colour tokens (`--amber-primary`,
  `--globird-primary`) with semantic ones (`--accent-positive`,
  `--accent-negative`, `--accent-neutral`). Aligns with the "no
  per-provider colours" pivot from Phase 3.0.
- WebSocket URL detection MUST use `location.protocol` (AEGIS rule from
  CLAUDE.md). Token MUST come from URL params or postMessage. Keep
  identical to current `dashboard.html` pattern.

#### Commit 3.5/2 — Wire entity reads (rollup sensors, ranked alternatives, backfill status)
**Touches:**
- `custom_components/pricehawk/www/dashboard.html`

**What it does:**
- Subscribes via the existing WebSocket pattern to:
  - `sensor.pricehawk_current_cost_*` (5 entities, swap on tab click)
  - `sensor.pricehawk_best_alternative_cost_*` (5)
  - `sensor.pricehawk_savings_*` (5)
  - `sensor.pricehawk_named_comparator_cost_*` (5, hidden if not pinned)
  - `sensor.pricehawk_ranked_alternatives` (1, reads `alternatives` attr)
  - `sensor.pricehawk_backfill_status` (1, reads `days_loaded`, `last_run`)
  - `sensor.pricehawk_last_updated` (1, reads `daily_cost_history` for chart)
- Renders the ranked-alternatives table from
  `attributes.alternatives` of the `ranked_alternatives` sensor.
  Click handler opens drill-in card with the row's plan data.
- Drill-in "Pin as Named Comparator" button navigates to the HA
  OptionsFlow URL (`/config/integrations/integration/pricehawk` →
  pre-populates the named_comparator step).

#### Commit 3.5/3 — Design spec update + dashboard_config exposure
**Touches:**
- `assets/DESIGN.claude.md` — add a "PriceHawk Dashboard" section noting
  divergence from the Claude marketing-site spec (PriceHawk is a dark
  data-dashboard, not a warm-canvas editorial site). Keep the Claude
  spec intact for reference; PriceHawk inherits only the typography
  rationale (humanist sans + monospace numerics).
- `custom_components/pricehawk/dashboard_config.py` — confirm cache-busting
  query param still works after rewrite; no functional change to
  panel registration.
- `CHANGELOG.md` — note dashboard rewrite under [Unreleased].

### 5.3 Phase 3.5 risk assessment

**Most likely surprises:**
1. **Iframe sandbox CSP** at `dashboard.html:6` allows `'unsafe-inline'`
   for scripts/styles — fine. But the `connect-src` directive
   currently allows insecure and secure WebSocket localhost schemes plus secure
   Nabu Casa WebSocket hosts.
   If Ryan's HA is on `homeassistant.local` (not localhost), confirm the
   `connect-src` covers that. Likely needs `wss://*.local` added.
2. **Drill-in "Pin as Named Comparator" needs a way to deep-link into
   the HA OptionsFlow step.** HA doesn't support arbitrary step deep-linking
   in the integrations UI; the best we can do is navigate to the
   integration's config page and let the user click "Configure → Pin".
   **REVISIT:** if this UX is bad, add a custom HA service
   `pricehawk.set_named_comparator` (commit 3.4/3) that takes a plan_id
   and writes options directly. Dashboard then calls the service via WS.
3. **Empty-state UI** for first-run users with zero history. Show:
   "Backfill running... (3 days loaded of estimated 10)". The
   `backfill_status` sensor's `days_loaded` attribute drives this.
4. **Ranked alternatives sensor attribute size** — up to 20 plans × ~7
   keys per plan = ~1.5 KB. Under HA's 2 KB warning threshold.

**Rollback:** revert all 3 commits. Old dashboard.html is in git history.
The cache-busting epoch in `dashboard_config.py:106` forces browsers to
reload after revert.

**Success metrics:**
- Dashboard renders on Ryan's HA with no console errors.
- Period tabs swap rollup values within 300ms.
- Drill-in card shows full plan body fields (peak, off-peak, shoulder
  windows; incentives; supply charge).

---

## 6. Cross-cutting test strategy

### 6.1 What's already mockable via `tests/conftest.py`
- `homeassistant.*` module tree (top-level + helpers + util.dt + core)
- `ConfigEntryNotReady` class
- `CALLBACK_TYPE = type(None)` for type-annotation imports

### 6.2 What MUST be added to `tests/conftest.py` for these phases
```python
# Add to _mods dict for Phase 3.2 backfill tests:
_mods["homeassistant.components"] = _MockModule()
_mods["homeassistant.components.recorder"] = _MockModule()
_mods["homeassistant.components.recorder.history"] = _MockModule()
_mods["homeassistant"].components = _mods["homeassistant.components"]
_mods["homeassistant.components"].recorder = _mods["homeassistant.components.recorder"]
_mods["homeassistant.components.recorder"].history = _mods["homeassistant.components.recorder.history"]
```
This unblocks importing `coordinator.py` and `backfill.py` in tests
without exploding on the `recorder.history` import path. Per-test
mocks then patch `state_changes_during_period` to return fixture data.

### 6.3 Unit vs integration breakdown
| Phase | Unit (pure-logic) | Integration (HA mocks needed) |
|---|---|---|
| 3.2 | history_replay (14 tests) + backfill core (6) | coordinator `_build_backfill_plan_set` (2), service-call wiring (2) |
| 3.3 | rollup (18) | sensor `native_value` smoke (3) |
| 3.4 | none — all integration | OptionsFlow step (4), provider lifecycle (2) |
| 3.5 | none | manual on Ryan's HA + JS console |

### 6.4 Pattern to follow for new pure-logic modules
Mirror `tests/test_coordinator_ranking.py` exactly:
- Module-level class per public function (`TestStatesToHalfHourSlots`, etc).
- `unittest.mock.AsyncMock` + `patch(...)` for async dependencies.
- `asyncio.run(...)` to drive async coroutines from sync test bodies.
- No pytest-asyncio fixtures — keep it stdlib.

---

## 7. Pre-merge checklist per phase

Run BEFORE pushing any commit:
```bash
ruff check .                            && \
mypy . --ignore-missing-imports         && \
bandit -r . -ll                          && \
pytest --tb=short -q
```
(matches the CLAUDE.md "Pre-Push Local Checks" rule).

After push, monitor:
1. GitHub Actions: `python-ci.yml`, `pr-checks.yml`, `docs-check.yml`,
   `security-scan.yml` must all pass.
2. CodeRabbit walkthrough; address all CRITICAL + MAJOR findings inline,
   file LOW → issues per CLAUDE.md hard rule #3.

---

## 8. Anticipated CodeRabbit findings + pre-emptive fixes

Based on `.coderabbit.yaml` recipes and the past PR history visible in
`test_review_improvements.py`:

### 8.1 `scrub-secrets` recipe
- Will scan all new files for hardcoded tokens/API keys.
- **Pre-emptive:** no secrets in new code. The named-comparator OptionsFlow
  doesn't add credentials. Backfill reuses existing recorder credentials
  (none). Dashboard token is already redacted from logs
  (`dashboard_config.py:133-135`).

### 8.2 `no-hardcoded-rates` recipe
- Will flag any c/kWh literal in source.
- **Pre-emptive:** `WINDOW_DAYS` integers are days, not rates — fine.
  No tariff rate literals in any new module.

### 8.3 `amber-api-limits` recipe
- Will flag loops hitting api.amber.com.au.
- **Pre-emptive:** Phase 3.2 deletes the Amber-API backfill call entirely.
  Nothing in 3.3 / 3.4 / 3.5 touches Amber.

### 8.4 `dashboard-protocol-safety` recipe
- Will flag a hardcoded insecure WebSocket scheme literal (the
  `ws-//` prefix, defanged here to avoid tripping security scans) in
  dashboard.html.
- **Pre-emptive:** keep `location.protocol`-based WS URL construction
  from the existing dashboard. Token via URL param + postMessage only.

### 8.5 Generic `path: "**/*.py"` recipe
Will flag:
- Bare `except:` → use `except Exception` with logger.exception, mirror
  `cdr/ranking.py:391-400` pattern.
- Missing type hints on public functions → all API signatures in this
  doc are typed.
- `from datetime import date` shadowing builtins → use `from datetime
  import date as _date` if conflict arises (unlikely here).
- Late-imported modules inside functions (e.g. `from .backfill import …`
  inside `async_run_backfill`) — CR may flag as "should be top-level".
  Defensible: avoids HA recorder import at module load (matches existing
  pattern at `coordinator.py:1050-1054` and `__init__.py:106-110`).
  Annotate with `# noqa: PLC0415` comment + one-line justification.
- Decimal vs float mixing in `cdr/rollup.py`. Use float throughout
  (history rows are already floats; sums fit in IEEE-754 for 365-day
  AUD ranges without precision loss).

### 8.6 Linus-style review reactions
Patterns Ryan's local reviewer typically catches:
- **Over-abstraction.** Don't create a `RollupStrategy` interface for 3
  subclasses. The inline `if self._ROLLUP_KIND == "current"` dispatch in
  the base class is fine for 3 kinds.
- **Premature performance.** No need to memoise `filter_window` results
  per tick — 365-row scans are negligible.
- **Defensive try/except around things that can't fail.** Don't wrap
  pure arithmetic in try blocks. Only wrap I/O and evaluator calls.
- **Logging at INFO level for per-tick events.** Backfill INFOs OK,
  per-day-row writes DEBUG only.

---

## 9. Open questions deferred (NOT blocking implementation)

Marked here so we don't re-litigate during execution:

1. **REVISIT** — calendar month vs rolling 30 days for rollup windows.
   Locked: rolling. Easy to flip later by changing `WINDOW_DAYS` and
   `filter_window` semantics.
2. **REVISIT** — separate `_backfill_lock` vs sharing `_ranking_lock`.
   Locked: shared. Split if contention observed.
3. **REVISIT** — flat `alt_<planid>` keys vs nested `"alts": {...}` in
   `daily_cost_history`. Locked: flat. Refactor if recorder attribute
   sizes hurt.
4. **REVISIT** — dashboard "Pin as Named Comparator" UX. Locked: deep-link
   to HA Configure page. Add a `pricehawk.set_named_comparator` service
   only if the UX testing on Ryan's HA shows the deep-link is too slow.

---

## 10. File-path summary (for the executing model)

All paths relative to `<REPO_ROOT>`:

**New files:**
- `custom_components/pricehawk/cdr/history_replay.py` (3.2/1)
- `custom_components/pricehawk/cdr/rollup.py` (3.3/1)
- `tests/test_history_replay.py` (3.2/1)
- `tests/test_rollup.py` (3.3/1)

**Rewritten files:**
- `custom_components/pricehawk/backfill.py` (3.2/2)
- `custom_components/pricehawk/www/dashboard.html` (3.5/1-2)
- `tests/test_backfill.py` (3.2/2)

**Modified files:**
- `custom_components/pricehawk/coordinator.py` (3.2/3, 3.4/1)
- `custom_components/pricehawk/__init__.py` (3.2/3, 3.2/4)
- `custom_components/pricehawk/sensor.py` (3.2/4, 3.3/2, 3.4/2)
- `custom_components/pricehawk/config_flow.py` (3.4/1)
- `custom_components/pricehawk/const.py` (3.4/1)
- `custom_components/pricehawk/services.yaml` (3.2/4)
- `custom_components/pricehawk/dashboard_config.py` (3.5/3)
- `custom_components/pricehawk/strings.json` (3.3/3)
- `custom_components/pricehawk/translations/en.json` (3.3/3)
- `tests/conftest.py` (3.2/2 — recorder mocks)
- `tests/test_config_flow_phase_3.py` (3.4/1)
- `tests/test_coordinator_helpers.py` (3.2/3, 3.4/1)
- `tests/test_review_improvements.py` (3.2/4, 3.3/2)
- `assets/DESIGN.claude.md` (3.5/3)
- `CHANGELOG.md` (every phase under [Unreleased])

**Total estimated commits:** 12 (3.2: 4 + 3.3: 3 + 3.4: 2 + 3.5: 3 = within roadmap's 10-14 range).
