"""Binary sensors for Flight Dashboard."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN, EVENT_UPDATED
from .selected import get_selected_flight, get_flight_position


class FlightDashboardSelectedHasPositionBinarySensor(BinarySensorEntity):
    _attr_name = "Flight Dashboard Selected Has Position"
    _attr_unique_id = "flight_status_tracker_selected_has_position"
    _attr_icon = "mdi:map-marker-radius"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._unsub_state = None
        self._unsub_bus = None
        self._is_on = False
        self._flight: dict[str, Any] | None = None

    async def async_added_to_hass(self) -> None:
        @callback
        def _kick(_event=None) -> None:
            self.hass.async_create_task(self._refresh())

        self._unsub_state = async_track_state_change_event(
            self.hass,
            ["sensor.flight_status_tracker_upcoming_flights", "select.flight_status_tracker_selected_flight"],
            _kick,
        )
        self._unsub_bus = self.hass.bus.async_listen(EVENT_UPDATED, _kick)

        await self._refresh()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._unsub_bus:
            self._unsub_bus()
            self._unsub_bus = None

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pos = get_flight_position(self._flight)
        return {
            "flight_key": (self._flight or {}).get("flight_key"),
            "latitude": (pos or {}).get("lat"),
            "longitude": (pos or {}).get("lon"),
        }

    async def _refresh(self) -> None:
        self._flight = get_selected_flight(self.hass)
        pos = get_flight_position(self._flight)
        self._is_on = bool(pos and pos.get("lat") is not None and pos.get("lon") is not None)
        self.async_write_ha_state()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    async_add_entities([FlightDashboardSelectedHasPositionBinarySensor(hass, entry)])
