"""Flight Status Tracker integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .services import async_register_services
from .services_preview import async_register_preview_services
from .directory import async_refresh_builtin_airports_cache
from .providers.airportsdata.directory import AIRPORTSDATA_AIRPORTS_URL


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Flight Status Tracker from a config entry."""

    # Options provider callable (used by services to read API keys etc.)
    def _options_provider():
        return dict(entry.options)

    # Migrate directory options to airportsdata built-in if applicable
    options = dict(entry.options)
    changed = False
    src = options.get("directory_source")
    if src not in ("aviationstack", "airlabs", "fr24"):
        # Default all non-API sources to built-in airportsdata
        options["directory_source"] = "airportsdata"
        changed = True
    if options.get("directory_source") == "airportsdata":
        if options.get("cache_ttl_days") != 30:
            options["cache_ttl_days"] = 30
            changed = True
    if "directory_airports_url" in options:
        options.pop("directory_airports_url", None)
        changed = True
    if changed:
        hass.config_entries.async_update_entry(entry, options=options)

    # Register manual flight services (add/remove/clear)
    await async_register_services(hass)

    # Register preview/confirm/clear-preview services
    await async_register_preview_services(hass, _options_provider)

    # Refresh built-in airports cache on reload if empty/stale
    await async_refresh_builtin_airports_cache(hass, _options_provider())

    # Load sensor/button/select/etc platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
