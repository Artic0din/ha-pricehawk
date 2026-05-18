# Contributing

## Three-agent workflow

Claude Code builds → Codex reviews locally → GitHub Copilot reviews inline
on the PR → CI verifies → Ryan merges manually.

- **Builder (Claude Code)** — see `CLAUDE.md` for build discipline.
- **Local reviewer (Codex)** — see `AGENTS.md` for review priorities.
- **PR reviewer (Copilot)** — see `.github/copilot-instructions.md`.

## Branches and PRs

- Feature branches off `main`. No direct commits to `main`.
- Conventional commit messages: `{type}({scope}): {description}`.
- Valid types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `ci`,
  `perf`, `build`, `revert`, `style`.
- PR title follows the same convention (squash merge puts PR title in
  history).
- Open PRs as draft. Mark ready only after Codex local review passes.

## CI gate

Branch protection requires the literal `CI passed` check. Individual job
results (lint, typecheck, gitleaks, tests, codecov) feed into the rollup
via `needs:` in the caller `ci.yml`.

## Override label

The optional Claude Code Action gate (disabled in v1.1.0 baseline) can be
bypassed with the `ai-review-override` label. Use only for:
- Anthropic API outage
- Trailer malformed but actual review fine
- Production incident requiring immediate merge

Document the reason in the PR body when applying the label.

## Draft PRs

Draft PRs skip the optional Claude review; the merge gate (when enabled)
passes open until you mark Ready for review.

## Local checks before push

Run the same checks CI runs:
- TypeScript: `pnpm lint && pnpm exec tsc --noEmit && pnpm test:coverage && pnpm build`
- Python: `uv run ruff check && uv run pyright && uv run pytest`
- Swift: `swiftlint --strict && swift build && swift test`

Never push with failing local checks.

## Secrets

No `.env`, `.pem`, `.key`, `credentials.json`, tokens, or API keys in git.
Run `git diff --staged | grep -iE "key|secret|token|password"` before push.
