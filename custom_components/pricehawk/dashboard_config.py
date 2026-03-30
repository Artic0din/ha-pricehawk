"""Dashboard panel registration for PriceHawk."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "pricehawk-dashboard"
PANEL_TITLE = "PriceHawk"
PANEL_ICON = "mdi:flash"

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
    """Copy PriceHawk icon SVG and dashboard HTML to www/pricehawk/.

    Always overwrites to ensure the latest version is deployed.
    The HTML dashboard becomes accessible at /local/pricehawk/dashboard.html.
    """
    src_html = Path(__file__).parent / "www" / "dashboard.html"
    dest_dir = hass.config.path("www", "pricehawk")
    icon_path = os.path.join(dest_dir, "icon.svg")
    html_path = os.path.join(dest_dir, "dashboard.html")

    def _copy_assets() -> None:
        os.makedirs(dest_dir, exist_ok=True)
        # Always write icon
        with open(icon_path, "w", encoding="utf-8") as f:
            f.write(PRICEHAWK_ICON_SVG)
        # Always copy HTML dashboard (overwrite to pick up updates)
        if src_html.exists():
            shutil.copy2(str(src_html), html_path)
        else:
            _LOGGER.warning(
                "PriceHawk: dashboard.html source not found at %s", src_html
            )

    try:
        await hass.async_add_executor_job(_copy_assets)
        _LOGGER.info(
            "PriceHawk: www assets copied to %s (icon + dashboard HTML)", dest_dir
        )
    except Exception:
        _LOGGER.warning(
            "PriceHawk: could not copy www assets to %s", dest_dir, exc_info=True
        )


async def setup_panel_iframe(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register a sidebar panel_iframe pointing to the HTML dashboard.

    Uses async_register_built_in_panel with component_name="iframe" to create
    a sidebar entry that loads /local/pricehawk/dashboard.html.

    NOTE: The HA long-lived access token is appended as a URL query parameter.
    This is a security concern (tokens in URLs can appear in logs/referrer headers).
    Future improvement: use a session-based auth approach instead.
    """
    from homeassistant.components.frontend import (
        async_register_built_in_panel,
        async_remove_panel,
    )

    # Build the dashboard URL
    ha_token = entry.data.get("ha_token", "")
    dashboard_url = "/local/pricehawk/dashboard.html"
    if ha_token:
        dashboard_url += f"?token={ha_token}"

    # Remove existing panel first (token may have changed on re-setup)
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


async def remove_panel(hass: HomeAssistant) -> None:
    """Remove the PriceHawk sidebar panel on unload."""
    try:
        from homeassistant.components.frontend import async_remove_panel

        async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
        _LOGGER.info("PriceHawk: sidebar panel removed")
    except Exception:
        _LOGGER.debug("PriceHawk: panel removal skipped (not registered)")
