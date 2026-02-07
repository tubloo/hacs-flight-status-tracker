"""Flightradar24 directory provider (airports)."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .client import FR24Client, FR24Error, FR24RateLimitError
from ...rate_limit import set_block


class FR24DirectoryProvider:
    def __init__(self, hass: HomeAssistant, api_key: str, use_sandbox: bool = False, api_version: str = "v1") -> None:
        self.hass = hass
        self.api_key = api_key.strip()
        self.use_sandbox = use_sandbox
        self.api_version = api_version

    async def async_get_airport(self, iata: str) -> dict[str, Any] | None:
        if not iata:
            return None
        code = iata.strip().upper()
        client = FR24Client(self.hass, api_key=self.api_key, use_sandbox=self.use_sandbox, api_version=self.api_version)
        try:
            data = await client.airport_full(code)
        except FR24RateLimitError as e:
            set_block(self.hass, "fr24", e.retry_after or 900, "rate_limited")
            return None
        except FR24Error:
            return None
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        tz = None
        tz_obj = data.get("timezone")
        if isinstance(tz_obj, dict):
            tz = tz_obj.get("name")
        return {
            "iata": data.get("iata") or code,
            "icao": data.get("icao"),
            "name": data.get("name"),
            "city": data.get("city"),
            "country": (data.get("country") or {}).get("code") if isinstance(data.get("country"), dict) else None,
            "tz": tz,
            "lat": data.get("lat"),
            "lon": data.get("lon"),
            "source": "fr24",
        }
