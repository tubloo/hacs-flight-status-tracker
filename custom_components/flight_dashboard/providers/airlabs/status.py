"""AirLabs status provider."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .._shared.status_base import FlightStatus


def _retry_after_from_code(code: str) -> int | None:
    if code == "minute_limit_exceeded":
        return 60
    if code == "hour_limit_exceeded":
        return 60 * 60
    if code == "month_limit_exceeded":
        return 24 * 60 * 60
    return None


def _error_type(code: str, message: str) -> str:
    code_l = (code or "").lower()
    msg_l = (message or "").lower()
    if code_l in {"minute_limit_exceeded", "hour_limit_exceeded"} or "rate" in msg_l or "limit" in msg_l:
        return "rate_limited"
    if code_l in {"month_limit_exceeded"} or "quota" in msg_l:
        return "quota_exceeded"
    if code_l in {"unknown_api_key", "expired_api_key"} or "api key" in msg_l:
        return "auth_error"
    if code_l in {"wrong_params", "unknown_method"}:
        return "bad_request"
    if code_l == "not_found":
        return "no_match"
    return "provider_error"


def _iso(s: str | None) -> str | None:
    if not s:
        return None
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return s
    except Exception:
        return s


class AirLabsStatusProvider:
    def __init__(self, hass: HomeAssistant, api_key: str) -> None:
        self.hass = hass
        self.api_key = api_key.strip()

    async def async_get_status(self, flight: dict[str, Any]) -> FlightStatus | None:
        """Fetch flight status from AirLabs and normalize fields."""
        airline = (flight.get("airline_code") or "").strip()
        number = str(flight.get("flight_number") or "").strip()
        if not airline or not number:
            return None

        flight_iata = f"{airline}{number}"
        url = "https://airlabs.co/api/v9/flight"
        params = {"api_key": self.api_key, "flight_iata": flight_iata}

        session = async_get_clientsession(self.hass)
        async with session.get(url, params=params, timeout=25) as resp:
            payload = await resp.json(content_type=None)
            retry_after = resp.headers.get("Retry-After")

        if isinstance(payload, dict) and payload.get("error"):
            err = payload.get("error")
            if isinstance(err, dict):
                code = str(err.get("code") or "")
                message = str(err.get("message") or "")
            else:
                code = ""
                message = str(err)
            err_type = _error_type(code, message)
            ra = int(retry_after) if retry_after and retry_after.isdigit() else _retry_after_from_code(code)
            details = {
                "provider": "airlabs",
                "state": "unknown",
                "error": err_type,
                "error_code": code or None,
                "error_message": message or str(err),
            }
            if ra:
                details["retry_after"] = ra
            return FlightStatus(provider="airlabs", state="unknown", details=details)

        if isinstance(payload, dict) and resp.status in (429, 402):
            details = {"provider": "airlabs", "state": "unknown", "error": "rate_limited"}
            if retry_after and retry_after.isdigit():
                details["retry_after"] = int(retry_after)
            return FlightStatus(provider="airlabs", state="unknown", details=details)
        resp_obj = payload.get("response") if isinstance(payload, dict) else None
        if not isinstance(resp_obj, dict):
            # Sometimes errors are in payload["error"]
            if isinstance(payload, dict) and payload.get("error"):
                return FlightStatus(
                    provider="airlabs",
                    state="unknown",
                    details={"provider": "airlabs", "state": "unknown", "error": payload.get("error")},
                )
            return None

        status = (resp_obj.get("status") or "unknown").lower()

        # AirLabs exposes both local and UTC variants. Prefer UTC when present;
        # the API sometimes returns local times that are already shifted.
        dep_sched = resp_obj.get("dep_time_utc") or resp_obj.get("dep_scheduled") or resp_obj.get("dep_time")
        arr_sched = resp_obj.get("arr_time_utc") or resp_obj.get("arr_scheduled") or resp_obj.get("arr_time")
        dep_est = resp_obj.get("dep_estimated_utc") or resp_obj.get("dep_estimated")
        dep_act = resp_obj.get("dep_actual_utc") or resp_obj.get("dep_actual")
        arr_est = resp_obj.get("arr_estimated_utc") or resp_obj.get("arr_estimated")
        arr_act = resp_obj.get("arr_actual_utc") or resp_obj.get("arr_actual")

        details = {
            "provider": "airlabs",
            "state": status,
            "dep_scheduled": _iso(dep_sched),
            "dep_estimated": _iso(dep_est),
            "dep_actual": _iso(dep_act),
            "arr_scheduled": _iso(arr_sched),
            "arr_estimated": _iso(arr_est),
            "arr_actual": _iso(arr_act),
            "dep_iata": resp_obj.get("dep_iata") or resp_obj.get("departure_iata"),
            "arr_iata": resp_obj.get("arr_iata") or resp_obj.get("arrival_iata"),
            "airline_name": resp_obj.get("airline_name"),
            "terminal_dep": resp_obj.get("dep_terminal"),
            "gate_dep": resp_obj.get("dep_gate"),
            "terminal_arr": resp_obj.get("arr_terminal"),
            "gate_arr": resp_obj.get("arr_gate"),
            "delay_minutes": resp_obj.get("delay"),
            # useful for OpenSky enrichment sometimes
            "icao24": (resp_obj.get("hex") or resp_obj.get("icao24") or "").lower().strip() or None,
        }

        return FlightStatus(provider="airlabs", state=status, details=details)
