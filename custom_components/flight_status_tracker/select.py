"""Select entities for Flight Status Tracker."""
from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import EVENT_UPDATED, SIGNAL_MANUAL_FLIGHTS_UPDATED

SENSOR_ENTITY_ID = "sensor.flight_status_tracker_upcoming_flights"


def _option_for_flight(f: dict[str, Any]) -> str:
    raw = (f.get("flight_key") or "").strip()
    if " | " in raw:
        raw = raw.split(" | ", 1)[0].strip()
    return raw


def _sanitize_option(option: str) -> str:
    opt = (option or "").strip()
    if " | " in opt:
        opt = opt.split(" | ", 1)[0].strip()
    return opt


class FlightDashboardRemoveFlightSelect(SelectEntity):
    _attr_name = "Flight Status Tracker Remove Flight"
    _attr_unique_id = "flight_status_tracker_remove_flight_select"
    _attr_icon = "mdi:airplane-remove"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._attr_options: list[str] = ["No flights"]
        self._attr_current_option: str = "No flights"
        self._unsub_state = None
        self._unsub_bus = None
        self._unsub_dispatcher = None

    async def async_added_to_hass(self) -> None:
        @callback
        def _kick(_event=None) -> None:
            self.hass.async_create_task(self._refresh_options())

        self._unsub_state = async_track_state_change_event(self.hass, [SENSOR_ENTITY_ID], _kick)
        self._unsub_bus = self.hass.bus.async_listen(EVENT_UPDATED, _kick)
        self._unsub_dispatcher = async_dispatcher_connect(self.hass, SIGNAL_MANUAL_FLIGHTS_UPDATED, _kick)

        await self._refresh_options()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._unsub_bus:
            self._unsub_bus()
            self._unsub_bus = None
        if self._unsub_dispatcher:
            self._unsub_dispatcher()
            self._unsub_dispatcher = None

    async def _refresh_options(self) -> None:
        st = self.hass.states.get(SENSOR_ENTITY_ID)
        flights = (st.attributes.get("flights") if st else None) or []

        opts = [_option_for_flight(f) for f in flights if f.get("flight_key")]
        if not opts:
            opts = ["No flights"]

        self._attr_options = opts
        if self._attr_current_option not in self._attr_options:
            self._attr_current_option = self._attr_options[0]

        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        opt = _sanitize_option(option)
        if opt in self._attr_options:
            self._attr_current_option = opt
            self.async_write_ha_state()


class FlightDashboardSelectedFlightSelect(SelectEntity):
    _attr_name = "Flight Status Tracker Selected Flight"
    _attr_unique_id = "flight_status_tracker_selected_flight"
    _attr_icon = "mdi:airplane"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._attr_options: list[str] = ["No flights"]
        self._attr_current_option: str = "No flights"
        self._unsub_state = None
        self._unsub_bus = None
        self._unsub_dispatcher = None

    async def async_added_to_hass(self) -> None:
        @callback
        def _kick(_event=None) -> None:
            self.hass.async_create_task(self._refresh_options())

        self._unsub_state = async_track_state_change_event(self.hass, [SENSOR_ENTITY_ID], _kick)
        self._unsub_bus = self.hass.bus.async_listen(EVENT_UPDATED, _kick)
        self._unsub_dispatcher = async_dispatcher_connect(self.hass, SIGNAL_MANUAL_FLIGHTS_UPDATED, _kick)

        await self._refresh_options()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._unsub_bus:
            self._unsub_bus()
            self._unsub_bus = None
        if self._unsub_dispatcher:
            self._unsub_dispatcher()
            self._unsub_dispatcher = None

    async def _refresh_options(self) -> None:
        st = self.hass.states.get(SENSOR_ENTITY_ID)
        flights = (st.attributes.get("flights") if st else None) or []

        opts = [_option_for_flight(f) for f in flights if f.get("flight_key")]
        if not opts:
            opts = ["No flights"]

        self._attr_options = opts
        if self._attr_current_option not in self._attr_options:
            self._attr_current_option = self._attr_options[0]

        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        opt = _sanitize_option(option)
        if opt in self._attr_options:
            self._attr_current_option = opt
            self.async_write_ha_state()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    async_add_entities(
        [
            FlightDashboardRemoveFlightSelect(hass, entry),
            FlightDashboardSelectedFlightSelect(hass, entry),
        ]
    )
