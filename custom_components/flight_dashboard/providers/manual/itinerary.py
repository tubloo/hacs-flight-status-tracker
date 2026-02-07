"""Manual itinerary provider -> canonical schema dicts.

Reads from manual_store but supports BOTH:
- legacy stored fields: scheduled_departure / scheduled_arrival (top-level)
- canonical stored fields: dep.scheduled / arr.scheduled

Also ensures airline_logo_url is populated when airline_code is present:
- If airline_logo_url missing, derive from a provider-neutral URL pattern:
  https://pics.avs.io/64/64/{IATA}.png
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ...manual_store import async_list_manual_flights


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


def _as_utc_iso(dt: datetime | None) -> str | None:
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
    return None if cur in ("", [], {}) else cur


def _default_logo_url(airline_code: str | None) -> str | None:
    if not airline_code:
        return None
    code = str(airline_code).strip().upper()
    if not code:
        return None
    # Common airline logo CDN used by many flight apps
    return f"https://pics.avs.io/64/64/{code}.png"


class ManualItineraryProvider:
    """Manual flights provider (canonical output)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_get_segments(self, start_utc: datetime, end_utc: datetime) -> list[dict[str, Any]]:
        flights = await async_list_manual_flights(self.hass)
        out: list[dict[str, Any]] = []

        for raw in flights:
            dep_sched_val = _get_nested(raw, "dep", "scheduled") or raw.get("scheduled_departure")
            arr_sched_val = _get_nested(raw, "arr", "scheduled") or raw.get("scheduled_arrival")

            dep_sched_dt = _parse_dt(dep_sched_val)
            arr_sched_dt = _parse_dt(arr_sched_val)

            dep_sched_iso = _as_utc_iso(dep_sched_dt)
            if not dep_sched_iso:
                continue

            dep_sched_utc = dt_util.parse_datetime(dep_sched_iso)
            if not dep_sched_utc:
                continue

            if dep_sched_utc < start_utc or dep_sched_utc > end_utc:
                continue

            dep_iata = _get_nested(raw, "dep", "airport", "iata") or raw.get("dep_airport")
            arr_iata = _get_nested(raw, "arr", "airport", "iata") or raw.get("arr_airport")

            dep_air_tz = _get_nested(raw, "dep", "airport", "tz") or raw.get("dep_airport_tz")
            arr_air_tz = _get_nested(raw, "arr", "airport", "tz") or raw.get("arr_airport_tz")

            dep_air_tz_short = _get_nested(raw, "dep", "airport", "tz_short") or raw.get("dep_tz_short")
            arr_air_tz_short = _get_nested(raw, "arr", "airport", "tz_short") or raw.get("arr_tz_short")

            dep_air_name = _get_nested(raw, "dep", "airport", "name") or raw.get("dep_airport_name")
            arr_air_name = _get_nested(raw, "arr", "airport", "name") or raw.get("arr_airport_name")

            dep_air_city = _get_nested(raw, "dep", "airport", "city") or raw.get("dep_airport_city")
            arr_air_city = _get_nested(raw, "arr", "airport", "city") or raw.get("arr_airport_city")

            dep_terminal = _get_nested(raw, "dep", "terminal") or raw.get("terminal_dep")
            dep_gate = _get_nested(raw, "dep", "gate") or raw.get("gate_dep")
            arr_terminal = _get_nested(raw, "arr", "terminal") or raw.get("terminal_arr")
            arr_gate = _get_nested(raw, "arr", "gate") or raw.get("gate_arr")

            dep_est = _get_nested(raw, "dep", "estimated") or raw.get("dep_estimated")
            dep_act = _get_nested(raw, "dep", "actual") or raw.get("dep_actual")
            arr_est = _get_nested(raw, "arr", "estimated") or raw.get("arr_estimated")
            arr_act = _get_nested(raw, "arr", "actual") or raw.get("arr_actual")

            status = raw.get("status") if isinstance(raw.get("status"), dict) else None

            airline_code = raw.get("airline_code")
            airline_logo_url = raw.get("airline_logo_url") or _default_logo_url(airline_code)

            out.append(
                {
                    "source": raw.get("source") or "manual",
                    "flight_key": raw.get("flight_key"),
                    "airline_code": airline_code,
                    "flight_number": raw.get("flight_number"),
                    "airline_name": raw.get("airline_name"),
                    "airline_logo_url": airline_logo_url,
                    "aircraft_type": raw.get("aircraft_type"),
                    "travellers": raw.get("travellers") or [],
                    "status_state": raw.get("status_state") or (status or {}).get("state") or "Unknown",
                    "notes": raw.get("notes"),
                    "status": status,
                    # expose stored schedule fields so backfill logic doesn't overwrite them
                    "scheduled_departure": dep_sched_iso,
                    "scheduled_arrival": _as_utc_iso(arr_sched_dt),
                    "dep": {
                        "airport": {
                            "iata": dep_iata,
                            "name": dep_air_name,
                            "city": dep_air_city,
                            "tz": dep_air_tz,
                            "tz_short": dep_air_tz_short,
                        },
                        "scheduled": dep_sched_iso,
                        "estimated": dep_est,
                        "actual": dep_act,
                        "terminal": dep_terminal,
                        "gate": dep_gate,
                    },
                    "arr": {
                        "airport": {
                            "iata": arr_iata,
                            "name": arr_air_name,
                            "city": arr_air_city,
                            "tz": arr_air_tz,
                            "tz_short": arr_air_tz_short,
                        },
                        "scheduled": _as_utc_iso(arr_sched_dt),
                        "estimated": arr_est,
                        "actual": arr_act,
                        "terminal": arr_terminal,
                        "gate": arr_gate,
                    },
                }
            )

        return out
