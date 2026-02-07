"""Status update manager with smart refresh scheduling."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .status_resolver import apply_status
from .schedule_lookup import lookup_schedule
from .directory import get_airport
from .manual_store import async_update_manual_flight
from .status_providers import async_fetch_status, async_fetch_position


STATUS_CACHE_KEY = "status_cache"
CONF_DELAY_GRACE_MINUTES = "delay_grace_minutes"
CONF_POSITION_PROVIDER = "position_provider"


def _status_cache(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    return hass.data.setdefault(DOMAIN, {}).setdefault(STATUS_CACHE_KEY, {})


def clear_status_cache(hass: HomeAssistant, flight_key: str | None = None) -> None:
    cache = _status_cache(hass)
    if flight_key:
        cache.pop(flight_key, None)
    else:
        cache.clear()


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


def _date_in_tz(val: Any, tzname: str | None) -> str | None:
    dt = _parse_dt(val)
    if not dt:
        return None
    if dt.tzinfo is None and tzname:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(tzname))
        except Exception:
            pass
    elif dt.tzinfo is not None and tzname:
        try:
            dt = dt.astimezone(ZoneInfo(tzname))
        except Exception:
            pass
    return dt.date().isoformat()


def _status_date_mismatch(flight: dict[str, Any], status: dict[str, Any]) -> bool:
    dep = flight.get("dep") or {}
    dep_air = dep.get("airport") or {}
    tzname = dep_air.get("tz")
    flight_dep = dep.get("scheduled_local") or dep.get("scheduled")
    status_dep = status.get("dep_scheduled") or status.get("dep_estimated") or status.get("dep_actual")
    if not flight_dep or not status_dep:
        return False
    flight_date = _date_in_tz(flight_dep, tzname)
    status_date = _date_in_tz(status_dep, tzname)
    if not flight_date or not status_date:
        return False
    return flight_date != status_date


def _best_time(flight: dict[str, Any], side: str, keys: list[str]) -> datetime | None:
    block = flight.get(side) or {}
    for k in keys:
        dt = _parse_dt(block.get(k))
        if dt:
            return dt_util.as_utc(dt) if dt.tzinfo else dt_util.as_utc(dt_util.as_local(dt))
    return None


def _compute_delay_status(flight: dict[str, Any], grace_minutes: int) -> tuple[str, int | None]:
    state = (flight.get("status_state") or "unknown").lower()
    if state in ("cancelled", "canceled"):
        return "Cancelled", None

    dep = flight.get("dep") or {}
    arr = flight.get("arr") or {}

    dep_sched = _parse_dt(dep.get("scheduled"))
    dep_est = _parse_dt(dep.get("actual") or dep.get("estimated"))
    arr_sched = _parse_dt(arr.get("scheduled"))
    arr_est = _parse_dt(arr.get("actual") or arr.get("estimated"))

    ref_sched = None
    ref_est = None
    if arr_sched and arr_est:
        ref_sched, ref_est = arr_sched, arr_est
    elif dep_sched and dep_est:
        ref_sched, ref_est = dep_sched, dep_est

    if not ref_sched or not ref_est:
        return "Unknown", None

    delta = dt_util.as_utc(ref_est) - dt_util.as_utc(ref_sched)
    minutes = int(round(delta.total_seconds() / 60))
    if minutes > grace_minutes:
        return "Delayed", minutes
    return "On Time", minutes


def _duration_minutes(dep_dt: datetime | None, arr_dt: datetime | None) -> int | None:
    if not dep_dt or not arr_dt:
        return None
    dep_utc = dt_util.as_utc(dep_dt) if dep_dt.tzinfo else dt_util.as_utc(dt_util.as_local(dep_dt))
    arr_utc = dt_util.as_utc(arr_dt) if arr_dt.tzinfo else dt_util.as_utc(dt_util.as_local(arr_dt))
    delta = (arr_utc - dep_utc).total_seconds() / 60.0
    if delta < 0:
        return None
    return int(round(delta))


def _compute_durations(flight: dict[str, Any]) -> dict[str, int | None]:
    dep = flight.get("dep") or {}
    arr = flight.get("arr") or {}

    dep_sched = _parse_dt(dep.get("scheduled"))
    arr_sched = _parse_dt(arr.get("scheduled"))
    dep_est = _parse_dt(dep.get("actual") or dep.get("estimated"))
    arr_est = _parse_dt(arr.get("actual") or arr.get("estimated"))
    dep_act = _parse_dt(dep.get("actual"))
    arr_act = _parse_dt(arr.get("actual"))

    scheduled = _duration_minutes(dep_sched, arr_sched)
    estimated = _duration_minutes(dep_est, arr_est)
    actual = _duration_minutes(dep_act, arr_act)

    best = actual if actual is not None else (estimated if estimated is not None else scheduled)

    return {
        "duration_scheduled_minutes": scheduled,
        "duration_estimated_minutes": estimated,
        "duration_actual_minutes": actual,
        "duration_minutes": best,
    }


def _coerce_state_by_time(flight: dict[str, Any], now: datetime, future_grace_minutes: int = 90) -> None:
    """Prevent impossible states when scheduled times are clearly in the future."""
    state = (flight.get("status_state") or "unknown").lower()
    if state not in ("en route", "arrived", "cancelled", "canceled"):
        return

    dep = _best_time(flight, "dep", ["actual", "estimated", "scheduled"])
    if not dep:
        return

    now_utc = dt_util.as_utc(now)
    if now_utc < dep - timedelta(minutes=future_grace_minutes):
        flight["status_state"] = "Scheduled"


def _apply_assumed_arrival(flight: dict[str, Any], now: datetime, grace_minutes: int = 15) -> None:
    """If provider hasn't updated after arrival + grace, assume arrival."""
    state = (flight.get("status_state") or "unknown").lower()
    if state in ("arrived", "cancelled", "canceled", "landed"):
        return

    arr = _best_time(flight, "arr", ["actual", "estimated", "scheduled"])
    if not arr:
        return
    now_utc = dt_util.as_utc(now)
    if now_utc <= arr + timedelta(minutes=grace_minutes):
        return

    updated_at = _parse_dt(flight.get("status_updated_at"))
    if updated_at and dt_util.as_utc(updated_at) >= arr + timedelta(minutes=grace_minutes):
        return

    flight["status_state"] = "Arrived"
    status = flight.get("status") if isinstance(flight.get("status"), dict) else {}
    if not isinstance(status, dict):
        status = {}
    status.setdefault("provider", (status.get("provider") or "unknown"))
    status["error"] = "assumed_arrival"
    status["error_message"] = "No provider update after arrival; assumed Arrived."
    flight["status"] = status


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
    if arr and now > arr + timedelta(hours=1):
        return None

    # In air or very close to departure -> frequent
    if dep and now >= dep - timedelta(hours=1) and (not arr or now <= arr):
        return max(ttl_seconds, 15 * 60)

    if dep and now < dep:
        delta = dep - now
        # Far future: refresh occasionally (keeps estimates updated)
        if delta > timedelta(hours=6):
            return max(ttl_seconds, 6 * 60 * 60)
        if delta > timedelta(hours=2):
            return max(ttl_seconds, 30 * 60)
        return max(ttl_seconds, 10 * 60)

    # Stop refreshing once arrived/cancelled
    if state in ("arrived", "cancelled", "landed"):
        return None

    # If diverted, treat like active/en route (refresh frequently)
    if state == "diverted":
        return max(ttl_seconds, 15 * 60)

    # Fallback: periodic but not frequent
    return max(ttl_seconds, 60 * 60)




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
    grace_minutes = int(options.get(CONF_DELAY_GRACE_MINUTES, 10))

    # Apply cached status to all flights first
    for f in flights:
        key = f.get("flight_key")
        if not key:
            continue
        cached = cache.get(key)
        if not cached:
            continue
        status = cached.get("status")
        # If status provider changed, invalidate cached status so we refetch.
        if isinstance(status, dict) and _status_date_mismatch(f, status):
            status = {
                "provider": status_provider,
                "error": "date_mismatch",
                "error_message": "Provider returned status for different operating date",
            }
        if isinstance(status, dict):
            status_provider = (status.get("provider") or "").lower()
            configured_provider = (options.get("status_provider") or "flightradar24").lower()
            if status_provider and status_provider != configured_provider:
                cache.pop(key, None)
                continue
        if isinstance(status, dict):
            # If cached status appears to be for a different operating date, drop it and refetch.
            if _status_date_mismatch(f, status):
                cache.pop(key, None)
                continue
            f["status"] = status
            f["status_updated_at"] = cached.get("updated_at")
            apply_status(f, status)
            _coerce_state_by_time(f, now)
            _apply_assumed_arrival(f, now)
        delay_state, delay_minutes = _compute_delay_status(f, grace_minutes)
        f["delay_status"] = delay_state
        f["delay_status_key"] = (delay_state or "unknown").lower().replace(" ", "_")
        f["delay_minutes"] = delay_minutes
        f.update(_compute_durations(f))

    # Determine which flights are due
    due: list[dict[str, Any]] = []
    next_times: list[datetime] = []

    force_refresh = bool(hass.data.get(DOMAIN, {}).get("force_status_refresh"))
    if force_refresh:
        # Force refresh all flights once, then clear the flag
        hass.data.get(DOMAIN, {})["force_status_refresh"] = False

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

        if force_refresh or not next_check_dt or now >= dt_util.as_utc(next_check_dt):
            due.append(f)
        else:
            next_times.append(dt_util.as_utc(next_check_dt))

    # Refresh due flights (sequential to limit API calls)
    for f in due:
        state = (f.get("status_state") or "unknown").lower()
        if state in ("arrived", "cancelled", "landed"):
            key = f.get("flight_key")
            if key:
                cache.pop(key, None)
            continue
        # Ensure airport TZ is available before status lookup (helps date selection)
        dep_air = ((f.get("dep") or {}).get("airport") or {})
        arr_air = ((f.get("arr") or {}).get("airport") or {})
        if dep_air.get("iata") and not dep_air.get("tz"):
            try:
                dep_info = await get_airport(hass, options, dep_air.get("iata"))
                if dep_info and dep_info.get("tz"):
                    dep_air["tz"] = dep_info.get("tz")
            except Exception:
                pass
        if arr_air.get("iata") and not arr_air.get("tz"):
            try:
                arr_info = await get_airport(hass, options, arr_air.get("iata"))
                if arr_info and arr_info.get("tz"):
                    arr_air["tz"] = arr_info.get("tz")
            except Exception:
                pass
        if dep_air or arr_air:
            dep = (f.get("dep") or {})
            arr = (f.get("arr") or {})
            dep["airport"] = dep_air
            arr["airport"] = arr_air
            f["dep"] = dep
            f["arr"] = arr
        status_provider = (options.get("status_provider") or "flightradar24").lower()
        position_provider = (options.get(CONF_POSITION_PROVIDER) or "same_as_status").lower()
        if position_provider in ("same_as_status", "same", "status"):
            position_provider = status_provider

        status = await async_fetch_status(hass, options, f)
        position = None
        if position_provider and position_provider != status_provider:
            position = await async_fetch_position(hass, options, f, position_provider)
        key = f.get("flight_key")
        if not key:
            continue
        if isinstance(status, dict):
            # If provider returns an error without useful fields, keep last status but surface error
            if status.get("error"):
                prev_status = f.get("status") if isinstance(f.get("status"), dict) else {}
                has_signal = any(
                    status.get(k)
                    for k in (
                        "provider_state",
                        "dep_estimated",
                        "arr_estimated",
                        "dep_actual",
                        "arr_actual",
                        "position",
                        "arr_iata",
                        "dep_iata",
                    )
                )
                if isinstance(prev_status, dict) and prev_status and not has_signal:
                    prev_status.setdefault("provider", status_provider)
                    for k in ("error", "error_code", "error_message", "detail", "retry_after"):
                        if status.get(k) is not None:
                            prev_status[k] = status.get(k)
                    f["status"] = prev_status
                    f["status_updated_at"] = now.isoformat()
                else:
                    f["status"] = status
                    f["status_updated_at"] = now.isoformat()
                    if position:
                        status["position"] = position
                        status["position_provider"] = position_provider
                    apply_status(f, status)
                    _coerce_state_by_time(f, now)
                    _apply_assumed_arrival(f, now)
            else:
                f["status"] = status
                f["status_updated_at"] = now.isoformat()
                if position:
                    status["position"] = position
                    status["position_provider"] = position_provider
                apply_status(f, status)
                _coerce_state_by_time(f, now)
                _apply_assumed_arrival(f, now)
        elif position:
            f["position"] = position

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

        else:
            # No status from provider: keep last data but surface error
            prev_status = f.get("status") if isinstance(f.get("status"), dict) else {}
            if not isinstance(prev_status, dict):
                prev_status = {}
            prev_status.setdefault("provider", status_provider)
            prev_status["error"] = "no_status"
            prev_status["error_message"] = "Provider returned no status"
            f["status"] = prev_status
            f["status_updated_at"] = now.isoformat()

        _apply_assumed_arrival(f, now)

        delay_state, delay_minutes = _compute_delay_status(f, grace_minutes)
        f["delay_status"] = delay_state
        f["delay_status_key"] = (delay_state or "unknown").lower().replace(" ", "_")
        f["delay_minutes"] = delay_minutes
        f.update(_compute_durations(f))
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
