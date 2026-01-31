"""AirLabs status provider."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .base import FlightStatus


def _iso(s: str | None) -> str | None:
    if not s:
        return None
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return s
    except Exception:
        return s


class AirLabsStatusProvider:
    def __init__(self, hass: HomeAssistant, api_key: str) -> None:
        self.hass = hass
        self.api_key = api_key.strip()

    async def async_get_status(self, flight: dict[str, Any]) -> FlightStatus | None:
        """Fetch flight status from AirLabs and normalize fields."""
        airline = (flight.get("airline_code") or "").strip()
        number = str(flight.get("flight_number") or "").strip()
        if not airline or not number:
            return None

        flight_iata = f"{airline}{number}"
        url = "https://airlabs.co/api/v9/flight"
        params = {"api_key": self.api_key, "flight_iata": flight_iata}

        session = async_get_clientsession(self.hass)
        async with session.get(url, params=params, timeout=25) as resp:
            payload = await resp.json(content_type=None)

        resp_obj = payload.get("response") if isinstance(payload, dict) else None
        if not isinstance(resp_obj, dict):
            # Sometimes errors are in payload["error"]
            if isinstance(payload, dict) and payload.get("error"):
                return FlightStatus(
                    provider="airlabs",
                    state="unknown",
                    details={"provider": "airlabs", "state": "unknown", "error": payload.get("error")},
                )
            return None

        status = (resp_obj.get("status") or "unknown").lower()

        dep_sched = resp_obj.get("dep_scheduled") or resp_obj.get("dep_time_utc") or resp_obj.get("dep_time")
        arr_sched = resp_obj.get("arr_scheduled") or resp_obj.get("arr_time_utc") or resp_obj.get("arr_time")
        dep_est = resp_obj.get("dep_estimated_utc") or resp_obj.get("dep_estimated")
        dep_act = resp_obj.get("dep_actual_utc") or resp_obj.get("dep_actual")
        arr_est = resp_obj.get("arr_estimated_utc") or resp_obj.get("arr_estimated")
        arr_act = resp_obj.get("arr_actual_utc") or resp_obj.get("arr_actual")

        details = {
            "provider": "airlabs",
            "state": status,
            "dep_scheduled": _iso(dep_sched),
            "dep_estimated": _iso(dep_est),
            "dep_actual": _iso(dep_act),
            "arr_scheduled": _iso(arr_sched),
            "arr_estimated": _iso(arr_est),
            "arr_actual": _iso(arr_act),
            "dep_iata": resp_obj.get("dep_iata") or resp_obj.get("departure_iata"),
            "arr_iata": resp_obj.get("arr_iata") or resp_obj.get("arrival_iata"),
            "airline_name": resp_obj.get("airline_name"),
            "terminal_dep": resp_obj.get("dep_terminal"),
            "gate_dep": resp_obj.get("dep_gate"),
            "terminal_arr": resp_obj.get("arr_terminal"),
            "gate_arr": resp_obj.get("arr_gate"),
            "delay_minutes": resp_obj.get("delay"),
            # useful for OpenSky enrichment sometimes
            "icao24": (resp_obj.get("hex") or resp_obj.get("icao24") or "").lower().strip() or None,
        }

        return FlightStatus(provider="airlabs", state=status, details=details)
