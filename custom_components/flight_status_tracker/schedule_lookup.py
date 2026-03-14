"""Provider-agnostic schedule lookup.

Input: query like "AI 157" or "AI157" and a date string "YYYY-MM-DD"
Output: canonical Flight v3 dict (minimal schedule) OR error.

This module is designed so we can swap providers later.
Provider selection behavior:
1) Use only the explicitly selected schedule provider
2) Legacy "auto" values are coerced to default provider for compatibility

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
CONF_AVIATIONSTACK_KEY = "aviationstack_access_key"
CONF_AIRLABS_KEY = "airlabs_api_key"
CONF_FLIGHTAPI_KEY = "flightapi_api_key"
CONF_FR24_API_KEY = "fr24_api_key"
CONF_FR24_SANDBOX_KEY = "fr24_sandbox_key"
CONF_FR24_USE_SANDBOX = "fr24_use_sandbox"

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


async def _lookup_mock_fixture(
    hass: HomeAssistant, airline_code: str, flight_number: str, date_str: str
) -> dict[str, Any] | None:
    try:
        from importlib import resources
        import json
    except Exception:
        return None

    def _load() -> dict[str, Any]:
        data = resources.files("custom_components.flight_status_tracker.fixtures").joinpath("mock_flights.json").read_text()
        return json.loads(data)

    fixtures = await hass.async_add_executor_job(_load)
    rec = fixtures.get(f"{airline_code}{flight_number}|{date_str}")
    return rec if isinstance(rec, dict) else None


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
    use_sandbox = bool(options.get(CONF_FR24_USE_SANDBOX, False))
    fr24_key = (options.get(CONF_FR24_API_KEY) or "").strip()
    fr24_sandbox_key = (options.get(CONF_FR24_SANDBOX_KEY) or "").strip()
    fr24_active_key = fr24_sandbox_key if use_sandbox and fr24_sandbox_key else fr24_key
    av_key = (options.get(CONF_AVIATIONSTACK_KEY) or "").strip()
    al_key = (options.get(CONF_AIRLABS_KEY) or "").strip()
    fa_key = (options.get(CONF_FLIGHTAPI_KEY) or "").strip()

    # Match Options Flow defaults when options are missing.
    schedule_pref = (options.get(CONF_SCHEDULE_PROVIDER) or "flightapi").lower()
    if schedule_pref == "aviationstack" and not av_key:
        return {"error": "no_provider", "hint": "Aviationstack API key is required for schedule lookup."}
    if schedule_pref == "airlabs" and not al_key:
        return {"error": "no_provider", "hint": "AirLabs API key is required for schedule lookup."}
    if schedule_pref == "flightapi" and not fa_key:
        return {"error": "no_provider", "hint": "FlightAPI.io API key is required for schedule lookup."}
    if schedule_pref == "flightradar24" and not fr24_active_key:
        return {"error": "no_provider", "hint": "FR24 API key is required for schedule lookup."}

    # Legacy compatibility: "auto" is removed; coerce to default provider.
    if schedule_pref == "auto":
        schedule_pref = "flightapi"
    strict_provider = True
    order = [schedule_pref]
    _LOGGER.debug("Schedule lookup providers order=%s pref=%s", order, schedule_pref)

    last_provider_error: dict[str, Any] | None = None

    # Only short-circuit on mock when explicitly selected as primary provider.
    if order and order[0] == "mock":
        rec = await _lookup_mock_fixture(hass, airline_code, flight_number, date_str)
        if rec:
            return {"flight": rec, "provider": "mock"}

    # Import lazily so missing deps won't crash HA if provider not used
    if "flightradar24" in order and fr24_active_key and not is_blocked(hass, "fr24"):
        try:
            from .providers.flightradar24.status import Flightradar24StatusProvider
        except Exception as e:
            _LOGGER.warning("FR24 status provider import failed: %s", e)
            Flightradar24StatusProvider = None  # type: ignore

        if Flightradar24StatusProvider:
            # For FR24 schedule we still need dep/arr airports + sched times.
            # Your manual schedule entry already exists; but for “just flight+date”
            # we rely on flight-summary/full returning origin/dest + schedule-ish datetimes.
            # If FR24 plan doesn’t include sched, you still get origin/dest + type.
            fr24_version = (options.get("fr24_api_version") or "v1").strip()
            statusp = Flightradar24StatusProvider(
                hass, api_key=fr24_active_key, use_sandbox=use_sandbox, api_version=fr24_version
            )

            # Build a minimal “flight” shell so provider can match
            flight_shell = {
                "airline_code": airline_code,
                "flight_number": flight_number,
                "dep": {"airport": {"iata": dep_airport}, "scheduled": f"{date_str}T00:00:00+00:00"},
                "arr": {"airport": {"iata": arr_airport}, "scheduled": f"{date_str}T00:00:00+00:00"},
            }
            _LOGGER.debug("FR24 schedule lookup for %s %s on %s", airline_code, flight_number, date_str)
            try:
                st = await statusp.async_get_status(flight_shell)
            except Exception as e:
                st = {"error": "network", "detail": str(e)}
            if isinstance(st, dict):
                record_api_call(hass, "flightradar24", flow="schedule", outcome=_outcome_from_error(st.get("error")))
            else:
                record_api_call(hass, "flightradar24", flow="schedule", outcome="success")
            if isinstance(st, dict) and st.get("error") in ("rate_limited", "quota_exceeded"):
                reason = st.get("error")
                block_for = st.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
                set_block(hass, "fr24", block_for, reason)
                st = None
            if st and st.get("error"):
                if log_errors:
                    _LOGGER.warning("FR24 schedule lookup error: %s", st.get("detail") or st.get("error"))
                else:
                    _LOGGER.debug("FR24 schedule lookup error: %s", st.get("detail") or st.get("error"))
                err = st.get("error")
                if err == "quota_exceeded":
                    return {"error": "provider_error", "hint": "FR24 quota exceeded. Try again later."}
                if err == "rate_limited":
                    return {"error": "provider_error", "hint": "FR24 rate limit reached. Try again later."}
                return {"error": "provider_error", "hint": st.get("detail") or "FR24 error"}
            if st and not st.get("error"):
                # Prefer true scheduled timestamps. Do not substitute estimated/actual.
                # If FR24 doesn't provide scheduled, keep as None so preview can show incomplete.
                dep_sched = st.get("dep_scheduled")
                arr_sched = st.get("arr_scheduled")

                # Try to pull route if present in provider extras (future-proof)
                dep_iata = st.get("dep_iata") or st.get("orig_iata") or st.get("origin_iata") or None
                arr_iata = st.get("arr_iata") or st.get("dest_iata") or st.get("destination_iata") or None

                flight = {
                    "schema_version": 3,
                    "source": "manual",
                    "flight_key": None,
                    "airline_code": airline_code,
                    "flight_number": flight_number,
                    "airline_name": st.get("airline_name"),
                    "airline_logo_url": st.get("airline_logo_url"),
                    "aircraft_type": st.get("aircraft_type"),
                    "travellers": [],
                    "status_state": _normalize_status_state(st.get("state"), "flightradar24"),
                    "notes": None,
                    "dep": {
                        "airport": {"iata": dep_iata, "name": None, "city": None, "tz": st.get("dep_tz"), "tz_short": None},
                        "scheduled": dep_sched,
                        "estimated": st.get("dep_estimated"),
                        "actual": st.get("dep_actual"),
                        "terminal": st.get("terminal_dep"),
                        "gate": st.get("gate_dep"),
                    },
                    "arr": {
                        "airport": {"iata": arr_iata, "name": None, "city": None, "tz": st.get("arr_tz"), "tz_short": None},
                        "scheduled": arr_sched,
                        "estimated": st.get("arr_estimated"),
                        "actual": st.get("arr_actual"),
                        "terminal": st.get("terminal_arr"),
                        "gate": st.get("gate_arr"),
                    },
                }
                norm = _normalize_flight_times(flight)
                if not _matches_requested_date(norm, date_str):
                    _LOGGER.debug(
                        "FR24 candidate date mismatch requested=%s dep_local=%s; trying next provider",
                        date_str,
                        _departure_local_date(norm),
                    )
                else:
                    return {"flight": norm, "provider": "flightradar24"}
        else:
            _LOGGER.warning("FR24 status provider could not be loaded.")

    if "aviationstack" in order and av_key and not is_blocked(hass, "aviationstack"):
        try:
            from .providers.aviationstack.status import AviationstackStatusProvider
        except Exception:
            AviationstackStatusProvider = None  # type: ignore

        if AviationstackStatusProvider:
            try:
                st = await AviationstackStatusProvider(hass, av_key).async_get_status(
                    {
                        "airline_code": airline_code,
                        "flight_number": flight_number,
                        "scheduled_departure": f"{date_str}T00:00:00+00:00",
                        "dep_airport": dep_airport,
                        "arr_airport": arr_airport,
                    }
                )
                details = st.details if st else None
            except Exception as e:
                details = {"error": "network", "error_message": str(e)}
            record_api_call(
                hass,
                "aviationstack",
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
                    "status_state": _normalize_status_state(details.get("state"), "aviationstack"),
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
                        "Aviationstack candidate date mismatch requested=%s dep_local=%s; trying next provider",
                        date_str,
                        _departure_local_date(norm),
                    )
                else:
                    return {"flight": norm, "provider": "aviationstack"}
            if isinstance(details, dict) and details.get("error") in ("rate_limited", "quota_exceeded"):
                reason = details.get("error")
                block_for = details.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
                set_block(hass, "aviationstack", block_for, reason)
                details = None
            if isinstance(details, dict) and details.get("error"):
                if log_errors:
                    _LOGGER.warning("Aviationstack schedule lookup error: %s", details.get("error"))
                else:
                    _LOGGER.debug("Aviationstack schedule lookup error: %s", details.get("error"))
                err = details.get("error")
                if err == "quota_exceeded":
                    payload = {
                        "error": "provider_error",
                        "hint": "Aviationstack quota exceeded. Try again later.",
                        "provider": "aviationstack",
                    }
                    if strict_provider:
                        return payload
                    last_provider_error = payload
                elif err == "rate_limited":
                    payload = {
                        "error": "provider_error",
                        "hint": "Aviationstack rate limit reached. Try again later.",
                        "provider": "aviationstack",
                    }
                    if strict_provider:
                        return payload
                    last_provider_error = payload
                elif err == "plan_restricted":
                    payload = {
                        "error": "provider_error",
                        "hint": "Aviationstack plan does not include flights endpoint. Upgrade plan or use another schedule provider.",
                        "provider": "aviationstack",
                    }
                    if strict_provider:
                        return payload
                    last_provider_error = payload
                elif err == "auth_error":
                    payload = {
                        "error": "provider_error",
                        "hint": "Aviationstack key invalid or unauthorized.",
                        "provider": "aviationstack",
                    }
                    if strict_provider:
                        return payload
                    last_provider_error = payload
                else:
                    payload = {
                        "error": "provider_error",
                        "hint": details.get("error_message") or "Aviationstack error. Try another provider or verify the date.",
                        "provider": "aviationstack",
                    }
                    if strict_provider:
                        return payload
                    last_provider_error = payload

    if "airlabs" in order and al_key and not is_blocked(hass, "airlabs"):
        try:
            from .providers.airlabs.status import AirLabsStatusProvider
        except Exception:
            AirLabsStatusProvider = None  # type: ignore

        if AirLabsStatusProvider:
            try:
                st = await AirLabsStatusProvider(hass, al_key).async_get_status(
                    {
                        "airline_code": airline_code,
                        "flight_number": flight_number,
                        "scheduled_departure": f"{date_str}T00:00:00+00:00",
                        "dep_airport": dep_airport,
                        "arr_airport": arr_airport,
                    }
                )
                details = st.details if st else None
            except Exception as e:
                details = {"error": "network", "error_message": str(e)}
            record_api_call(
                hass,
                "airlabs",
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
                    "status_state": _normalize_status_state(details.get("state"), "airlabs"),
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
                        "AirLabs candidate date mismatch requested=%s dep_local=%s; trying next provider",
                        date_str,
                        _departure_local_date(norm),
                    )
                else:
                    return {"flight": norm, "provider": "airlabs"}
            if isinstance(details, dict) and details.get("error") in ("rate_limited", "quota_exceeded"):
                reason = details.get("error")
                block_for = details.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
                set_block(hass, "airlabs", block_for, reason)
                details = None
            if isinstance(details, dict) and details.get("error"):
                if log_errors:
                    _LOGGER.warning("AirLabs schedule lookup error: %s", details.get("error"))
                else:
                    _LOGGER.debug("AirLabs schedule lookup error: %s", details.get("error"))
                if details.get("error") == "quota_exceeded":
                    payload = {
                        "error": "provider_error",
                        "hint": "AirLabs quota exceeded. Try again later.",
                        "provider": "airlabs",
                    }
                    if strict_provider:
                        return payload
                    last_provider_error = payload
                elif details.get("error") == "rate_limited":
                    payload = {
                        "error": "provider_error",
                        "hint": "AirLabs rate limit reached. Try again later.",
                        "provider": "airlabs",
                    }
                    if strict_provider:
                        return payload
                    last_provider_error = payload
                else:
                    payload = {
                        "error": "provider_error",
                        "hint": details.get("error_message") or "AirLabs error. Try another provider or verify the date.",
                        "provider": "airlabs",
                    }
                    if strict_provider:
                        return payload
                    last_provider_error = payload

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
                        "scheduled_departure": f"{date_str}T00:00:00+00:00",
                        "dep_airport": dep_airport,
                        "arr_airport": arr_airport,
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
                details = None
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
                    if strict_provider:
                        return payload
                    last_provider_error = payload
                elif err == "rate_limited":
                    payload = {
                        "error": "provider_error",
                        "hint": "FlightAPI.io rate limit reached. Try again later.",
                        "provider": "flightapi",
                    }
                    if strict_provider:
                        return payload
                    last_provider_error = payload
                elif err == "auth_error":
                    payload = {
                        "error": "provider_error",
                        "hint": "FlightAPI.io key invalid or unauthorized.",
                        "provider": "flightapi",
                    }
                    if strict_provider:
                        return payload
                    last_provider_error = payload
                elif err and err != "no_match":
                    payload = {
                        "error": "provider_error",
                        "hint": "FlightAPI.io error. Try another provider or verify the date.",
                        "provider": "flightapi",
                    }
                    if strict_provider:
                        return payload
                    last_provider_error = payload

    if not (av_key or al_key or fa_key or fr24_active_key):
        return {
            "error": "no_provider",
            "hint": "No schedule provider configured. Add an API key in Flight Status Tracker options.",
        }

    if last_provider_error:
        return last_provider_error

    # If we reach here, we couldn't match (or providers not implemented for schedule lookups)
    return {"error": "no_match", "hint": "No match found for that date (or provider limits). Try a different date."}
