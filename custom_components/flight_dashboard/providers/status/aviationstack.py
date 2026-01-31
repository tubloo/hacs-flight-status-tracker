"""Aviationstack status provider."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .base import FlightStatus


def _parse_dt(s: str | None) -> str | None:
    if not s:
        return None
    try:
        # Keep ISO string; HA UI will format later if needed
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return s
    except Exception:
        return s


class AviationstackStatusProvider:
    def __init__(self, hass: HomeAssistant, access_key: str) -> None:
        self.hass = hass
        self.access_key = access_key.strip()

    async def async_get_status(self, flight: dict[str, Any]) -> FlightStatus | None:
        """Fetch flight status from Aviationstack and normalize fields."""
        airline = (flight.get("airline_code") or "").strip()
        number = str(flight.get("flight_number") or "").strip()
        if not airline or not number:
            return None

        flight_iata = f"{airline}{number}"

        # IMPORTANT: free plans often require HTTP; we try http first then https
        base_urls = [
            "http://api.aviationstack.com/v1/flights",
            "https://api.aviationstack.com/v1/flights",
        ]

        # Try with and without flight_date (plans vary)
        flight_date = None
        sd = flight.get("scheduled_departure")
        if isinstance(sd, str) and len(sd) >= 10:
            flight_date = sd[:10]

        query_variants: list[dict[str, Any]] = [
            {"flight_iata": flight_iata, "limit": 10},
        ]
        if flight_date:
            query_variants.insert(0, {"flight_iata": flight_iata, "flight_date": flight_date, "limit": 10})

        session = async_get_clientsession(self.hass)

        last_error = None
        for url in base_urls:
            for params_extra in query_variants:
                params = {"access_key": self.access_key, **params_extra}
                async with session.get(url, params=params, timeout=25) as resp:
                    payload = await resp.json(content_type=None)

                if isinstance(payload, dict) and "error" in payload:
                    last_error = payload.get("error")
                    continue

                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, list) or not data:
                    continue

                # Pick best match by dep/arr airport when available.
                dep_airport = (flight.get("dep_airport") or "").strip().upper()
                arr_airport = (flight.get("arr_airport") or "").strip().upper()

                best = None
                for it in data:
                    dep = (it.get("departure") or {}).get("iata")
                    arr = (it.get("arrival") or {}).get("iata")
                    if dep_airport and arr_airport and dep == dep_airport and arr == arr_airport:
                        best = it
                        break
                if best is None:
                    best = data[0]

                fs = (best.get("flight_status") or "unknown").lower()
                state = fs if fs else "unknown"

                dep = best.get("departure") or {}
                arr = best.get("arrival") or {}

                airline_name = (best.get("airline") or {}).get("name")
                details = {
                    "provider": "aviationstack",
                    "state": state,
                    "dep_scheduled": _parse_dt(dep.get("scheduled")),
                    "dep_estimated": _parse_dt(dep.get("estimated")),
                    "dep_actual": _parse_dt(dep.get("actual")),
                    "arr_scheduled": _parse_dt(arr.get("scheduled")),
                    "arr_estimated": _parse_dt(arr.get("estimated")),
                    "arr_actual": _parse_dt(arr.get("actual")),
                    "dep_iata": dep.get("iata"),
                    "arr_iata": arr.get("iata"),
                    "airline_name": airline_name,
                    "terminal_dep": dep.get("terminal"),
                    "gate_dep": dep.get("gate"),
                    "terminal_arr": arr.get("terminal"),
                    "gate_arr": arr.get("gate"),
                    "delay_minutes": dep.get("delay") or arr.get("delay"),
                }

                return FlightStatus(provider="aviationstack", state=state, details=details)

        # No match. If API error existed, surface it as unknown status.
        if last_error:
            return FlightStatus(
                provider="aviationstack",
                state="unknown",
                details={"provider": "aviationstack", "state": "unknown", "error": last_error},
            )

        return None
