# Sensors

PriceHawk exposes ~30 sensor entities under the `sensor.pricehawk_*` prefix.
All entities derive their slug from the entity name using HA's standard rules — note that a digit-letter boundary inserts an underscore (`3month` becomes `3_month`).

## Core sensors

| Entity | What it reports | Unit |
|---|---|---|
| `sensor.pricehawk_best_provider` | Winner provider ID right now | str |
| `sensor.pricehawk_cheapest_today` | Cheapest provider for today | str |
| `sensor.pricehawk_best_rate` | Lowest live rate across providers | c/kWh |
| `sensor.pricehawk_saving_today` | Savings vs winner so far today | AUD |
| `sensor.pricehawk_saving_month` | Cumulative savings this month | AUD |
| `sensor.pricehawk_metrics_won` | Days won per provider, MTD | dict (attrs) |
| `sensor.pricehawk_last_updated` | Last coordinator tick | timestamp |
| `sensor.pricehawk_winner_explanation` | "Why X won today" bullet list | str + attrs |
| `sensor.pricehawk_ranked_alternatives` | Top-K ranked alternatives | list (attrs) |
| `sensor.pricehawk_backfill_status` | Backfill job state | str |

## Window-rollup sensors (Phase 3)

For each window in `{today, week, month, 3_month, year}` PriceHawk exposes four sensors:

| Pattern | What it reports |
|---|---|
| `sensor.pricehawk_current_cost_<window>` | Your current plan's cost for the window |
| `sensor.pricehawk_best_alternative_cost_<window>` | Cheapest ranked alternative for the window |
| `sensor.pricehawk_savings_<window>` | `current − best_alternative` for the window |
| `sensor.pricehawk_named_comparator_cost_<window>` | Your pinned comparator's cost (when set) |

All four have `device_class: monetary` and `state_class: total`.
Attributes include `days_in_window` (gates partial-window display in the dashboard) and `best_alternative_plan_id` (on the best-alternative + savings sensors).

Examples:

```
sensor.pricehawk_current_cost_today
sensor.pricehawk_best_alternative_cost_week
sensor.pricehawk_savings_3_month
sensor.pricehawk_named_comparator_cost_year
```

## Amber-only sensors

Active only when Amber is the current provider.

| Entity | What it reports |
|---|---|
| `sensor.pricehawk_amber_daily_charges` | Today's network + market + retail charges |
| `sensor.pricehawk_amber_forecast_peak` | Forecast peak in next 24h |
| `sensor.pricehawk_amber_forecast_dip` | Forecast cheapest window in next 24h |
| `sensor.pricehawk_amber_forecast_average` | Forecast 24h average |

## Provider-level sensors

For each enabled provider:

| Pattern | What it reports |
|---|---|
| `sensor.pricehawk_<provider>_rate` | Live import rate (c/kWh) |
| `sensor.pricehawk_<provider>_export_rate` | Live FiT rate (c/kWh, when applicable) |
| `sensor.pricehawk_<provider>_cost_today` | Net daily cost so far |

`<provider>` slugs: `amber`, `globird`, `flow_power`, `localvolts`.

## Current plan sensors

| Entity | What it reports |
|---|---|
| `sensor.pricehawk_current_plan_cost_today` | Your active plan's running daily cost |
| `sensor.pricehawk_current_plan_import_cost` | Today's import cost component |
| `sensor.pricehawk_current_plan_daily_supply` | Today's daily supply charge accrual |

## Incentive trackers

| Entity | What it reports |
|---|---|
| `sensor.pricehawk_zerohero_status` | ZEROHERO daily credit eligibility state |

## Attribute conventions

- All monetary sensors use `device_class: monetary` + `state_class: total` so the Energy dashboard can graph them.
- Rate sensors use `device_class: monetary` + `state_class: measurement`.
- Forecast sensors expose `forecast_at` ISO timestamp + `cents_per_kwh` numeric attribute.
- The ranked-alternatives sensor's `alternatives` attribute is a list of `{plan_id, retailer, score, cost_*}` dicts — read it with template sensors or query directly in automations.

## Naming consistency

All entities are auto-derived from `_attr_name` strings starting with `PriceHawk `, which HA slugifies to `pricehawk_…`.
If you rename an entity in the UI, you take responsibility for keeping the dashboard's `ENTITY[…]` lookup table aligned.
The bundled dashboard always uses the default slugs.
