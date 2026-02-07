"""FlightAPI.io status/schedule provider.

Docs: https://docs.flightapi.io/
Endpoint used: GET /airline/{apiKey}?num=XX&date=YYYYMMDD&name=YY
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any
import asyncio
import logging

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .._shared.status_base import FlightStatus

_LOGGER = logging.getLogger(__name__)


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 6:
        return "*" * len(key)
    return f"{key[:4]}***{key[-2:]}"


def _error_type(status: int, payload: Any) -> str:
    msg = ""
    if isinstance(payload, dict):
        msg = str(payload.get("message") or payload.get("error") or "")
    msg_l = msg.lower()
    if status in (401, 403) or "api key" in msg_l or "unauthorized" in msg_l:
        return "auth_error"
    if status in (404, 410) or "no such flight" in msg_l or "no flight" in msg_l:
        return "no_match"
    if status == 429 or "rate" in msg_l or "limit" in msg_l:
        return "rate_limited"
    if status == 402 or "quota" in msg_l or "credits" in msg_l:
        return "quota_exceeded"
    if status == 400 or "invalid" in msg_l:
        return "bad_request"
    return "provider_error"


def _date_to_yyyymmdd(s: str | None) -> str | None:
    if not s or len(s) < 10:
        return None
    try:
        d = datetime.fromisoformat(s[:10]).date()
        return d.strftime("%Y%m%d")
    except Exception:
        return None


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_human_time(s: str | None, base_date: date | None, tz_hint) -> str | None:
    if not s or not base_date:
        return None
    raw = s.strip()
    if not raw:
        return None
    patterns = (
        "%I:%M %p, %b %d",  # 10:45 PM, Jan 25
        "%H:%M, %b %d",     # 14:55, Jul 16
    )
    for fmt in patterns:
        try:
            dt = datetime.strptime(raw, fmt)
            dt = dt.replace(year=base_date.year)
            if tz_hint:
                dt = dt.replace(tzinfo=tz_hint)
            return _iso(dt)
        except Exception:
            continue
    return None


def _local_date_from_sched(sched: str | None, tzname: str | None) -> date | None:
    if not sched:
        return None
    try:
        dt = datetime.fromisoformat(sched.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if tzname:
        try:
            dt = dt.astimezone(ZoneInfo(tzname))
        except Exception:
            pass
    return dt.date()


def _parse_human_time_naive(s: str | None, base_date: date | None) -> str | None:
    """Parse 'HH:MM, Mon DD' without timezone; return naive ISO string."""
    if not s or not base_date:
        return None
    raw = s.strip()
    if not raw:
        return None
    patterns = (
        "%I:%M %p, %b %d",
        "%H:%M, %b %d",
    )
    for fmt in patterns:
        try:
            dt = datetime.strptime(raw, fmt)
            dt = dt.replace(year=base_date.year)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            continue
    return None


def _pick_parts(payload: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Extract departure/arrival/aircraft/status dicts from FlightAPI list payload."""
    if not isinstance(payload, list) or len(payload) == 0:
        return None, None, None, None
    dep = None
    arr = None
    aircraft = None
    status = None
    for item in payload:
        if not isinstance(item, dict):
            continue
        if not dep and isinstance(item.get("departure"), dict):
            dep = item.get("departure")
        if not arr and isinstance(item.get("arrival"), dict):
            arr = item.get("arrival")
        if not aircraft and isinstance(item.get("aircraft"), dict):
            aircraft = item.get("aircraft")
        if not status and "status" in item:
            st = item.get("status")
            if isinstance(st, dict):
                status = st
            elif isinstance(st, str):
                status = {"status": st}
        if dep and arr and aircraft and status:
            break
    return dep, arr, aircraft, status


def _pick_segments_from_flights(payload: Any) -> list[dict[str, Any]]:
    """Extract segment list from payload with 'flights' array."""
    if not isinstance(payload, dict):
        return []
    flights = payload.get("flights")
    if not isinstance(flights, list):
        return []
    out: list[dict[str, Any]] = []
    for item in flights:
        if not isinstance(item, dict):
            continue
        dep_code = item.get("departureAirportCode")
        arr_code = item.get("arrivalAirportCode")
        dep_time = item.get("departureTime")
        arr_time = item.get("arrivalTime")
        status_text = (
            item.get("displayStatus")
            or item.get("display_status")
            or item.get("status")
        )
        out.append(
            {
                "dep_iata": dep_code,
                "arr_iata": arr_code,
                "dep_time": dep_time,
                "arr_time": arr_time,
                "airline_name": item.get("airline"),
                "airline_code": item.get("airlineCode"),
                "status": status_text,
            }
        )
    return out


def _pick_status_text(*objs: Any) -> str | None:
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        val = obj.get("displayStatus") or obj.get("display_status") or obj.get("status")
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, (int, float)):
            # Keep numeric as last resort; caller may still normalize to Unknown.
            return str(val)
    return None


class FlightAPIStatusProvider:
    def __init__(self, hass: HomeAssistant, api_key: str) -> None:
        self.hass = hass
        self.api_key = api_key.strip()

    async def async_get_status(self, flight: dict[str, Any]) -> FlightStatus | None:
        airline = (flight.get("airline_code") or "").strip().upper()
        number = str(flight.get("flight_number") or "").strip()
        if not airline or not number:
            return None

        # Prefer departure local date (airport tz) to avoid UTC-date mismatches
        dep = flight.get("dep") or {}
        dep_air = dep.get("airport") or {}
        dep_tz = dep_air.get("tz")
        sched_local = dep.get("scheduled_local")
        # Prefer scheduled_departure if provided, else dep.scheduled
        sched = flight.get("scheduled_departure") or dep.get("scheduled")
        yyyymmdd = _date_to_yyyymmdd(sched_local)
        if not yyyymmdd and sched:
            d_local = _local_date_from_sched(sched, dep_tz)
            if d_local:
                yyyymmdd = d_local.strftime("%Y%m%d")
        if not yyyymmdd:
            yyyymmdd = _date_to_yyyymmdd(sched)
        if not yyyymmdd:
            return None
        base_date_from_input = None
        try:
            base_date_from_input = datetime.strptime(yyyymmdd, "%Y%m%d").date()
        except Exception:
            try:
                base_date_from_input = datetime.fromisoformat(sched[:10]).date() if sched else None
            except Exception:
                base_date_from_input = None

        url = f"https://api.flightapi.io/airline/{self.api_key}"
        params = {"num": number, "date": yyyymmdd, "name": airline}

        session = async_get_clientsession(self.hass)
        _LOGGER.debug("FlightAPI request: %s params=%s key=%s", url, params, _mask_key(self.api_key))
        try:
            async with session.get(url, params=params, timeout=25) as resp:
                payload = await resp.json(content_type=None)
                retry_after = resp.headers.get("Retry-After")
            _LOGGER.debug("FlightAPI response: status=%s type=%s", resp.status, type(payload).__name__)
        except asyncio.TimeoutError:
            _LOGGER.warning("FlightAPI request timed out")
            details = {"provider": "flightapi", "state": "unknown", "error": "timeout"}
            return FlightStatus(provider="flightapi", state="unknown", details=details)
        except aiohttp.ClientError as err:
            _LOGGER.warning("FlightAPI request failed: %s", err)
            details = {"provider": "flightapi", "state": "unknown", "error": "network"}
            return FlightStatus(provider="flightapi", state="unknown", details=details)

        if resp.status >= 400:
            err_type = _error_type(resp.status, payload)
            details = {"provider": "flightapi", "state": "unknown", "error": err_type}
            if retry_after and retry_after.isdigit():
                details["retry_after"] = int(retry_after)
            return FlightStatus(provider="flightapi", state="unknown", details=details)

        # Some errors are returned in JSON even with 200
        if isinstance(payload, dict) and (payload.get("error") or payload.get("message")):
            err_type = _error_type(400, payload)
            details = {
                "provider": "flightapi",
                "state": "unknown",
                "error": err_type,
                "error_message": str(payload.get("error") or payload.get("message")),
            }
            return FlightStatus(provider="flightapi", state="unknown", details=details)

        if isinstance(payload, list):
            _LOGGER.debug("FlightAPI response list len=%s", len(payload))
            if payload:
                for i, item in enumerate(payload[:4]):
                    if not isinstance(item, dict):
                        continue
                    _LOGGER.debug(
                        "FlightAPI item[%s] keys=%s has_departure=%s has_arrival=%s",
                        i,
                        sorted(list(item.keys())),
                        isinstance(item.get("departure"), dict),
                        isinstance(item.get("arrival"), dict),
                    )
                    dep = item.get("departure")
                    arr = item.get("arrival")
                    if isinstance(dep, dict):
                        _LOGGER.debug("FlightAPI item[%s].departure keys=%s", i, sorted(list(dep.keys())))
                    if isinstance(arr, dict):
                        _LOGGER.debug("FlightAPI item[%s].arrival keys=%s", i, sorted(list(arr.keys())))
        dep_obj, arr_obj, aircraft_obj, status_obj = _pick_parts(payload)

        base_dt = _parse_iso((dep_obj or {}).get("departureDateTime"))
        # Use the requested flight date as the canonical base date to avoid
        # mismatches when API returns nearby operating days.
        base_date = base_date_from_input or (base_dt.date() if base_dt else None)
        dep_tz_hint = base_dt.tzinfo if base_dt else None
        arr_dt_hint = _parse_iso((arr_obj or {}).get("arrivalDateTime"))
        arr_tz_hint = arr_dt_hint.tzinfo if arr_dt_hint else None

        # Handle payload with "flights" list (schedule-only format)
        segments = _pick_segments_from_flights(payload)
        if segments:
            dep_filter = (flight.get("dep_airport") or flight.get("dep_iata") or "").strip().upper()
            arr_filter = (flight.get("arr_airport") or flight.get("arr_iata") or "").strip().upper()
            filtered = [
                s for s in segments
                if (not dep_filter or (s.get("dep_iata") or "").upper() == dep_filter)
                and (not arr_filter or (s.get("arr_iata") or "").upper() == arr_filter)
            ]
            candidates = filtered if filtered else segments
            # If still multiple and no disambiguation, pick the first segment (earliest)
            # Sort by departure time when available to keep deterministic choice
            def _sort_key(s):
                t = s.get("dep_time") or ""
                return t
            chosen = sorted(candidates, key=_sort_key)[0]
            # FlightAPI "departureTime"/"arrivalTime" are local times without a trusted tz.
            # Return naive ISO; later normalization uses airport tz.
            dep_sched = _parse_human_time_naive(chosen.get("dep_time"), base_date)
            arr_sched = _parse_human_time_naive(chosen.get("arr_time"), base_date)
            details = {
                "provider": "flightapi",
                "state": (chosen.get("status") or "unknown"),
                "dep_scheduled": dep_sched,
                "arr_scheduled": arr_sched,
                "dep_iata": chosen.get("dep_iata"),
                "arr_iata": chosen.get("arr_iata"),
                "airline_name": chosen.get("airline_name"),
            }
            return FlightStatus(provider="flightapi", state=details["state"], details=details)

        if not dep_obj and not arr_obj:
            _LOGGER.debug("FlightAPI no matching flight objects for %s%s on %s", airline, number, yyyymmdd)
            return None

        dep_iata = (dep_obj or {}).get("airportCode")
        arr_iata = (arr_obj or {}).get("airportCode")
        dep_tz_from_airport = None
        arr_tz_from_airport = None

        def _tz_for_times(tz_hint, tz_from_airport):
            if tz_hint is None:
                return tz_from_airport
            try:
                if tz_from_airport and tz_hint.utcoffset(datetime.utcnow()) == timezone.utc.utcoffset(datetime.utcnow()):
                    return tz_from_airport
            except Exception:
                pass
            return tz_hint

        # scheduled/estimated/actual are provided as airport-local times (no trusted tz).
        # Return naive ISO; later normalization uses airport tz.
        dep_sched = _parse_human_time_naive((dep_obj or {}).get("scheduledTime"), base_date) or _iso(base_dt)
        arr_sched = _parse_human_time_naive((arr_obj or {}).get("scheduledTime"), base_date) or _iso(arr_dt_hint)
        dep_est = _parse_human_time_naive((dep_obj or {}).get("estimatedTime"), base_date)
        arr_est = _parse_human_time_naive((arr_obj or {}).get("estimatedTime"), base_date)
        dep_act = _parse_human_time_naive((dep_obj or {}).get("offGroundTime") or (dep_obj or {}).get("outGateTime"), base_date)
        arr_act = _parse_human_time_naive((arr_obj or {}).get("onGroundTime") or (arr_obj or {}).get("inGateTime"), base_date)

        # Aircraft type (try a few common keys)
        aircraft_type = None
        if isinstance(aircraft_obj, dict):
            aircraft_type = (
                aircraft_obj.get("icao")
                or aircraft_obj.get("type")
                or aircraft_obj.get("model")
                or aircraft_obj.get("name")
            )

        details = {
            "provider": "flightapi",
            "state": _pick_status_text(status_obj, dep_obj, arr_obj) or "unknown",
            "dep_scheduled": dep_sched,
            "dep_estimated": dep_est,
            "dep_actual": dep_act,
            "arr_scheduled": arr_sched,
            "arr_estimated": arr_est,
            "arr_actual": arr_act,
            "dep_iata": dep_iata,
            "arr_iata": arr_iata,
            "airline_name": (dep_obj or {}).get("airlineName") or (arr_obj or {}).get("airlineName"),
            "aircraft_type": aircraft_type,
            "terminal_dep": (dep_obj or {}).get("terminal"),
            "gate_dep": (dep_obj or {}).get("gate"),
            "terminal_arr": (arr_obj or {}).get("terminal"),
            "gate_arr": (arr_obj or {}).get("gate"),
        }

        return FlightStatus(provider="flightapi", state=details["state"], details=details)
