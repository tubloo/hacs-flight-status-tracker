"""Storage helpers for Flight Dashboard.

This module provides:
- Preview storage (single preview object used by preview/confirm flow)
- Optional static cache storage (airports/airlines) for future use

All storage is server-side using Home Assistant's Store.
"""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, SCHEMA_VERSION

# Storage versioning
_STORAGE_VERSION = 1

# Store keys
_STORE_PREVIEW = f"{DOMAIN}.add_preview"
_STORE_CACHE = f"{DOMAIN}.static_cache"


def _store(hass: HomeAssistant, key: str) -> Store:
    return Store(hass, _STORAGE_VERSION, key)


# ----------------------------
# Preview (single object storage)
# ----------------------------
async def async_load_preview(hass: HomeAssistant) -> dict[str, Any] | None:
    """Load the current add-flight preview object."""
    data = await _store(hass, _STORE_PREVIEW).async_load()
    if not data:
        return None
    preview = data.get("preview")
    return preview if isinstance(preview, dict) else None


async def async_save_preview(hass: HomeAssistant, preview: dict[str, Any]) -> None:
    """Save the add-flight preview object."""
    await _store(hass, _STORE_PREVIEW).async_save(
        {
            "schema_version": SCHEMA_VERSION,
            "preview": preview,
        }
    )


async def async_clear_preview(hass: HomeAssistant) -> None:
    """Clear the add-flight preview object."""
    await _store(hass, _STORE_PREVIEW).async_save(
        {
            "schema_version": SCHEMA_VERSION,
            "preview": None,
        }
    )


# ----------------------------
# Static cache (optional)
# ----------------------------
async def async_load_static_cache(hass: HomeAssistant) -> dict[str, Any]:
    """Load static cache (airports/airlines)."""
    data = await _store(hass, _STORE_CACHE).async_load()
    if not data or not isinstance(data.get("cache"), dict):
        return {}
    return data["cache"]


async def async_save_static_cache(hass: HomeAssistant, cache: dict[str, Any]) -> None:
    """Save static cache (airports/airlines)."""
    await _store(hass, _STORE_CACHE).async_save(
        {
            "schema_version": SCHEMA_VERSION,
            "cache": cache,
        }
    )
