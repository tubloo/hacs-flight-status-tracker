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


def _normalize_status_state(provider_state: str | None, provider: str | None) -> str:
    if not provider_state:
        return "Unknown"
    raw = str(provider_state).strip()
    if not raw:
        return "Unknown"
    s = raw.lower()

    # Generic mappings (canonical set for UI)
    if s in ("scheduled", "schedule", "plan", "planned"):
        return "Scheduled"
    if s in ("active", "enroute", "en route", "en-route", "in air", "in-air", "airborne", "departed", "cruising"):
        return "En Route"
    if s in ("landed", "arrived", "arrival", "arrived_gate"):
        return "Arrived"
    if s in ("cancelled", "canceled"):
        return "Cancelled"
    if s in ("diverted",):
        return "Diverted"
    if s in ("unknown", "n/a", "na"):
        return "Unknown"

    # Provider-specific nuances (if needed)
    if (provider or "").lower() == "opensky":
        return "En Route"

    return "Unknown"


def apply_status(flight: dict[str, Any], status: dict[str, Any] | None) -> dict[str, Any]:
    """Apply normalized status dict onto a canonical flight dict."""
    if not status:
        return flight

    provider_state = status.get("provider_state") or status.get("state")
    provider = status.get("provider")
    if provider_state:
        status["provider_state"] = provider_state
    # Avoid duplicate fields
    status.pop("state", None)
    flight.pop("status_provider_state", None)

    prev_state = flight.get("status_state") or "Unknown"
    normalized_state = _normalize_status_state(provider_state, provider) or "Unknown"
    # If provider yields unknown (or no state) but we already have a meaningful state,
    # keep the existing state to avoid flipping to Unknown on transient errors.
    if normalized_state == "Unknown" and prev_state != "Unknown":
        if not provider_state or str(provider_state).strip().lower() in ("unknown", "n/a", "na"):
            flight["status_state"] = prev_state
        else:
            flight["status_state"] = normalized_state
    else:
        flight["status_state"] = normalized_state

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
    # Keep airline name only at flight level to avoid duplication
    if status.get("airline_name") and not flight.get("airline_name"):
        flight["airline_name"] = status.get("airline_name")
    status.pop("airline_name", None)
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

    # Diverted destination (only when status is Diverted and provider arrival differs)
    try:
        orig_arr_iata = (arr_air.get("iata") or "").strip().upper()
        status_arr_iata = (status.get("arr_iata") or status.get("arr_airport_iata") or "").strip().upper()
        if flight.get("status_state") == "Diverted" and status_arr_iata and status_arr_iata != orig_arr_iata:
            flight["diverted_to_iata"] = status_arr_iata
            diverted_airport = {
                "iata": status_arr_iata,
                "name": status.get("arr_airport_name"),
                "city": status.get("arr_airport_city"),
                "tz": status.get("arr_tz"),
                "tz_short": status.get("arr_tz_short"),
            }
            # prune empty keys
            flight["diverted_to_airport"] = {k: v for k, v in diverted_airport.items() if v}
        else:
            flight.pop("diverted_to_iata", None)
            flight.pop("diverted_to_airport", None)
    except Exception:
        # Never fail status application due to diverted handling
        pass

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
