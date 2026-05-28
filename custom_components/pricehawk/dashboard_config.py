"""Dashboard panel registration for PriceHawk."""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "pricehawk-dashboard"
PANEL_TITLE = "PriceHawk"
PANEL_ICON = "mdi:flash"

# Constitution P14 (prefer systemic fixes) + P20 (observability) —
# centralise manifest-version lookup so every panel/resource cache-buster
# uses the same code path. Failures are LOGGED (not silently swallowed)
# and fall back to the supplied sentinel so callers stay resilient.
_VERSION_UNKNOWN = "unknown"


async def _get_manifest_version(hass: HomeAssistant, default: str = _VERSION_UNKNOWN) -> str:
    """Return the integration's manifest version, or ``default`` on failure.

    Looks up the loaded ``pricehawk`` integration via
    ``homeassistant.loader.async_get_integration`` and reads
    ``manifest["version"]``. Any failure (loader unavailable, integration
    not loaded yet, missing key, unexpected manifest shape) is logged at
    WARNING level so operators have a diagnostic trail when the dashboard
    cache-buster reads ``unknown`` — previously the ``except Exception:
    version = "unknown"`` pattern produced a user-visible bad version
    with zero log output, making the failure mode invisible.
    """
    try:
        # Local import — ``homeassistant.loader`` is HA-only and must not
        # be imported at module top to keep pure-Python tests importable
        # under the harness conftest mock.
        from homeassistant.loader import async_get_integration  # noqa: PLC0415

        integration = await async_get_integration(hass, "pricehawk")
        return integration.manifest.get("version", default)
    except Exception as exc:  # noqa: BLE001 — loader can raise broadly
        _LOGGER.warning(
            "PriceHawk: version lookup failed (%s); using %r",
            exc,
            default,
        )
        return default


# Phase 10 PR-13 — Lit panel_custom (no LLAT). Registered alongside the
# iframe panel during the migration window; legacy iframe stays until
# the Lit panel reaches feature parity (follow-up Playwright UAT PR).
PANEL_V2_URL_PATH = "pricehawk"
PANEL_V2_TITLE = "PriceHawk v2"
PANEL_V2_MODULE = "pricehawk-panel"  # custom element name in the JS module

# Phase 10 PR-14 — Lovelace custom card. Auto-registered as a frontend
# resource on entry setup; appears in the "Add Card" picker.
LOVELACE_CARD_FILENAME = "pricehawk-card.js"
LOVELACE_CARD_RESOURCE_URL = "/local/pricehawk/pricehawk-card.js"

# Inline SVG icon (PriceHawk hawk logo)
PRICEHAWK_ICON_SVG = """\
<svg width="512" height="512" viewBox="0 0 512 512" xmlns="http://www.w3.org/2000/svg">
  <rect width="512" height="512" rx="108" fill="#111111"/>
  <path d="M 100 200 C 130 130, 300 95, 390 145 C 425 165, 415 215, 370 228 L 285 242 L 308 182 C 265 162, 195 168, 162 202 Z" fill="#FF8C00"/>
  <path d="M 285 242 L 232 325 L 272 325 L 195 435 L 342 300 L 294 300 L 370 228 Z" fill="#FFD600"/>
  <path d="M 285 242 L 294 300 L 370 228 Z" fill="#FFB300"/>
</svg>
"""


async def copy_www_assets(hass: HomeAssistant) -> None:
    """Copy PriceHawk icon SVG, legacy dashboard HTML, and v2 Lit panel JS.

    Always overwrites to ensure the latest version is deployed.
    The HTML dashboard becomes accessible at /local/pricehawk/dashboard.html.
    The v2 Lit panel JS becomes accessible at /local/pricehawk/pricehawk-panel.js.
    """
    src_dir = Path(__file__).parent
    src_html = src_dir / "www" / "dashboard.html"
    src_panel_js = src_dir / "www" / "pricehawk-panel.js"
    src_card_js = src_dir / "www" / LOVELACE_CARD_FILENAME
    src_icon_png = src_dir / "icon.png"
    dest_dir = hass.config.path("www", "pricehawk")
    icon_svg_path = os.path.join(dest_dir, "icon.svg")
    icon_png_path = os.path.join(dest_dir, "icon.png")
    html_path = os.path.join(dest_dir, "dashboard.html")
    panel_js_path = os.path.join(dest_dir, "pricehawk-panel.js")
    card_js_path = os.path.join(dest_dir, LOVELACE_CARD_FILENAME)

    def _copy_assets() -> None:
        os.makedirs(dest_dir, exist_ok=True)
        # Always write SVG icon
        with open(icon_svg_path, "w", encoding="utf-8") as f:
            f.write(PRICEHAWK_ICON_SVG)
        # Copy PNG icon (used by dashboard favicon and nav brand)
        if src_icon_png.exists():
            shutil.copy2(str(src_icon_png), icon_png_path)
        # Always copy HTML dashboard (overwrite to pick up updates)
        if src_html.exists():
            shutil.copy2(str(src_html), html_path)
        else:
            _LOGGER.warning("PriceHawk: dashboard.html source not found at %s", src_html)
        # Phase 10 PR-13 — copy v2 Lit panel JS.
        if src_panel_js.exists():
            shutil.copy2(str(src_panel_js), panel_js_path)
        else:
            _LOGGER.warning(
                "PriceHawk: pricehawk-panel.js source not found at %s",
                src_panel_js,
            )
        # Phase 10 PR-14 — copy Lovelace card JS.
        if src_card_js.exists():
            shutil.copy2(str(src_card_js), card_js_path)
        else:
            _LOGGER.warning(
                "PriceHawk: %s source not found at %s",
                LOVELACE_CARD_FILENAME,
                src_card_js,
            )

    try:
        await hass.async_add_executor_job(_copy_assets)
        _LOGGER.info("PriceHawk: www assets copied to %s (icon + dashboard HTML)", dest_dir)
    except Exception:
        _LOGGER.warning("PriceHawk: could not copy www assets to %s", dest_dir, exc_info=True)


async def setup_panel_iframe(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register a sidebar panel_iframe pointing to the HTML dashboard.

    Uses async_register_built_in_panel with component_name="iframe" to create
    a sidebar entry that loads /local/pricehawk/dashboard.html.

    A ``?v=<cache-buster>`` query param is appended so that browser and
    service-worker caches automatically invalidate on every HACS upgrade —
    without this, clients keep serving the previous dashboard.html.

    Codex P0-1 (full-repo review 2026-05-23): the HA long-lived access
    token is NO LONGER appended to the URL. Tokens in URLs leak via
    browser history, referrer headers, screenshots, panel config dumps
    and logs — redacting the log line did not stop the other paths.
    The iframe page (``dashboard.html``) already has a four-method
    auth-fallback chain (URL → parent frame ``hassConnection`` →
    parent ``localStorage.hassTokens`` → local ``localStorage``); it
    falls back cleanly to method 2/3/4 in HA's same-origin iframe
    context, so removing method 1 from the Python side is safe.
    The new ``setup_panel_custom_v2`` Lit panel is the long-term
    replacement that bypasses iframes entirely — see PR #97.

    ``entry`` is kept in the signature so callers don't change; the
    parameter is currently unused.
    """
    del entry  # No longer reads `ha_token` from the entry.
    from homeassistant.components.frontend import (
        async_register_built_in_panel,
        async_remove_panel,
    )

    # Look up the integration's manifest version for cache busting.
    # Failures are logged inside the helper (Constitution P20).
    version = await _get_manifest_version(hass)

    # Build the dashboard URL with version + epoch cache-buster.
    # The epoch portion guarantees every HA restart / integration reload yields a
    # new iframe URL, defeating the 31-day max-age set by HA's /local/ static
    # handler — without it, browsers and the HA companion app can pin a stale
    # dashboard.html for weeks even after a HACS upgrade.
    cache_token = f"{version}.{int(time.time())}"
    dashboard_url = f"/local/pricehawk/dashboard.html?v={cache_token}"

    # Remove existing panel first (cache-buster changes on re-setup)
    try:
        async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
        _LOGGER.debug("PriceHawk: removed existing panel before re-registering")
    except Exception:
        # Panel didn't exist yet — that's fine
        pass

    try:
        async_register_built_in_panel(
            hass,
            component_name="iframe",
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            frontend_url_path=PANEL_URL_PATH,
            config={"url": dashboard_url},
            require_admin=False,
        )
        # Codex P0-1: dashboard_url no longer contains a token; safe to
        # log verbatim. Kept the log line for ops visibility on the
        # cache-buster value so users can confirm a HACS upgrade rolled
        # through.
        _LOGGER.info(
            "PriceHawk: sidebar panel registered at /%s -> %s",
            PANEL_URL_PATH,
            dashboard_url,
        )
    except Exception:
        _LOGGER.error(
            "PriceHawk: failed to register sidebar panel. "
            "The dashboard is still accessible at /local/pricehawk/dashboard.html",
            exc_info=True,
        )


async def setup_panel_custom_v2(hass: HomeAssistant) -> None:
    """Phase 10 PR-13 — register Lit panel_custom (no LLAT in URL).

    Lives alongside the legacy iframe panel during the migration. Auth
    runs through the host page's WebSocket session — no long-lived
    access token threaded through query params. Version-busted module
    URL invalidates the browser cache on every HACS upgrade.

    Per HA docs at https://developers.home-assistant.io/docs/frontend/custom-ui/registering-resources/,
    ``trust_external=False`` + ``embed_iframe=False`` is the recommended
    pattern for first-party panels.
    """
    from homeassistant.components.frontend import (
        async_register_built_in_panel,
        async_remove_panel,
    )

    # Failures are logged inside the helper (Constitution P20).
    version = await _get_manifest_version(hass)

    cache_token = f"{version}.{int(time.time())}"
    module_url = f"/local/pricehawk/pricehawk-panel.js?v={cache_token}"

    # Remove existing v2 panel before re-registering (handles reload cycles).
    try:
        async_remove_panel(hass, PANEL_V2_URL_PATH, warn_if_unknown=False)
    except Exception:
        pass

    try:
        async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title=PANEL_V2_TITLE,
            sidebar_icon=PANEL_ICON,
            frontend_url_path=PANEL_V2_URL_PATH,
            config={
                "_panel_custom": {
                    "name": PANEL_V2_MODULE,
                    "module_url": module_url,
                    "embed_iframe": False,
                    "trust_external": False,
                }
            },
            require_admin=False,
        )
        _LOGGER.info(
            "PriceHawk v2 panel registered at /%s -> %s",
            PANEL_V2_URL_PATH,
            module_url,
        )
    except Exception:
        _LOGGER.error(
            "PriceHawk: failed to register v2 panel_custom. Legacy iframe dashboard is unaffected.",
            exc_info=True,
        )


async def register_lovelace_card_resource(hass: HomeAssistant) -> None:
    """Phase 10 PR-14 — auto-register the PriceHawk Lovelace card resource.

    Best-effort: HA's Lovelace resources API is mode-dependent
    (storage vs YAML mode). Storage mode supports
    ``ResourceStorageCollection.async_create_item``; YAML mode requires
    the user to add the resource manually. We attempt the storage-mode
    path; on failure (YAML mode, or HA version drift), log a hint
    pointing at the manual-add instructions.
    """
    try:
        from homeassistant.components import lovelace  # noqa: F401, PLC0415
    except Exception:
        _LOGGER.info(
            "PriceHawk Lovelace card: lovelace component not available; "
            "skipping auto-registration. Add manually via Resources: %s",
            LOVELACE_CARD_RESOURCE_URL,
        )
        return

    try:
        # Failures are logged inside the helper (Constitution P20).
        # Lovelace card resources have historically used "1" as the
        # version sentinel; preserved here to keep stale-resource URLs
        # comparable across upgrades.
        version = await _get_manifest_version(hass, default="1")

        resource_url = f"{LOVELACE_CARD_RESOURCE_URL}?v={version}"
        # Modern HA exposes resources via hass.data["lovelace"].resources.
        ll_data = hass.data.get("lovelace")
        ll_resources = getattr(ll_data, "resources", None)
        if ll_resources is None:
            _LOGGER.info(
                "PriceHawk Lovelace card: Lovelace storage not ready "
                "(YAML mode?). Add resource manually: %s",
                resource_url,
            )
            return
        # Avoid duplicate registration on entry reload.
        existing = [
            r
            for r in getattr(ll_resources, "async_items", lambda: [])()
            if str(r.get("url", "")).startswith(LOVELACE_CARD_RESOURCE_URL)
        ]
        if existing:
            _LOGGER.debug(
                "PriceHawk Lovelace card: resource already registered",
            )
            return
        await ll_resources.async_create_item({"res_type": "module", "url": resource_url})
        _LOGGER.info(
            "PriceHawk Lovelace card: resource registered at %s",
            resource_url,
        )
    except Exception:
        _LOGGER.warning(
            "PriceHawk Lovelace card: auto-register failed. Add manually "
            "via Settings > Dashboards > Resources: url=%s, type=module",
            LOVELACE_CARD_RESOURCE_URL,
            exc_info=True,
        )


async def remove_panel(hass: HomeAssistant) -> None:
    """Remove the PriceHawk sidebar panels on unload."""
    from homeassistant.components.frontend import async_remove_panel

    for path in (PANEL_URL_PATH, PANEL_V2_URL_PATH):
        try:
            async_remove_panel(hass, path, warn_if_unknown=False)
            _LOGGER.info("PriceHawk: sidebar panel %s removed", path)
        except Exception:
            _LOGGER.debug(
                "PriceHawk: panel %s removal skipped (not registered)",
                path,
            )
