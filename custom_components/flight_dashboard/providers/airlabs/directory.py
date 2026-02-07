"""AirLabs directory provider (airports/airlines)."""
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


class AirLabsDirectoryProvider:
    def __init__(self, hass: HomeAssistant, api_key: str) -> None:
        self.hass = hass
        self.api_key = api_key.strip()

    async def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(url, params=params, timeout=25) as resp:
                payload = await resp.json(content_type=None)
            return payload if isinstance(payload, dict) else None
        except Exception as e:
            _LOGGER.debug("AirLabs directory request failed: %s", e)
            return None

    async def async_get_airport(self, iata: str) -> dict[str, Any] | None:
        url = "https://airlabs.co/api/v9/airports"
        payload = await self._get_json(url, {"api_key": self.api_key, "iata_code": iata.upper()})
        if not payload:
            return None
        if payload.get("error"):
            return None
        resp_obj = payload.get("response")
        if isinstance(resp_obj, list) and resp_obj:
            a = resp_obj[0]
            return {
                "iata": a.get("iata_code") or iata.upper(),
                "icao": a.get("icao_code"),
                "name": _first(a.get("name"), a.get("airport_name")),
                "city": _first(a.get("city"), a.get("city_name")),
                "country": _first(a.get("country_code"), a.get("country")),
                "tz": a.get("timezone"),
                "lat": a.get("lat"),
                "lon": a.get("lng"),
                "source": "airlabs",
            }
        return None

    async def async_get_airline(self, iata: str) -> dict[str, Any] | None:
        url = "https://airlabs.co/api/v9/airlines"
        payload = await self._get_json(url, {"api_key": self.api_key, "iata_code": iata.upper()})
        if not payload:
            return None
        if payload.get("error"):
            return None
        resp_obj = payload.get("response")
        if isinstance(resp_obj, list) and resp_obj:
            al = resp_obj[0]
            return {
                "iata": al.get("iata_code") or iata.upper(),
                "icao": al.get("icao_code"),
                "name": _first(al.get("name"), al.get("airline_name")),
                "country": _first(al.get("country_code"), al.get("country")),
                "callsign": al.get("callsign"),
                "logo": al.get("logo") or al.get("logo_url"),
                "source": "airlabs",
            }
        return None
_LOGGER = logging.getLogger(__name__)
