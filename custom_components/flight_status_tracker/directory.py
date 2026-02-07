"""Airport/airline directory lookup with optional caching."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from .providers.aviationstack.directory import AviationstackDirectoryProvider
from .providers.airlabs.directory import AirLabsDirectoryProvider
from .providers.flightradar24.directory import FR24DirectoryProvider
from .providers.airportsdata.directory import (
    AIRPORTSDATA_AIRPORTS_URL,
    async_get_airports_index as airportsdata_get_index,
    async_get_airport as airportsdata_get_airport,
)
from .providers.openflights.directory import (
    OPENFLIGHTS_AIRLINES_URL,
    OPENFLIGHTS_AIRPORTS_URL,
    async_get_airport as openflights_get_airport,
    async_get_airline as openflights_get_airline,
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
from .rate_limit import is_blocked

_LOGGER = logging.getLogger(__name__)

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


def _directory_source(options: dict[str, Any]) -> str:
    # Match Options Flow defaults when options are missing.
    src = str(options.get("directory_source", "airportsdata") or "airportsdata").strip().lower()
    return src


def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except Exception:
        return None


async def _ensure_airportsdata_cache(hass: HomeAssistant, ttl_days: int = 30) -> None:
    """Load the full airportsdata CSV into local cache if missing or stale."""
    cache = await async_load_cache(hass)
    meta = cache.setdefault("meta", {})
    airports = cache.get("airports") or {}
    fetched_at = meta.get("airportsdata_fetched_at")
    dt = _parse_dt(fetched_at) if isinstance(fetched_at, str) else None
    if dt and airports:
        age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
        if age.total_seconds() <= ttl_days * 86400:
            return

    index = await airportsdata_get_index(hass, AIRPORTSDATA_AIRPORTS_URL)
    if not isinstance(index, dict) or not index:
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    cache["airports"] = {k: {**v, "fetched_at": now_iso} for k, v in index.items()}
    meta["airportsdata_fetched_at"] = now_iso
    await async_save_cache(hass, cache)


async def async_refresh_builtin_airports_cache(hass: HomeAssistant, options: dict[str, Any]) -> None:
    """Refresh built-in airportsdata cache on reload when empty or stale."""
    source = _directory_source(options)
    if source != "airportsdata":
        return
    cache_enabled = bool(_get_option(options, "cache_directory", True))
    if not cache_enabled:
        return
    ttl_days = int(_get_option(options, "cache_ttl_days", 30))
    if ttl_days <= 0:
        ttl_days = 30
    await _ensure_airportsdata_cache(hass, ttl_days=ttl_days)


async def get_airport(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    iata = (iata or "").strip().upper()
    if not iata:
        return None

    directory_source = _directory_source(options)
    cache_enabled = bool(_get_option(options, "cache_directory", True))
    ttl_days = int(_get_option(options, "cache_ttl_days", 30))
    if directory_source == "airportsdata":
        ttl_days = 30
    airports_url = OPENFLIGHTS_AIRPORTS_URL if directory_source in ("openflights", "custom") else AIRPORTSDATA_AIRPORTS_URL

    def _is_complete_airport(data: dict[str, Any] | None) -> bool:
        if not isinstance(data, dict):
            return False
        return bool(data.get("name") and data.get("city") and data.get("tz"))

    if cache_enabled and directory_source == "airportsdata":
        await _ensure_airportsdata_cache(hass, ttl_days=30)

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
    if directory_source in ("auto", "aviationstack") and av_key and not is_blocked(hass, "aviationstack"):
        providers.append(AviationstackDirectoryProvider(hass, av_key))
    if directory_source in ("auto", "airlabs") and al_key and not is_blocked(hass, "airlabs"):
        providers.append(AirLabsDirectoryProvider(hass, al_key))
    if directory_source in ("auto", "fr24") and fr24_active_key and not is_blocked(hass, "fr24"):
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

    # Fallback: directory file (airportsdata CSV or OpenFlights .dat)
    try:
        if directory_source in ("airportsdata", "auto"):
            data = await airportsdata_get_airport(hass, iata, airports_url)
        elif directory_source in ("openflights", "custom", "aviationstack", "airlabs", "fr24"):
            data = await openflights_get_airport(hass, iata, airports_url)
        else:
            data = None
        if data:
            if cache_enabled:
                await async_set_airport(hass, iata, data)
            return data
    except Exception as e:
        _LOGGER.debug("Directory airport fallback failed for %s: %s", iata, e)

    return None


async def get_airline(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    iata = (iata or "").strip().upper()
    if not iata:
        return None

    cache_enabled = bool(_get_option(options, "cache_directory", True))
    ttl_days = int(_get_option(options, "cache_ttl_days", 30))
    source = _directory_source(options)
    airlines_url = str(_get_option(options, "directory_airlines_url", "")).strip() or OPENFLIGHTS_AIRLINES_URL

    if cache_enabled:
        cached = await async_get_airline(hass, iata)
        if is_fresh(cached, ttl_days):
            return cached

    av_key = (options.get("aviationstack_access_key") or "").strip()
    al_key = (options.get("airlabs_api_key") or "").strip()

    providers = []
    if source in ("auto", "aviationstack") and av_key:
        providers.append(AviationstackDirectoryProvider(hass, av_key))
    if source in ("auto", "airlabs") and al_key:
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

    # Fallback: airlines.dat (OpenFlights or user-provided URL)
    if source == "custom" and not airlines_url:
        airlines_url = OPENFLIGHTS_AIRLINES_URL
    try:
        if source in ("auto", "openflights", "custom", "aviationstack", "airlabs", "fr24"):
            data = await openflights_get_airline(hass, iata, airlines_url)
        else:
            data = None
        if data:
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
