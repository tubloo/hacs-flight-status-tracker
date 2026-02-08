"""Flight Status Tracker integration."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, PLATFORMS
from .services import async_register_services
from .services_preview import async_register_preview_services
from .directory import async_refresh_builtin_airports_cache, async_refresh_builtin_airlines_cache


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Flight Status Tracker from a config entry."""

    # Options provider callable (used by services to read API keys etc.)
    def _options_provider():
        return dict(entry.options)

    # Register manual flight services (add/remove/clear)
    await async_register_services(hass)

    # Register preview/confirm/clear-preview services
    await async_register_preview_services(hass, _options_provider)

    # Refresh built-in directory caches on reload if empty/stale
    await async_refresh_builtin_airports_cache(hass)
    await async_refresh_builtin_airlines_cache(hass)

    # Keep directory datasets fresh for long-running HA instances.
    # The refresh functions are TTL-guarded, so running this daily is cheap.
    async def _periodic_refresh(_now) -> None:
        await async_refresh_builtin_airports_cache(hass)
        await async_refresh_builtin_airlines_cache(hass)

    unsub = async_track_time_interval(hass, _periodic_refresh, timedelta(days=1))
    hass.data.setdefault(DOMAIN, {}).setdefault("unsub_directory_refresh", {})[entry.entry_id] = unsub

    # Load sensor/button/select/etc platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unsubs = hass.data.get(DOMAIN, {}).get("unsub_directory_refresh") or {}
    unsub = unsubs.pop(entry.entry_id, None) if isinstance(unsubs, dict) else None
    if unsub:
        try:
            unsub()
        except Exception:
            pass
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
