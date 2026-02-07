"""Flightradar24 enrichment provider (airports/airlines metadata for caching).

This is used to populate server-side cached master data:
- airport name/city/tz
- airline name (via ICAO endpoint when ICAO is known)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant

from .client import FR24Client, FR24Error


@dataclass
class Flightradar24EnrichmentProvider:
    hass: HomeAssistant
    api_key: str
    use_sandbox: bool = False

    async def async_airport_full(self, code: str) -> dict[str, Any] | None:
        client = FR24Client(self.hass, api_key=self.api_key, use_sandbox=self.use_sandbox)
        try:
            data = await client.airport_full(code)
        except FR24Error:
            return None
        except Exception:
            return None

        # Normalize defensively
        city = data.get("city")
        if isinstance(city, dict):
            city = city.get("name")

        tz = data.get("timezone") or data.get("tz") or data.get("time_zone")
        return {
            "iata": data.get("iata") or data.get("iata_code") or code,
            "name": data.get("name") or data.get("airport_name"),
            "city": city,
            "tz": tz,
        }

    async def async_airline_light_by_icao(self, icao: str) -> dict[str, Any] | None:
        client = FR24Client(self.hass, api_key=self.api_key, use_sandbox=self.use_sandbox)
        try:
            data = await client.airline_light_by_icao(icao)
        except FR24Error:
            return None
        except Exception:
            return None

        return {
            "name": data.get("name"),
            "icao": data.get("icao"),
            "iata": data.get("iata"),
        }
