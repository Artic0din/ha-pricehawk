# Configuration

PriceHawk is configured entirely through the HA UI — there is nothing to add to `configuration.yaml`.

## First-time setup

**Settings → Devices & Services → Add Integration → PriceHawk**

The wizard walks you through five steps.

### Step 1 — Current retailer

Pick whichever describes your real billing arrangement:

| Choice | What it means |
|---|---|
| **Amber Electric** | You're on Amber's wholesale plan with an API key |
| **GloBird Energy** | You're on GloBird (any plan: ZEROHERO, FOUR4FREE, BOOST, GLOSAVE) |
| **Flow Power** | You're on Flow Power, in a covered region |
| **LocalVolts** | You're on LocalVolts as an end-customer |
| **Other (no API)** | Origin, AGL, Red, EnergyAustralia, Engie, OVO, or any other CDR-published retailer — wizard routes you through the CDR plan picker |

### Step 2 — Retailer credentials

What you enter depends on Step 1:

- **Amber:** API key (created at [app.amber.com.au/dev](https://app.amber.com.au/dev)).
  Site auto-discovered.
- **GloBird:** plan template + per-period rates, daily supply, FiT, incentives.
  Defaults pre-fill from the four published GloBird plan templates.
- **Flow Power:** region (NSW/VIC/QLD/SA), base rate, daily supply, Happy Hour FiT (region-default applied).
- **LocalVolts:** API key, partner ID, NMI, daily supply, buy ceiling cents.
- **Other:** wizard surfaces the CDR plan picker — search by brand, pick the plan that matches your actual bill.

### Step 3 — Grid power sensor

Mandatory.
Select the HA sensor that reports watts in/out of the grid:

- **Positive** values = importing from the grid
- **Negative** values = exporting (solar/battery)

Common sources:

- Smart meter (e.g. `sensor.power_meter_grid_power`)
- Powerwall gateway (`sensor.powerwall_grid_power`)
- Enphase Envoy (`sensor.envoy_net_grid_power`)
- PowerSync (`sensor.power_sync_grid_power`)

If your sensor reports kW instead of W, PriceHawk auto-detects the unit_of_measurement attribute.

### Step 4 — Long-Lived Access Token (optional)

Required only for the live WebSocket dashboard.
Create in **Profile → Security → Long-Lived Access Tokens**.
PriceHawk stores it in the config entry (`config_entry.data.ha_token`), never in code or git.

### Step 5 — Confirm

Wizard validates the API key, persists the entry, kicks off a one-shot ranking job, and registers the dashboard panel.
Within ~60 seconds the dashboard appears in the sidebar.

## Options flow (post-setup)

**Settings → Devices & Services → PriceHawk → Configure**

Lets you change without re-creating the entry:

- GloBird rates and incentive toggles
- Flow Power region / PEA override
- Named comparator (pin one CDR plan to track head-to-head)
- Amber network daily charge + subscription fee
- OVO interest reward balance
- VPP batteries enrolled (ENGIE/EA PowerResponse)

## GloBird tariff editor

GloBird has no API, so its rates must be configured manually.
The editor handles every quirk:

| Feature | Supported |
|---|---|
| Flat or TOU import rates | ✅ |
| Stepped pricing (first X kWh / remainder) | ✅ |
| Multiple time windows per period | ✅ (e.g. Shoulder = 9pm–12am + 12am–10am + 2pm–4pm) |
| Separate import / export TOU schedules | ✅ |
| ZEROHERO ($1/day credit) | ✅ |
| Super Export (15c/kWh) | ✅ |
| FOUR4FREE (free-power window) | ✅ |
| Critical Peak | ✅ |
| Daily supply charge | ✅ |

Defaults pre-fill from the four bundled plan PDFs:

- `Victorian_Energy_Fact_Sheet_GLO707520MR_Electricity_CZ_6.pdf`
- `Victorian_Energy_Fact_Sheet_GLO723308MR_Electricity_CZ_6.pdf`
- `Victorian_Energy_Fact_Sheet_GLO724553MR_Electricity_CZ_6.pdf`
- `Victorian_Energy_Fact_Sheet_GLO730962MR_Electricity_CZ_6.pdf`

When GloBird publishes a rate change, edit the values in the OptionsFlow and PriceHawk recomputes daily costs from the next coordinator tick.

## Storage location

Config-entry data lives in `/config/.storage/core.config_entries` on the HA host.
This is HA-internal — never edit it manually unless you know what you're doing.
PriceHawk caches a parsed snapshot in memory; changes via the UI trigger a reload automatically.

For deploy-driven config patches (testing, UAT), the workflow is **stop HA → edit `core.config_entries` → start HA** — a hot edit will be overwritten by the in-memory cache on next save.
