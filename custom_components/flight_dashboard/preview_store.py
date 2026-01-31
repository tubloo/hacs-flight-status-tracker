"""Server-side preview store for 'add flight' flow.

Compatibility layer for older modules that expect preview_store.* helpers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant

DOMAIN = "flight_dashboard"
DATA_KEY = "add_preview"


@dataclass
class PreviewState:
    ready: bool = False
    error: str | None = None
    hint: str | None = None
    input: dict[str, Any] | None = None
    flight: dict[str, Any] | None = None


def get_preview(hass: HomeAssistant) -> PreviewState:
    data = hass.data.setdefault(DOMAIN, {})
    st = data.get(DATA_KEY)
    if isinstance(st, PreviewState):
        return st
    st = PreviewState()
    data[DATA_KEY] = st
    return st


def set_preview(hass: HomeAssistant, st: PreviewState) -> None:
    hass.data.setdefault(DOMAIN, {})[DATA_KEY] = st


def clear_preview(hass: HomeAssistant) -> None:
    hass.data.setdefault(DOMAIN, {})[DATA_KEY] = PreviewState()


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
