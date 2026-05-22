"""Diagnostics platform for PriceHawk (Phase 8 / PR-7).

Returns a redacted snapshot of the config entry + selected coordinator
state for the HA "Download diagnostics" button.

Every API key and HA token field is replaced with ``**REDACTED**`` by
``async_redact_data``. The CDR plan envelope is also redacted because
it's large (~15 KB per plan) — not a secret but blows up the output
size if a user has 5+ entries. See D-P8-3.

The output is intentionally JSON-serialisable (no datetimes, no aiohttp
session refs, no asyncio.Lock refs). Diagnostics is invoked via HA's
diagnostics REST endpoint which calls ``json.dumps`` on the result.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_API_KEY,
    CONF_CDR_PLAN,
    CONF_DWT_OE_API_KEY,
    CONF_HA_TOKEN,
    CONF_LOCALVOLTS_API_KEY,
    CONF_NAMED_COMPARATOR_PLAN,
)

TO_REDACT = {
    CONF_API_KEY,
    CONF_DWT_OE_API_KEY,
    CONF_LOCALVOLTS_API_KEY,
    CONF_HA_TOKEN,
    # Plan envelopes — not secret but large (15 KB each). Drop to keep
    # diagnostics output small. See D-P8-3.
    CONF_CDR_PLAN,
    CONF_NAMED_COMPARATOR_PLAN,
    # DWT-OE / Amber / LocalVolts static plans (per-comparator PRD
    # envelopes from Phase 7 PR-4 — same large-but-not-secret rationale).
    "amber_static_plan",
    "flow_power_static_plan",
    "localvolts_static_plan",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a PriceHawk config entry."""
    del hass  # not currently needed; reserved for future expansion
    coordinator = getattr(
        getattr(entry, "runtime_data", None), "coordinator", None
    )

    redacted_data = async_redact_data(dict(entry.data), TO_REDACT)
    redacted_options = async_redact_data(dict(entry.options), TO_REDACT)
    redaction_count = (
        sum(1 for k in entry.data if k in TO_REDACT)
        + sum(1 for k in entry.options if k in TO_REDACT)
    )

    runtime_state: dict[str, Any] = {}
    if coordinator is not None:
        runtime_state = {
            "amber_mode": getattr(coordinator, "_amber_mode", None),
            "flow_power_mode": getattr(coordinator, "_flow_power_mode", None),
            "localvolts_mode": getattr(coordinator, "_localvolts_mode", None),
            "reauth_provider_id": getattr(
                coordinator, "_reauth_provider_id", None
            ),
            "registered_provider_ids": sorted(
                getattr(coordinator, "_providers", {}).keys()
            ),
            "wholesale_settlement": getattr(
                coordinator, "_wholesale_settlement", ""
            ),
            "wholesale_c": getattr(coordinator, "_wholesale_c", None),
            "amber_import_c": getattr(coordinator, "_amber_import_c", None),
            "amber_export_c": getattr(coordinator, "_amber_export_c", None),
            "saving_month_aud": getattr(
                coordinator, "_saving_month_aud", None
            ),
            "daily_cost_history_len": len(
                getattr(coordinator, "_daily_cost_history", []) or []
            ),
            "ranking_last_run_at": _safe_iso(
                getattr(coordinator, "_ranking_last_run_at", None)
            ),
            "backfill_status": getattr(coordinator, "_backfill_status", None),
        }
        # DWT price attribution snapshot, if a DWT provider is the
        # current plan (Phase 7 PR-2b).
        dwt = getattr(coordinator, "_dwt_provider", None)
        if dwt is not None:
            last_price = getattr(dwt, "last_price", None)
            runtime_state["dwt"] = {
                "region": getattr(dwt, "region", None),
                "last_price_aud_per_mwh": (
                    last_price.price_aud_per_mwh if last_price else None
                ),
                "last_price_interval_end_utc": (
                    last_price.interval_end_utc.isoformat()
                    if last_price
                    else None
                ),
                "attribution": (
                    last_price.attribution if last_price else None
                ),
            }

    return {
        "entry_id": entry.entry_id,
        "entry_data": redacted_data,
        "entry_options": redacted_options,
        "runtime_state": runtime_state,
        "_redaction_count": redaction_count,
    }


def _safe_iso(value: Any) -> str | None:
    """ISO-format a datetime if present; else return None."""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if not callable(isoformat):
        return None
    result = isoformat()
    return result if isinstance(result, str) else None
