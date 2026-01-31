"""Flight Dashboard sensor: exposes canonical flight timeline fields.

Sensor rebuilds automatically when manual flights change (dispatcher signal).
"""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any, Callable

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_MANUAL_FLIGHTS_UPDATED
from .coordinator_agg import merge_segments
from .providers.itinerary.manual import ManualItineraryProvider
from .status_manager import async_update_statuses
from .tz_short import tz_short_name
from .airport_tz import get_airport_tz


SCHEMA_VERSION = 3
_LOGGER = logging.getLogger(__name__)

SCHEMA_DOC = """\
Flight Dashboard schema (v3)

Per flight:
- dep.scheduled/estimated/actual
- arr.scheduled/estimated/actual
- dep.airport.tz + tz_short
- arr.airport.tz + tz_short
- airline_logo_url (optional), aircraft_type (optional)
""".strip()

SCHEMA_EXAMPLE: dict[str, Any] = {
    "flight_key": "AI-157-DEL-2026-01-30",
    "source": "manual",
    "airline_code": "AI",
    "flight_number": "157",
    "aircraft_type": "B788",
    "travellers": ["Sumit", "Parul"],
    "status_state": "scheduled",
    "dep": {"airport": {"iata": "DEL", "tz": "Asia/Kolkata", "tz_short": "IST", "city": "Delhi"}, "scheduled": "2026-01-30T14:00:00+00:00"},
    "arr": {"airport": {"iata": "CPH", "tz": "Europe/Copenhagen", "tz_short": "CET", "city": "Copenhagen"}, "scheduled": "2026-01-30T18:55:00+00:00"},
}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    entities = [FlightDashboardUpcomingFlightsSensor(hass, entry)]
    try:
        from .preview_sensor import FlightDashboardAddPreviewSensor
    except Exception:
        FlightDashboardAddPreviewSensor = None  # type: ignore
    if FlightDashboardAddPreviewSensor:
        entities.append(FlightDashboardAddPreviewSensor(hass, entry))
    async_add_entities(entities, True)


class FlightDashboardUpcomingFlightsSensor(SensorEntity):
    _attr_name = "Flight Dashboard Upcoming Flights"
    _attr_icon = "mdi:airplane-clock"

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = f"{DOMAIN}_upcoming_flights"
        self._flights: list[dict[str, Any]] = []
        self._unsub: Callable[[], None] | None = None
        self._next_refresh_unsub: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        # Rebuild now
        await self._rebuild()

        # Rebuild whenever manual flights are updated
        @callback
        def _on_manual_updated() -> None:
            self.hass.async_create_task(self._rebuild())

        self._unsub = async_dispatcher_connect(self.hass, SIGNAL_MANUAL_FLIGHTS_UPDATED, _on_manual_updated)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._next_refresh_unsub:
            self._next_refresh_unsub()
            self._next_refresh_unsub = None

    @property
    def native_value(self) -> str:
        n = len(self._flights)
        return f"{n} flight" if n == 1 else f"{n} flights"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "schema_doc": SCHEMA_DOC,
            "schema_example": SCHEMA_EXAMPLE,
            "flights": self._flights,
        }

    async def _rebuild(self) -> None:
        """Rebuild the flight list and schedule the next smart refresh."""
        if self._next_refresh_unsub:
            self._next_refresh_unsub()
            self._next_refresh_unsub = None

        now = dt_util.utcnow()
        options = dict(self.entry.options)
        include_past = int(options.get("include_past_hours", 6))
        days_ahead = int(options.get("days_ahead", 30))
        max_flights = int(options.get("max_flights", 50))

        start = now - timedelta(hours=include_past)
        end = now + timedelta(days=days_ahead)

        segments: list[dict[str, Any]] = []
        providers = options.get("itinerary_providers") or ["manual"]

        if "manual" in providers:
            segments.extend(await ManualItineraryProvider(self.hass).async_get_segments(start, end))

        if "tripit" in providers:
            try:
                from .providers.itinerary.tripit import TripItItineraryProvider
            except Exception as e:
                _LOGGER.debug("TripIt provider not available: %s", e)
            else:
                try:
                    segments.extend(await TripItItineraryProvider(self.hass, options).async_get_segments(start, end))
                except Exception as e:
                    _LOGGER.debug("TripIt provider failed: %s", e)

        flights = merge_segments(segments)

        # Limit number of flights early (oldest first)
        if max_flights and len(flights) > max_flights:
            flights = flights[:max_flights]

        flights, next_refresh = await async_update_statuses(self.hass, options, flights)

        for flight in flights:
            flight["editable"] = (flight.get("source") or "manual") == "manual"

            dep = (flight.get("dep") or {})
            arr = (flight.get("arr") or {})
            dep_air = (dep.get("airport") or {})
            arr_air = (arr.get("airport") or {})

            dep_sched = dep.get("scheduled")
            arr_sched = arr.get("scheduled")

            if not dep_air.get("tz"):
                dep_air["tz"] = get_airport_tz(dep_air.get("iata"), options)
            if not arr_air.get("tz"):
                arr_air["tz"] = get_airport_tz(arr_air.get("iata"), options)

            if dep_air.get("tz") and not dep_air.get("tz_short"):
                dep_air["tz_short"] = tz_short_name(dep_air.get("tz"), dep_sched)
            if arr_air.get("tz") and not arr_air.get("tz_short"):
                arr_air["tz_short"] = tz_short_name(arr_air.get("tz"), arr_sched)

            dep["airport"] = dep_air
            arr["airport"] = arr_air
            flight["dep"] = dep
            flight["arr"] = arr

            # keep output clean (drop old UI-only duplicates if they exist)
            for legacy in (
                "dep_local_str","arr_local_str","dep_viewer_str","arr_viewer_str",
                "dep_local_hm_new","dep_local_hm_sched","dep_viewer_hm_new","dep_viewer_hm_sched",
                "arr_local_hm_new","arr_local_hm_sched","arr_viewer_hm_new","arr_viewer_hm_sched",
                "delay_minutes","gate_dep","gate_arr","terminal_dep","terminal_arr",
            ):
                flight.pop(legacy, None)

        self._flights = flights
        self.async_write_ha_state()

        if next_refresh:
            @callback
            def _scheduled_refresh(_now) -> None:
                self.hass.async_create_task(self._rebuild())

            self._next_refresh_unsub = async_track_point_in_utc_time(self.hass, _scheduled_refresh, next_refresh)
