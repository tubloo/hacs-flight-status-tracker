"""Server-side preview store for 'add flight' flow."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

async def async_get_preview(hass: HomeAssistant) -> dict[str, Any] | None:
    """Async wrapper to load preview from storage."""
    from .storage import async_load_preview

    return await async_load_preview(hass)


async def async_set_preview(hass: HomeAssistant, preview: dict[str, Any] | None) -> None:
    """Async wrapper to save/clear preview in storage."""
    from .storage import async_clear_preview, async_save_preview

    if preview is None:
        await async_clear_preview(hass)
    else:
        await async_save_preview(hass, preview)
