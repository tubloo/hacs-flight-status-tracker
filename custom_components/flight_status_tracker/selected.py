"""Helpers for selected flight resolution."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


UPCOMING_SENSOR = "sensor.flight_status_tracker_upcoming_flights"
SELECT_ENTITY_ID = "select.flight_status_tracker_selected_flight"


def _extract_flight_key(option: str | None) -> str:
    if not option:
        return ""
    if " | " not in option:
        return option.strip()
    return option.split(" | ", 1)[0].strip()


def get_selected_flight(hass: HomeAssistant) -> dict[str, Any] | None:
    st = hass.states.get(UPCOMING_SENSOR)
    flights = (st.attributes.get("flights") if st else None) or []

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
