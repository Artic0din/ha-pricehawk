# Troubleshooting

## Dashboard says "Disconnected"

The dashboard couldn't open its WebSocket back to HA.

**Check:**

1. Did you provide a Long-Lived Access Token during setup?
   Without one the dashboard tries to inherit from the parent HA frame; in standalone tabs that fails.
2. Hard-refresh the panel (Cmd+Shift+R / Ctrl+Shift+R).
   The HTML is cache-busted on every integration version bump, but a stuck Service Worker or aggressive browser cache can still serve an old copy.
3. Confirm your HA instance is reachable from the browser at the same host the panel was loaded from — the dashboard derives `ws://` vs `wss://` from `location.protocol`, so a reverse proxy that downgrades the connection will break it.

If still failing, open DevTools → Console.
A `401` on the WebSocket open frame means the token is invalid; generate a new one via **Profile → Security → Long-Lived Access Tokens** and update the config entry.

## Dashboard shows "Nothing accrues" — Today is stuck

You have setup running, but `sensor.pricehawk_current_plan_cost_today` shows only the daily supply charge, never any consumption cost.

**Root cause (most common):** `grid_power_sensor` is missing or set to an entity that doesn't exist.

**Fix:**

1. **Settings → Devices & Services → PriceHawk → Configure**
2. Confirm the **Grid power sensor** field points at a real, populated entity.
3. Open Developer Tools → States and verify the entity reports non-zero watts when you have load on.
4. If you can't pick the entity in the dropdown, it may not have a `state_class` HA recognises — confirm it's a `sensor` domain entity with a numeric state.

If the sensor is set but readings still don't flow:

- Check the unit_of_measurement: PriceHawk handles `W` and `kW` only.
- Tail the HA log: `Settings → System → Logs → Filter: pricehawk` — coordinator ticks log the read grid value.

## Window tabs are empty (no data in Week/Month/Year)

Per-window sensors need historical data to compute.
A fresh install has none.

**Fix:**

```yaml
service: pricehawk.backfill_history
data:
  days: 10
```

Replays HA recorder history into `daily_cost_history`.
Recorder retention defaults to ~10 days; if you want more, raise `purge_keep_days` in your `recorder:` config and wait for natural retention to grow.

## Savings shows higher than current cost

If `saving_today` ($1.39) > `current_cost_today` ($1.20), something's off.
The legacy `saving_today` sensor was Amber-vs-current-plan, which is the wrong semantic when you're already on Amber.

**Fix:** PriceHawk 1.5+ uses `current − best_alternative` consistently.
Hard-refresh the dashboard.
If the discrepancy persists, restart HA so the coordinator re-runs the rollup pass.

## Savings column doesn't change when I switch tabs

A `setActiveWindow()` regression in pre-1.5.0-beta.2 versions; fixed in commit `c6fdbb2`.
Update to ≥ 1.5.0-beta.2.

## Ranking sensor is empty / stale

`sensor.pricehawk_ranked_alternatives` should populate within 60s of setup.
If it stays empty:

- Force a manual run: `pricehawk.rank_alternatives` via Developer Tools.
- Check `Settings → System → Logs → Filter: pricehawk.cdr` for HTTP errors against `consumer.cdr.gov.au`.
- The first-ranking-done event gates the dashboard from showing a misleading "no alternatives" state — if you see that, the job is still in flight.

## "UnboundLocalError on daily_saving"

Fixed in commit `129f7d6` (1.5.0-beta.2).
Pre-existing bug that surfaced when Amber was not configured and the daily rollover ran.
Update to ≥ 1.5.0-beta.2.

## After deploying via SSH the integration won't restart

Direct deploys (`tar -czf ... | ssh root@homeassistant.local 'tar -xzf - -C /config/custom_components/pricehawk'`) replace files in place.
HA detects the change on next core reload, but if the manifest version didn't change you may need to:

1. `ha core restart` from the HA OS terminal, or
2. **Developer Tools → YAML → All YAML configuration → Reload** then disable/re-enable the integration.

For UAT, bump `manifest.json`'s `version` even on transient builds so HA picks up the new code without manual intervention.

## Entity slugs don't match the dashboard

The dashboard's `ENTITY` lookup hardcodes the slugs PriceHawk generates from `_attr_name`.
HA inserts an underscore at digit-letter boundaries, so:

- `PriceHawk Current Cost 3 Months` → `sensor.pricehawk_current_cost_3_month` (note the underscore inside `3_month`)
- `PriceHawk Best Alternative Cost Year` → `sensor.pricehawk_best_alternative_cost_year`

If you renamed an entity in the HA UI, restore the original slug or edit `www/dashboard.html`'s `ENTITY` map to match.

## CDR catalogue is stale

The nightly job at 00:30 AEST refreshes the catalogue.
If HA was down at 00:30, the catalogue won't refresh until the next run.
Force a manual refresh:

```yaml
service: pricehawk.rank_alternatives
```

This pulls the latest catalogue before ranking.

## Getting more detail

Set the integration's log level to debug:

**Settings → System → Logs → top-right ⋯ → Configure logger**

Add: `custom_components.pricehawk: debug` and reload.

For SSH-deployed UAT installs:

```bash
ssh root@homeassistant.local 'tail -F /config/home-assistant.log | grep pricehawk'
```
