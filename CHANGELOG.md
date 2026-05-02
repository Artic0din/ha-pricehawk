# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.4.0-dev] - unreleased

### Added

- **Provider abstraction** (`custom_components/pricehawk/providers/`) — common Protocol with thin Amber and GloBird adapters, plus new Flow Power and LocalVolts implementations
- **Flow Power provider** — wholesale-pass-through with Happy Hour FiT (5:30–7:30pm: 45c NSW/QLD/SA, 35c VIC, 0c TAS) and PEA (Price Efficiency Adjustment). Logic adapted from `bolagnaise/Flow-Power-HA` (MIT)
- **LocalVolts provider** — P2P matching engine with buy ceiling / sell floor; fresh `aiohttp` client; 5-min API intervals aggregated to 30-min volume-weighted average (no GPL contamination)
- **AEMO NEMWeb client** (`aemo_api.py`) — pulls wholesale RRP from public dispatch reports (no API key, no Amber account required); used as the wholesale source for Flow Power
- **"Why X won" explanation engine** (`explanation.py`) — deterministic per-day winner breakdown with good/bad/neu bullets, ported from VoltCompare's `buildExplanation`
- **Generic per-provider sensors** (`sensor.pricehawk_<id>_import_rate`, `_export_rate`, `_cost_today`) registered automatically for every active provider
- **Winner explanation sensor** (`sensor.pricehawk_winner_explanation`) — section label as state, bullets as attributes
- **Setup flow rework** — first step asks which retailer the user is currently with, then conditionally collects credentials (Amber API key only if primary is Amber, LocalVolts credentials only if primary is LocalVolts)
- **V3 dashboard mockup** at `assets/dashboard-v3-mockup.html`

### Changed

- Setup no longer requires an Amber API key for non-Amber customers — Flow Power and GloBird work standalone
- Coordinator persistence now serialises every active provider; daily winner tracking generalised from `{amber, globird}` to any registered provider id
- Daily cost history records one entry per active provider per day

### Architectural notes

- **Wholesale source for Flow Power is AEMO direct, not Amber's `spotPerKwh`.** Amber's "spot" field bundles network charges, and an Amber API token requires being or having been an Amber customer — neither acceptable for a non-Amber comparator.
- **Provider availability is asymmetric**: GloBird and Flow Power are universally available comparators (no credentials), while Amber and LocalVolts are only enabled when they are the user's primary (since their APIs require a customer account).

## [1.3.0] - 2026-04-17

### Added

- Brand directory for HA 2026.3+ icon display (`custom_components/pricehawk/brand/`)
- TOU 24-hour coverage validation warning in config flow
- Config flow tests (27 tests for window parsing, overlap, tariff building)
- Tariff engine edge case tests (midnight crossing, negative rates, empty windows)
- Accuracy validation test suite (17 tests against real Amber billing data)
- Content Security Policy meta tag on deployed dashboard
- AEGIS-derived guardrails in CLAUDE.md
- Pre-commit Gitleaks hook configuration
- `requirements.txt` for CI pip cache

### Changed

- Extracted shared form builders from ConfigFlow and OptionsFlow (reduced duplication)
- Gap protection now clamps to 6 min instead of discarding (captures partial energy after restarts)
- `from_dict()` requires explicit HA-timezone date parameter (no `date.today()` fallback)
- State persisted immediately after daily rollover (prevents crash data loss)
- Amber API Retry-After delay capped at 30 seconds (was 300)
- Retry-After handles HTTP-date format with ValueError fallback

### Fixed

- CI shell injection in wiki-update.yml and claude-assistant.yml
- CI write-all permissions restricted in validate.yaml and coderabbit-nitpicks.yml
- Removed hardcoded `sensor.sandhurst_*` entity IDs from dashboard
- Fixed unused imports flagged by ruff (F401, F841)
- Fixed pre-existing test_constructor_creates_engines assertion (supply charge)

### Removed

- Stale `energy-dashboard.html` (hardcoded JWT token, wrong entity IDs)

### Security

- Deleted hardcoded HA Long-Lived Access Token from repo-root dashboard
- Added CSP headers to deployed dashboard (default-src 'none', connect-src 'self')
- CI workflows hardened against shell injection and permission escalation

## [1.2.0] - 2026-04-12

### Added

- V2 dashboard with glass card design, IBM Plex Mono, dark/light mode
- Amber price forecast on rate comparison chart
- 14-day savings history with daily winner streaks
- GloBird incentive tracker (ZEROHERO, Super Export)
- Mobile responsive layout (1200/768/480px breakpoints)
- WebSocket real-time updates via HA API

### Fixed

- Dashboard entity IDs corrected for PriceHawk sensors
- Forecast display conversion from dollars to cents
- Rate chart label and X-axis timeline

## [1.1.2] - 2026-03-31

### Changed

- 7-day price history buffer (was 48h)

### Fixed

- Yesterday/weekly chart tab display

## [1.1.0] - 2026-03-31

### Added

- Daily cost history (180-day buffer)
- kW unit auto-detection from sensor attributes

### Fixed

- Stats layout formatting

## [1.0.0] - 2026-03-30

### Added

- Initial release: Amber vs GloBird energy cost comparison
- Real-time rate comparison (Amber wholesale vs GloBird TOU/flat)
- 5 GloBird plans: ZEROHERO, FOUR4FREE, BOOST, GLOSAVE, Custom
- Editable TOU time windows
- Demand charge support
- ZEROHERO credit tracking ($1/day)
- Super Export tracking (15c/kWh cap)
- Directional savings calculation
- 21 sensor entities
- Sidebar dashboard panel
- HACS custom repository installation
