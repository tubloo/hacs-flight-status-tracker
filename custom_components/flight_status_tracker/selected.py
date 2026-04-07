"""Helpers for selected flight resolution."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import DATA_UPCOMING_FLIGHTS, DOMAIN

UPCOMING_SENSOR = "sensor.flight_status_tracker_upcoming_flights"
SELECT_ENTITY_ID = "select.flight_status_tracker_selected_flight"


def _extract_flight_key(option: str | None) -> str:
    if not option:
        return ""
    if " | " not in option:
        return option.strip()
    return option.split(" | ", 1)[0].strip()


def get_upcoming_flights(hass: HomeAssistant) -> list[dict[str, Any]]:
    domain_data = hass.data.get(DOMAIN, {})
    cached = domain_data.get(DATA_UPCOMING_FLIGHTS) if isinstance(domain_data, dict) else None
    if isinstance(cached, list):
        return [f for f in cached if isinstance(f, dict)]

    st = hass.states.get(UPCOMING_SENSOR)
    flight_keys = (st.attributes.get("flight_keys") if st else None) or []
    if not isinstance(flight_keys, list):
        flight_keys = []

    by_key: dict[str, dict[str, Any]] = {}
    for ent in hass.states.async_all("sensor"):
        key = ent.attributes.get("flight_key")
        flight = ent.attributes.get("flight")
        if not isinstance(key, str) or not key.strip():
            continue
        if not isinstance(flight, dict):
            continue
        by_key[key] = flight

    if flight_keys:
        ordered = [by_key[k] for k in flight_keys if isinstance(k, str) and k in by_key]
        if ordered:
            return ordered

    return list(by_key.values())


def get_selected_flight(hass: HomeAssistant) -> dict[str, Any] | None:
    flights = get_upcoming_flights(hass)

    sel = hass.states.get(SELECT_ENTITY_ID)
    key = _extract_flight_key(sel.state if sel else None)

    if key:
        for f in flights:
            if isinstance(f, dict) and f.get("flight_key") == key:
                return f
        # If a specific selection exists but isn't found, avoid falling back
        # to a different flight which can show the wrong details.
        return None

    return flights[0] if flights else None


def get_flight_position(flight: dict[str, Any] | None) -> dict[str, Any] | None:
    if not flight:
        return None
    pos = flight.get("position")
    if isinstance(pos, dict) and pos.get("lat") is not None and pos.get("lon") is not None:
        return pos
    status = flight.get("status") or {}
    pos = status.get("position")
    if isinstance(pos, dict):
        return pos
    return None
