"""Status update manager with smart refresh scheduling."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .status_resolver import apply_status
from .manual_store import async_update_manual_flight


STATUS_CACHE_KEY = "status_cache"


def _status_cache(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    return hass.data.setdefault(DOMAIN, {}).setdefault(STATUS_CACHE_KEY, {})


def _parse_dt(val: Any) -> datetime | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        dt = dt_util.parse_datetime(val)
        if dt is not None:
            return dt
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def _best_time(flight: dict[str, Any], side: str, keys: list[str]) -> datetime | None:
    block = flight.get(side) or {}
    for k in keys:
        dt = _parse_dt(block.get(k))
        if dt:
            return dt_util.as_utc(dt) if dt.tzinfo else dt_util.as_utc(dt_util.as_local(dt))
    return None


def compute_next_refresh_seconds(flight: dict[str, Any], now: datetime, ttl_minutes: int) -> int | None:
    """Compute next refresh interval in seconds.

    Strategy:
    - More frequent near departure/arrival or in-flight.
    - Less frequent when far out.
    - Stop once the flight is sufficiently in the past.
    - Always respect a minimum TTL to ration provider calls.
    """
    now = dt_util.as_utc(now)
    ttl_seconds = max(60, int(ttl_minutes) * 60)

    dep = _best_time(flight, "dep", ["actual", "estimated", "scheduled"])
    arr = _best_time(flight, "arr", ["actual", "estimated", "scheduled"])
    state = (flight.get("status_state") or "unknown").lower()

    if not dep and not arr:
        return None

    # If far in the past, stop refreshing
    if arr and now > arr + timedelta(hours=6):
        return None

    # In air or very close to departure -> frequent
    if dep and now >= dep - timedelta(hours=1) and (not arr or now <= arr):
        return max(ttl_seconds, 15 * 60)

    if dep and now < dep:
        delta = dep - now
        if delta > timedelta(days=2):
            return max(ttl_seconds, 12 * 60 * 60)
        if delta > timedelta(days=1):
            return max(ttl_seconds, 6 * 60 * 60)
        if delta > timedelta(hours=6):
            return max(ttl_seconds, 2 * 60 * 60)
        if delta > timedelta(hours=2):
            return max(ttl_seconds, 30 * 60)
        return max(ttl_seconds, 10 * 60)

    # If landed/cancelled and within a few hours, check rarely
    if state in ("landed", "cancelled"):
        return max(ttl_seconds, 3 * 60 * 60)

    # Fallback: periodic but not frequent
    return max(ttl_seconds, 60 * 60)


async def _fetch_status(hass: HomeAssistant, options: dict[str, Any], flight: dict[str, Any]) -> dict[str, Any] | None:
    """Fetch provider status for a flight, honoring configured provider preference."""
    provider = (options.get("status_provider") or "flightradar24").lower()
    use_sandbox = bool(options.get("fr24_use_sandbox", False))
    fr24_key = (options.get("fr24_api_key") or "").strip()
    fr24_sandbox_key = (options.get("fr24_sandbox_key") or "").strip()
    fr24_active_key = fr24_sandbox_key if use_sandbox and fr24_sandbox_key else fr24_key
    av_key = (options.get("aviationstack_access_key") or "").strip()
    al_key = (options.get("airlabs_api_key") or "").strip()
    os_user = (options.get("opensky_username") or "").strip()
    os_pass = (options.get("opensky_password") or "").strip()
    fr24_version = (options.get("fr24_api_version") or "v1").strip()

    # Helper to unwrap provider output into status dict
    def _unwrap(res: Any) -> dict[str, Any] | None:
        if res is None:
            return None
        if isinstance(res, dict):
            return res
        details = getattr(res, "details", None)
        if isinstance(details, dict):
            return details
        return None

    # Provider preference with fallbacks if missing key
    if provider == "flightradar24" and fr24_active_key:
        from .providers.status.flightradar24 import Flightradar24StatusProvider

        res = await Flightradar24StatusProvider(
            hass, api_key=fr24_active_key, use_sandbox=use_sandbox, api_version=fr24_version
        ).async_get_status(flight)
        return _unwrap(res)

    if provider == "aviationstack" and av_key:
        from .providers.status.aviationstack import AviationstackStatusProvider

        res = await AviationstackStatusProvider(hass, av_key).async_get_status(flight)
        return _unwrap(res)

    if provider == "airlabs" and al_key:
        from .providers.status.airlabs import AirLabsStatusProvider

        res = await AirLabsStatusProvider(hass, al_key).async_get_status(flight)
        return _unwrap(res)

    if provider == "opensky" and (os_user or os_pass):
        # OpenSky can work without auth but is rate-limited; only use if configured
        from .providers.status.opensky import OpenSkyEnrichmentProvider

        res = await OpenSkyEnrichmentProvider(hass).async_get_status(flight)
        return _unwrap(res)

    if provider == "local":
        from .providers.status.local import LocalStatusProvider

        dep = _parse_dt((flight.get("dep") or {}).get("scheduled"))
        if dep is None:
            return None
        arr = _parse_dt((flight.get("arr") or {}).get("scheduled"))
        res = await LocalStatusProvider().async_get_status(
            flight_key=flight.get("flight_key") or "",
            airline_code=flight.get("airline_code") or "",
            flight_number=flight.get("flight_number") or "",
            dep_airport=((flight.get("dep") or {}).get("airport") or {}).get("iata") or "",
            arr_airport=((flight.get("arr") or {}).get("airport") or {}).get("iata") or "",
            scheduled_departure=dep,
            scheduled_arrival=arr,
            now=dt_util.utcnow(),
        )
        return _unwrap(res)

    if provider == "mock":
        from .providers.status.mock import MockStatusProvider

        res = await MockStatusProvider().async_get_status(flight)
        return _unwrap(res)

    # Fallback: try any configured provider in priority order
    if fr24_key:
        from .providers.status.flightradar24 import Flightradar24StatusProvider

        use_sandbox = bool(options.get("fr24_use_sandbox", False))
        res = await Flightradar24StatusProvider(
            hass, api_key=fr24_key, use_sandbox=use_sandbox, api_version=fr24_version
        ).async_get_status(flight)
        return _unwrap(res)
    if av_key:
        from .providers.status.aviationstack import AviationstackStatusProvider

        res = await AviationstackStatusProvider(hass, av_key).async_get_status(flight)
        return _unwrap(res)
    if al_key:
        from .providers.status.airlabs import AirLabsStatusProvider

        res = await AirLabsStatusProvider(hass, al_key).async_get_status(flight)
        return _unwrap(res)

    return None


async def async_update_statuses(
    hass: HomeAssistant, options: dict[str, Any], flights: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], datetime | None]:
    """Apply cached status, refresh due flights, and return next refresh time.

    This avoids a fixed polling interval and instead computes a per-flight
    next_check to reduce API usage while keeping nearby flights fresh.
    """
    cache = _status_cache(hass)
    now = dt_util.utcnow()
    ttl_minutes = int(options.get("status_ttl_minutes", 5))

    # Apply cached status to all flights first
    for f in flights:
        key = f.get("flight_key")
        if not key:
            continue
        cached = cache.get(key)
        if not cached:
            continue
        status = cached.get("status")
        if isinstance(status, dict):
            f["status"] = status
            f["status_updated_at"] = cached.get("updated_at")
            apply_status(f, status)

    # Determine which flights are due
    due: list[dict[str, Any]] = []
    next_times: list[datetime] = []

    for f in flights:
        key = f.get("flight_key")
        if not key:
            continue
        cached = cache.get(key, {})
        next_check = cached.get("next_check")
        if isinstance(next_check, str):
            next_check_dt = _parse_dt(next_check)
        elif isinstance(next_check, datetime):
            next_check_dt = next_check
        else:
            next_check_dt = None

        if not next_check_dt or now >= dt_util.as_utc(next_check_dt):
            due.append(f)
        else:
            next_times.append(dt_util.as_utc(next_check_dt))

    # Refresh due flights (sequential to limit API calls)
    for f in due:
        status = await _fetch_status(hass, options, f)
        key = f.get("flight_key")
        if not key:
            continue
        if isinstance(status, dict):
            f["status"] = status
            f["status_updated_at"] = now.isoformat()
            apply_status(f, status)

            # Backfill missing dep/arr airports and scheduled times into manual storage
            if (f.get("source") or "manual") == "manual":
                dep_air = (f.get("dep") or {}).get("airport") or {}
                arr_air = (f.get("arr") or {}).get("airport") or {}
                updates: dict[str, Any] = {}
                if not f.get("dep_airport") and dep_air.get("iata"):
                    updates["dep_airport"] = dep_air.get("iata")
                if not f.get("arr_airport") and arr_air.get("iata"):
                    updates["arr_airport"] = arr_air.get("iata")
                if not f.get("scheduled_departure"):
                    updates["scheduled_departure"] = (f.get("dep") or {}).get("scheduled") or status.get("dep_scheduled")
                if not f.get("scheduled_arrival"):
                    updates["scheduled_arrival"] = (f.get("arr") or {}).get("scheduled") or status.get("arr_scheduled")
                if updates:
                    await async_update_manual_flight(hass, key, updates)
        # Compute next refresh time
        refresh_seconds = compute_next_refresh_seconds(f, now, ttl_minutes)
        if refresh_seconds is None:
            cache.pop(key, None)
            continue
        next_dt = now + timedelta(seconds=refresh_seconds)
        cache[key] = {
            "status": f.get("status") if isinstance(f.get("status"), dict) else status,
            "updated_at": now.isoformat(),
            "next_check": next_dt.isoformat(),
        }
        next_times.append(next_dt)

    next_time = min(next_times) if next_times else None
    return flights, next_time
