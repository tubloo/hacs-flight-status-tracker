"""OpenSky enrichment provider (ADS-B state vectors)."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .._shared.status_base import FlightStatus


class OpenSkyEnrichmentProvider:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_get_status(self, flight: dict[str, Any]) -> FlightStatus | None:
        icao24 = (flight.get("icao24") or "").lower().strip()
        if not icao24:
            return None

        # https://opensky-network.org/api/states/all?icao24=...
        url = "https://opensky-network.org/api/states/all"
        params = {"icao24": icao24}

        session = async_get_clientsession(self.hass)
        async with session.get(url, params=params, timeout=25) as resp:
            payload = await resp.json(content_type=None)

        states = payload.get("states") if isinstance(payload, dict) else None
        if not isinstance(states, list) or not states:
            return None

        # state vector format: list fields (documented by OpenSky)
        # [icao24, callsign, origin_country, time_position, last_contact, longitude, latitude, baro_altitude, on_ground, velocity, ...]
        s = states[0]
        if not isinstance(s, list) or len(s) < 11:
            return None

        details = {
            "provider": "opensky",
            "state": "tracking",
            "track": {
                "source": "opensky",
                "icao24": s[0],
                "callsign": (s[1] or "").strip() if len(s) > 1 else None,
                "origin_country": s[2] if len(s) > 2 else None,
                "longitude": s[5] if len(s) > 5 else None,
                "latitude": s[6] if len(s) > 6 else None,
                "altitude_m": s[7] if len(s) > 7 else None,
                "on_ground": s[8] if len(s) > 8 else None,
                "velocity_mps": s[9] if len(s) > 9 else None,
                "heading_deg": s[10] if len(s) > 10 else None,
            },
        }

        return FlightStatus(provider="opensky", state="tracking", details=details)
