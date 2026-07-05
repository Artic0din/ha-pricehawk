@AGENTS.md
@ENGINEERING_CONSTITUTION.md

# Claude Code — ha-pricehawk specific

## When implementing

Apply the constitution. When stuck between approaches, use its Priority Rules:
- Correctness over speed
- Systemic fix over local fix
- Maintainability over convenience

Before pushing, self-check against constitution principles 11 (Define Done), 12 (Root-Cause First), 13 (No Regression by Design).

## Commit format

`{type}({scope}): {description}`

Valid types: `feat`, `fix`, `test`, `refactor`, `perf`, `docs`, `style`, `chore`
Never use: `sync`, `wip`, `update`, anything else.

Valid scopes for ha-pricehawk: `config-flow`, `tariffs`, `sensor`, `amber`, `globird`, `dashboard`, `ci`, `tests`, `deps`.

## Branch rules

- Never commit directly to `main`
- Branch naming: `{type}/{description}-{issue-number}` (e.g. `feat/super-export-incentive-42`)
- One feature per branch, no exceptions

## PR workflow

1. Open as **draft** while iterating
2. Run `/self-review` before flipping to ready
3. Flip to ready when CI is green locally
4. Codex + Sentry review on push
5. Address P0/P1 only via `/fix-review`
6. Reply to each thread: `Fixed in <sha>. <one-line rationale>`
7. Cap fix loop at 3 rounds; if same finding reappears, stop and surface
8. Squash on merge (no force-push during review — breaks line-anchored comments)

## Reply formats

- Fix applied: `Fixed in <sha>. <one-line rationale>`
- Disagreement: `Disagree: <reason>. Leaving as-is.` (do not resolve unilaterally)
- P2/P3 acknowledged: `Acknowledged — tracked for later.` (do not fix inline)

## Home Assistant guardrails

- Never run background processes via SSH to a live HA instance
- Never edit `/config/.storage/*.json` directly on a live HA instance
- Verify entity names via `/api/states` before referencing in code or tests

## Secrets

- Never commit `.env`, tokens, API keys, or credentials
- Run `gitleaks detect` before every push
- The `energy-dashboard.html` at repo root is DELETED — do not recreate

## Auto-merge

Acceptable for ha-pricehawk because there are no high-risk paths (no auth, payments, PII, or migrations beyond config entry version). CI green + Codex P0/P1 clean + threads resolved = merge.

## Slash commands

- `/plan` — explore issue, propose design, no code
- `/implement` — execute against PLAN.md in fresh context
- `/self-review` — local lint, typecheck, tests, gitleaks, codex pre-review
- `/fix-review` — fetch latest Codex comments, apply P0/P1, push, reply
- `/ship` — rebase autosquash, push, flip to ready, enable auto-merge
