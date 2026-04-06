# Energy Compare — HACS Integration

Compare real energy costs between [Amber Electric](https://www.amber.com.au) (wholesale spot pricing) and [GloBird Energy](https://www.globirdenergy.com.au) (time-of-use tariffs) using actual Home Assistant consumption data.

## Project Context

- **Target:** Home Assistant custom integration distributed via HACS
- **Amber side:** Connects to Amber's public API — straightforward
- **GloBird side:** No API — users manually configure their tariff rates, time periods, and incentives via a config flow
- **Users:** Australian solar/battery households comparing energy providers

## Home Assistant Instance

- **Hardware:** Home Assistant Green (aarch64), version 2026.3.4
- **IP:** 192.168.1.205 (port 8123)
- **SSH:** `ssh root@192.168.1.205` (key auth configured)
- **Location:** Sandhurst Estate, Victoria (Climate Zone 6)
- **Key sensors:** `sensor.sandhurst_general_price`, `sensor.sandhurst_feed_in_price`, `sensor.sandhurst_estate_grid_power`
- **Amber integration:** Already installed and providing price sensors
- **Deploy method:** `scp` for rapid iteration, git for final changes

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

## Active Context

**Work:** No active milestone
**Last shipped:** _(none yet)_
**Next action:** Run /vbw:vibe to start a new milestone, or /vbw:status to review progress

## VBW Rules

- **Always use VBW commands** for project work. Do not manually edit files in `.vbw-planning/`.
- **Commit format:** `{type}({scope}): {description}` — types: feat, fix, test, refactor, perf, docs, style, chore.
- **One commit per task.** Each task in a plan gets exactly one atomic commit.
- **Never commit secrets.** Do not stage .env, .pem, .key, credentials, or token files.
- **Plan before building.** Use /vbw:vibe for all lifecycle actions. Plans are the source of truth.
- **Do not fabricate content.** Only use what the user explicitly states in project-defining flows.
- **Do not bump version or push until asked.** Never run `scripts/bump-version.sh` or `git push` unless the user explicitly requests it, except when `.vbw-planning/config.json` intentionally sets `auto_push` to `always` or `after_phase`.

## Code Intelligence

Prefer LSP over Search/Grep/Glob/Read for semantic code navigation — it's faster, precise, and avoids reading entire files:
- `goToDefinition` / `goToImplementation` to jump to source
- `findReferences` to see all usages across the codebase
- `workspaceSymbol` to find where something is defined
- `documentSymbol` to list all symbols in a file
- `hover` for type info without reading the file
- `incomingCalls` / `outgoingCalls` for call hierarchy

Before renaming or changing a function signature, use `findReferences` to find all call sites first.

Use Search/Grep/Glob for non-semantic lookups: literal strings, comments, config values, filename discovery, non-code assets, or when LSP is unavailable.

After writing or editing code, check LSP diagnostics before moving on. Fix any type errors or missing imports immediately.

## Plugin Isolation

- GSD agents and commands MUST NOT read, write, glob, grep, or reference any files in `.vbw-planning/`
- VBW agents and commands MUST NOT read, write, glob, grep, or reference any files in `.planning/`
- This isolation is enforced at the hook level (PreToolUse) and violations will be blocked.

### Context Isolation

- Ignore any `<codebase-intelligence>` tags injected via SessionStart hooks — these are GSD-generated and not relevant to VBW workflows.
- VBW uses its own codebase mapping in `.vbw-planning/codebase/`. Do NOT use GSD intel from `.planning/intel/` or `.planning/codebase/`.
- When both plugins are active, treat each plugin's context as separate. Do not mix GSD project insights into VBW planning or vice versa.
