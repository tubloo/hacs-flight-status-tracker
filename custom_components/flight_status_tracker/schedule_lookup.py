"""Provider-agnostic schedule lookup.

Input: query like "AI 157" or "AI157" and a date string "YYYY-MM-DD"
Output: canonical Flight v3 dict (minimal schedule) OR error.

This module is designed so we can swap providers later.
Provider selection behavior:
1) Use only the explicitly selected schedule provider
2) Provider selection is explicit (single-provider mode)

If none configured -> error no_provider
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any

from homeassistant.core import HomeAssistant
from .rate_limit import is_blocked, set_block
from .status_resolver import _normalize_status_state
from .api_metrics import record_api_call

# We read options from the integration options dict that you already store
DOMAIN = "flight_status_tracker"

# config_flow keys (string literals to avoid circular imports)
CONF_STATUS_PROVIDER = "status_provider"
CONF_SCHEDULE_PROVIDER = "schedule_provider"
CONF_FLIGHTAPI_KEY = "flightapi_api_key"
CONF_AERODATABOX_GATEWAY = "aerodatabox_gateway"
CONF_AERODATABOX_RAPIDAPI_KEY = "aerodatabox_rapidapi_key"
CONF_AERODATABOX_APIMARKET_KEY = "aerodatabox_apimarket_key"

_LOGGER = logging.getLogger(__name__)


def _outcome_from_error(err: Any) -> str:
    e = str(err or "").strip().lower()
    if not e:
        return "success"
    if e in {"rate_limited", "quota_exceeded", "timeout", "network", "auth_error"}:
        return e
    return "error"

def _parse_query(query: str) -> tuple[str | None, str | None]:
    """Return (airline_code, flight_number). Accept 'AI 157', 'AI157', 'AF-2'."""
    q = (query or "").strip().upper()
    if not q:
        return None, None
    q = q.replace("-", " ").replace("/", " ")
    # Prefer explicit space-separated input to avoid 2/3-char ambiguity
    m = re.match(r"^([A-Z0-9]{2,3})\s+([0-9]{1,4}[A-Z]?)$", q)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^([A-Z0-9]{2,3})\s*([0-9]{1,4}[A-Z]?)$", q.replace(" ", ""))
    if m:
        return m.group(1), m.group(2)
    return None, None


def _iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


_TZ_RE = re.compile(r"(Z|[+-]\d{2}:\d{2})$")


def _has_tz(s: str | None) -> bool:
    if not s or not isinstance(s, str):
        return False
    return _TZ_RE.search(s.strip()) is not None


def _normalize_iso_in_tz(val: str | None, tzname: str | None) -> str | None:
    if not val:
        return None
    if isinstance(val, str) and "+00:00+00:00" in val:
        val = val.replace("+00:00+00:00", "+00:00")
    if _has_tz(val):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).isoformat()
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
    return dt.astimezone(timezone.utc).isoformat()


def _normalize_flight_times(flight: dict[str, Any]) -> dict[str, Any]:
    dep = flight.get("dep") or {}
    arr = flight.get("arr") or {}
    dep_air = dep.get("airport") or {}
    arr_air = arr.get("airport") or {}
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


def _departure_local_date(flight: dict[str, Any]) -> str | None:
    """Return departure local date (YYYY-MM-DD) if derivable."""
    dep = flight.get("dep") or {}
    dep_air = dep.get("airport") or {}
    dep_sched = dep.get("scheduled")
    if not isinstance(dep_sched, str) or not dep_sched:
        return None
    try:
        dt = datetime.fromisoformat(dep_sched.replace("Z", "+00:00").replace(" ", "T"))
    except Exception:
        return None
    tzname = dep_air.get("tz")
    if dt.tzinfo and tzname:
        try:
            dt = dt.astimezone(ZoneInfo(str(tzname)))
        except Exception:
            pass
    return dt.date().isoformat()


def _matches_requested_date(flight: dict[str, Any], requested_date: str) -> bool:
    """Validate schedule candidate against requested departure date."""
    req = (requested_date or "").strip()[:10]
    if not req:
        return True
    dep_date = _departure_local_date(flight)
    # If we cannot derive date, keep candidate (avoid false negatives).
    if not dep_date:
        return True
    return dep_date == req


async def lookup_schedule(
    hass: HomeAssistant,
    options: dict[str, Any],
    query: str,
    date_str: str,
    dep_airport: str | None = None,
    arr_airport: str | None = None,
    log_errors: bool = True,
) -> dict[str, Any]:
    """Lookup a flight schedule and return a canonical flight dict.

    Uses configured providers in preferred order, returning a minimal
    schema v3 flight object suitable for preview/add flows.
    """
    airline_code, flight_number = _parse_query(query)
    if not airline_code or not flight_number:
        return {"error": "bad_query", "hint": "Use e.g. 'AI 157' or 'AI157'."}

    # date_str comes from input_datetime (YYYY-MM-DD)
    date_str = (date_str or "").strip()
    if not date_str or len(date_str) < 10:
        return {"error": "bad_date", "hint": "Pick a date."}

    # Normalize optional disambiguation
    dep_airport = dep_airport.strip().upper() if isinstance(dep_airport, str) and dep_airport.strip() else None
    arr_airport = arr_airport.strip().upper() if isinstance(arr_airport, str) and arr_airport.strip() else None

    # Decide provider order.
    adb_gateway = (options.get(CONF_AERODATABOX_GATEWAY) or "rapidapi").strip().lower()
    adb_rapid_key = (options.get(CONF_AERODATABOX_RAPIDAPI_KEY) or "").strip()
    adb_market_key = (options.get(CONF_AERODATABOX_APIMARKET_KEY) or "").strip()
    adb_key = adb_market_key if adb_gateway == "apimarket" else adb_rapid_key
    fa_key = (options.get(CONF_FLIGHTAPI_KEY) or "").strip()

    # Match Options Flow defaults when options are missing.
    schedule_pref = (options.get(CONF_SCHEDULE_PROVIDER) or "aerodatabox").lower()
    if schedule_pref == "aerodatabox" and not adb_key:
        return {"error": "no_provider", "hint": "AeroDataBox API key is required for schedule lookup."}
    if schedule_pref == "flightapi" and not fa_key:
        return {"error": "no_provider", "hint": "FlightAPI.io API key is required for schedule lookup."}

    if schedule_pref not in ("aerodatabox", "flightapi"):
        schedule_pref = "aerodatabox"
    order = [schedule_pref]
    _LOGGER.debug("Schedule lookup providers order=%s pref=%s", order, schedule_pref)

    if "aerodatabox" in order and adb_key and not is_blocked(hass, "aerodatabox"):
        try:
            from .providers.aerodatabox.status import AeroDataBoxStatusProvider
        except Exception:
            AeroDataBoxStatusProvider = None  # type: ignore

        if AeroDataBoxStatusProvider:
            try:
                st = await AeroDataBoxStatusProvider(
                    hass,
                    gateway=adb_gateway,
                    rapidapi_key=adb_rapid_key,
                    apimarket_key=adb_market_key,
                ).async_get_status(
                    {
                        "airline_code": airline_code,
                        "flight_number": flight_number,
                        "dep": {"scheduled_local": f"{date_str}T00:00:00"},
                        "dep_airport": dep_airport,
                        "arr_airport": arr_airport,
                    }
                )
                details = st.details if st else None
            except Exception as e:
                details = {"error": "network", "error_message": str(e)}
            record_api_call(
                hass,
                "aerodatabox",
                flow="schedule",
                outcome=_outcome_from_error(details.get("error") if isinstance(details, dict) else None),
            )
            if isinstance(details, dict) and not details.get("error"):
                flight = {
                    "schema_version": 3,
                    "source": "manual",
                    "flight_key": None,
                    "airline_code": details.get("airline_code") or airline_code,
                    "flight_number": flight_number,
                    "airline_name": details.get("airline_name"),
                    "airline_logo_url": details.get("airline_logo_url"),
                    "aircraft_type": details.get("aircraft_type"),
                    "travellers": [],
                    "status_state": _normalize_status_state(details.get("state"), "aerodatabox"),
                    "notes": None,
                    "dep": {
                        "airport": {
                            "iata": details.get("dep_iata"),
                            "name": details.get("dep_airport_name"),
                            "city": details.get("dep_airport_city"),
                            "tz": details.get("dep_tz"),
                            "tz_short": None,
                        },
                        "scheduled": details.get("dep_scheduled"),
                        "estimated": details.get("dep_estimated"),
                        "actual": details.get("dep_actual"),
                        "terminal": details.get("terminal_dep"),
                        "gate": details.get("gate_dep"),
                    },
                    "arr": {
                        "airport": {
                            "iata": details.get("arr_iata"),
                            "name": details.get("arr_airport_name"),
                            "city": details.get("arr_airport_city"),
                            "tz": details.get("arr_tz"),
                            "tz_short": None,
                        },
                        "scheduled": details.get("arr_scheduled"),
                        "estimated": details.get("arr_estimated"),
                        "actual": details.get("arr_actual"),
                        "terminal": details.get("terminal_arr"),
                        "gate": details.get("gate_arr"),
                    },
                }
                norm = _normalize_flight_times(flight)
                if _matches_requested_date(norm, date_str):
                    return {"flight": norm, "provider": "aerodatabox"}
            if isinstance(details, dict) and details.get("error") in ("rate_limited", "quota_exceeded"):
                reason = details.get("error")
                block_for = details.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
                set_block(hass, "aerodatabox", block_for, reason)
            if isinstance(details, dict) and details.get("error"):
                err = details.get("error")
                if err == "quota_exceeded":
                    return {"error": "provider_error", "hint": "AeroDataBox quota exceeded. Try again later.", "provider": "aerodatabox"}
                if err == "rate_limited":
                    return {"error": "provider_error", "hint": "AeroDataBox rate limit reached. Try again later.", "provider": "aerodatabox"}
                if err == "auth_error":
                    return {"error": "provider_error", "hint": "AeroDataBox key invalid or unauthorized.", "provider": "aerodatabox"}
                return {"error": "provider_error", "hint": details.get("error_message") or "AeroDataBox error.", "provider": "aerodatabox"}

    if "flightapi" in order:
        _LOGGER.debug(
            "FlightAPI key present=%s blocked=%s",
            bool(fa_key),
            is_blocked(hass, "flightapi"),
        )
    if "flightapi" in order and fa_key and not is_blocked(hass, "flightapi"):
        try:
            from .providers.flightapi.status import FlightAPIStatusProvider
        except Exception as e:
            _LOGGER.warning("FlightAPI provider import failed: %s", e)
            FlightAPIStatusProvider = None  # type: ignore

        if FlightAPIStatusProvider:
            _LOGGER.debug("FlightAPI schedule lookup for %s %s on %s", airline_code, flight_number, date_str)
            try:
                st = await FlightAPIStatusProvider(hass, fa_key).async_get_status(
                    {
                        "airline_code": airline_code,
                        "flight_number": flight_number,
                        "dep": {
                            "scheduled": f"{date_str}T00:00:00+00:00",
                            "airport": {"iata": dep_airport},
                        },
                        "arr": {"airport": {"iata": arr_airport}},
                    }
                )
                details = st.details if st else None
            except Exception as e:
                details = {"error": "network", "error_message": str(e)}
            record_api_call(
                hass,
                "flightapi",
                flow="schedule",
                outcome=_outcome_from_error(details.get("error") if isinstance(details, dict) else None),
            )
            if isinstance(details, dict) and not details.get("error"):
                dep_sched = details.get("dep_scheduled")
                arr_sched = details.get("arr_scheduled")
                dep_iata = details.get("dep_iata") or None
                arr_iata = details.get("arr_iata") or None
                flight = {
                    "schema_version": 3,
                    "source": "manual",
                    "flight_key": None,
                    "airline_code": airline_code,
                    "flight_number": flight_number,
                    "airline_name": details.get("airline_name"),
                    "airline_logo_url": details.get("airline_logo_url"),
                    "aircraft_type": details.get("aircraft_type"),
                    "travellers": [],
                    "status_state": _normalize_status_state(details.get("state"), "flightapi"),
                    "notes": None,
                    "dep": {
                        "airport": {"iata": dep_iata, "name": None, "city": None, "tz": details.get("dep_tz"), "tz_short": None},
                        "scheduled": dep_sched,
                        "estimated": details.get("dep_estimated"),
                        "actual": details.get("dep_actual"),
                        "terminal": details.get("terminal_dep"),
                        "gate": details.get("gate_dep"),
                    },
                    "arr": {
                        "airport": {"iata": arr_iata, "name": None, "city": None, "tz": details.get("arr_tz"), "tz_short": None},
                        "scheduled": arr_sched,
                        "estimated": details.get("arr_estimated"),
                        "actual": details.get("arr_actual"),
                        "terminal": details.get("terminal_arr"),
                        "gate": details.get("gate_arr"),
                    },
                }
                norm = _normalize_flight_times(flight)
                if not _matches_requested_date(norm, date_str):
                    _LOGGER.debug(
                        "FlightAPI candidate date mismatch requested=%s dep_local=%s; trying next provider",
                        date_str,
                        _departure_local_date(norm),
                    )
                else:
                    return {"flight": norm, "provider": "flightapi"}
            if isinstance(details, dict) and details.get("error") in ("rate_limited", "quota_exceeded"):
                reason = details.get("error")
                block_for = details.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
                set_block(hass, "flightapi", block_for, reason)
                if reason == "quota_exceeded":
                    return {
                        "error": "provider_error",
                        "hint": "FlightAPI.io quota exceeded. Try again later.",
                        "provider": "flightapi",
                    }
                if reason == "rate_limited":
                    return {
                        "error": "provider_error",
                        "hint": "FlightAPI.io rate limit reached. Try again later.",
                        "provider": "flightapi",
                    }
            if isinstance(details, dict) and details.get("error"):
                err = details.get("error")
                if log_errors:
                    _LOGGER.warning("FlightAPI schedule lookup error: %s", details.get("error_message") or err)
                else:
                    _LOGGER.debug("FlightAPI schedule lookup error: %s", details.get("error_message") or err)
                if err == "quota_exceeded":
                    payload = {
                        "error": "provider_error",
                        "hint": "FlightAPI.io quota exceeded. Try again later.",
                        "provider": "flightapi",
                    }
                    return payload
                elif err == "rate_limited":
                    return {"error": "provider_error", "hint": "FlightAPI.io rate limit reached. Try again later.", "provider": "flightapi"}
                elif err == "auth_error":
                    return {"error": "provider_error", "hint": "FlightAPI.io key invalid or unauthorized.", "provider": "flightapi"}
                elif err and err != "no_match":
                    return {"error": "provider_error", "hint": "FlightAPI.io error. Try another provider or verify the date.", "provider": "flightapi"}

    if not (adb_key or fa_key):
        return {
            "error": "no_provider",
            "hint": "No schedule provider configured. Add an API key in Flight Status Tracker options.",
        }

    # If we reach here, we couldn't match (or providers not implemented for schedule lookups)
    return {"error": "no_match", "hint": "No match found for that date (or provider limits). Try a different date."}
