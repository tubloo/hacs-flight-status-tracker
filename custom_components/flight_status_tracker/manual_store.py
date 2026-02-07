"""Persistent manual flights store (server-side).

Stores manual flights in HA .storage and notifies listeners on change.

This module is provider-agnostic: it stores whatever details are provided, but
at minimum needs airline_code, flight_number, dep_airport, arr_airport, and
scheduled times.

It supports BOTH legacy timestamp fields and canonical dep/arr forms:
- legacy: scheduled_departure / scheduled_arrival
- canonical: dep.scheduled / arr.scheduled
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_MANUAL_FLIGHTS_UPDATED
from .status_resolver import _normalize_status_state

_STORE_KEY = f"{DOMAIN}.manual_flights"
_STORE_VERSION = 1


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


def _as_iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo:
        return dt_util.as_utc(dt).isoformat()
    return dt_util.as_utc(dt_util.as_local(dt)).isoformat()


def _get_nested(raw: dict[str, Any], *path: str) -> Any:
    cur: Any = raw
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _mk_flight_key(airline_code: str, flight_number: str, dep_airport: str, dep_date: str) -> str:
    # dep_date is YYYY-MM-DD
    return f"{airline_code}-{flight_number}-{dep_airport}-{dep_date}"


def _normalize_travellers(travellers: Any) -> list[str]:
    if travellers is None:
        return []
    if isinstance(travellers, str):
        t = travellers.strip()
        return [t] if t else []
    if isinstance(travellers, list):
        out: list[str] = []
        for x in travellers:
            if x is None:
                continue
            s = str(x).strip()
            if s:
                out.append(s)
        return out
    return [str(travellers).strip()]


async def _store(hass: HomeAssistant) -> Store:
    return Store(hass, _STORE_VERSION, _STORE_KEY)


def _normalize_delay_status(val: Any) -> str | None:
    if not val:
        return None
    s = str(val).strip()
    if not s:
        return None
    raw = s.lower().replace(" ", "_")
    if raw in ("on_time", "ontime"):
        return "On Time"
    if raw in ("delayed", "delay"):
        return "Delayed"
    if raw in ("cancelled", "canceled"):
        return "Cancelled"
    if raw in ("arrived", "landed"):
        return "Arrived"
    if raw in ("unknown", "n/a", "na"):
        return "Unknown"
    return " ".join(w.capitalize() for w in raw.split("_"))


async def async_list_manual_flights(hass: HomeAssistant) -> list[dict[str, Any]]:
    st = await _store(hass)
    data = await st.async_load() or {}
    flights = data.get("flights") or []
    if not isinstance(flights, list):
        return []

    # One-time migration: normalize stored status strings to Title Case.
    changed = False
    for f in flights:
        if not isinstance(f, dict):
            continue
        status_state = f.get("status_state")
        norm_state = _normalize_status_state(status_state, None)
        if status_state != norm_state:
            f["status_state"] = norm_state
            changed = True

        delay_status = f.get("delay_status")
        norm_delay = _normalize_delay_status(delay_status)
        if norm_delay and delay_status != norm_delay:
            f["delay_status"] = norm_delay
            changed = True

    if changed:
        await st.async_save({"flights": flights})
        async_dispatcher_send(hass, SIGNAL_MANUAL_FLIGHTS_UPDATED)

    return flights


async def async_save_manual_flights(hass: HomeAssistant, flights: list[dict[str, Any]]) -> None:
    st = await _store(hass)
    await st.async_save({"flights": flights})
    async_dispatcher_send(hass, SIGNAL_MANUAL_FLIGHTS_UPDATED)


async def async_add_manual_flight(
    hass: HomeAssistant,
    *,
    airline_code: str,
    flight_number: str,
    dep_airport: str,
    arr_airport: str,
    scheduled_departure: str | None = None,
    scheduled_arrival: str | None = None,
    dep_scheduled: str | None = None,
    arr_scheduled: str | None = None,
    travellers: Any = None,
    notes: str | None = None,
    airline_name: str | None = None,
    airline_logo_url: str | None = None,
    aircraft_type: str | None = None,
    dep_airport_name: str | None = None,
    dep_airport_city: str | None = None,
    dep_airport_tz: str | None = None,
    arr_airport_name: str | None = None,
    arr_airport_city: str | None = None,
    arr_airport_tz: str | None = None,
) -> str:
    flights = await async_list_manual_flights(hass)

    airline_code = str(airline_code).strip().upper()
    flight_number = str(flight_number).strip()
    dep_airport = str(dep_airport).strip().upper()
    arr_airport = str(arr_airport).strip().upper()

    # Allow either legacy or canonical scheduled timestamps
    dep_sched_val = dep_scheduled or scheduled_departure
    arr_sched_val = arr_scheduled or scheduled_arrival

    dep_dt = _parse_dt(dep_sched_val)
    arr_dt = _parse_dt(arr_sched_val)

    dep_iso = _as_iso_utc(dep_dt)
    arr_iso = _as_iso_utc(arr_dt)

    if not dep_iso:
        raise ValueError("scheduled_departure/dep_scheduled is required and must be a valid datetime")

    dep_date = dep_iso[:10]  # YYYY-MM-DD
    flight_key = _mk_flight_key(airline_code, flight_number, dep_airport, dep_date)

    # Upsert behavior
    existing_idx = next((i for i, f in enumerate(flights) if f.get("flight_key") == flight_key), None)

    rec: dict[str, Any] = {
        "source": "manual",
        "flight_key": flight_key,
        "airline_code": airline_code,
        "flight_number": flight_number,
        "airline_name": airline_name,
        "airline_logo_url": airline_logo_url,
        "aircraft_type": aircraft_type,
        "dep_airport": dep_airport,
        "arr_airport": arr_airport,
        "dep_airport_name": dep_airport_name,
        "dep_airport_city": dep_airport_city,
        "dep_airport_tz": dep_airport_tz,
        "arr_airport_name": arr_airport_name,
        "arr_airport_city": arr_airport_city,
        "arr_airport_tz": arr_airport_tz,
        # keep legacy fields for compatibility
        "scheduled_departure": dep_iso,
        "scheduled_arrival": arr_iso,
        "travellers": _normalize_travellers(travellers),
        "notes": notes,
    }

    if existing_idx is None:
        flights.append(rec)
    else:
        flights[existing_idx] = {**flights[existing_idx], **rec}

    await async_save_manual_flights(hass, flights)
    return flight_key


async def async_add_manual_flight_record(hass: HomeAssistant, flight: dict[str, Any]) -> str:
    """Add a manual flight from a canonical flight dict.

    Expected input is the canonical schema v3 flight object.
    """
    airline_code = flight.get("airline_code") or ""
    flight_number = flight.get("flight_number") or ""
    dep_airport = (
        _get_nested(flight, "dep", "airport", "iata")
        or flight.get("dep_airport")
        or flight.get("dep_airport_iata")
        or flight.get("dep_iata")
        or flight.get("origin_iata")
        or flight.get("orig_iata")
        or ""
    )
    arr_airport = (
        _get_nested(flight, "arr", "airport", "iata")
        or flight.get("arr_airport")
        or flight.get("arr_airport_iata")
        or flight.get("arr_iata")
        or flight.get("destination_iata")
        or flight.get("dest_iata")
        or ""
    )
    dep_sched = (flight.get("dep") or {}).get("scheduled") or flight.get("dep_scheduled") or flight.get("scheduled_departure")
    arr_sched = (flight.get("arr") or {}).get("scheduled") or flight.get("arr_scheduled") or flight.get("scheduled_arrival")

    if not airline_code or not flight_number or not dep_airport or not arr_airport:
        raise ValueError("airline_code, flight_number, dep_airport, and arr_airport are required")

    dep_air = (flight.get("dep") or {}).get("airport") or {}
    arr_air = (flight.get("arr") or {}).get("airport") or {}

    return await async_add_manual_flight(
        hass,
        airline_code=airline_code,
        flight_number=flight_number,
        dep_airport=dep_airport,
        arr_airport=arr_airport,
        dep_scheduled=dep_sched,
        arr_scheduled=arr_sched,
        travellers=flight.get("travellers"),
        notes=flight.get("notes"),
        airline_name=flight.get("airline_name"),
        airline_logo_url=flight.get("airline_logo_url"),
        aircraft_type=flight.get("aircraft_type"),
        dep_airport_name=dep_air.get("name"),
        dep_airport_city=dep_air.get("city"),
        dep_airport_tz=dep_air.get("tz"),
        arr_airport_name=arr_air.get("name"),
        arr_airport_city=arr_air.get("city"),
        arr_airport_tz=arr_air.get("tz"),
    )


async def async_update_manual_flight(
    hass: HomeAssistant, flight_key: str, updates: dict[str, Any]
) -> bool:
    """Update an existing manual flight with new fields."""
    flights = await async_list_manual_flights(hass)
    updated = False
    for i, f in enumerate(flights):
        if f.get("flight_key") != flight_key:
            continue
        flights[i] = {**f, **updates}
        updated = True
        break
    if updated:
        await async_save_manual_flights(hass, flights)
    return updated


async def async_remove_manual_flight(hass: HomeAssistant, flight_key: str) -> bool:
    flights = await async_list_manual_flights(hass)
    before = len(flights)
    flights = [f for f in flights if f.get("flight_key") != flight_key]
    if len(flights) == before:
        return False
    await async_save_manual_flights(hass, flights)
    return True


async def async_clear_manual_flights(hass: HomeAssistant) -> int:
    flights = await async_list_manual_flights(hass)
    n = len(flights)
    await async_save_manual_flights(hass, [])
    return n
