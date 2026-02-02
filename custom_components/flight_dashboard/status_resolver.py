"""Status resolver -> normalizes provider data into canonical dep/arr timestamps.

This file ONLY maps provider fields into canonical keys on the flight dict.
It does not compute viewer-local UI strings.

Canonical additions:
- flight.aircraft_type (optional)
"""
from __future__ import annotations

from datetime import datetime
import re
from zoneinfo import ZoneInfo
from typing import Any

from homeassistant.util import dt as dt_util


def _pick_iso(*vals: Any) -> str | None:
    """Pick first non-empty datetime/iso and return ISO string (UTC if datetime)."""
    for v in vals:
        if not v:
            continue
        if isinstance(v, datetime):
            return dt_util.as_utc(v).isoformat() if v.tzinfo else dt_util.as_utc(dt_util.as_local(v)).isoformat()
        if isinstance(v, str):
            return v
    return None


def _pick_str(*vals: Any) -> str | None:
    for v in vals:
        if not v:
            continue
        if isinstance(v, str):
            s = v.strip()
            if s:
                return s
        else:
            # allow provider to pass non-string identifiers (e.g. dict), but ignore by default
            continue
    return None


_TZ_RE = re.compile(r"(Z|[+-]\d{2}:\d{2})$")


def _has_tz(s: str | None) -> bool:
    if not s or not isinstance(s, str):
        return False
    return _TZ_RE.search(s.strip()) is not None


def _normalize_iso_in_tz(val: str | None, tzname: str | None) -> str | None:
    """Normalize a naive ISO string using an airport timezone, return UTC ISO."""
    if not val:
        return None
    if isinstance(val, str) and "+00:00+00:00" in val:
        val = val.replace("+00:00+00:00", "+00:00")
    if _has_tz(val):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt_util.as_utc(dt).isoformat()
        except Exception:
            return val
    if not tzname:
        return val
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00").replace(" ", "T"))
    except Exception:
        return val
    try:
        dt = dt.replace(tzinfo=ZoneInfo(tzname))
    except Exception:
        return val
    return dt_util.as_utc(dt).isoformat()


def apply_status(flight: dict[str, Any], status: dict[str, Any] | None) -> dict[str, Any]:
    """Apply normalized status dict onto a canonical flight dict."""
    if not status:
        return flight

    flight["status_state"] = status.get("state") or flight.get("status_state") or "unknown"

    dep = flight.setdefault("dep", {})
    arr = flight.setdefault("arr", {})
    dep_air = dep.setdefault("airport", {})
    arr_air = arr.setdefault("airport", {})

    # Provider may provide tz names
    if status.get("dep_tz"):
        dep_air["tz"] = status.get("dep_tz")
    if status.get("arr_tz"):
        arr_air["tz"] = status.get("arr_tz")

    # timestamps
    dep["estimated"] = _pick_iso(status.get("dep_estimated"), dep.get("estimated"))
    dep["actual"] = _pick_iso(status.get("dep_actual"), dep.get("actual"))
    arr["estimated"] = _pick_iso(status.get("arr_estimated"), arr.get("estimated"))
    arr["actual"] = _pick_iso(status.get("arr_actual"), arr.get("actual"))

    # gates/terminals
    if status.get("terminal_dep"):
        dep["terminal"] = status.get("terminal_dep")
    if status.get("gate_dep"):
        dep["gate"] = status.get("gate_dep")
    if status.get("terminal_arr"):
        arr["terminal"] = status.get("terminal_arr")
    if status.get("gate_arr"):
        arr["gate"] = status.get("gate_arr")

    # optional airline/airport identity enrichment
    if status.get("airline_name") and not flight.get("airline_name"):
        flight["airline_name"] = status.get("airline_name")
    if status.get("airline_logo_url") and not flight.get("airline_logo_url"):
        flight["airline_logo_url"] = status.get("airline_logo_url")

    if status.get("dep_airport_name") and not dep_air.get("name"):
        dep_air["name"] = status.get("dep_airport_name")
    if status.get("dep_airport_city") and not dep_air.get("city"):
        dep_air["city"] = status.get("dep_airport_city")
    if status.get("arr_airport_name") and not arr_air.get("name"):
        arr_air["name"] = status.get("arr_airport_name")
    if status.get("arr_airport_city") and not arr_air.get("city"):
        arr_air["city"] = status.get("arr_airport_city")

    if status.get("position"):
        flight["position"] = status.get("position")

    # aircraft type (provider-agnostic mapping)
    # Accept a few common keys so different providers can normalize into this resolver:
    # - "aircraft_type" (preferred)
    # - "aircraft" (string)
    # - "aircraft_icao" / "aircraft_iata"
    # - "aircraft_model"
    if not flight.get("aircraft_type"):
        flight["aircraft_type"] = _pick_str(
            status.get("aircraft_type"),
            status.get("aircraft_model"),
            status.get("aircraft"),
            status.get("aircraft_icao"),
            status.get("aircraft_iata"),
        )

    dep["airport"] = dep_air
    arr["airport"] = arr_air
    flight["dep"] = dep
    flight["arr"] = arr

    # Normalize naive timestamps using airport timezone (if available)
    dep_tz = dep_air.get("tz")
    arr_tz = arr_air.get("tz")
    dep["scheduled"] = _normalize_iso_in_tz(dep.get("scheduled"), dep_tz)
    dep["estimated"] = _normalize_iso_in_tz(dep.get("estimated"), dep_tz)
    dep["actual"] = _normalize_iso_in_tz(dep.get("actual"), dep_tz)
    arr["scheduled"] = _normalize_iso_in_tz(arr.get("scheduled"), arr_tz)
    arr["estimated"] = _normalize_iso_in_tz(arr.get("estimated"), arr_tz)
    arr["actual"] = _normalize_iso_in_tz(arr.get("actual"), arr_tz)

    flight["dep"] = dep
    flight["arr"] = arr
    return flight
