"""Aviationstack directory provider (airports/airlines)."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
import logging
from homeassistant.helpers.aiohttp_client import async_get_clientsession


def _first(*vals):
    for v in vals:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


class AviationstackDirectoryProvider:
    def __init__(self, hass: HomeAssistant, access_key: str) -> None:
        self.hass = hass
        self.access_key = access_key.strip()

    async def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(url, params=params, timeout=25) as resp:
                payload = await resp.json(content_type=None)
            return payload if isinstance(payload, dict) else None
        except Exception as e:
            _LOGGER.debug("Aviationstack directory request failed: %s", e)
            return None

    async def async_get_airport(self, iata: str) -> dict[str, Any] | None:
        base_urls = [
            "http://api.aviationstack.com/v1/airports",
            "https://api.aviationstack.com/v1/airports",
        ]
        for url in base_urls:
            payload = await self._get_json(
                url, {"access_key": self.access_key, "iata_code": iata.upper(), "limit": 5}
            )
            if not payload:
                continue
            if payload.get("error"):
                continue
            data = payload.get("data")
            if isinstance(data, list) and data:
                a = data[0]
                city = _first(a.get("city"), a.get("city_name"))
                name = _first(a.get("airport_name"), a.get("name"))
                country = _first(a.get("country_name"), a.get("country"))
                return {
                    "iata": a.get("iata_code") or iata.upper(),
                    "icao": a.get("icao_code"),
                    "name": name,
                    "city": city,
                    "country": country,
                    "tz": a.get("timezone"),
                    "lat": a.get("latitude"),
                    "lon": a.get("longitude"),
                    "source": "aviationstack",
                }
        return None

    async def async_get_airline(self, iata: str) -> dict[str, Any] | None:
        base_urls = [
            "http://api.aviationstack.com/v1/airlines",
            "https://api.aviationstack.com/v1/airlines",
        ]
        for url in base_urls:
            payload = await self._get_json(
                url, {"access_key": self.access_key, "iata_code": iata.upper(), "limit": 5}
            )
            if not payload:
                continue
            if payload.get("error"):
                continue
            data = payload.get("data")
            if isinstance(data, list) and data:
                al = data[0]
                return {
                    "iata": al.get("iata_code") or iata.upper(),
                    "icao": al.get("icao_code"),
                    "name": _first(al.get("airline_name"), al.get("name")),
                    "country": _first(al.get("country_name"), al.get("country")),
                    "callsign": al.get("callsign"),
                    "logo": al.get("logo") or al.get("logo_url"),
                    "source": "aviationstack",
                }
        return None
_LOGGER = logging.getLogger(__name__)
