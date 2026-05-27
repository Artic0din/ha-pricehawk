# Third-Party Notices

This integration vendors source code from other projects. License texts
for each are reproduced under ``LICENSES/``.

## Flow Power HA (bolagnaise/Flow-Power-HA)

- **Source**: https://github.com/bolagnaise/Flow-Power-HA
- **Upstream commit**: `3c2a9bb77dfa30eab3646a31703e10ad6743d10f`
- **License**: MIT (see [`LICENSES/flow-power-ha.LICENSE`](LICENSES/flow-power-ha.LICENSE))
- **Copyright**: 2025 bolagnaise

Vendored under `custom_components/pricehawk/wholesale/flow_power/`.
Files in this directory are treated as third-party and not modified.

### Vendored files (by PR)

| File | PR | Upstream path |
|---|---|---|
| `pricing.py` | PR 3a (#184) | `custom_components/flow_power_ha/pricing.py` |
| `const.py` (PEA slice) | PR 3a (#184) | `custom_components/flow_power_ha/const.py` (subset) |
| `tariff_utils.py` | PR 3b (#TBD) | `custom_components/flow_power_ha/tariff_utils.py` |
| `const.py` (network/tariff append) | PR 3b (#TBD) | `custom_components/flow_power_ha/const.py` (subset) |

### SHA bump procedure

1. Identify the new upstream commit SHA.
2. For each vendored file, run `diff` against upstream to confirm the
   only intended change.
3. Update the **Upstream commit** field above.
4. Open a single `chore(deps): bump Flow Power vendor to <SHA>` PR.
