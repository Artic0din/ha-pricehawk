"""Vendored Flow Power calculation modules.

Source: https://github.com/bolagnaise/Flow-Power-HA
Upstream commit: 3c2a9bb77dfa30eab3646a31703e10ad6743d10f
License: MIT (see ``LICENSES/flow-power-ha.LICENSE``)
Provenance: ``NOTICES.md`` at repo root.

PR 3a vendored :mod:`.pricing` and the constants it requires. PR 3b
adds :mod:`.tariff_utils` (aemo_to_tariff wrapper) plus the related
network/region constants. PR 3c will append the AEMO/portal HTTP
clients. Provider wiring lands in PR 4.

Modules in this package are treated as third-party: do NOT edit. If
upstream changes, re-vendor via the SHA bump procedure documented in
``NOTICES.md``.
"""
