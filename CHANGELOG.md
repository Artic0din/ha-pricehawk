# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- Documentation audit (2026-06-12): README badges now point at the real `ci.yml` workflow (old python-ci/security-scan/CodeRabbit badges were dead); review-process docs reference Codex, not CodeRabbit; AGENTS.md install command corrected to requirements.txt (no pyproject.toml exists); auto-merge and draft-PR allowances removed from AGENTS.md per hub hard rules; CLAUDE.md reduced to a two-line import shim with its unique content merged into AGENTS.md; HA requirement documented as 2025.x+ (hacs.json bump pending).
- Consolidated 9 PR-time workflows into one `ci.yml` (lint + types + tests + HACS + hassfest + gitleaks) so branch protection has a single status target.
- Replaced CodeRabbit-aware Claude assistant workflow with a Codex-aware one; `@claude` mention triggers the fix-loop and `--max-turns 30` is the load-bearing cap.
- Rewrote `CLAUDE.md` as a thin override that imports `@AGENTS.md` + `@ENGINEERING_CONSTITUTION.md` instead of duplicating `AGENTS.md` verbatim.
- SHA-pinned `home-assistant/actions/hassfest`, `hacs/action`, and `gitleaks/gitleaks-action` per repo action-pinning policy.

### Added

- `ENGINEERING_CONSTITUTION.md` at repo root — global engineering standards referenced by `CLAUDE.md`.
- `.github/CODEOWNERS` scoped to `.github/` + `manifest.json` only (irreversibility, not architectural sensitivity).
- `.github/pull_request_template.md` with Problem / Approach / Scope / Test plan / Risk / Reviewer focus / Constitution check.
- `.github/dependabot.yml` for weekly pip + monthly github-actions updates.
- `.claude/commands/{self-review,fix-review,ship}.md` slash commands for the Claude-Codex loop.
- `AGENTS.md` Review guidelines section with explicit P0/P1/P2/P3 severity rules so Codex review noise stays bounded.

### Removed

- `coderabbit-nitpicks.yml`, `dual-loop-review.yml`, `pr-checks.yml`, `lint.yml`, `python-ci.yml`, `security-scan.yml`, `validate.yaml`, `docs-check.yml` workflows (folded into `ci.yml` or made redundant by hassfest).
- Duplicated `CLAUDE.md` content that mirrored `AGENTS.md`.

### Deferred

- `wiki-update.yml` moved to `.github/workflows.disabled/` pending a deliberate decision to re-enable as post-merge-only.

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
