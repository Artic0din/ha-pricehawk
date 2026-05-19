# Dashboard

The PriceHawk dashboard is a single self-contained HTML file (`custom_components/pricehawk/www/dashboard.html`) that connects to Home Assistant over WebSocket and renders live data from the coordinator.

## Layout

```
┌────────────────────────────────────────────────────────────┐
│  PriceHawk                                       [☀ / 🌙]   │
├────────────────────────────────────────────────────────────┤
│  [ Today ] [ Week ] [ Month ] [ 3M ] [ Year ]              │  ← window tabs
├────────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────────┐  ┌──────────┐  ┌────────┐  │
│  │ Current  │  │ Best alt     │  │ Savings  │  │ Named  │  │  ← hero cards
│  │ $X.XX    │  │ $X.XX        │  │ $X.XX    │  │ $X.XX  │  │
│  └──────────┘  └──────────────┘  └──────────┘  └────────┘  │
├────────────────────────────────────────────────────────────┤
│  Ranked alternatives (top 20)                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ # │ Plan              │ Cost (window) │ Savings │ ▾  │  │
│  │ 1 │ Origin Predictable │ $XX.XX       │ +$X.XX │ ▼  │  │
│  │ ↳ drill-in row (inline, expands here)                 │  │
│  │ 2 │ AGL Value Saver   │ $XX.XX       │ +$X.XX │ ▾  │  │
│  └──────────────────────────────────────────────────────┘  │
├────────────────────────────────────────────────────────────┤
│  24h rate timeline    │ Daily cost breakdown                │
│  [chart]              │ [bar chart]                         │
├────────────────────────────────────────────────────────────┤
│  Why X won today                                            │
│  • Free 11–2pm: 1.4 kWh imported at $0 — saved $0.38       │
│  • Super Export 12–2pm: 3.2 kWh exported @ 15c — +$0.48    │
├────────────────────────────────────────────────────────────┤
│  Amber 24h forecast │ GloBird incentive tracker             │
└────────────────────────────────────────────────────────────┘
```

## Window tabs

Five tabs scope every figure on the page to the active window.

| Tab | Window | Notes |
|---|---|---|
| Today | Midnight AEST → now | Partial-day mode applies |
| Week | Most recent ISO week | Partial-week mode applies |
| Month | Calendar month | Partial-month mode applies |
| 3 Months | Last 90 days rolling | Sensor slug: `..._3_month` (digit-letter boundary inserts underscore) |
| Year | Calendar year | Partial-year mode applies |

Switching tab re-renders hero cards, the ranked-alternatives table, and the savings column.
No page reload — the dashboard already has every window cached on `coordinator.data`.

## Hero cards

| Card | Source | Notes |
|---|---|---|
| Current cost | `sensor.pricehawk_current_cost_<window>` | What your active plan would charge for the window |
| Best alternative cost | `sensor.pricehawk_best_alternative_cost_<window>` | Cheapest ranked plan for the window |
| Savings | `sensor.pricehawk_savings_<window>` | `current − best_alternative` |
| Named comparator | `sensor.pricehawk_named_comparator_cost_<window>` | Only shown when you've pinned a plan via OptionsFlow |

For Today, all four cards fall back to live realtime sensors when the window-rollup hasn't recomputed yet.

## Ranked alternatives table

- Top 20 plans by deep-rank score for the active window.
- Each row: rank, plan name, retailer, cost for the window, savings delta.
- **Inline drill-in:** clicking ▾ expands a row below the clicked entry — no footer, no modal — showing the day-by-day breakdown for the past 14 days.
- The pinned named comparator (if set) is highlighted with a chip.

The savings column uses a proportional scaling pass: if a plan didn't run during the full window, its cost is approximated as `best_alt_cost * (alt_score / best_score)` so partial-data alternatives still rank fairly.

## Partial-window display

If you've been running PriceHawk for less than the active window's duration, the dashboard shows the partial figure (e.g. "$4.20 over 3 days") rather than accruing a misleading "month" total.
The rollup sensor's `days_in_window` attribute drives the gate.

## Live updates

WebSocket-driven.
The dashboard connects with this fallback chain for the access token:

1. URL parameter (`?token=…`)
2. `window.parent.hassConnection`
3. `localStorage.hassTokens`
4. `window.parent.localStorage.hassTokens`

WebSocket URL is derived from `location.protocol` (never hardcoded `ws://`).

## Cache busting

The HTML is served from `/local/community/pricehawk/dashboard.html` with a query string `?v=<version>.<epoch>` set by `dashboard_config.py`.
Every integration version bump forces every browser to re-fetch the HTML.
If you ever need to force a refresh manually, hard-reload the panel (Cmd+Shift+R / Ctrl+Shift+R).

## Theme

Dark by default with a light toggle (`☀ / 🌙`).
Theme preference is persisted in `localStorage` on the HA host that loaded the page.

## Troubleshooting

See [troubleshooting.md](troubleshooting.md) for "Disconnected" banners, missing data, and stuck consumption.
