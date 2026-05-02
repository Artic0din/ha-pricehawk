<p align="center">
  <img src="assets/logo-dark.png" alt="PriceHawk" width="200">
</p>

<h1 align="center">PriceHawk</h1>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=Artic0din&repository=ha-pricehawk&category=integration"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open in HACS"></a>
  <a href="https://github.com/Artic0din/ha-pricehawk/actions/workflows/python-ci.yml"><img src="https://github.com/Artic0din/ha-pricehawk/actions/workflows/python-ci.yml/badge.svg" alt="Python CI"></a>
</p>

<p align="center"><strong>See exactly which Australian energy retailer is cheapest for your home — based on what you actually use, right now.</strong></p>

PriceHawk runs four energy plans in parallel against your real Home Assistant data, so instead of guessing whether you'd save by switching, you can see it. Pick your current retailer at setup and the dashboard shows you, every 30 seconds, what your bill would look like under each of the alternatives.

## Who it's for

Australian solar and battery households on the National Electricity Market (NEM) who want to:

- Know if they'd be better off on a different plan — without manual spreadsheets
- Time appliances and battery dispatch around live wholesale prices
- See "why" they won or lost a day, not just the numbers

## What it compares

| Provider | Notes |
|---|---|
| **Amber Electric** | Wholesale pass-through, real-time spot pricing |
| **GloBird Energy** | ZEROHERO, FOUR4FREE, BOOST, GLOSAVE — pre-filled defaults |
| **Flow Power** | Wholesale + 5:30–7:30pm Happy Hour FiT (45c NSW/QLD/SA, 35c VIC) |
| **LocalVolts** | Peer-to-peer matched wholesale (ACT, NSW, QLD, SA, TAS) |

You pick your **current** provider during setup. PriceHawk always compares you against **GloBird and Flow Power** automatically — neither needs an account, so they're free comparators. Amber and LocalVolts only get added if they're your current provider, because their APIs are only open to existing customers.

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

- **Cheapest right now** — the provider with the lowest live rate, plus how much you'd save vs your current plan
- **Daily cost breakdown** — bar chart showing import charges, feed-in credits, and daily supply for each provider
- **24-hour rate timeline** — live wholesale price chart with current price marker
- **Why X won today** — plain-English bullet points explaining the day's winner ("Free 11–2pm: 1.4 kWh imported at $0 — saved $0.38")
- **Amber 24h forecast** — peak / dip / average price for the next 24 hours, when you're on Amber
- **GloBird incentive tracker** — live progress on free-power window, super-export, and the $1/day ZEROHERO credit
- **14-day savings history** — daily winner streaks and cumulative savings trend
- **Mobile-friendly** — works on phones, tablets, and the HA companion app

The dashboard is dark-mode by default with a light-mode toggle. Everything updates live over WebSocket — no refresh needed.

## What you need

- **Home Assistant 2024.1** or newer
- **A grid power sensor** in HA (smart meter, Powerwall, Envoy, or similar — anything that reports watts in/out of the grid)
- **An account** with whichever retailer is your current provider (only required if it's Amber or LocalVolts, which need an API key)

## Frequently asked

**Do I need an Amber account to use this?**
Only if Amber is your current provider. If you're on GloBird, Flow Power, or LocalVolts, PriceHawk works without any Amber connection — wholesale prices come directly from AEMO.

**Will it work in Victoria?**
Yes — GloBird, Flow Power (with 35c FiT), and Amber all cover Victoria. LocalVolts doesn't operate in VIC.

**Does it cost anything to run?**
No. PriceHawk uses public APIs (Amber's free dev API, AEMO NEMWeb's public dispatch reports, the LocalVolts customer API) — no subscriptions or paid services.

**What happens if my retailer changes their rates?**
For Amber, Flow Power, and LocalVolts: rates are pulled live, so changes show up automatically. For GloBird: rates are configured manually — edit them via **Settings → Devices & Services → PriceHawk → Configure**.

**Can I add my own retailer?**
Not yet — PriceHawk's tariff engine is being refactored to make this easier. For now, the four supported retailers cover the majority of dynamic-pricing Australian households.

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
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install ruff mypy bandit pytest pytest-cov
```

### Checks

```bash
ruff check .
mypy . --ignore-missing-imports
pytest --tb=short -q
```

### Branch strategy

- `main` — stable, protected. All changes via PR.
- `dev` — current development branch
- Feature branches: `feat/description`, `fix/description`, `chore/description`

### Commit format

`{type}({scope}): {description}` — types: `feat`, `fix`, `test`, `refactor`, `perf`, `docs`, `style`, `chore`.

</details>
