"""Flight Status Tracker integration."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DOMAIN,
    PLATFORMS,
    SERVICE_ADD_MANUAL_FLIGHT,
    SERVICE_REMOVE_MANUAL_FLIGHT,
    SERVICE_CLEAR_MANUAL_FLIGHTS,
    SERVICE_REFRESH_NOW,
    SERVICE_PRUNE_LANDED,
    SERVICE_PREVIEW_FLIGHT,
    SERVICE_CONFIRM_ADD,
    SERVICE_CLEAR_PREVIEW,
    SERVICE_ADD_FLIGHT,
)
from .services import async_register_services
from .services_preview import async_register_preview_services
from .directory import async_refresh_builtin_airports_cache, async_refresh_builtin_airlines_cache
from .api_metrics import async_init_api_metrics, async_flush_api_metrics


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Flight Status Tracker from a config entry."""
    await async_init_api_metrics(hass)

    # Options provider callable (used by services to read API keys etc.)
    def _options_provider():
        return dict(entry.options)

    # Register manual flight services (add/remove/clear)
    await async_register_services(hass)

    # Register preview/confirm/clear-preview services
    await async_register_preview_services(hass, _options_provider)

    # Refresh built-in directory caches on reload if empty/stale.
    # Run in background to avoid delaying platform/entity startup on slow networks.
    hass.async_create_task(async_refresh_builtin_airports_cache(hass))
    hass.async_create_task(async_refresh_builtin_airlines_cache(hass))

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
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not ok:
        return False

    # Remove domain services when no config entry for this integration remains.
    if not hass.config_entries.async_entries(DOMAIN):
        await async_flush_api_metrics(hass)
        for service in (
            SERVICE_ADD_MANUAL_FLIGHT,
            SERVICE_REMOVE_MANUAL_FLIGHT,
            SERVICE_CLEAR_MANUAL_FLIGHTS,
            SERVICE_REFRESH_NOW,
            SERVICE_PRUNE_LANDED,
            SERVICE_PREVIEW_FLIGHT,
            SERVICE_CONFIRM_ADD,
            SERVICE_CLEAR_PREVIEW,
            SERVICE_ADD_FLIGHT,
        ):
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)
    return True
