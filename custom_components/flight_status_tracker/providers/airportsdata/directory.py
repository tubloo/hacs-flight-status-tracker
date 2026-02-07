"""Airportsdata directory provider (airports.csv from mbor setti/airportsdata)."""
from __future__ import annotations

import csv
from io import StringIO
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ...const import DOMAIN

AIRPORTSDATA_AIRPORTS_URL = "https://github.com/mborsetti/airportsdata/raw/main/airportsdata/airports.csv"

_AIRPORTS_CACHE_KEY = "airportsdata_airports_cache"


async def _get_airportsdata_index(
    hass: HomeAssistant,
    url: str,
) -> dict[str, dict[str, Any]] | None:
    """Download and cache airports.csv as a dict keyed by IATA."""
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
        reader = csv.DictReader(StringIO(text))
        for row in reader:
            iata = (row.get("iata") or "").strip().upper()
            if not iata:
                continue
            tz = (row.get("tz") or "").strip()
            if tz == "\\N":
                tz = ""
            if tz == "Asia/Calcutta":
                tz = "Asia/Kolkata"
            lat = (row.get("lat") or "").strip()
            lon = (row.get("lon") or "").strip()
            index[iata] = {
                "iata": iata,
                "icao": (row.get("icao") or "").strip() or None,
                "name": (row.get("name") or "").strip() or None,
                "city": (row.get("city") or "").strip() or None,
                "country": (row.get("country") or "").strip() or None,
                "tz": tz or None,
                "lat": lat or None,
                "lon": lon or None,
                "source": "airportsdata",
            }
    except Exception:
        return None

    cache[_AIRPORTS_CACHE_KEY] = {"index": index, "url": url}
    return index


async def async_get_airports_index(
    hass: HomeAssistant,
    url: str | None = None,
) -> dict[str, dict[str, Any]] | None:
    """Return the full airportsdata index (IATA -> data)."""
    src = (url or "").strip() or AIRPORTSDATA_AIRPORTS_URL
    return await _get_airportsdata_index(hass, src)


async def async_get_airport(
    hass: HomeAssistant,
    iata: str,
    url: str | None = None,
) -> dict[str, Any] | None:
    """Fetch a single airport from airports.csv and cache it."""
    code = (iata or "").strip().upper()
    if not code:
        return None
    src = (url or "").strip() or AIRPORTSDATA_AIRPORTS_URL
    index = await _get_airportsdata_index(hass, src)
    if not isinstance(index, dict):
        return None
    return index.get(code)
