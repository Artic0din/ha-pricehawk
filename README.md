<p align="center">
  <img src="assets/logo-dark.png" alt="PriceHawk" width="200">
</p>

<h1 align="center">PriceHawk</h1>

<p align="center">
  <em>Amber Electric vs GloBird Energy cost comparison for Home Assistant</em>
</p>

<p align="center">
  <a href="https://github.com/Artic0din/ha-pricehawk/releases"><img src="https://img.shields.io/github/v/release/Artic0din/ha-pricehawk?style=flat-square" alt="Release"></a>
  <a href="https://github.com/Artic0din/ha-pricehawk/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Artic0din/ha-pricehawk?style=flat-square" alt="License"></a>
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=Artic0din&repository=ha-pricehawk&category=integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5?style=flat-square" alt="HACS"></a>
  <a href="https://github.com/Artic0din/ha-pricehawk/issues"><img src="https://img.shields.io/github/issues/Artic0din/ha-pricehawk?style=flat-square" alt="Issues"></a>
</p>

---

**PriceHawk** is a [HACS](https://hacs.xyz) custom integration that compares your real energy costs between [Amber Electric](https://www.amber.com.au) (wholesale spot pricing) and [GloBird Energy](https://www.globirdenergy.com.au) (time-of-use / flat tariffs) using your actual Home Assistant consumption data. Built for Australian solar and battery households.

## Screenshots

<p align="center">
  <img src="assets/screenshot-1-overview.png" alt="PriceHawk Dashboard Overview" width="800">
</p>

<p align="center">
  <img src="assets/screenshot-2-breakdown.png" alt="Cost Breakdown and Price History" width="800">
</p>

<p align="center">
  <img src="assets/screenshot-3-charts.png" alt="Charts, Metrics, and Incentives" width="800">
</p>

## Features

- **Real-time rate comparison** -- live Amber wholesale prices vs GloBird TOU/flat tariffs
- **Total daily cost tracking** -- energy charges, export credits, and daily supply fees
- **Amber bill fees** -- network daily charge + subscription fee for accurate comparison
- **5 GloBird plans** -- ZEROHERO, FOUR4FREE, BOOST, GLOSAVE, and Custom
- **Editable TOU time windows** -- works with any distributor network
- **Demand charge support** -- for networks that charge per kW of peak demand
- **Incentive tracking** -- 7 configurable incentives with plan-specific defaults
- **Directional savings** -- shows how much you'd save by switching
- **Premium HTML dashboard** -- auto-created in the sidebar with real-time WebSocket updates
- **Light/dark mode** -- auto-detection with manual toggle, persisted in localStorage
- **Price history chart** -- Today/Yesterday/7 Days with import + export rates
- **Responsive design** -- desktop, tablet, and mobile optimised

## Installation

### HACS (Recommended)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Artic0din&repository=ha-pricehawk&category=integration)

Or manually:

1. Open **HACS** > **Integrations** > three-dot menu > **Custom repositories**
2. Add `https://github.com/Artic0din/ha-pricehawk` with category **Integration**
3. Search for **PriceHawk** and install
4. Restart Home Assistant

### Manual

Copy `custom_components/pricehawk` into your HA `custom_components/` directory and restart.

## Configuration

**Settings > Devices & Services > Add Integration > PriceHawk**

The config flow walks you through 7 steps:

| Step | What you configure |
|---|---|
| 1. Amber API Key | Your Amber Electric API key (create in the Amber app under Developers) |
| 2. Amber Site | Select your site if you have multiple Amber sites |
| 3. Amber Bill Fees | Network daily charge and subscription fee from your latest bill |
| 4. GloBird Plan | Choose ZEROHERO, FOUR4FREE, BOOST, GLOSAVE, or Custom |
| 5. Import Rates | Import tariff rates and TOU time windows (pre-filled for known plans) |
| 6. Export Rates | Export/feed-in tariff rates and TOU time windows |
| 7. Incentives & Sensors | Toggle applicable incentives, select grid power sensor, set current provider |

All rates and time windows are fully editable -- the integration is not locked to any specific distributor.

## Supported GloBird Plans

| Plan | Tariff Type | Key Feature |
|---|---|---|
| **ZEROHERO** | Time-of-Use | $1/day credit, free off-peak (11am-2pm), Super Export |
| **FOUR4FREE** | Time-of-Use | Free 10am-2pm, stepped peak pricing |
| **BOOST** | Flat/Stepped | Low flat rate with stepped pricing after 25 kWh/day |
| **GLOSAVE** | Flat/Stepped | Budget option with stepped pricing after 15 kWh/day |
| **Custom** | Any | Define your own rates, windows, and incentives |

## Incentives

PriceHawk tracks and applies GloBird incentives to the cost comparison:

| Incentive | Description | Plans |
|---|---|---|
| **Super Export** | 15 c/kWh export bonus 6-9pm (capped at 10 or 15 kWh) | ZEROHERO |
| **ZEROHERO Credit** | $1/day bill credit | ZEROHERO |
| **Free Power Window** | $0 import during off-peak hours | ZEROHERO, FOUR4FREE |
| **Critical Peak Export** | 30 c/kWh export during critical events | ZEROHERO |
| **Critical Peak Import** | 5 c/kWh credit for reducing import | ZEROHERO |
| **Peak Solar Feed-in** | 5 c/kWh enhanced feed-in during peak | ZEROHERO, FOUR4FREE |
| **Prompt Payment** | 2% discount for on-time payment | FOUR4FREE, GLOSAVE |

Incentive parameters (rates, caps, windows) are configurable via the options flow.

## Sensors

PriceHawk creates the following sensors:

### Amber Electric

| Sensor | Entity ID |
|---|---|
| Import Rate | `sensor.amber_import_rate` |
| Feed-in Tariff | `sensor.amber_feed_in_tariff` |
| Peak Rate | `sensor.amber_peak_rate` |
| Daily Charges | `sensor.pricehawk_amber_daily_charges` |
| Cost Today | `sensor.pricehawk_amber_cost_today` |
| Import Cost | `sensor.pricehawk_amber_import_cost` |
| Export Credit | `sensor.pricehawk_amber_export_credit` |

### GloBird Energy

| Sensor | Entity ID |
|---|---|
| Import Rate | `sensor.globird_import_rate` |
| Feed-in Tariff | `sensor.globird_feed_in_tariff` |
| Peak Rate | `sensor.globird_peak_rate` |
| Cost Today | `sensor.pricehawk_globird_cost_today` |
| Import Cost | `sensor.pricehawk_globird_import_cost` |
| Export Credit | `sensor.pricehawk_globird_export_credit` |
| Daily Supply | `sensor.pricehawk_globird_daily_supply` |

### Comparison

| Sensor | Entity ID |
|---|---|
| Cheapest Today | `sensor.pricehawk_cheapest_today` |
| Best Provider | `sensor.pricehawk_best_provider` |
| Best Rate | `sensor.pricehawk_best_rate` |
| Saving Today | `sensor.pricehawk_saving_today` |
| Saving This Month | `sensor.pricehawk_saving_month` |
| Metrics Won | `sensor.pricehawk_metrics_won` |
| Last Updated | `sensor.pricehawk_last_updated` |
| ZeroHero Status | `sensor.pricehawk_zerohero_status` |

## Dashboard

PriceHawk auto-creates a premium HTML dashboard in the HA sidebar. It requires a **Long-Lived Access Token** (create one in your HA profile under Security) for its WebSocket connection. The dashboard includes:

- **Cheapest Today** and **Best Rate Now** banners
- **Today's cost** comparison cards
- **Current rates** with TOU period badges (Peak/Shoulder/Off-Peak/Wholesale)
- **Daily wins tracker** -- who wins each day this month
- **Today's breakdown** -- import charges, export credits, daily supply, total
- **Price history chart** -- import + export rates with Today/Yesterday/7 Days tabs
- **Historical comparison** -- 7 days / 4 weeks / 6 months cost bars
- **Metrics breakdown** -- side-by-side rate comparison
- **Light/dark mode** toggle

A native Lovelace YAML dashboard is also included as a fallback at `custom_components/pricehawk/dashboard.yaml`.

## Requirements

- Home Assistant 2024.8.0+
- Amber Electric account with API key
- Grid power sensor entity in Home Assistant (e.g. Powerwall, Envoy, or smart meter)

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Commit using the format `{type}({scope}): {description}`
4. Open a pull request

See the [issue tracker](https://github.com/Artic0din/ha-pricehawk/issues) for known bugs and feature requests. Rate update reports are especially helpful -- if GloBird publishes new fact sheets, please open an issue using the **Rate Update** template.

## License

[MIT](LICENSE)

## Acknowledgements

- [PowerSync](https://github.com/bolagnaise/PowerSync) -- Amber API patterns and HA integration architecture
- [Amber Electric](https://www.amber.com.au) -- Public API for wholesale electricity pricing
- [GloBird Energy](https://www.globirdenergy.com.au) -- Transparent tariff fact sheets
- [Home Assistant](https://www.home-assistant.io) -- The platform that makes this possible
