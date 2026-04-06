# GloBird Configurable Incentives & Bug Fix

**Date:** 2026-04-06
**Status:** Approved

## Problem

1. **Options flow crash:** `OptionsFlowWithReload` + `add_update_listener` conflict causes `ValueError` on HA 2026.3+. Users cannot edit options after initial setup.
2. **Hardcoded incentive parameters:** Super Export cap (10 kWh), window (6-8pm), rate (15c/kWh) and ZEROHERO credit window (6-8pm) are module-level constants in `tariff_engine.py`. Users cannot configure these.
3. **Stale default rates:** `const.py` defaults reflect old fact sheets (Sep 2025 - Mar 2026). New April 2026 fact sheets have different rates across all plans.

## Solution

### 1. Bug Fix: Remove update listener

Remove `entry.add_update_listener(async_options_updated)` and the `async_options_updated` callback from `__init__.py`. `OptionsFlowWithReload` already triggers a full reload (calls `async_setup_entry` again), making the manual listener redundant and now forbidden.

### 2. Configurable Incentive Parameters

When a user enables an incentive toggle, show additional parameter fields below it.

**Super Export parameters:**
- `super_export_cap_kwh`: Number (default 15.0, min 1, max 50, step 0.5)
- `super_export_window_start`: Time string HH:MM (default "18:00")
- `super_export_window_end`: Time string HH:MM (default "21:00")
- `super_export_rate`: Number c/kWh (default 15.0, min 0, max 100, step 0.1)

**ZEROHERO Credit parameters:**
- `zerohero_window_start`: Time string HH:MM (default "18:00")
- `zerohero_window_end`: Time string HH:MM (default "21:00")

**Storage format** (in `config_entry.options["incentives"]`):
```python
{
    "super_export": True,
    "super_export_cap_kwh": 15.0,
    "super_export_window_start": "18:00",
    "super_export_window_end": "21:00",
    "super_export_rate": 15.0,
    "zerohero_credit": True,
    "zerohero_window_start": "18:00",
    "zerohero_window_end": "21:00",
}
```

**Backward compatibility:** If keys are missing (existing installs), fall back to old defaults (10 kWh, 6-8pm) so existing users aren't disrupted.

### 3. Tracker Refactoring

`SuperExportTracker.__init__` and `ZeroHeroTracker.__init__` accept config parameters instead of reading module-level constants. The `TariffEngine` constructor extracts these from the incentives config and passes them through.

### 4. Update Default Rates

Update `const.py` GLOBIRD_PLAN_DEFAULTS to April 2026 fact sheets:

| Plan | Daily Supply | Import Rates | Export |
|------|-------------|-------------|--------|
| ZEROHERO | 115.50 | Peak 39.60, Shoulder 27.50, Offpeak 0.00 | Flat 0.00 (incentives replace) |
| FOUR4FREE | 103.40 | Step1 27.72 / Step2 30.25 (15 kWh) | Peak 5.00, Shoulder 0.00 |
| BOOST | 110.00 | Step1 21.23 / Step2 25.30 (25 kWh) | Peak 3.00, Shoulder 0.10, Offpeak 0.00 |
| GLOSAVE | 88.00 | Step1 22.66 / Step2 28.05 (15 kWh) | Peak 3.00, Shoulder 0.10, Offpeak 0.00 |

ZEROHERO export tariff changes from Variable FiT TOU to flat 0.00 (super export + peak solar feed-in incentives replace it).

Add `peak_solar_feedin` to ZEROHERO default incentives (2c/kWh, 4-11pm).

### 5. Files Changed

| File | Changes |
|------|---------|
| `__init__.py` | Remove `add_update_listener` + `async_options_updated` |
| `const.py` | Update default rates, add incentive param defaults, ZEROHERO export to flat |
| `config_flow.py` | Add parameter fields when incentives toggled on (both config + options flows) |
| `tariff_engine.py` | Trackers accept config params; remove module-level constants |
| `strings.json` | Labels/descriptions for new fields |
| `translations/en.json` | Same |
| `tests/` | Update for configurable params |
