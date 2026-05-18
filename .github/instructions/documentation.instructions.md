---
applyTo: "**/*.md"
---

# Documentation rules

## Semantic line breaks (sembr.org)
- One sentence per line.
- Additional breaks at clause boundaries (commas, semicolons, em dashes)
  for sentences over ~80 characters.
- Never wrap prose into paragraphs of joined lines.
- Never break inside hyphenated words or code spans.

## Structure
- Mermaid for diagrams, not embedded images.
- Link to authoritative source; don't duplicate content.
- One sentence per line in all markdown — README, docs, comments, PR
  descriptions, commit messages with body.

## CHANGELOG.md
- Follow Keep a Changelog format.
- Every PR adds an entry under `[Unreleased]`.
- Version headings: `## [1.2.3] - 2026-MM-DD`.

## README.md
- Badge row: CI status, coverage, version.
- Local development setup.
- Link to `CONTRIBUTING.md`.
- Link to `docs/` directory structure.
