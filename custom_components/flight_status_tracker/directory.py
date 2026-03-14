"""Airport/airline directory lookup and local caching.

Design goals:
- No API keys required for airport/airline enrichment by default.
- Cache full airport + airline datasets locally.
- Refresh datasets roughly monthly.
"""
from __future__ import annotations

import logging
import csv
from io import StringIO
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
from .api_metrics import record_api_call

_LOGGER = logging.getLogger(__name__)

_CACHE_TTL_DAYS = 30
_PROVIDER_CACHE_TTL_DAYS = 7
_META_AIRPORTSDATA_FETCHED_AT = "airportsdata_fetched_at"
_META_OPENFLIGHTS_AIRLINES_FETCHED_AT = "openflights_airlines_fetched_at"
_META_OPENFLIGHTS_AIRLINES_SCHEMA_VERSION = "openflights_airlines_schema_version"
_OPENFLIGHTS_AIRLINES_SCHEMA_VERSION = 4
CONF_DIRECTORY_SOURCE_MODE = "directory_source_mode"
DIRECTORY_SOURCE_INBUILT = "inbuilt"
DIRECTORY_SOURCE_PROVIDER = "provider"
DEFAULT_DIRECTORY_SOURCE_MODE = DIRECTORY_SOURCE_INBUILT
_SOURCE_FLIGHTAPI = "flightapi_iata"

def airline_logo_url(iata: str | None) -> str | None:
    """Return a lightweight logo URL for airline IATA code."""
    if not iata:
        return None
    code = str(iata).strip().upper()
    if not code:
        return None
    return f"https://pics.avs.io/64/64/{code}.png"


def _directory_mode(options: dict[str, Any] | None) -> str:
    raw = str((options or {}).get(CONF_DIRECTORY_SOURCE_MODE) or DEFAULT_DIRECTORY_SOURCE_MODE).strip().lower()
    if raw in (DIRECTORY_SOURCE_INBUILT, DIRECTORY_SOURCE_PROVIDER):
        return raw
    return DEFAULT_DIRECTORY_SOURCE_MODE


def _is_source(entry: dict[str, Any] | None, source: str) -> bool:
    if not isinstance(entry, dict):
        return False
    return str(entry.get("source") or "").strip().lower() == source


def _pick_str(data: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        val = data.get(k)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _extract_candidates(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results", "response", "airlines", "airports", "result"):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
        if payload:
            return [payload]
    return []


def _normalize_tz(tz: str | None) -> str | None:
    if not tz:
        return None
    out = tz.strip()
    if out == "Asia/Calcutta":
        return "Asia/Kolkata"
    return out or None


async def _flightapi_iata_candidates(
    hass: HomeAssistant,
    api_key: str,
    name: str,
    lookup_type: str,
) -> list[dict[str, Any]]:
    if not api_key or not name:
        return []
    url = f"https://api.flightapi.io/iata/{api_key}"
    params = {"name": name, "type": lookup_type}
    try:
        session = async_get_clientsession(hass)
        async with session.get(url, params=params, timeout=20) as resp:
            if resp.status != 200:
                _LOGGER.debug("FlightAPI iata lookup failed status=%s type=%s name=%s", resp.status, lookup_type, name)
                record_api_call(hass, "flightapi", flow="directory", outcome="error")
                return []
            payload = await resp.json(content_type=None)
    except Exception as e:
        _LOGGER.debug("FlightAPI iata lookup error type=%s name=%s err=%s", lookup_type, name, e)
        record_api_call(hass, "flightapi", flow="directory", outcome="error")
        return []
    record_api_call(hass, "flightapi", flow="directory", outcome="success")
    return _extract_candidates(payload)

def _airline_score(item: dict[str, Any]) -> int:
    score = 0
    if str(item.get("active") or "").strip().upper() == "Y":
        score += 2
    if item.get("icao"):
        score += 1
    if item.get("name"):
        score += 1
    return score


def _select_airline_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not records:
        return None
    active = [r for r in records if str(r.get("active") or "").strip().upper() == "Y"]
    pool = active or records
    best = max(pool, key=_airline_score)

    names = {
        str(r.get("name") or "").strip().lower()
        for r in pool
        if str(r.get("name") or "").strip()
    }
    out = dict(best)
    if len(names) > 1:
        # Ambiguous code in upstream dataset; avoid writing a likely-wrong label.
        out["name"] = None
        out["ambiguous"] = True
    return out


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
    schema_version = int(meta.get(_META_OPENFLIGHTS_AIRLINES_SCHEMA_VERSION, 0) or 0)
    if not force and dt and airlines and schema_version >= _OPENFLIGHTS_AIRLINES_SCHEMA_VERSION:
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
    by_iata: dict[str, list[dict[str, Any]]] = {}
    try:
        reader = csv.reader(StringIO(text))
        for row in reader:
            # Format: Airline ID, Name, Alias, IATA, ICAO, Callsign, Country, Active
            if len(row) < 8:
                continue
            iata_code = (row[3] or "").strip().upper()
            if not iata_code or iata_code == "\\N":
                continue
            record = {
                "iata": iata_code,
                "icao": (row[4] or "").strip() or None,
                "name": (row[1] or "").strip() or None,
                "country": (row[6] or "").strip() or None,
                "active": (row[7] or "").strip() or None,
                "source": "openflights",
            }
            by_iata.setdefault(iata_code, []).append(record)
    except Exception as e:
        _LOGGER.debug("OpenFlights airlines parse failed: %s", e)
        return

    index: dict[str, dict[str, Any]] = {}
    for iata_code, records in by_iata.items():
        chosen = _select_airline_record(records)
        if not chosen:
            continue
        chosen["logo_url"] = airline_logo_url(iata_code)
        chosen["fetched_at"] = now_iso
        index[iata_code] = chosen

    cache["airlines"] = index
    meta[_META_OPENFLIGHTS_AIRLINES_FETCHED_AT] = now_iso
    meta[_META_OPENFLIGHTS_AIRLINES_SCHEMA_VERSION] = _OPENFLIGHTS_AIRLINES_SCHEMA_VERSION
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


def _is_complete_airport(data: dict[str, Any] | None) -> bool:
    if not isinstance(data, dict):
        return False
    return bool(data.get("name") and data.get("city") and data.get("tz"))


async def _get_airport_inbuilt(hass: HomeAssistant, iata: str) -> dict[str, Any] | None:
    await _ensure_airportsdata_cache(hass, ttl_days=_CACHE_TTL_DAYS)
    cached = await async_get_airport(hass, iata)
    if is_fresh(cached, _CACHE_TTL_DAYS) and _is_complete_airport(cached):
        return cached

    try:
        data = await openflights_get_airport(hass, iata, OPENFLIGHTS_AIRPORTS_URL)
    except Exception as e:
        _LOGGER.debug("OpenFlights airport fallback failed for %s: %s", iata, e)
        data = None
    if data:
        await async_set_airport(hass, iata, data)
        return await async_get_airport(hass, iata)
    return cached


async def _get_airline_inbuilt(hass: HomeAssistant, iata: str) -> dict[str, Any] | None:
    await _ensure_openflights_airlines_cache(hass, ttl_days=_CACHE_TTL_DAYS)
    cached = await async_get_airline(hass, iata)
    if is_fresh(cached, _CACHE_TTL_DAYS):
        return cached

    try:
        session = async_get_clientsession(hass)
        async with session.get(OPENFLIGHTS_AIRLINES_URL, timeout=30) as resp:
            if resp.status != 200:
                return cached
            text = await resp.text()
    except Exception:
        return cached

    records: list[dict[str, Any]] = []
    reader = csv.reader(StringIO(text))
    for row in reader:
        if len(row) < 8:
            continue
        iata_code = (row[3] or "").strip().upper()
        if iata_code != iata:
            continue
        records.append(
            {
                "iata": iata_code,
                "icao": (row[4] or "").strip() or None,
                "name": (row[1] or "").strip() or None,
                "country": (row[6] or "").strip() or None,
                "active": (row[7] or "").strip() or None,
                "source": "openflights",
            }
        )
    if records:
        data = _select_airline_record(records) or {}
        data["logo_url"] = airline_logo_url(iata)
        await async_set_airline(hass, iata, data)
        return await async_get_airline(hass, iata)
    return cached


def _code_equals(candidate: dict[str, Any], expected: str, keys: tuple[str, ...]) -> bool:
    exp = expected.strip().upper()
    if not exp:
        return False
    for k in keys:
        val = candidate.get(k)
        if isinstance(val, str) and val.strip().upper() == exp:
            return True
    return False


async def _get_airline_provider(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    api_key = str(options.get("flightapi_api_key") or "").strip()
    candidates = await _flightapi_iata_candidates(hass, api_key, iata, "airline")
    if not candidates:
        return None

    code_keys = ("iata", "iata_code", "code", "fs", "airlineCode", "airline_code")
    matched = [c for c in candidates if _code_equals(c, iata, code_keys)]
    if not matched:
        return None
    c = matched[0]
    data = {
        "iata": iata,
        "icao": _pick_str(c, ("icao", "icao_code", "icaoCode")),
        "name": _pick_str(c, ("name", "airline_name", "airlineName")),
        "country": _pick_str(c, ("country", "country_name", "countryName")),
        "source": _SOURCE_FLIGHTAPI,
        "logo_url": airline_logo_url(iata),
    }
    await async_set_airline(hass, iata, data)
    return await async_get_airline(hass, iata)


async def _get_airport_provider(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    api_key = str(options.get("flightapi_api_key") or "").strip()
    candidates = await _flightapi_iata_candidates(hass, api_key, iata, "airport")
    if not candidates:
        return None

    code_keys = ("iata", "iata_code", "code", "fs", "airportCode", "airport_code")
    matched = [c for c in candidates if _code_equals(c, iata, code_keys)]
    if not matched:
        return None
    c = matched[0]
    tz = _normalize_tz(_pick_str(c, ("tz", "timezone", "timeZone", "timezone_name")))
    data = {
        "iata": iata,
        "icao": _pick_str(c, ("icao", "icao_code", "icaoCode")),
        "name": _pick_str(c, ("name", "airport_name", "airportName")),
        "city": _pick_str(c, ("city", "city_name", "cityName", "municipality")),
        "country": _pick_str(c, ("country", "country_name", "countryName")),
        "tz": tz,
        "source": _SOURCE_FLIGHTAPI,
    }
    await async_set_airport(hass, iata, data)
    return await async_get_airport(hass, iata)


async def get_airport(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    iata = (iata or "").strip().upper()
    if not iata:
        return None

    mode = _directory_mode(options)
    cached = await async_get_airport(hass, iata)
    if mode == DIRECTORY_SOURCE_PROVIDER and _is_source(cached, _SOURCE_FLIGHTAPI) and is_fresh(cached, _PROVIDER_CACHE_TTL_DAYS):
        return cached
    if mode == DIRECTORY_SOURCE_INBUILT and is_fresh(cached, _CACHE_TTL_DAYS) and _is_complete_airport(cached):
        return cached

    if mode == DIRECTORY_SOURCE_PROVIDER:
        provider = await _get_airport_provider(hass, options, iata)
        if provider:
            return provider
    return await _get_airport_inbuilt(hass, iata)


async def get_airline(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    iata = (iata or "").strip().upper()
    if not iata:
        return None

    mode = _directory_mode(options)
    cached = await async_get_airline(hass, iata)
    if mode == DIRECTORY_SOURCE_PROVIDER and _is_source(cached, _SOURCE_FLIGHTAPI) and is_fresh(cached, _PROVIDER_CACHE_TTL_DAYS):
        return cached
    if mode == DIRECTORY_SOURCE_INBUILT and is_fresh(cached, _CACHE_TTL_DAYS):
        return cached

    if mode == DIRECTORY_SOURCE_PROVIDER:
        provider = await _get_airline_provider(hass, options, iata)
        if provider:
            return provider
    return await _get_airline_inbuilt(hass, iata)


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
