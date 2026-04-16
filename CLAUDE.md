# PriceHawk — Energy Compare HACS Integration

**Stack:** Python, Home Assistant custom integration (HACS)

Compare real energy costs between [Amber Electric](https://www.amber.com.au) (wholesale spot pricing) and [GloBird Energy](https://www.globirdenergy.com.au) (time-of-use tariffs) using actual Home Assistant consumption data.

## Project Context

- **Target:** Home Assistant custom integration distributed via HACS
- **Amber side:** Connects to Amber's public API — straightforward
- **GloBird side:** No API — users manually configure their tariff rates, time periods, and incentives via a config flow
- **Users:** Australian solar/battery households comparing energy providers

## GloBird Plan Complexity

Three sample plans in project root (PDFs). Key variations the config flow must handle:
- **Flat vs TOU** import rates
- **Stepped pricing** (first X kWh at one rate, remainder at another)
- **Multiple time windows per period** (e.g., Shoulder = 9pm-12am + 12am-10am + 2pm-4pm)
- **Separate import and export TOU schedules**
- **Optional incentives:** ZEROHERO ($1/day credit), Super Export (15c/kWh), Critical Peak, free power windows
- **Daily supply charge** varies per plan

## Integration Structure

```
custom_components/energy_compare/
├── __init__.py
├── manifest.json
├── config_flow.py       # Amber API key + GloBird tariff builder
├── sensor.py            # Cost calculation sensors
├── const.py
├── strings.json
└── translations/
    └── en.json
```

## Code Conventions

- Follow Home Assistant integration development guidelines
- Use `async`/`await` for all I/O operations
- Config flow must validate Amber API key on entry
- All sensor calculations use HA's energy sensors as source data
- Support HACS installation via custom repository

## AEGIS-Derived Rules

_Generated from AEGIS diagnostic audit (2026-04-16). Review invalidation conditions before removing._

### Secrets

- NEVER hardcode tokens, API keys, or credentials in any file — use HA config entry storage
- NEVER commit files containing JWTs or Bearer tokens — run `gitleaks detect` before every push
- The `energy-dashboard.html` at repo root is DELETED — do not recreate

### Dashboard

- The canonical dashboard is `custom_components/pricehawk/www/dashboard.html` — there is no repo-root copy
- Dashboard entity IDs MUST use the `pricehawk_` prefix matching sensor.py
- Dashboard MUST use `location.protocol` for WebSocket URL detection, never hardcode ws://
- Dashboard MUST read token from URL params or postMessage, never hardcode

### CI/CD

- NEVER interpolate `${{ }}` directly in `run:` blocks — use `env:` intermediate variables
- NEVER use `permissions: write-all` — specify minimum required permissions per job

### Testing

- Config flow changes require corresponding test updates in test_config_flow.py
- Tariff rate calculation changes require edge case tests (negative rates, midnight boundaries, empty windows)

### State Persistence

- State restore MUST validate storage version before loading
- `from_dict()` methods MUST receive an explicit HA-timezone date — no `date.today()` fallback
