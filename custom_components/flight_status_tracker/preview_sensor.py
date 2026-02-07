"""Preview sensor for 'add manual flight' flow."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .preview_store import async_get_preview
from .const import SIGNAL_PREVIEW_UPDATED


class FlightDashboardAddPreviewSensor(SensorEntity):
    _attr_name = "Flight Dashboard Add Preview"
    _attr_unique_id = "flight_status_tracker_add_preview"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._preview: dict[str, Any] | None = None
        self._unsub = None

    async def async_added_to_hass(self) -> None:
        @callback
        def _kick(_event=None) -> None:
            self.hass.async_create_task(self._refresh())

        self._unsub = async_dispatcher_connect(self.hass, SIGNAL_PREVIEW_UPDATED, _kick)
        await self._refresh()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def _refresh(self) -> None:
        self._preview = await async_get_preview(self.hass)
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        if not self._preview:
            return "empty"
        return "ready" if self._preview.get("ready") else "incomplete"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"preview": self._preview} if self._preview else {"preview": None}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    async_add_entities([FlightDashboardAddPreviewSensor(hass, entry)])
