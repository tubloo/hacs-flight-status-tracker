"""Airport/airline directory lookup and local caching.

Design goals:
- No API keys required for airport/airline enrichment by default.
- Cache full airport + airline datasets locally.
- Refresh datasets roughly monthly.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .providers.airportsdata.directory import (
    AIRPORTSDATA_AIRPORTS_URL,
    async_get_airports_index as airportsdata_get_index,
)
from .providers.openflights.directory import (
    OPENFLIGHTS_AIRLINES_URL,
    OPENFLIGHTS_AIRPORTS_URL,
    async_get_airport as openflights_get_airport,
)
from .directory_store import (
    async_get_airport,
    async_get_airline,
    async_set_airport,
    async_set_airline,
    is_fresh,
    async_load_cache,
    async_save_cache,
    async_is_initialized,
    async_mark_initialized,
)

_LOGGER = logging.getLogger(__name__)

_CACHE_TTL_DAYS = 30
_META_AIRPORTSDATA_FETCHED_AT = "airportsdata_fetched_at"
_META_OPENFLIGHTS_AIRLINES_FETCHED_AT = "openflights_airlines_fetched_at"

def airline_logo_url(iata: str | None) -> str | None:
    """Return a lightweight logo URL for airline IATA code."""
    if not iata:
        return None
    code = str(iata).strip().upper()
    if not code:
        return None
    return f"https://pics.avs.io/64/64/{code}.png"


def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except Exception:
        return None


async def _ensure_airportsdata_cache(hass: HomeAssistant, ttl_days: int = 30) -> None:
    """Load the full airportsdata CSV into local cache if missing or stale."""
    await _ensure_airportsdata_cache_forceable(hass, ttl_days=ttl_days, force=False)


async def _ensure_airportsdata_cache_forceable(hass: HomeAssistant, ttl_days: int = 30, force: bool = False) -> None:
    """Load the full airportsdata CSV into local cache if missing/stale, optionally forcing refresh."""
    cache = await async_load_cache(hass)
    meta = cache.setdefault("meta", {})
    airports = cache.get("airports") or {}
    fetched_at = meta.get(_META_AIRPORTSDATA_FETCHED_AT)
    dt = _parse_dt(fetched_at) if isinstance(fetched_at, str) else None
    if not force and dt and airports:
        age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
        if age.total_seconds() <= ttl_days * 86400:
            return

    index = await airportsdata_get_index(hass, AIRPORTSDATA_AIRPORTS_URL)
    if not isinstance(index, dict) or not index:
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    cache["airports"] = {k: {**v, "fetched_at": now_iso} for k, v in index.items()}
    meta[_META_AIRPORTSDATA_FETCHED_AT] = now_iso
    await async_save_cache(hass, cache)


async def _ensure_openflights_airlines_cache(hass: HomeAssistant, ttl_days: int = 30) -> None:
    await _ensure_openflights_airlines_cache_forceable(hass, ttl_days=ttl_days, force=False)


async def _ensure_openflights_airlines_cache_forceable(hass: HomeAssistant, ttl_days: int = 30, force: bool = False) -> None:
    """Download and cache the full OpenFlights airlines.dat dataset locally."""
    cache = await async_load_cache(hass)
    meta = cache.setdefault("meta", {})
    airlines = cache.get("airlines") or {}
    fetched_at = meta.get(_META_OPENFLIGHTS_AIRLINES_FETCHED_AT)
    dt = _parse_dt(fetched_at) if isinstance(fetched_at, str) else None
    if not force and dt and airlines:
        age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
        if age.total_seconds() <= ttl_days * 86400:
            return

    try:
        session = async_get_clientsession(hass)
        async with session.get(OPENFLIGHTS_AIRLINES_URL, timeout=30) as resp:
            if resp.status != 200:
                return
            text = await resp.text()
    except Exception as e:
        _LOGGER.debug("OpenFlights airlines download failed: %s", e)
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    index: dict[str, dict[str, Any]] = {}
    try:
        for line in text.splitlines():
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
            logo = airline_logo_url(iata_code)
            index[iata_code] = {
                "iata": iata_code,
                "icao": (parts[4] or "").strip() or None,
                "name": (parts[1] or "").strip() or None,
                "country": (parts[6] or "").strip() or None,
                "source": "openflights",
                "logo_url": logo,
                "fetched_at": now_iso,
            }
    except Exception as e:
        _LOGGER.debug("OpenFlights airlines parse failed: %s", e)
        return

    cache["airlines"] = index
    meta[_META_OPENFLIGHTS_AIRLINES_FETCHED_AT] = now_iso
    await async_save_cache(hass, cache)


async def async_refresh_builtin_airports_cache(hass: HomeAssistant) -> None:
    """Refresh built-in airport dataset cache when empty/stale."""
    await _ensure_airportsdata_cache_forceable(hass, ttl_days=_CACHE_TTL_DAYS, force=False)


async def async_refresh_builtin_airports_cache_force(hass: HomeAssistant) -> None:
    """Force refresh built-in airport dataset cache now."""
    await _ensure_airportsdata_cache_forceable(hass, ttl_days=_CACHE_TTL_DAYS, force=True)


async def async_refresh_builtin_airlines_cache(hass: HomeAssistant) -> None:
    """Refresh built-in airline dataset cache when empty/stale."""
    await _ensure_openflights_airlines_cache_forceable(hass, ttl_days=_CACHE_TTL_DAYS, force=False)


async def async_refresh_builtin_airlines_cache_force(hass: HomeAssistant) -> None:
    """Force refresh built-in airline dataset cache now."""
    await _ensure_openflights_airlines_cache_forceable(hass, ttl_days=_CACHE_TTL_DAYS, force=True)


async def get_airport(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    iata = (iata or "").strip().upper()
    if not iata:
        return None

    await _ensure_airportsdata_cache(hass, ttl_days=_CACHE_TTL_DAYS)

    def _is_complete_airport(data: dict[str, Any] | None) -> bool:
        if not isinstance(data, dict):
            return False
        return bool(data.get("name") and data.get("city") and data.get("tz"))

    cached = await async_get_airport(hass, iata)
    if is_fresh(cached, _CACHE_TTL_DAYS) and _is_complete_airport(cached):
        return cached

    # Fallback: OpenFlights airports.dat (data-only, no API keys).
    try:
        data = await openflights_get_airport(hass, iata, OPENFLIGHTS_AIRPORTS_URL)
    except Exception as e:
        _LOGGER.debug("OpenFlights airport fallback failed for %s: %s", iata, e)
        data = None
    if data:
        await async_set_airport(hass, iata, data)
        return data

    return cached


async def get_airline(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    iata = (iata or "").strip().upper()
    if not iata:
        return None

    await _ensure_openflights_airlines_cache(hass, ttl_days=_CACHE_TTL_DAYS)

    cached = await async_get_airline(hass, iata)
    if is_fresh(cached, _CACHE_TTL_DAYS):
        return cached

    # If the full dataset fetch failed, fall back to a single-record lookup
    # via OpenFlights provider module, then persist it.
    try:
        session = async_get_clientsession(hass)
        async with session.get(OPENFLIGHTS_AIRLINES_URL, timeout=30) as resp:
            if resp.status != 200:
                return cached
            text = await resp.text()
    except Exception:
        return cached

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) < 8:
            continue
        iata_code = (parts[3] or "").strip().upper()
        if iata_code != iata:
            continue
        data = {
            "iata": iata_code,
            "icao": (parts[4] or "").strip() or None,
            "name": (parts[1] or "").strip() or None,
            "country": (parts[6] or "").strip() or None,
            "source": "openflights",
        }
        await async_set_airline(hass, iata_code, data)
        return await async_get_airline(hass, iata_code)

    return cached


async def warm_directory_cache(hass: HomeAssistant, options: dict[str, Any], flights: list[dict[str, Any]]) -> None:
    """Populate local directory cache on first run using known flights only."""
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
