"""Preview/confirm services for Flight Status Tracker.

This module is intentionally self-contained and does not rely on a specific
resolver function name from status_resolver.py (which may change over time).

Preview flow:
1) User calls flight_status_tracker.preview_flight with airline + flight_number + date
2) We store a preview object in storage immediately (so UI updates)
3) If a status/schedule provider is available, we attempt to enrich the preview
4) User calls flight_status_tracker.confirm_add to persist as a manual flight
5) User calls flight_status_tracker.clear_preview to clear preview
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo
from typing import Any, Callable

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DOMAIN,
    SERVICE_PREVIEW_FLIGHT,
    SERVICE_CONFIRM_ADD,
    SERVICE_CLEAR_PREVIEW,
    SERVICE_ADD_FLIGHT,
    SIGNAL_PREVIEW_UPDATED,
)
from .manual_store import async_add_manual_flight_record
from .schedule_lookup import lookup_schedule
from .directory import airline_logo_url, get_airport, get_airline, upsert_airline_aircraft_image
from .tz_short import tz_short_name
from .preview_store import async_get_preview, async_set_preview

_LOGGER = logging.getLogger(__name__)

async def _notify(hass: HomeAssistant, title: str, message: str) -> None:
    try:
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {"title": title, "message": message},
            blocking=False,
        )
    except Exception:
        _LOGGER.info("%s: %s", title, message)


SERVICE_SCHEMA_PREVIEW = vol.Schema(
    {
        vol.Optional("query"): cv.string,            # e.g. "AI 157"
        vol.Optional("airline"): cv.string,          # IATA like "AI"
        vol.Optional("flight_number"): cv.string,    # "157"
        vol.Optional("date"): cv.string,             # "YYYY-MM-DD"
        vol.Optional("dep_airport"): cv.string,      # optional IATA to disambiguate
        vol.Optional("travellers", default=[]): vol.Any([cv.string], cv.string),
        vol.Optional("notes", default=""): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)


def _parse_query(query: str | None) -> tuple[str | None, str | None]:
    """Parse an airline+flight query like 'AI 157' or 'AI157'."""
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


def _norm_travellers(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    s = str(val).strip()
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def _build_flight_key(airline: str, flight_number: str, dep_iata: str | None, date: str) -> str:
    # dep_iata may be unknown at preview-time. Use XXX so it's still stable.
    dep = (dep_iata or "XXX").upper()
    return f"{airline.upper()}-{flight_number}-{dep}-{date}"


def _normalize_iso_in_tz(val: str | None, tzname: str | None) -> str | None:
    if not val:
        return None
    # Guard against accidental double-offset strings like "+00:00+00:00"
    if isinstance(val, str) and "+00:00+00:00" in val:
        val = val.replace("+00:00+00:00", "+00:00")
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00").replace(" ", "T"))
    except Exception:
        return val
    if dt.tzinfo is None:
        if not tzname:
            return val
        try:
            dt = dt.replace(tzinfo=ZoneInfo(tzname))
        except Exception:
            return val
    return dt.astimezone(timezone.utc).isoformat()


_TZ_RE = re.compile(r"(Z|[+-]\d{2}:\d{2})$")


def _has_tz(val: str | None) -> bool:
    if not val or not isinstance(val, str):
        return False
    return _TZ_RE.search(val.strip()) is not None


def _provider_label(options: dict[str, Any] | None) -> str:
    provider = str(((options or {}).get("schedule_provider") or (options or {}).get("status_provider") or "")).strip().lower()
    if provider == "aerodatabox":
        return "AeroDataBox"
    if provider == "flightapi":
        return "FlightAPI.io"
    return "The provider"


def _preview_complete(flight: dict[str, Any] | None, provider_label: str) -> tuple[bool, str | None, str | None]:
    """Return whether preview has minimum required fields to add."""
    if not isinstance(flight, dict):
        return False, "invalid_preview", "Preview data is invalid."
    dep_airport = ((flight.get("dep") or {}).get("airport") or {}).get("iata")
    arr_airport = ((flight.get("arr") or {}).get("airport") or {}).get("iata")
    dep_airport_name = ((flight.get("dep") or {}).get("airport") or {}).get("name")
    arr_airport_name = ((flight.get("arr") or {}).get("airport") or {}).get("name")
    dep = (flight.get("dep") or {})
    arr = (flight.get("arr") or {})
    dep_time = dep.get("scheduled") or dep.get("estimated") or dep.get("actual")
    arr_time = arr.get("scheduled") or arr.get("estimated") or arr.get("actual")
    dep_airport_missing = not dep_airport and not dep_airport_name
    arr_airport_missing = not arr_airport and not arr_airport_name
    dep_airport_incomplete = not dep_airport
    arr_airport_incomplete = not arr_airport
    dep_time_missing = not dep_time
    arr_time_missing = not arr_time

    if dep_airport_missing and arr_airport_missing and dep_time_missing and arr_time_missing:
        return False, "missing_both_airports_times", f"{provider_label} found the flight, but departure and arrival details are incomplete."
    if dep_airport_missing and dep_time_missing and not arr_airport_missing and not arr_time_missing:
        return False, "missing_departure_airport_time", f"{provider_label} found the flight, but departure airport and timing details are incomplete."
    if arr_airport_incomplete and arr_time_missing and not dep_airport_missing and not dep_time_missing:
        return False, "missing_arrival_airport_time", f"{provider_label} found the flight, but arrival airport and timing details are incomplete."
    if dep_airport_missing and arr_airport_missing:
        return False, "missing_both_airports", f"{provider_label} found the flight, but departure and arrival airport details are incomplete."
    if dep_time_missing and arr_time_missing:
        return False, "missing_both_times", f"{provider_label} found the flight, but departure and arrival timing details are incomplete."
    if dep_airport_missing:
        return False, "missing_departure_airport", f"{provider_label} found the flight, but departure airport details are incomplete."
    if arr_airport_missing:
        return False, "missing_arrival_airport", f"{provider_label} found the flight, but arrival airport details are incomplete."
    if dep_airport_incomplete and dep_time_missing:
        return False, "missing_departure_airport_time", f"{provider_label} found the flight, but departure airport and timing details are incomplete."
    if arr_airport_incomplete and arr_time_missing:
        return False, "missing_arrival_airport_time", f"{provider_label} found the flight, but arrival airport and timing details are incomplete."
    if dep_time_missing:
        return False, "missing_departure_time", f"{provider_label} found the flight, but departure timing details are incomplete."
    if arr_time_missing:
        return False, "missing_arrival_time", f"{provider_label} found the flight, but arrival timing details are incomplete."
    if dep_airport_incomplete:
        return False, "missing_departure_airport", f"{provider_label} found the flight, but departure airport details are incomplete."
    if arr_airport_incomplete:
        return False, "missing_arrival_airport", f"{provider_label} found the flight, but arrival airport details are incomplete."
    return True, None, None


def _preview_error_message(error_code: str | None, provider_label: str, hint: str | None = None) -> str:
    code = (error_code or "").strip()
    if code == "bad_query":
        return "Enter an airline code and flight number, like TK 717."
    if code == "bad_date":
        return "Select a date for the flight."
    if code == "no_match":
        return "No matching flight was found for that date."
    if code == "no_provider":
        return f"{provider_label} is unavailable right now. ({code})"
    if code == "provider_error":
        hint_text = (hint or "").strip()
        lower_hint = hint_text.lower()
        if "quota" in lower_hint or "subscription" in lower_hint or "limit" in lower_hint:
            return f"{provider_label} subscription limit has been reached. ({code})"
        if "unauthorized" in lower_hint or "invalid" in lower_hint or "auth" in lower_hint:
            return f"{provider_label} rejected the request. ({code})"
        return f"{provider_label} returned an error for this flight. ({code})"
    if code:
        return f"{provider_label} returned an error for this flight. ({code})"
    return hint or "Unable to preview this flight."


async def _try_enrich_preview(
    hass: HomeAssistant,
    options: dict[str, Any],
    airline: str,
    flight_number: str,
    date_str: str,
    dep_airport: str | None = None,
    arr_airport: str | None = None,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Try to enrich preview using schedule lookup providers."""
    result = await lookup_schedule(
        hass, options, f"{airline} {flight_number}", date_str, dep_airport=dep_airport, arr_airport=arr_airport
    )
    if result.get("flight"):
        return result, None, None
    return None, result.get("error") or "no_match_or_no_provider", result.get("hint")


async def async_register_preview_services(
    hass: HomeAssistant,
    options_provider: Callable[[], dict[str, Any]],
) -> None:
    """Register preview/confirm/clear services."""
    async def _svc_preview(call: ServiceCall) -> None:
        """Build a preview record from minimal inputs and store it server-side."""
        airline = str(call.data.get("airline", "")).strip().upper()
        flight_number = str(call.data.get("flight_number", "")).strip()
        dep_airport = str(call.data.get("dep_airport", "")).strip().upper() or None
        if not airline or not flight_number:
            q_airline, q_fnum = _parse_query(call.data.get("query"))
            airline = airline or (q_airline or "")
            flight_number = flight_number or (q_fnum or "")
        date_str = str(call.data.get("date", "")).strip()
        travellers = _norm_travellers(call.data.get("travellers"))
        notes = str(call.data.get("notes", "")).strip() or None

        if not date_str:
            preview = {
                "ready": False,
                "error_code": "bad_date",
                "error": "Select a date for the flight.",
                "hint": None,
                "input": {
                    "airline": airline,
                    "flight_number": flight_number,
                    "date": date_str,
                    "dep_airport": dep_airport or "",
                    "travellers": travellers,
                    "notes": notes or "",
                },
                "flight": None,
                "status_raw": None,
            }
            await async_set_preview(hass, preview)
            async_dispatcher_send(hass, SIGNAL_PREVIEW_UPDATED)
            return

        if not airline or not flight_number:
            preview = {
                "ready": False,
                "error_code": "bad_query",
                "error": "Enter an airline code and flight number, like TK 717.",
                "hint": None,
                "input": {
                    "airline": airline,
                    "flight_number": flight_number,
                    "date": date_str,
                    "dep_airport": dep_airport or "",
                    "travellers": travellers,
                    "notes": notes or "",
                },
                "flight": None,
                "status_raw": None,
            }
            await async_set_preview(hass, preview)
            async_dispatcher_send(hass, SIGNAL_PREVIEW_UPDATED)
            return

        preview: dict[str, Any] = {
            "ready": False,
            "error": None,
            "hint": None,
            "input": {
                "airline": airline,
                "flight_number": flight_number,
                "date": date_str,
                "dep_airport": dep_airport or "",
                "travellers": travellers,
                "notes": notes or "",
            },
            "flight": None,
            "status_raw": None,
        }

        flight: dict[str, Any] = {
            "source": "preview",
            "flight_key": _build_flight_key(airline, flight_number, None, date_str),
            "airline_code": airline,
            "flight_number": flight_number,
            "travellers": travellers,
            "notes": notes,
            "status_state": "Unknown",
            "airline_name": None,
            "airline_logo_url": airline_logo_url(airline),
            "aircraft_type": None,
            "dep": {
                "airport": {"iata": None, "name": None, "city": None, "tz": None, "tz_short": None},
                "scheduled": None,
                "estimated": None,
                "actual": None,
                "terminal": None,
                "gate": None,
            },
            "arr": {
                "airport": {"iata": None, "name": None, "city": None, "tz": None, "tz_short": None},
                "scheduled": None,
                "estimated": None,
                "actual": None,
                "terminal": None,
                "gate": None,
            },
        }

        options = options_provider()
        provider_label = _provider_label(options)
        status_raw, err, hint = await _try_enrich_preview(
            hass, options, airline, flight_number, date_str, dep_airport, None
        )

        if status_raw:
            if isinstance(status_raw, dict) and isinstance(status_raw.get("flight"), dict):
                enriched = status_raw.get("flight")
                enriched["travellers"] = travellers
                enriched["notes"] = notes
                preview["flight"] = enriched
            else:
                preview["flight"] = flight
            preview["status_raw"] = status_raw
            # Enrich preview with directory data (airport/airline) before completeness check
            f = preview.get("flight") or {}
            dep = (f.get("dep") or {})
            arr = (f.get("arr") or {})
            dep_air = (dep.get("airport") or {})
            arr_air = (arr.get("airport") or {})

            if f.get("airline_code") and (not f.get("airline_name") or not f.get("airline_logo_url") or not f.get("aircraft_image_url")):
                airline_info = await get_airline(hass, options, f.get("airline_code"))
                if airline_info:
                    f["airline_name"] = airline_info.get("name") or f.get("airline_name")
                    if not f.get("airline_logo_url"):
                        f["airline_logo_url"] = airline_info.get("logo") or airline_info.get("logo_url") or f.get("airline_logo_url")
                    if not f.get("aircraft_image_url"):
                        f["aircraft_image_url"] = airline_info.get("aircraft_image_url") or f.get("aircraft_image_url")

            # Cache-first enrichment for aircraft image URL:
            # if preview already has provider image and directory is missing it, persist for future reuse.
            if f.get("airline_code") and f.get("aircraft_image_url"):
                await upsert_airline_aircraft_image(
                    hass,
                    f.get("airline_code"),
                    f.get("aircraft_image_url"),
                )

            if dep_air.get("iata") and (not dep_air.get("name") or not dep_air.get("city") or not dep_air.get("tz")):
                airport = await get_airport(hass, options, dep_air.get("iata"))
                if airport:
                    dep_air["name"] = dep_air.get("name") or airport.get("name")
                    dep_air["city"] = dep_air.get("city") or airport.get("city")
                    dep_air["tz"] = dep_air.get("tz") or airport.get("tz")
            if arr_air.get("iata") and (not arr_air.get("name") or not arr_air.get("city") or not arr_air.get("tz")):
                airport = await get_airport(hass, options, arr_air.get("iata"))
                if airport:
                    arr_air["name"] = arr_air.get("name") or airport.get("name")
                    arr_air["city"] = arr_air.get("city") or airport.get("city")
                    arr_air["tz"] = arr_air.get("tz") or airport.get("tz")

            # No static fallback: only use directory providers / cache

            dep_sched = dep.get("scheduled")
            arr_sched = arr.get("scheduled")
            if dep_air.get("tz") and not dep_air.get("tz_short"):
                dep_air["tz_short"] = tz_short_name(dep_air.get("tz"), dep_sched)
            if arr_air.get("tz") and not arr_air.get("tz_short"):
                arr_air["tz_short"] = tz_short_name(arr_air.get("tz"), arr_sched)

            dep["airport"] = dep_air
            arr["airport"] = arr_air

            def _to_local(ts: Any, tzname: str | None) -> str | None:
                if not ts or not isinstance(ts, str):
                    return None
                dt = dt_util.parse_datetime(ts)
                if not dt:
                    return None
                if tzname:
                    if not dt.tzinfo:
                        # Naive schedule values from providers are usually airport-local.
                        try:
                            dt = dt.replace(tzinfo=ZoneInfo(tzname))
                        except Exception:
                            return dt.isoformat()
                    try:
                        return dt.astimezone(ZoneInfo(tzname)).isoformat()
                    except Exception:
                        return dt.isoformat()
                try:
                    # No airport timezone known yet: keep provider wall-clock value.
                    return dt.isoformat()
                except Exception:
                    return None

            dep["scheduled_local"] = _to_local(dep.get("scheduled"), dep_air.get("tz"))
            dep["estimated_local"] = _to_local(dep.get("estimated"), dep_air.get("tz"))
            dep["actual_local"] = _to_local(dep.get("actual"), dep_air.get("tz"))
            arr["scheduled_local"] = _to_local(arr.get("scheduled"), arr_air.get("tz"))
            arr["estimated_local"] = _to_local(arr.get("estimated"), arr_air.get("tz"))
            arr["actual_local"] = _to_local(arr.get("actual"), arr_air.get("tz"))
            f["dep"] = dep
            f["arr"] = arr

            # Normalize naive timestamps using airport tz once it is known
            dep["scheduled"] = _normalize_iso_in_tz(dep.get("scheduled"), dep_air.get("tz"))
            dep["estimated"] = _normalize_iso_in_tz(dep.get("estimated"), dep_air.get("tz"))
            dep["actual"] = _normalize_iso_in_tz(dep.get("actual"), dep_air.get("tz"))
            arr["scheduled"] = _normalize_iso_in_tz(arr.get("scheduled"), arr_air.get("tz"))
            arr["estimated"] = _normalize_iso_in_tz(arr.get("estimated"), arr_air.get("tz"))
            arr["actual"] = _normalize_iso_in_tz(arr.get("actual"), arr_air.get("tz"))
            # If still naive (no tz info), coerce to UTC so UI can render times
            if isinstance(dep.get("scheduled"), str) and not _has_tz(dep.get("scheduled")):
                dep["scheduled"] = dep.get("scheduled") + "+00:00"
            if isinstance(arr.get("scheduled"), str) and not _has_tz(arr.get("scheduled")):
                arr["scheduled"] = arr.get("scheduled") + "+00:00"
            f["dep"] = dep
            f["arr"] = arr
            preview["flight"] = f
            # Ensure flight_key is set once dep IATA is known
            dep_iata = ((f.get("dep") or {}).get("airport") or {}).get("iata")
            if not f.get("flight_key") or f.get("flight_key") == _build_flight_key(airline, flight_number, None, date_str):
                f["flight_key"] = _build_flight_key(f.get("airline_code") or airline, f.get("flight_number") or flight_number, dep_iata, date_str)
                preview["flight"] = f

            # If provider returned multiple matches, require user disambiguation
            ready, error_code, error_message = _preview_complete(preview.get("flight"), provider_label)
            preview["ready"] = ready
            preview["error_code"] = None if ready else error_code
            preview["error"] = None if ready else error_message
            preview["hint"] = None
            # Warn (but allow add) if logo is missing
            if ready:
                f = preview.get("flight") or {}
                if not f.get("airline_logo_url"):
                    f["airline_logo_url"] = airline_logo_url(airline)
                if not f.get("airline_logo_url"):
                    preview["warning"] = "Airline logo not available."
                # Warn if city is missing but allow add
                dep_air = ((f.get("dep") or {}).get("airport") or {})
                arr_air = ((f.get("arr") or {}).get("airport") or {})
                missing_details = []
                if dep_air.get("iata") and (not dep_air.get("name") or not dep_air.get("city") or not dep_air.get("tz")):
                    missing_details.append(dep_air.get("iata"))
                if arr_air.get("iata") and (not arr_air.get("name") or not arr_air.get("city") or not arr_air.get("tz")):
                    missing_details.append(arr_air.get("iata"))
                if missing_details:
                    msg = "Airport details missing for: " + ", ".join(missing_details)
                    preview["warning"] = (preview.get("warning") + " " if preview.get("warning") else "") + msg
        else:
            preview["flight"] = flight
            preview["ready"] = False
            preview["error_code"] = err or "no_match_or_no_provider"
            preview["error"] = _preview_error_message(err, provider_label, hint)
            preview["hint"] = None

        await async_set_preview(hass, preview)
        async_dispatcher_send(hass, SIGNAL_PREVIEW_UPDATED)

    async def _svc_confirm(call: ServiceCall) -> None:
        """Persist the current preview as a manual flight."""
        preview = await async_get_preview(hass)
        if not (preview or {}).get("ready"):
            return
        f = (preview or {}).get("flight")
        if not isinstance(f, dict):
            return
        # Backstop: compute flight_key if missing
        if not f.get("flight_key"):
            dep_iata = ((f.get("dep") or {}).get("airport") or {}).get("iata")
            f["flight_key"] = _build_flight_key(f.get("airline_code"), f.get("flight_number"), dep_iata, (preview or {}).get("input", {}).get("date"))

        try:
            flight_key = await async_add_manual_flight_record(hass, f)
            await async_set_preview(hass, None)
            async_dispatcher_send(hass, SIGNAL_PREVIEW_UPDATED)
        except Exception as e:
            _LOGGER.exception("Confirm add failed")

    async def _svc_clear(call: ServiceCall) -> None:
        """Clear any stored preview."""
        await async_set_preview(hass, None)
        async_dispatcher_send(hass, SIGNAL_PREVIEW_UPDATED)

    async def _svc_add_flight(call: ServiceCall) -> None:
        """Add a flight directly from minimal inputs without preview."""
        airline = str(call.data.get("airline", "")).strip().upper()
        flight_number = str(call.data.get("flight_number", "")).strip()
        dep_airport = str(call.data.get("dep_airport", "")).strip().upper() or None
        if not airline or not flight_number:
            q_airline, q_fnum = _parse_query(call.data.get("query"))
            airline = airline or (q_airline or "")
            flight_number = flight_number or (q_fnum or "")
        date_str = str(call.data.get("date", "")).strip()
        travellers = _norm_travellers(call.data.get("travellers"))
        notes = str(call.data.get("notes", "")).strip() or None

        if not date_str:
            await _notify(hass, "Flight Status Tracker", "Add flight failed: date is required.")
            return
        if not airline or not flight_number:
            await _notify(hass, "Flight Status Tracker", "Add flight failed: airline and flight number are required.")
            return

        result = await lookup_schedule(
            hass,
            options_provider(),
            f"{airline} {flight_number}",
            date_str,
            dep_airport=dep_airport,
        )
        flight = result.get("flight") if isinstance(result, dict) else None
        if isinstance(flight, dict):
            flight["travellers"] = travellers
            flight["notes"] = notes
            try:
                await async_add_manual_flight_record(hass, flight)
                return
            except Exception as e:
                _LOGGER.exception("Add flight failed")
                await _notify(hass, "Flight Status Tracker", f"Add flight failed: {e}")
                return

        hint = result.get("hint") if isinstance(result, dict) else None
        await _notify(
            hass,
            "Flight Status Tracker",
            f"Add flight failed: {hint or 'No matching flight found or provider error.'}",
        )

    for service in (SERVICE_PREVIEW_FLIGHT, SERVICE_CONFIRM_ADD, SERVICE_CLEAR_PREVIEW, SERVICE_ADD_FLIGHT):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)

    hass.services.async_register(DOMAIN, SERVICE_PREVIEW_FLIGHT, _svc_preview, schema=SERVICE_SCHEMA_PREVIEW)
    hass.services.async_register(DOMAIN, SERVICE_CONFIRM_ADD, _svc_confirm)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_PREVIEW, _svc_clear)
    hass.services.async_register(DOMAIN, SERVICE_ADD_FLIGHT, _svc_add_flight, schema=SERVICE_SCHEMA_PREVIEW)

    _LOGGER.info(
        "Registered preview services: %s, %s, %s",
        SERVICE_PREVIEW_FLIGHT,
        SERVICE_CONFIRM_ADD,
        SERVICE_CLEAR_PREVIEW,
    )
