"""Service registrations for manual flight management.

Backwards compatibility:
- button.py imports SERVICE_CLEAR and SERVICE_REMOVE from here
- older code may import SERVICE_ADD too

So we export those constants as aliases.

Also: __init__.py calls async_register_services(hass, options_provider),
so we accept an optional 2nd arg.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    SERVICE_ADD_MANUAL_FLIGHT,
    SERVICE_REMOVE_MANUAL_FLIGHT,
    SERVICE_CLEAR_MANUAL_FLIGHTS,
    SERVICE_REFRESH_NOW,
    SERVICE_PRUNE_LANDED,
    SIGNAL_MANUAL_FLIGHTS_UPDATED,
)
from .manual_store import async_add_manual_flight, async_remove_manual_flight, async_clear_manual_flights
from .status_manager import clear_status_cache

_LOGGER = logging.getLogger(__name__)

# --- Backwards-compatible exports expected by other platforms ---
SERVICE_ADD = SERVICE_ADD_MANUAL_FLIGHT
SERVICE_REMOVE = SERVICE_REMOVE_MANUAL_FLIGHT
SERVICE_CLEAR = SERVICE_CLEAR_MANUAL_FLIGHTS
# --------------------------------------------------------------


ADD_SCHEMA = vol.Schema(
    {
        vol.Required("airline_code"): cv.string,
        vol.Required("flight_number"): cv.string,
        vol.Required("dep_airport"): cv.string,
        vol.Required("arr_airport"): cv.string,
        # legacy names
        vol.Optional("scheduled_departure"): cv.string,
        vol.Optional("scheduled_arrival"): cv.string,
        # canonical names (accepted)
        vol.Optional("dep_scheduled"): cv.string,
        vol.Optional("arr_scheduled"): cv.string,
        vol.Optional("travellers"): vol.Any(cv.string, [cv.string]),
        vol.Optional("notes"): cv.string,
    }
)

REMOVE_SCHEMA = vol.Schema({vol.Required("flight_key"): cv.string})
CLEAR_SCHEMA = vol.Schema({})
REFRESH_SCHEMA = vol.Schema({})
PRUNE_SCHEMA = vol.Schema(
    {
        vol.Optional("hours", default=0): vol.All(int, vol.Clamp(min=0, max=168)),
    }
)


async def async_register_services(hass: HomeAssistant, _options_provider: Any | None = None) -> None:
    """Register services. Accepts an unused optional options_provider for compatibility."""

    async def _add(call: ServiceCall) -> None:
        data = ADD_SCHEMA(dict(call.data))

        try:
            flight_key = await async_add_manual_flight(
                hass,
                airline_code=data["airline_code"],
                flight_number=data["flight_number"],
                dep_airport=data["dep_airport"],
                arr_airport=data["arr_airport"],
                scheduled_departure=data.get("scheduled_departure"),
                scheduled_arrival=data.get("scheduled_arrival"),
                dep_scheduled=data.get("dep_scheduled"),
                arr_scheduled=data.get("arr_scheduled"),
                travellers=data.get("travellers"),
                notes=data.get("notes"),
            )
        except Exception as e:
            _LOGGER.exception("Add manual flight failed: %s", e)
            return

        _LOGGER.info("Flight added: %s", flight_key)

    async def _remove(call: ServiceCall) -> None:
        data = REMOVE_SCHEMA(dict(call.data))
        ok = await async_remove_manual_flight(hass, data["flight_key"])
        if ok:
            _LOGGER.info("Removed: %s", data["flight_key"])
        else:
            _LOGGER.info("Not found: %s", data["flight_key"])

    async def _clear(call: ServiceCall) -> None:
        _ = CLEAR_SCHEMA(dict(call.data))
        n = await async_clear_manual_flights(hass)
        _LOGGER.info("Cleared %s manual flights", n)

    async def _refresh(call: ServiceCall) -> None:
        _ = REFRESH_SCHEMA(dict(call.data))
        clear_status_cache(hass)
        hass.data.setdefault(DOMAIN, {})["force_status_refresh"] = True
        sensors = hass.data.get(DOMAIN, {}).get("upcoming_sensors") or {}
        if isinstance(sensors, dict) and sensors:
            # Force an immediate rebuild + status refresh
            for sensor in list(sensors.values()):
                try:
                    await sensor._rebuild()  # type: ignore[attr-defined]
                except Exception as e:
                    _LOGGER.debug("Immediate refresh failed: %s", e)
        else:
            async_dispatcher_send(hass, SIGNAL_MANUAL_FLIGHTS_UPDATED)
        _LOGGER.info("Refresh triggered")

    async def _prune(call: ServiceCall) -> None:
        data = PRUNE_SCHEMA(dict(call.data))
        hours = int(data.get("hours", 0))
        cutoff = dt_util.utcnow() - timedelta(hours=hours)

        st = hass.states.get("sensor.flight_status_tracker_upcoming_flights")
        flights = (st.attributes.get("flights") if st else None) or []

        removed = 0
        for f in flights:
            if not isinstance(f, dict):
                continue
            status = (f.get("status_state") or "").lower()
            if status not in ("arrived", "cancelled"):
                continue
            arr = (f.get("arr") or {})
            arr_time = arr.get("actual") or arr.get("estimated") or arr.get("scheduled")
            dt = dt_util.parse_datetime(arr_time) if isinstance(arr_time, str) else None
            if not dt:
                continue
            dt = dt_util.as_utc(dt) if dt.tzinfo else dt_util.as_utc(dt_util.as_local(dt))
            if dt <= cutoff:
                if await async_remove_manual_flight(hass, f.get("flight_key", "")):
                    removed += 1

        notify(hass, f"Removed {removed} past flights", title="Flight Dashboard — Pruned")
        _LOGGER.info("Removed %s past flights", removed)

    hass.services.async_register(DOMAIN, SERVICE_ADD_MANUAL_FLIGHT, _add, schema=ADD_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_REMOVE_MANUAL_FLIGHT, _remove, schema=REMOVE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_MANUAL_FLIGHTS, _clear, schema=CLEAR_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_NOW, _refresh, schema=REFRESH_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_PRUNE_LANDED, _prune, schema=PRUNE_SCHEMA)
