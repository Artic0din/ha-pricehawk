"""Vendored Flow Power calculation modules.

Source: https://github.com/bolagnaise/Flow-Power-HA
Upstream commit: 3c2a9bb77dfa30eab3646a31703e10ad6743d10f
License: MIT (see ``LICENSES/flow-power-ha.LICENSE``)
Provenance: ``NOTICES.md`` at repo root.

PR 3a vendors :mod:`.pricing` and the constants it requires. Subsequent
PRs append :mod:`.tariff_utils` (PR 3b) and the AEMO/portal HTTP clients
(PR 3c). Provider wiring lands in PR 4.

Modules in this package are treated as third-party: do NOT edit. If
upstream changes, re-vendor via the SHA bump procedure documented in
``NOTICES.md``.
"""
