# Third-Party Notices

This integration vendors source code from other projects. License texts
for each are reproduced under ``LICENSES/``.

## Flow Power HA (bolagnaise/Flow-Power-HA)

- **Source**: https://github.com/bolagnaise/Flow-Power-HA
- **Upstream commit**: `3c2a9bb77dfa30eab3646a31703e10ad6743d10f`
- **License**: MIT (see [`LICENSES/flow-power-ha.LICENSE`](LICENSES/flow-power-ha.LICENSE))
- **Copyright**: 2025 bolagnaise

Vendored under `custom_components/pricehawk/wholesale/flow_power/`.
Files in this directory are treated as third-party — verbatim from
upstream **except** for the targeted forks tracked below. Every fork is
labelled with a `FORK(#PR)` comment at the call site so a re-vendor knows
where the patches go.

### Vendored files (by PR)

| File | PR | Upstream path |
|---|---|---|
| `pricing.py` | PR 3a (#184) | `custom_components/flow_power_ha/pricing.py` |
| `const.py` (PEA slice) | PR 3a (#184) | `custom_components/flow_power_ha/const.py` (subset) |
| `tariff_utils.py` | PR 3b (#186) | `custom_components/flow_power_ha/tariff_utils.py` (with forks below) |
| `const.py` (network/tariff append) | PR 3b (#186) | `custom_components/flow_power_ha/const.py` (subset, with forks below) |

### Forks against upstream

These changes diverge from the upstream commit recorded above. Each
addresses a Codex P1 finding on PR #186 where leaving the vendor verbatim
would ship a known correctness bug for real customers. Re-apply on every
SHA bump.

| File | Fork | Rationale |
|---|---|---|
| `const.py` `REGION_NETWORKS["NSW1"]` | Added `"Evoenergy"` | ACT is priced in the NSW1 NEM region; upstream's omission blocks ACT/Evoenergy customers from the region-driven selection flow even though the rest of the tables include the DNSP. |
| `const.py` `NETWORK_API_NAME["United"]` | `"victoria"` → `"united"` | `aemo_to_tariff` ships a dedicated `united` backend; routing through `victoria` returns generic placeholder rates and miscalculates every United Energy customer's PEA. |
| `const.py` `NETWORK_MODULE_NAME["United"]` | `"victoria"` → `"united"` | Paired with the above so `importlib` loads the right module's tariff schedule. |
| `const.py` `NETWORK_TIMEZONE` | **New table** (no upstream) | Required by the `compute_avg_daily_tariff` fork below — maps each `aemo_to_tariff` network parameter to its IANA timezone. |
| `tariff_utils.py` `compute_avg_daily_tariff` | DNSP-local timezone instead of fixed UTC+10 | The 48-slot daily sweep was anchored at midnight AEST, biasing the average for SA (+9:30 / +10:30 DST), NSW/VIC/TAS (DST), and the 1 July tariff transition. v2 PEA subtracts this average from every calc, so the bias flows into user-visible dollar numbers. |
| `tariff_utils.py` `get_tariff_codes_for_network` | Fallback chain (`tariffs` → `get_tariffs()` → `tariffs_YYYY_YY`) | Recent `aemo_to_tariff` releases moved schedules off the top-level `tariffs` dict for modules like `ausgrid`/`sapower`, so upstream's lookup returns `[]` for valid DNSPs once HA installs the library at PR 4. |

### SHA bump procedure

1. Identify the new upstream commit SHA.
2. For each vendored file, run `diff` against the new upstream — expect
   the FORK(#PR) lines listed above; any unexpected drift needs review.
3. Re-apply each fork on top of the new upstream content. If upstream
   has independently fixed a forked issue, delete the fork entry from
   this table and the matching `FORK(#PR)` comment in the source.
4. Update the **Upstream commit** field above.
5. Open a single `chore(deps): bump Flow Power vendor to <SHA>` PR.
