"""OpenFlights directory provider (airports/airlines from .dat files)."""
from __future__ import annotations

import csv
from io import StringIO
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ...const import DOMAIN

OPENFLIGHTS_AIRLINES_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"
OPENFLIGHTS_AIRPORTS_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat"

_AIRPORTS_CACHE_KEY = "openflights_airports_cache"
_AIRLINES_CACHE_KEY = "openflights_airlines_cache"


async def _get_openflights_airports_index(
    hass: HomeAssistant,
    url: str,
) -> dict[str, dict[str, Any]] | None:
    """Download and cache an airports.dat-style file as a dict keyed by IATA."""
    cache = hass.data.setdefault(DOMAIN, {})
    cached = cache.get(_AIRPORTS_CACHE_KEY)
    if isinstance(cached, dict) and cached.get("index") and cached.get("url") == url:
        return cached["index"]

    try:
        session = async_get_clientsession(hass)
        async with session.get(url, timeout=30) as resp:
            if resp.status != 200:
                return None
            text = await resp.text()
    except Exception:
        return None

    index: dict[str, dict[str, Any]] = {}
    try:
        reader = csv.reader(StringIO(text))
        for row in reader:
            # Format: Airport ID, Name, City, Country, IATA, ICAO, Lat, Lon,
            # Altitude, Timezone, DST, TZ database time zone, type, source
            if len(row) < 12:
                continue
            iata = (row[4] or "").strip().upper()
            if not iata or iata == "\\N":
                continue
            tz = (row[11] or "").strip()
            if tz == "\\N":
                tz = ""
            # Normalize legacy alias to modern IANA name
            if tz == "Asia/Calcutta":
                tz = "Asia/Kolkata"
            lat = (row[6] or "").strip()
            lon = (row[7] or "").strip()
            index[iata] = {
                "iata": iata,
                "icao": (row[5] or "").strip() or None,
                "name": (row[1] or "").strip() or None,
                "city": (row[2] or "").strip() or None,
                "country": (row[3] or "").strip() or None,
                "tz": tz or None,
                "lat": lat or None,
                "lon": lon or None,
                "source": "openflights",
            }
    except Exception:
        return None

    cache[_AIRPORTS_CACHE_KEY] = {"index": index, "url": url}
    return index


async def _get_openflights_airlines_index(
    hass: HomeAssistant,
    url: str,
) -> dict[str, dict[str, Any]] | None:
    """Download and cache an airlines.dat-style file as a dict keyed by IATA."""
    cache = hass.data.setdefault(DOMAIN, {})
    cached = cache.get(_AIRLINES_CACHE_KEY)
    if isinstance(cached, dict) and cached.get("index") and cached.get("url") == url:
        return cached["index"]

    try:
        session = async_get_clientsession(hass)
        async with session.get(url, timeout=30) as resp:
            if resp.status != 200:
                return None
            text = await resp.text()
    except Exception:
        return None

    index: dict[str, dict[str, Any]] = {}
    try:
        for line in StringIO(text):
            line = line.strip()
            if not line:
                continue
            # Format: Airline ID, Name, Alias, IATA, ICAO, Callsign, Country, Active
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) < 8:
                continue
            iata_code = (parts[3] or "").strip().upper()
            if not iata_code or iata_code == "\\N":
                continue
            index[iata_code] = {
                "iata": iata_code,
                "icao": (parts[4] or "").strip() or None,
                "name": (parts[1] or "").strip() or None,
                "country": (parts[6] or "").strip() or None,
                "source": "openflights",
            }
    except Exception:
        return None

    cache[_AIRLINES_CACHE_KEY] = {"index": index, "url": url}
    return index


async def async_get_airport(
    hass: HomeAssistant,
    iata: str,
    url: str | None = None,
) -> dict[str, Any] | None:
    """Fetch a single airport from an airports.dat-style file and cache it."""
    code = (iata or "").strip().upper()
    if not code:
        return None
    src = (url or "").strip() or OPENFLIGHTS_AIRPORTS_URL
    index = await _get_openflights_airports_index(hass, src)
    if not isinstance(index, dict):
        return None
    return index.get(code)


async def async_get_airline(
    hass: HomeAssistant,
    iata: str,
    url: str | None = None,
) -> dict[str, Any] | None:
    """Fetch a single airline from an airlines.dat-style file and cache it."""
    code = (iata or "").strip().upper()
    if not code:
        return None
    src = (url or "").strip() or OPENFLIGHTS_AIRLINES_URL
    index = await _get_openflights_airlines_index(hass, src)
    if not isinstance(index, dict):
        return None
    return index.get(code)
