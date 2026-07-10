"""Airport/airline directory lookup and local caching.

Design goals:
- No API keys required for airport/airline enrichment by default.
- Cache full airport + airline datasets locally.
- Refresh datasets roughly monthly.
"""
from __future__ import annotations

import logging
import csv
import re
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
_OPENFLIGHTS_AIRLINES_SCHEMA_VERSION = 5
CONF_DIRECTORY_SOURCE_MODE = "directory_source_mode"
DIRECTORY_SOURCE_INBUILT = "inbuilt"
DIRECTORY_SOURCE_PROVIDER = "provider"
DEFAULT_DIRECTORY_SOURCE_MODE = DIRECTORY_SOURCE_INBUILT
_CONF_SCHEDULE_PROVIDER = "schedule_provider"
_CONF_STATUS_PROVIDER = "status_provider"
_CONF_AERODATABOX_GATEWAY = "aerodatabox_gateway"
_CONF_AERODATABOX_RAPIDAPI_KEY = "aerodatabox_rapidapi_key"
_CONF_AERODATABOX_APIMARKET_KEY = "aerodatabox_apimarket_key"
_SOURCE_FLIGHTAPI = "flightapi_iata"
_SOURCE_AERODATABOX = "aerodatabox"
_PROVIDER_SOURCES = {_SOURCE_AERODATABOX, _SOURCE_FLIGHTAPI}

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


def _is_provider_source(entry: dict[str, Any] | None) -> bool:
    if not isinstance(entry, dict):
        return False
    src = str(entry.get("source") or "").strip().lower()
    return src in _PROVIDER_SOURCES


def normalize_airline_name(iata: str | None, name: str | None) -> str | None:
    _ = iata
    normalized = str(name or "").strip()
    return normalized or None


def _normalize_airline_record(entry: dict[str, Any] | None, iata: str | None = None) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return entry
    code = str(iata or entry.get("iata") or "").strip().upper()
    if not code:
        return entry
    normalized_name = normalize_airline_name(code, entry.get("name"))
    source = str(entry.get("source") or "").strip().lower()
    if source == "openflights" and entry.get("ambiguous"):
        normalized_name = None
    if normalized_name == entry.get("name") and entry.get("iata") == code:
        return entry
    return {**entry, "iata": code, "name": normalized_name}


def _provider_order(options: dict[str, Any]) -> list[str]:
    """Return the single configured provider for directory enrichment."""
    for key in (_CONF_SCHEDULE_PROVIDER, _CONF_STATUS_PROVIDER):
        val = str(options.get(key) or "").strip().lower()
        if val in ("aerodatabox", "flightapi"):
            return [val]
    return ["aerodatabox"]


def _pick_str(data: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        val = data.get(k)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _normalize_image_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    out = value.strip()
    if not out:
        return None
    if out.startswith("http://") or out.startswith("https://"):
        return out
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


def _merge_airport(primary: dict[str, Any] | None, fallback: dict[str, Any] | None) -> dict[str, Any] | None:
    """Merge missing airport fields from fallback into primary."""
    if not isinstance(primary, dict) and not isinstance(fallback, dict):
        return None
    if not isinstance(primary, dict):
        return dict(fallback or {})
    if not isinstance(fallback, dict):
        return dict(primary)

    out = dict(primary)
    for key in ("name", "city", "country", "tz", "icao", "lat", "lon"):
        if not out.get(key) and fallback.get(key):
            out[key] = fallback.get(key)
    out["iata"] = out.get("iata") or fallback.get("iata")
    return out


_AIRPORT_STOPWORDS = {
    "airport",
    "international",
    "intl",
    "aerodrome",
    "airfield",
    "terminal",
}


def _name_tokens(name: str | None) -> set[str]:
    if not name:
        return set()
    parts = re.findall(r"[a-zA-Z]+", name.lower())
    return {p for p in parts if len(p) >= 3 and p not in _AIRPORT_STOPWORDS}


async def _infer_airport_missing_fields(
    hass: HomeAssistant, airport: dict[str, Any] | None, iata: str
) -> dict[str, Any] | None:
    """Best-effort fill for missing city/tz using existing cached airport records."""
    if not isinstance(airport, dict):
        return airport
    if airport.get("city") and airport.get("tz"):
        return airport

    name = str(airport.get("name") or "").strip()
    if not name:
        return airport

    tokens = _name_tokens(name)
    if not tokens:
        return airport

    cache = await async_load_cache(hass)
    airports = (cache.get("airports") or {}) if isinstance(cache, dict) else {}
    if not isinstance(airports, dict) or not airports:
        return airport

    best: dict[str, Any] | None = None
    best_score = 0
    lowered_name = name.lower()
    for code, cand in airports.items():
        if str(code).upper() == iata:
            continue
        if not isinstance(cand, dict):
            continue
        city = str(cand.get("city") or "").strip()
        tz = str(cand.get("tz") or "").strip()
        if not city and not tz:
            continue
        cand_name = str(cand.get("name") or "").strip()
        cand_tokens = _name_tokens(f"{city} {cand_name}")
        overlap = len(tokens & cand_tokens)
        score = overlap
        if city and city.lower() in lowered_name:
            score += 3
        if score > best_score:
            best_score = score
            best = cand

    if not best or best_score < 2:
        return airport

    out = dict(airport)
    if not out.get("city"):
        out["city"] = best.get("city")
    if not out.get("tz"):
        out["tz"] = best.get("tz")
    if not out.get("country"):
        out["country"] = best.get("country")
    return out


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
    normalized_cached = _normalize_airline_record(cached, iata)
    if normalized_cached != cached and normalized_cached:
        await async_set_airline(hass, iata, normalized_cached)
        cached = await async_get_airline(hass, iata)
    else:
        cached = normalized_cached
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
        data = _normalize_airline_record(data, iata) or data
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


async def _get_airline_provider_flightapi(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    api_key = str(options.get("flightapi_api_key") or "").strip()
    candidates = await _flightapi_iata_candidates(hass, api_key, iata, "airline")
    if not candidates:
        return None

    code_keys = ("iata", "iata_code", "code", "fs", "airlineCode", "airline_code")
    matched = [c for c in candidates if _code_equals(c, iata, code_keys)]
    c: dict[str, Any] | None = matched[0] if matched else None
    if c is None and len(candidates) == 1:
        # FlightAPI may return a single authoritative hit with non-IATA code fields
        # (e.g. ICAO/FS only). Accept it instead of falling back to OpenFlights.
        c = candidates[0]
    if c is None:
        return None
    data = {
        "iata": iata,
        "icao": _pick_str(c, ("icao", "icao_code", "icaoCode")),
        "name": normalize_airline_name(iata, _pick_str(c, ("name", "airline_name", "airlineName"))),
        "country": _pick_str(c, ("country", "country_name", "countryName")),
        "source": _SOURCE_FLIGHTAPI,
        "logo_url": airline_logo_url(iata),
    }
    await async_set_airline(hass, iata, data)
    return await async_get_airline(hass, iata)


async def _get_airport_provider_flightapi(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    api_key = str(options.get("flightapi_api_key") or "").strip()
    candidates = await _flightapi_iata_candidates(hass, api_key, iata, "airport")
    if not candidates:
        return None

    code_keys = ("iata", "iata_code", "code", "fs", "airportCode", "airport_code")
    matched = [c for c in candidates if _code_equals(c, iata, code_keys)]
    c: dict[str, Any] | None = matched[0] if matched else None
    if c is None and len(candidates) == 1:
        # Same handling as airlines: prefer a single provider hit over inbuilt fallback.
        c = candidates[0]
    if c is None:
        return None
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


def _aerodatabox_auth(options: dict[str, Any]) -> tuple[str, dict[str, str], bool]:
    gateway = str(options.get(_CONF_AERODATABOX_GATEWAY) or "rapidapi").strip().lower()
    if gateway == "apimarket":
        key = str(options.get(_CONF_AERODATABOX_APIMARKET_KEY) or "").strip()
        if not key:
            return "", {}, False
        return (
            "https://prod.api.market/api/v1/aedbx/aerodatabox",
            {
                "x-magicapi-key": key,
                "accept": "application/json",
            },
            True,
        )

    key = str(options.get(_CONF_AERODATABOX_RAPIDAPI_KEY) or "").strip()
    if not key:
        return "", {}, False
    return (
        "https://aerodatabox.p.rapidapi.com",
        {
            "X-RapidAPI-Key": key,
            "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com",
            "accept": "application/json",
        },
        True,
    )


async def _get_airport_provider_aerodatabox(
    hass: HomeAssistant, options: dict[str, Any], iata: str
) -> dict[str, Any] | None:
    base_url, headers, enabled = _aerodatabox_auth(options)
    if not enabled:
        return None

    url = f"{base_url}/airports/iata/{iata}"
    params = {"withRunways": "false", "withTime": "true"}
    try:
        session = async_get_clientsession(hass)
        async with session.get(url, params=params, headers=headers, timeout=20) as resp:
            if resp.status != 200:
                record_api_call(hass, "aerodatabox", flow="directory", outcome="error")
                return None
            payload = await resp.json(content_type=None)
    except Exception as e:
        _LOGGER.debug("AeroDataBox airport directory lookup failed for %s: %s", iata, e)
        record_api_call(hass, "aerodatabox", flow="directory", outcome="error")
        return None

    record_api_call(hass, "aerodatabox", flow="directory", outcome="success")
    if not isinstance(payload, dict):
        return None

    city = payload.get("municipalityName")
    local_time = payload.get("localTime")
    tz = None
    if isinstance(local_time, dict):
        tz = _normalize_tz(_pick_str(local_time, ("timezoneIana", "timezone")))
    data = {
        "iata": iata,
        "icao": _pick_str(payload, ("icao", "icaoCode")),
        "name": _pick_str(payload, ("name", "shortName")),
        "city": city if isinstance(city, str) and city.strip() else None,
        "country": _pick_str(payload, ("countryName",)),
        "tz": tz,
        "source": _SOURCE_AERODATABOX,
    }
    await async_set_airport(hass, iata, data)
    return await async_get_airport(hass, iata)


async def _get_airline_provider_aerodatabox(
    hass: HomeAssistant, options: dict[str, Any], iata: str
) -> dict[str, Any] | None:
    # AeroDataBox currently has no dedicated airline-directory endpoint
    # (single-airline details by code). Keep provider order semantics
    # and fall through to the next provider/inbuilt source.
    _ = (hass, options, iata)
    return None


async def _get_airline_provider(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    for provider in _provider_order(options):
        if provider == "aerodatabox":
            data = await _get_airline_provider_aerodatabox(hass, options, iata)
        elif provider == "flightapi":
            data = await _get_airline_provider_flightapi(hass, options, iata)
        else:
            data = None
        if data:
            return data
    return None


async def _get_airport_provider(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    for provider in _provider_order(options):
        if provider == "aerodatabox":
            data = await _get_airport_provider_aerodatabox(hass, options, iata)
        elif provider == "flightapi":
            data = await _get_airport_provider_flightapi(hass, options, iata)
        else:
            data = None
        if data:
            return data
    return None


async def get_airport(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    iata = (iata or "").strip().upper()
    if not iata:
        return None

    mode = _directory_mode(options)
    cached = await async_get_airport(hass, iata)
    if mode == DIRECTORY_SOURCE_PROVIDER and _is_provider_source(cached) and is_fresh(cached, _PROVIDER_CACHE_TTL_DAYS):
        if _is_complete_airport(cached):
            return cached
        # Provider cache is fresh but incomplete; backfill missing fields from inbuilt.
        fallback = await _get_airport_inbuilt(hass, iata)
        merged = _merge_airport(cached, fallback)
        merged = await _infer_airport_missing_fields(hass, merged, iata)
        if merged and merged != cached:
            await async_set_airport(hass, iata, merged)
            return await async_get_airport(hass, iata)
        return merged or cached
    if mode == DIRECTORY_SOURCE_INBUILT and is_fresh(cached, _CACHE_TTL_DAYS) and _is_complete_airport(cached):
        return cached

    if mode == DIRECTORY_SOURCE_PROVIDER:
        provider = await _get_airport_provider(hass, options, iata)
        if provider:
            if _is_complete_airport(provider):
                return provider
            fallback = await _get_airport_inbuilt(hass, iata)
            merged = _merge_airport(provider, fallback)
            merged = await _infer_airport_missing_fields(hass, merged, iata)
            if merged and merged != provider:
                await async_set_airport(hass, iata, merged)
                return await async_get_airport(hass, iata)
            return merged or provider
    return await _get_airport_inbuilt(hass, iata)


async def get_airline(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    iata = (iata or "").strip().upper()
    if not iata:
        return None

    mode = _directory_mode(options)
    if mode == DIRECTORY_SOURCE_INBUILT:
        await _ensure_openflights_airlines_cache(hass, ttl_days=_CACHE_TTL_DAYS)
    cached = await async_get_airline(hass, iata)
    normalized_cached = _normalize_airline_record(cached, iata)
    if normalized_cached != cached and normalized_cached:
        await async_set_airline(hass, iata, normalized_cached)
        cached = await async_get_airline(hass, iata)
    else:
        cached = normalized_cached
    if mode == DIRECTORY_SOURCE_PROVIDER and _is_provider_source(cached) and is_fresh(cached, _PROVIDER_CACHE_TTL_DAYS):
        return cached
    if mode == DIRECTORY_SOURCE_INBUILT and is_fresh(cached, _CACHE_TTL_DAYS):
        return cached

    if mode == DIRECTORY_SOURCE_PROVIDER:
        provider = await _get_airline_provider(hass, options, iata)
        if provider:
            return provider
    return await _get_airline_inbuilt(hass, iata)


async def upsert_airline_aircraft_image(
    hass: HomeAssistant,
    airline_iata: str,
    aircraft_image_url: str | None,
) -> bool:
    """Store aircraft image URL under airline directory when missing.

    Returns True when directory cache was updated.
    """
    iata = (airline_iata or "").strip().upper()
    url = _normalize_image_url(aircraft_image_url)
    if not iata or not url:
        return False

    cached = await async_get_airline(hass, iata)
    if cached and isinstance(cached, dict):
        existing = _normalize_image_url(cached.get("aircraft_image_url"))
        if existing:
            return False
        payload = {**cached, "aircraft_image_url": url}
    else:
        payload = {
            "iata": iata,
            "source": "provider_enriched",
            "logo_url": airline_logo_url(iata),
            "aircraft_image_url": url,
        }

    await async_set_airline(hass, iata, payload)
    return True


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
