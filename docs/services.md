# Services

PriceHawk registers three HA services under the `pricehawk.*` domain.
Call them from **Developer Tools → Services** or from automations.

## `pricehawk.rank_alternatives`

Runs the cheap-rank pipeline against your current retailer plus competitor retailers (AGL, Origin, EnergyAustralia, Red Energy).
Results are stored on the coordinator and surface via `sensor.pricehawk_ranked_alternatives`.

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `top_k` | int (1–100) | no | 20 | Number of cheapest alternatives to keep |

This service is also called automatically every night at 00:30 AEST.
Cheap-rank only — deep-rank-by-replay is invoked internally on the top-K results.

**Example:**

```yaml
service: pricehawk.rank_alternatives
data:
  top_k: 30
```

## `pricehawk.backfill_history`

Replays HA recorder grid-power history through your current plan and the top-K ranked alternatives, populating `daily_cost_history` for the last N days.

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `days` | int (1–90) | no | 30 | Days of history to recover |

Capped by HA recorder retention (typically 10 days unless `purge_keep_days` is raised in your `recorder:` config).
Status surfaces on `sensor.pricehawk_backfill_status`.

**Example:**

```yaml
service: pricehawk.backfill_history
data:
  days: 10
```

Use cases:

- Just installed PriceHawk and want immediate week/month figures
- Re-running after raising recorder retention
- Recovering after a config wipe

## `pricehawk.analyze_csv`

Compares Amber Electric usage data against your configured GloBird rates.
Called from the PriceHawk dashboard with pre-parsed CSV rows; you generally don't invoke it from automations.

| Field | Type | Required | Description |
|---|---|---|---|
| `rows` | list[dict] | yes | Parsed CSV rows. Each row: `{day, start_time, channel_type, price, usage, cost}` |

**Example:**

```yaml
service: pricehawk.analyze_csv
data:
  rows:
    - day: "2026-05-01"
      start_time: "00:00"
      channel_type: "import"
      price: 28.5
      usage: 0.45
      cost: 0.128
```

## When to call which

| Goal | Service |
|---|---|
| "Refresh the ranking now" | `rank_alternatives` |
| "Fill in week/month figures from history" | `backfill_history` |
| "Import an Amber CSV export and compare to GloBird" | `analyze_csv` (via dashboard wizard) |

## Failure modes

- `rank_alternatives` quietly no-ops if the CDR catalogue snapshot is stale and the network is unreachable; status shows `stale` in the sensor attrs.
- `backfill_history` short-circuits if the recorder window is shorter than `days`; the actual days replayed appear in `sensor.pricehawk_backfill_status`.
- `analyze_csv` raises `ServiceValidationError` if any row is missing a required field, with a Python-style key error indicating which.
