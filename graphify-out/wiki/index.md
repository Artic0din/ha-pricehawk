# ha-pricehawk Wiki — Index

Navigation entry point for the code map. Full detail lives in [`../GRAPH_REPORT.md`](../GRAPH_REPORT.md).

## Start here (highest blast radius)

- **`const`** — Constants for PriceHawk integration. (depended on by 5 modules)
- **`helpers`** — Shared helper functions for pricehawk integration. (depended on by 1 modules)
- **`wholesale.amber.calculator`** — Stateful cost accumulator for Amber Electric. (depended on by 2 modules)
- **`tariff_engine`** — GloBird tariff calculation engine. (depended on by 3 modules)
- **`wholesale.flow_power.const`** — Constants for the vendored Flow Power calculation modules. (depended on by 2 modules)

## Communities

1. mixed: (root), wholesale: `__init__ (package root)`, `backfill`, `config_flow`, `const`, `coordinator`, `csv_analyzer`, `dashboard_config`, `helpers`, `sensor`, `tariff_engine`, `wholesale.amber`, `wholesale.amber.calculator`, `wholesale.amber.provider`
2. `wholesale` subtree: `wholesale.flow_power`
3. `wholesale` subtree: `wholesale.flow_power.const`, `wholesale.flow_power.pricing`, `wholesale.flow_power.tariff_utils`
4. mixed: (root), wholesale: `wholesale`, `wholesale.protocol`

## Where do I find…

- **Cost/tariff math:** `tariff_engine`, `wholesale.flow_power.pricing`, `wholesale.flow_power.tariff_utils`, `wholesale.amber.calculator`
- **HA wiring:** `__init__ (package root)`, `coordinator`, `sensor`, `config_flow`
- **Provider abstraction:** `wholesale.protocol`, `wholesale.amber.provider`, `wholesale.flow_power`
- **Data import/backfill:** `backfill`, `csv_analyzer`
- **Constants/contracts:** `const`, `wholesale.flow_power.const`
