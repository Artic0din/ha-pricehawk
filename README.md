<p align="center">
  <img src="assets/logo-dark.png" alt="PriceHawk" width="200">
</p>

<h1 align="center">PriceHawk</h1>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=Artic0din&repository=ha-pricehawk&category=integration"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open in HACS"></a>
  <a href="https://github.com/Artic0din/ha-pricehawk/actions/workflows/python-ci.yml"><img src="https://github.com/Artic0din/ha-pricehawk/actions/workflows/python-ci.yml/badge.svg" alt="Python CI"></a>
</p>

<p align="center"><strong>See exactly which Australian energy retailer is cheapest for your home — based on what you actually use, right now.</strong></p>

PriceHawk runs your current retailer alongside dozens of ranked alternatives, scored against your real Home Assistant consumption.
Instead of guessing whether you'd save by switching, you see it — live, every 30 seconds, with the dashboard breaking the comparison down across **Today, This Week, This Month, 3 Months, and This Year**.

A nightly ranking job pulls every published retail plan from the Consumer Data Right (CDR) registry, scores it against your usage profile, and surfaces the cheapest matches.
Pick one as your **named comparator** to track head-to-head, and the dashboard shows the running savings delta against it.

## Who it's for

Australian solar and battery households on the National Electricity Market (NEM) who want to:

- Know if they'd be better off on a different plan — without manual spreadsheets
- Time appliances and battery dispatch around live wholesale prices
- See "why" they won or lost a day, not just the numbers

## What it compares

| Source | Pricing | Notes |
|---|---|---|
| **Amber Electric** | Live wholesale pass-through | API-driven, opens only to existing customers |
| **GloBird Energy** | Manually-configured TOU + incentives | ZEROHERO, FOUR4FREE, BOOST, GLOSAVE — pre-filled defaults |
| **Flow Power** | Wholesale + Happy Hour FiT | 45c NSW/QLD/SA, 35c VIC during 5:30–7:30pm |
| **LocalVolts** | Peer-to-peer matched wholesale | ACT, NSW, QLD, SA, TAS — API-driven, customer-only |
| **CDR-published plans** | Every retail plan listed by the AER | Origin, AGL, Red, EnergyAustralia, Engie, OVO, plus 30+ smaller retailers |

You pick your **current** retailer at setup.
PriceHawk always compares you against the live wholesale sources that don't need an account (**GloBird, Flow Power**), and ranks dozens of CDR-listed retail plans against your consumption profile so you can pick a named comparator without needing an API key.
Amber and LocalVolts only get added if they're your current provider, because their APIs are only open to existing customers.

If your retailer isn't directly supported but it publishes plans to the CDR registry (Origin, AGL, Red, EnergyAustralia, etc.), choose **"Other (no API)"** at setup — PriceHawk will rank you against the same CDR data and let you pin one plan as your tracked alternative.

## Screenshots

<p align="center">
  <img src="assets/screenshot-1-overview.png" alt="PriceHawk Dashboard Overview" width="800">
</p>

<p align="center">
  <img src="assets/screenshot-2-breakdown.png" alt="Cost Breakdown and Price History" width="800">
</p>

## Install

**Through HACS (recommended):**

1. In Home Assistant, open **HACS → Integrations → ⋯ menu → Custom repositories**
2. Add `https://github.com/Artic0din/ha-pricehawk` with category **Integration**
3. Search for "PriceHawk" and click **Install**
4. Restart Home Assistant

> **Trying the beta?** PriceHawk publishes pre-release versions tagged `-beta.N`. To see them in HACS, click the integration → ⋯ menu → **Redownload**, then toggle **Show beta versions** in the dialog.

## Set it up

1. **Settings → Devices & Services → Add Integration → PriceHawk**
2. Choose your **current energy retailer** (Amber, GloBird, Flow Power, or LocalVolts)
3. Enter that retailer's details — API key for Amber/LocalVolts, plan + tariffs for GloBird, region for Flow Power
4. Pick the **Home Assistant sensor** that reports your grid power (positive = importing, negative = exporting). This is usually from your smart meter, Powerwall, or Enphase Envoy.
5. (Optional) Paste a **Long-Lived Access Token** so the live dashboard can update in real time. Create one in your HA profile under **Security → Long-Lived Access Tokens**.

That's it. Within a minute the dashboard appears in your sidebar showing the comparison.

## What you'll see on the dashboard

- **Window tabs** — Today / This Week / This Month / 3 Months / This Year, each scaled to its own period so the savings delta and cost columns retune as you switch
- **Hero cards** — current cost on your plan, best alternative cost, and savings for the active window, with the named comparator cost when you've pinned one
- **Ranked alternatives table** — every retailer scored against your usage, with inline drill-in rows that expand into the day-by-day breakdown without leaving the page
- **Cheapest right now** — provider with the lowest live rate, plus how much you'd save vs your current plan
- **Daily cost breakdown** — bar chart showing import charges, feed-in credits, and daily supply
- **24-hour rate timeline** — live wholesale price chart with current-price marker
- **Why X won today** — plain-English bullet points explaining the day's winner ("Free 11–2pm: 1.4 kWh imported at $0 — saved $0.38")
- **Amber 24h forecast** — peak / dip / average price for the next 24 hours, when you're on Amber
- **GloBird incentive tracker** — live progress on free-power window, super-export, and the $1/day ZEROHERO credit
- **Partial-window mode** — when you've been running for less than a full window, the dashboard shows the partial figure rather than accruing a misleading total
- **Mobile-friendly** — works on phones, tablets, and the HA companion app

The dashboard is dark-mode by default with a light-mode toggle.
Everything updates live over WebSocket — no refresh needed.

See [docs/dashboard.md](docs/dashboard.md) for the full feature reference.

## What you need

- **Home Assistant 2024.1** or newer
- **A grid power sensor** in HA (smart meter, Powerwall, Envoy, or similar — anything that reports watts in/out of the grid)
- **An account** with whichever retailer is your current provider (only required if it's Amber or LocalVolts, which need an API key)

## Frequently asked

**Do I need an Amber account to use this?**
Only if Amber is your current provider. If you're on GloBird, Flow Power, or LocalVolts, PriceHawk works without any Amber connection — wholesale prices come directly from AEMO.

**Will it work in Victoria?**
Yes — GloBird, Flow Power (with 35c FiT), and Amber all cover Victoria. LocalVolts doesn't operate in VIC.

**What happens if my retailer changes their rates?**
For Amber, Flow Power, and LocalVolts: rates are pulled live, so changes show up automatically.
For GloBird: rates are configured manually — edit them via **Settings → Devices & Services → PriceHawk → Configure**.
For CDR-listed retailers: the nightly catalogue refresh (00:30 AEST) picks up published changes within 24 hours.

**Can I add my own retailer?**
If it publishes plans to the CDR registry (which all major AU retailers do under the AER Energy Made Easy programme), yes — pick **"Other (no API)"** at setup and PriceHawk will rank you against every plan it has on file.
The nightly ranker (00:30 AEST) refreshes the catalogue automatically.
Live-API support for new retailers (beyond Amber, Flow Power, LocalVolts) is on the Phase 4 roadmap.

**How is the ranking computed?**
A two-pass scorer: a fast "cheap-rank" heuristic (`peak_rate * 0.7 + daily_supply * 0.3`) prunes to top 20, then a deep-rank streams your recent grid-power history through each plan's full tariff schedule including incentives.
Run it manually via the `pricehawk.rank_alternatives` service.

**Where do my comparison costs come from?**
Either Amber's live API (if you're a customer) or AEMO NEMWeb's public dispatch reports (free, public).
GloBird/Flow Power/CDR plans run their tariff math on the same grid-power data, so every comparator sees the same energy flowing through different rate cards.

**Does it cost anything to run?**
No. PriceHawk uses public APIs (Amber's free dev API, AEMO NEMWeb's public dispatch reports, the LocalVolts customer API, the AER CDR registry) — no subscriptions or paid services.

## Documentation

| Topic | Doc |
|---|---|
| Architecture overview, module map, data flow | [docs/architecture.md](docs/architecture.md) |
| Dashboard reference and feature tour | [docs/dashboard.md](docs/dashboard.md) |
| Setup wizard, GloBird tariff configuration, options flow | [docs/configuration.md](docs/configuration.md) |
| Sensor reference (every entity PriceHawk exposes) | [docs/sensors.md](docs/sensors.md) |
| Services reference (`rank_alternatives`, `backfill_history`) | [docs/services.md](docs/services.md) |
| Troubleshooting (Disconnected dashboard, missing consumption, etc.) | [docs/troubleshooting.md](docs/troubleshooting.md) |
| Local development workflow, tests, conventions | [docs/development.md](docs/development.md) |

## License

[MIT](LICENSE)

## Acknowledgments

- [PowerSync](https://github.com/bolagnaise/PowerSync) — Amber API patterns and HA integration architecture
- [Flow-Power-HA](https://github.com/bolagnaise/Flow-Power-HA) — Happy Hour FiT and PEA logic (MIT)
- [VoltCompare](https://voltcompare.au) — Inspired the Why-X-won explanation engine
- [Amber Electric](https://www.amber.com.au), [GloBird Energy](https://www.globirdenergy.com.au), [Flow Power](https://flowpower.com.au), [LocalVolts](https://localvolts.com.au) — for the public APIs and tariff transparency
- [Home Assistant](https://www.home-assistant.io) — the platform that makes this possible

---

<details>
<summary><strong>For developers</strong></summary>

### Local setup

```bash
git clone https://github.com/Artic0din/ha-pricehawk.git
cd ha-pricehawk
uv sync --group dev
```

### Checks

```bash
uv run ruff check .
uv run ty check
uv run pytest --cov=custom_components/pricehawk --cov-fail-under=80
```

### Branch strategy

- `main` — stable, protected. All changes via PR.
- `dev` — current development branch
- Feature branches: `feat/description`, `fix/description`, `chore/description`

### Commit format

`{type}({scope}): {description}` — types: `feat`, `fix`, `test`, `refactor`, `perf`, `docs`, `style`, `chore`.

</details>
