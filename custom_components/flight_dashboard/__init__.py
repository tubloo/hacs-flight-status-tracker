"""Flight Dashboard integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .services import async_register_services
from .services_preview import async_register_preview_services


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Flight Dashboard from a config entry."""

    # Options provider callable (used by services to read API keys etc.)
    def _options_provider():
        return dict(entry.options)

    # Register manual flight services (add/remove/clear)
    await async_register_services(hass)

    # Register preview/confirm/clear-preview services
    await async_register_preview_services(hass, _options_provider)

    # Load sensor/button/select/etc platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
