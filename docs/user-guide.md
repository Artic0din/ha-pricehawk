# PriceHawk — User Guide

A detailed walkthrough of every PriceHawk feature, from install to advanced automations.

For internal architecture see [architecture.md](architecture.md).
For per-entity reference see [sensors.md](sensors.md).
For setup-flow specifics see [configuration.md](configuration.md).

---

## What is PriceHawk?

PriceHawk is a Home Assistant integration that compares the **real dollar cost** of your electricity across multiple retailers using your actual consumption data.

Most "compare energy plans" tools ask for a single average usage number and spit out a rough quarterly bill.
PriceHawk does the opposite.
It reads your existing 30-second grid-power sensor, evaluates every interval against multiple retailer plans in parallel, and accumulates real cost-per-day per provider.
After a few days you can see exactly which retailer would have charged you less, with no estimation, no marketing maths, and no committing to a new plan to find out.

It also works as a live cost-of-power readout — useful for solar/battery households on dynamic plans like Amber where the rate changes every 5 minutes.

## Who is this for?

Australian households with:

- A working Home Assistant install with an Energy Dashboard configured.
- A grid-power sensor (positive = importing from grid, negative = exporting).
  Typically from a Tesla Powerwall, Fronius/Sungrow inverter, Shelly EM, Emporia Vue, or a P1-port reader.
- An interest in switching retailers or moving to a dynamic-pricing plan.

It's especially useful if you already have solar or a battery, because most retailer quote tools wildly under- or over-estimate export earnings.

## Quick start — 5 minutes

1. In HA, open **Settings → Devices & Services → Add Integration**.
2. Search for **PriceHawk** and follow the wizard.
3. Pick your current retailer (Amber, GloBird, Flow Power, LocalVolts, "Dynamic Wholesale Tariff", or "Other — no API").
4. Provide credentials if asked (only Amber, LocalVolts, and DWT-OE need an API key).
5. Select your grid-power sensor when prompted.
6. Done.
   PriceHawk starts accumulating cost data on the next 30-second tick.

After a day, open the Energy Dashboard's cost picker — `sensor.pricehawk_today_cost` will be selectable as your cost source.
After a week, open the PriceHawk sidebar entry to see ranked alternatives.

## Provider choice — decision tree

PriceHawk supports seven "primary provider" types at setup.
Pick the one matching your actual retailer.
Comparators (the ones you're *not* on) can be added later from the Options page.

| Primary provider                              | API key needed? | Best for                                                                              |
| --------------------------------------------- | --------------- | ------------------------------------------------------------------------------------- |
| **Amber Electric**                            | Yes             | Customers on Amber's wholesale-pass-through plan. Live spot pricing every 5 min.      |
| **GloBird Energy** (via CDR)                  | No              | Most GloBird customers. Uses the public CDR plan envelope (no GloBird API key exists). |
| **Flow Power**                                | No              | Flow Power Premium customers. Uses AEMO NEMWeb DISPATCH wholesale + Flow's margin.    |
| **LocalVolts**                                | Yes             | LocalVolts peer-to-peer customers. Needs API key + partner ID + NMI.                  |
| **Dynamic Wholesale Tariff — OpenElectricity** | Yes             | Anyone curious what a wholesale-pass-through plan would cost. Uses OpenElectricity v4 SDK. |
| **Dynamic Wholesale Tariff — AEMO Direct**    | No              | Same as above but no API key — uses public NEMWeb DISPATCH directly. NEM-only (no WA). |
| **Other (CDR plan)**                          | No              | Any retailer with a CDR plan envelope. Pick the plan from the auto-fetched dropdown.  |

If you're not sure which DWT flavour to pick, choose **OpenElectricity** — it's faster, supports WEM, and rate-limit handling is cleaner.

## Setup walkthrough by provider

### Amber Electric

1. Get an API key from [Amber's developer page](https://app.amber.com.au/developers/).
2. Paste it into the setup wizard.
3. PriceHawk auto-fetches your site ID — pick the right one if you have multiple.
4. Enter your network daily charge and subscription fee (one-time, found on your Amber bill — values vary by network like Ausgrid, AusNet, etc.).
5. Done.

### GloBird (CDR path)

1. Pick "GloBird Energy" in the wizard.
2. PriceHawk auto-fetches the current GloBird PRD catalogue from the public CDR endpoint.
3. Select your plan from the dropdown (ZEROHERO, FOUR4FREE, GloSave, GloBoost, etc.).
4. Done — no API key, no manual rates.

If your plan isn't in the dropdown, the GloBird CDR endpoint may have an outage or the plan may no longer be on offer.
Use the retry prompt to refetch, or pick a different CDR plan that matches your tariff structure — manual tariff entry was removed in Phase 3.0f, every entry must reference a real CDR plan envelope.

### Dynamic Wholesale Tariff — OpenElectricity

1. Get a free API key from [OpenElectricity](https://platform.openelectricity.org.au/).
2. Paste into wizard, pick your NEM/WEM region (NSW1, VIC1, QLD1, SA1, TAS1, WEM).
3. Enter your daily supply charge (varies by network).
4. Done — PriceHawk pulls a fresh wholesale price every ~4 minutes.

### Dynamic Wholesale Tariff — AEMO Direct

Same as OpenElectricity but no API key.
Limitations:
- NEM only.
  WA users must use OpenElectricity.
- 5-minute settlement cadence (vs OpenElectricity's 5-min interval data).

### LocalVolts

1. Get API credentials from your LocalVolts dashboard (key + partner ID + NMI).
2. Paste all three into wizard.
3. Set guard rails: buy ceiling (cents/kWh max you'd pay) and sell floor (cents/kWh min you'd accept).
4. Done.

## Pricing modes (Comparators page)

Once installed, you can enable **comparator** providers via **Options → Comparators**.
A comparator is a "what if I was on this retailer" parallel simulation.

Each comparator (Amber, Flow Power, LocalVolts) has three modes:

- **off** — disabled.
  No cost accumulation, no API calls.
- **live_api** — full live polling.
  Needs the relevant API key/credentials.
  Most accurate for dynamic providers.
- **static_prd** — derive rates from a stored CDR plan envelope (no API needed).
  Useful for "what if I was on retailer X's static plan" — but you have to seed the plan first (see [configuration.md](configuration.md)).

Flow Power's `static_prd` is currently deferred — picking it falls back to `live_api` with a log warning.

## Dashboards

### Sidebar panel (`/pricehawk`)

A purpose-built Lit panel registered with HA's `panel_custom` mechanism.
Authenticates via your HA session — no token in URL.
Shows: today's chosen-plan cost, comparator costs, savings, best provider.
Full visual UI port from the legacy iframe dashboard is staged for a follow-up release.

The legacy iframe panel at `/pricehawk-dashboard` continues to work during the migration.

### Lovelace card (`pricehawk-cost-card`)

A compact custom card showing today's chosen-plan cost + an optional savings line.
Auto-registered as a Lovelace resource on entry setup — appears in the "Add Card" picker without manual Resources setup.

Storage-mode Lovelace gets the auto-register.
YAML-mode users see a log line on startup with the resource URL to add manually.

### Energy Dashboard integration

`sensor.pricehawk_today_cost` is set up to qualify for HA's Energy Dashboard cost picker:

- `device_class: MONETARY`
- `unit_of_measurement: AUD`
- `state_class: TOTAL`
- `last_reset` resets at local midnight

Open **Settings → Dashboards → Energy → Add Consumption → cost (entity)** and pick `sensor.pricehawk_today_cost`.
The `unique_id` is provider-independent — your dashboard pick survives if you later swap plans or migrate from a CDR entry to a DWT entry.

### External statistics dual-write

The coordinator writes daily provider costs to HA's external statistics on every midnight rollover, in addition to the existing JSON store.
A one-shot backfill on first setup converts your existing daily-cost history into stats entries.

This unlocks long-term cost history in the Energy Dashboard — you can drill in to monthly/yearly cost-by-provider views.

Negative-cost days (export-heavy with high FiT) produce a small dip in the cumulative sum — HA tolerates this per their docs.

## Ranked alternatives — the "should I switch?" sensor

After 1-2 days of accumulated data, `sensor.pricehawk_ranked_alternatives` populates with the count of cheaper alternative plans found by the nightly ranking job (state is an integer 0..top_k).
The job scans both your current retailer and the big-4 competitor brands, filters by your state/distributor/postcode, and persists only the top-K cheaper plans.
The `alternatives` attribute is a sorted list of per-plan summaries (`plan_id`, `display_name`, `brand`, peak/supply in cents, cheap-rank `score`), ascending by score so `alternatives[0]` is the cheapest.
A `last_run` attribute carries the ISO timestamp of the most recent successful ranking pass.

Use this with the **Cheapest plan alert** blueprint (see below) to get a notification when a plan exists that would have saved you more than $X/week.

Pin one of these plans as a permanent comparator via **Options → Named Comparator** — useful if you want to track a specific competitor over time.

## Blueprints — 5 ready automations

PriceHawk ships with 5 HA automation blueprints in `custom_components/pricehawk/blueprints/automation/pricehawk/`.
Import via **Settings → Automations & Scenes → Blueprints → Import Blueprint** with the file URL, or drop into `<config>/blueprints/automation/pricehawk/`.

1. **`cheapest_plan_alert.yaml`** — notify when a retailer would have saved more than a threshold over 7 days.
2. **`cheapest_30min_window.yaml`** — fire actions when the cheapest 30-min window of the next 24 hours starts.
   Use for dishwasher, EV pre-conditioning, hot water boost.
3. **`pause_ev_on_spike.yaml`** — turn off a switch (EV charger, pool pump) when wholesale price crosses a high threshold.
   Hysteresis-aware to prevent flapping.
4. **`daily_7pm_summary.yaml`** — daily cost + savings + best-provider notification at 7pm.
5. **`wholesale_spike_alert.yaml`** — early warning when wholesale spot price crosses a threshold.

Each blueprint exposes only the inputs that vary (threshold, notification target, switch entity) — defaults handle the rest.

## Maintenance

### Reauth (key rotation)

When Amber, LocalVolts, or OpenElectricity (DWT-OE) rejects your API key (HTTP 401/403), PriceHawk raises `ConfigEntryAuthFailed` and HA shows the "Reconfigure" prompt in **Settings → Devices & Services**.
Click it, paste the new key, done.
Daily cost history is preserved through the reauth — only the rotated field changes.

API keys never appear in log messages, UI errors, or exception strings.

### Reconfigure (settings tweaks)

The "Reconfigure" button also opens a per-provider settings page for non-credential edits:

- **Amber**: network daily charge, subscription fee.
- **LocalVolts**: daily supply, buy ceiling, sell floor.
- **DWT-OE / DWT-AEMO**: daily supply charge.

Region swap is deliberately not supported — PriceHawk's entry unique-ID is region-derived, swapping would invalidate it.
To change region, remove + re-add the entry.

### Diagnostics download

The **Download diagnostics** button on the integration page returns a JSON snapshot of entry data + options + selected coordinator runtime state.
All API keys, HA tokens, and CDR plan envelopes are replaced with `**REDACTED**`.
A `_redaction_count` field confirms the redaction list is hitting the targets.

Attach this to any issue you file — it's safe to share publicly.

### Repairs platform

PriceHawk raises HA Repair notifications when the integration is in a degraded state:

- **`grid_sensor_unavailable`** — your configured grid-power sensor has returned `None` for 10 consecutive reads (5 min at the 30-second coordinator interval).
  Check the sensor in **Developer Tools**.
- **`ranking_stale`** — the nightly CDR plan ranking job hasn't completed in over 36 hours.
  Usually means the retailer's CDR endpoint is having an outage.
  Issue auto-clears when ranking completes.

Multi-entry safe: issue IDs are prefixed with the entry_id so two PriceHawk entries don't collide.

### HACS Silver compliance

PriceHawk is **Silver tier** on the HACS quality scale.
Manifest declares `quality_scale: "silver"`.
`quality_scale.yaml` documents every Bronze/Silver/Gold/Platinum rule with `done` / `exempt` / `todo` status — honest tickbox.

## FAQ

**Q: Why does PriceHawk need a grid-power sensor?**
Because everything else is derived from it.
Every 30 seconds, PriceHawk reads grid power, multiplies by the elapsed interval, multiplies by every provider's rate-at-that-instant, and accumulates the result.
Without grid power there's no cost.
If your inverter only exposes `kWh imported today` (cumulative), you need to derive an instantaneous-power sensor via the HA `derivative` integration first.

**Q: Why are my comparator costs zero for the first day?**
Because the comparator providers don't have a history to back-fill — they only start accumulating from the moment they're enabled.
Wait 24 hours.

**Q: Why does my Amber comparator show "static_prd" mode but no rates?**
Because you selected `static_prd` but no static plan envelope is stored.
Either pick a CDR plan in the Named Comparator step, or change back to `live_api`.
PriceHawk hides `static_prd` from the Comparators dropdown if no plan is stored — but a setup that pre-selected it before this guard was added may still show stale state.

**Q: Why did my new DWT install fail with `ConfigEntryNotReady`?**
This was a bug in v1.6.0-beta.1 and earlier — DWT setup fields weren't persisted to the final entry, so `_build_dwt_provider()` couldn't find them.
Fixed in v1.6.0-beta.2+.
If still hitting this on a recent version, file an issue with diagnostics download.

**Q: Will PriceHawk make any network calls when all providers are in `off` mode?**
The 30-second coordinator tick will not — `_async_update_data` is gated on `PRICING_MODE_LIVE_API` for each provider's poll path, so per-tick polling stops.
The nightly ranking job is separate: `async_setup_entry` schedules `coordinator.schedule_daily_ranking()` unconditionally at 00:30 local, and it fetches the public CDR registry + competitor plan catalogues regardless of comparator pricing mode.
To stop the ranking traffic too, unload the integration entry — the daily job is not user-toggleable. You can also trigger a manual run via the `pricehawk.rank_alternatives` service.

**Q: Does PriceHawk store my API keys in plain text?**
No.
All keys go through HA's config-entry encrypted storage — same as any other HA integration.
They never appear in logs, UI errors, exception strings, or diagnostics downloads.

**Q: Can I run multiple PriceHawk entries (e.g. for two properties)?**
Yes.
Each entry's sensors are namespaced by `entry_id`.
Repairs issues are also `entry_id`-prefixed so they don't collide.

**Q: What's the difference between v3.0 and v2?**
v2 was the foundation — config flow, CDR plan picker, ranking, dashboard.
v3.0 adds dynamic-wholesale-tariff comparisons (Amber-style wholesale plans modelled against your usage), HACS Silver compliance (reauth, reconfigure, diagnostics, repairs), the new Lit panel + Lovelace card + Energy Dashboard pickable cost sensor, and 5 ready-made automation blueprints.

## Where things live

```
custom_components/pricehawk/
├── __init__.py                — entry setup + teardown
├── manifest.json              — HACS metadata, quality_scale
├── config_flow.py             — install wizard + Options + reauth + reconfigure
├── coordinator.py             — the 30s tick that runs everything
├── data.py                    — PriceHawkData runtime dataclass
├── sensor.py                  — all the entities you see in HA
├── diagnostics.py             — JSON snapshot for issue reports
├── statistics.py              — Energy Dashboard external-stats writer
├── static_pricing.py          — TOU window evaluator shared with cdr/evaluator
├── const.py                   — every config key, every provider id
├── strings.json               — UI labels (mirrored to translations/en.json)
├── providers/
│   ├── amber.py
│   ├── cdr_plan.py
│   ├── flow_power.py
│   ├── localvolts.py
│   ├── dynamic_wholesale_tariff.py
│   ├── openelectricity.py     — OpenElectricity v4 SDK wrapper
│   └── nemweb.py              — AEMO DISPATCH fallback
├── blueprints/automation/pricehawk/
│   ├── cheapest_plan_alert.yaml
│   ├── cheapest_30min_window.yaml
│   ├── pause_ev_on_spike.yaml
│   ├── daily_7pm_summary.yaml
│   └── wholesale_spike_alert.yaml
└── www/
    ├── pricehawk-card.js      — Lovelace custom card
    └── pricehawk-panel.js     — Lit sidebar panel
```

## Getting help

- **GitHub issues** — [github.com/Artic0din/ha-pricehawk/issues](https://github.com/Artic0din/ha-pricehawk/issues).
  Attach a diagnostics download.
- **Troubleshooting reference** — [troubleshooting.md](troubleshooting.md).
- **Setup specifics** — [configuration.md](configuration.md).
- **Architecture deep-dive** — [architecture.md](architecture.md).
