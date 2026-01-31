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
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.components.persistent_notification import async_create as notify

from .const import (
    DOMAIN,
    SERVICE_ADD_MANUAL_FLIGHT,
    SERVICE_REMOVE_MANUAL_FLIGHT,
    SERVICE_CLEAR_MANUAL_FLIGHTS,
    SERVICE_REFRESH_NOW,
    SIGNAL_MANUAL_FLIGHTS_UPDATED,
)
from .manual_store import async_add_manual_flight, async_remove_manual_flight, async_clear_manual_flights

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
            notify(hass, f"Add manual flight failed: {e}", title="Flight Dashboard — Error")
            return

        notify(hass, f"Flight added: {flight_key}", title="Flight Dashboard — Saved ✅")

    async def _remove(call: ServiceCall) -> None:
        data = REMOVE_SCHEMA(dict(call.data))
        ok = await async_remove_manual_flight(hass, data["flight_key"])
        if ok:
            notify(hass, f"Removed: {data['flight_key']}", title="Flight Dashboard — Removed")
        else:
            notify(hass, f"Not found: {data['flight_key']}", title="Flight Dashboard — Remove")

    async def _clear(call: ServiceCall) -> None:
        _ = CLEAR_SCHEMA(dict(call.data))
        n = await async_clear_manual_flights(hass)
        notify(hass, f"Cleared {n} manual flights", title="Flight Dashboard — Cleared")

    async def _refresh(call: ServiceCall) -> None:
        _ = REFRESH_SCHEMA(dict(call.data))
        async_dispatcher_send(hass, SIGNAL_MANUAL_FLIGHTS_UPDATED)
        notify(hass, "Refresh triggered", title="Flight Dashboard — Refresh")

    hass.services.async_register(DOMAIN, SERVICE_ADD_MANUAL_FLIGHT, _add, schema=ADD_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_REMOVE_MANUAL_FLIGHT, _remove, schema=REMOVE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_MANUAL_FLIGHTS, _clear, schema=CLEAR_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_NOW, _refresh, schema=REFRESH_SCHEMA)
