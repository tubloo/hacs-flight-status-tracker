"""Airport/airline directory lookup with optional caching."""
from __future__ import annotations

import logging
import csv
from io import StringIO
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .providers.directory.aviationstack import AviationstackDirectoryProvider
from .providers.directory.airlabs import AirLabsDirectoryProvider
from .providers.directory.fr24 import FR24DirectoryProvider
from .const import DOMAIN
from .directory_store import (
    async_get_airport,
    async_get_airline,
    async_set_airport,
    async_set_airline,
    is_fresh,
    async_is_initialized,
    async_mark_initialized,
)
from .rate_limit import is_blocked

_LOGGER = logging.getLogger(__name__)

OPENFLIGHTS_AIRLINES_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"
OPENFLIGHTS_AIRPORTS_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat"
OPENFLIGHTS_AIRPORTS_CACHE_KEY = "openflights_airports_cache"


async def _get_openflights_airports_index(hass: HomeAssistant) -> dict[str, dict[str, Any]] | None:
    """Download and cache OpenFlights airports.dat as a dict keyed by IATA."""
    cache = hass.data.setdefault(DOMAIN, {})
    cached = cache.get(OPENFLIGHTS_AIRPORTS_CACHE_KEY)
    if isinstance(cached, dict) and cached.get("index"):
        return cached["index"]

    try:
        session = async_get_clientsession(hass)
        async with session.get(OPENFLIGHTS_AIRPORTS_URL, timeout=30) as resp:
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

    cache[OPENFLIGHTS_AIRPORTS_CACHE_KEY] = {"index": index}
    return index


async def async_get_openflights_airport(hass: HomeAssistant, iata: str) -> dict[str, Any] | None:
    """Fetch a single airport from OpenFlights and cache it."""
    code = (iata or "").strip().upper()
    if not code:
        return None
    index = await _get_openflights_airports_index(hass)
    if not isinstance(index, dict):
        return None
    return index.get(code)

def airline_logo_url(iata: str | None) -> str | None:
    """Return a lightweight logo URL for airline IATA code."""
    if not iata:
        return None
    code = str(iata).strip().upper()
    if not code:
        return None
    return f"https://pics.avs.io/64/64/{code}.png"


def _get_option(options: dict[str, Any], key: str, default: Any) -> Any:
    val = options.get(key, default)
    return val if val is not None else default


async def get_airport(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    iata = (iata or "").strip().upper()
    if not iata:
        return None

    cache_enabled = bool(_get_option(options, "cache_directory", True))
    ttl_days = int(_get_option(options, "cache_ttl_days", 90))

    def _is_complete_airport(data: dict[str, Any] | None) -> bool:
        if not isinstance(data, dict):
            return False
        return bool(data.get("name") and data.get("city") and data.get("tz"))

    if cache_enabled:
        cached = await async_get_airport(hass, iata)
        if is_fresh(cached, ttl_days) and _is_complete_airport(cached):
            return cached

    av_key = (options.get("aviationstack_access_key") or "").strip()
    al_key = (options.get("airlabs_api_key") or "").strip()
    fr24_key = (options.get("fr24_api_key") or "").strip()
    fr24_sandbox_key = (options.get("fr24_sandbox_key") or "").strip()
    fr24_use_sandbox = bool(options.get("fr24_use_sandbox", False))
    fr24_version = (options.get("fr24_api_version") or "v1").strip()
    fr24_active_key = fr24_sandbox_key if fr24_use_sandbox and fr24_sandbox_key else fr24_key

    providers = []
    if av_key and not is_blocked(hass, "aviationstack"):
        providers.append(AviationstackDirectoryProvider(hass, av_key))
    if al_key and not is_blocked(hass, "airlabs"):
        providers.append(AirLabsDirectoryProvider(hass, al_key))
    if fr24_active_key and not is_blocked(hass, "fr24"):
        providers.append(FR24DirectoryProvider(hass, fr24_active_key, use_sandbox=fr24_use_sandbox, api_version=fr24_version))

    for p in providers:
        try:
            data = await p.async_get_airport(iata)
        except Exception as e:
            _LOGGER.debug("Directory provider failed for airport %s: %s", iata, e)
            data = None
        if data:
            merged = {}
            for k, v in data.items():
                if v is not None and v != "":
                    merged[k] = v
            if cache_enabled:
                await async_set_airport(hass, iata, merged)
            return merged

    # Fallback: OpenFlights airports.dat
    try:
        index = await _get_openflights_airports_index(hass)
        if isinstance(index, dict):
            data = index.get(iata)
            if data:
                if cache_enabled:
                    await async_set_airport(hass, iata, data)
                return data
    except Exception as e:
        _LOGGER.debug("OpenFlights airport fallback failed for %s: %s", iata, e)

    return None


async def get_airline(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    iata = (iata or "").strip().upper()
    if not iata:
        return None

    cache_enabled = bool(_get_option(options, "cache_directory", True))
    ttl_days = int(_get_option(options, "cache_ttl_days", 90))

    if cache_enabled:
        cached = await async_get_airline(hass, iata)
        if is_fresh(cached, ttl_days):
            return cached

    av_key = (options.get("aviationstack_access_key") or "").strip()
    al_key = (options.get("airlabs_api_key") or "").strip()

    providers = []
    if av_key:
        providers.append(AviationstackDirectoryProvider(hass, av_key))
    if al_key:
        providers.append(AirLabsDirectoryProvider(hass, al_key))

    for p in providers:
        try:
            data = await p.async_get_airline(iata)
        except Exception as e:
            _LOGGER.debug("Directory provider failed for airline %s: %s", iata, e)
            data = None
        if data:
            if cache_enabled:
                await async_set_airline(hass, iata, data)
            return data

    # Fallback: OpenFlights airlines.dat (IATA/ICAO/Name/Country/Active)
    try:
        session = async_get_clientsession(hass)
        async with session.get(OPENFLIGHTS_AIRLINES_URL, timeout=30) as resp:
            if resp.status == 200:
                text = await resp.text()
                for line in StringIO(text):
                    line = line.strip()
                    if not line:
                        continue
                    # Format: Airline ID, Name, Alias, IATA, ICAO, Callsign, Country, Active
                    parts = [p.strip().strip('"') for p in line.split(",")]
                    if len(parts) < 8:
                        continue
                    name = parts[1]
                    iata_code = parts[3].upper() if parts[3] else ""
                    icao_code = parts[4].upper() if parts[4] else ""
                    callsign = parts[5]
                    country = parts[6]
                    active = parts[7]
                    if iata_code != iata:
                        continue
                    data = {
                        "iata": iata_code,
                        "icao": icao_code or None,
                        "name": name or None,
                        "country": country or None,
                        "active": active or None,
                        "callsign": callsign or None,
                        "source": "openflights",
                    }
                    if cache_enabled:
                        await async_set_airline(hass, iata, data)
                    return data
    except Exception as e:
        _LOGGER.debug("OpenFlights airline fallback failed for %s: %s", iata, e)

    return None


async def warm_directory_cache(hass: HomeAssistant, options: dict[str, Any], flights: list[dict[str, Any]]) -> None:
    """Populate local directory cache on first run using known flights only."""
    cache_enabled = bool(_get_option(options, "cache_directory", True))
    if not cache_enabled:
        return
    if await async_is_initialized(hass):
        return

    # Collect unique IATA codes from flights
    airport_codes: set[str] = set()
    airline_codes: set[str] = set()
    for f in flights:
        airline = (f.get("airline_code") or "").strip().upper()
        if airline:
            airline_codes.add(airline)
        dep_iata = ((f.get("dep") or {}).get("airport") or {}).get("iata")
        arr_iata = ((f.get("arr") or {}).get("airport") or {}).get("iata")
        if dep_iata:
            airport_codes.add(str(dep_iata).strip().upper())
        if arr_iata:
            airport_codes.add(str(arr_iata).strip().upper())

    # Populate cache (calls provider only if not already cached/stale)
    for code in sorted(airport_codes):
        try:
            await get_airport(hass, options, code)
        except Exception:
            pass
    for code in sorted(airline_codes):
        try:
            await get_airline(hass, options, code)
        except Exception:
            pass

    await async_mark_initialized(hass)
