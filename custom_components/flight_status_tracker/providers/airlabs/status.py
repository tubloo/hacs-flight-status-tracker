"""AirLabs status provider."""
from __future__ import annotations

from datetime import date, datetime
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


def _date_token(val: Any) -> date | None:
    if not isinstance(val, str):
        return None
    raw = val.strip()
    if len(raw) < 10:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T")).date()
    except Exception:
        pass
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except Exception:
        return None


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
        try:
            async with session.get(url, params=params, timeout=25) as resp:
                payload = await resp.json(content_type=None)
                retry_after = resp.headers.get("Retry-After")
        except Exception:
            details = {"provider": "airlabs", "state": "unknown", "error": "network"}
            return FlightStatus(provider="airlabs", state="unknown", details=details)

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

        # Optional route disambiguation for multi-leg flight numbers.
        dep_filter = (flight.get("dep_airport") or flight.get("dep_iata") or "").strip().upper()
        arr_filter = (flight.get("arr_airport") or flight.get("arr_iata") or "").strip().upper()
        dep_iata_resp = (resp_obj.get("dep_iata") or resp_obj.get("departure_iata") or "").strip().upper()
        arr_iata_resp = (resp_obj.get("arr_iata") or resp_obj.get("arrival_iata") or "").strip().upper()
        if dep_filter and dep_iata_resp and dep_filter != dep_iata_resp:
            return None
        if arr_filter and arr_iata_resp and arr_filter != arr_iata_resp:
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

        # AirLabs /flight is not explicitly date-scoped; reject obvious wrong-day
        # matches for recurring flight numbers, while tolerating timezone edges.
        requested_dt = _date_token(flight.get("scheduled_departure"))
        if requested_dt is None:
            dep_req = (flight.get("dep") or {})
            requested_dt = _date_token(dep_req.get("scheduled"))
        response_dt = _date_token(dep_sched) or _date_token(dep_est) or _date_token(dep_act)
        if requested_dt and response_dt and abs((response_dt - requested_dt).days) > 1:
            return None

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
            # Optional live fields when available in payload.
            "lat": resp_obj.get("lat") or resp_obj.get("latitude"),
            "lon": resp_obj.get("lng") or resp_obj.get("lon") or resp_obj.get("longitude"),
            "alt": resp_obj.get("alt"),
            "track": resp_obj.get("dir") or resp_obj.get("track"),
            "gspeed": resp_obj.get("speed") or resp_obj.get("speed_kts"),
            "timestamp": resp_obj.get("updated") or resp_obj.get("ts"),
            # useful for OpenSky enrichment sometimes
            "icao24": (resp_obj.get("hex") or resp_obj.get("icao24") or "").lower().strip() or None,
        }

        return FlightStatus(provider="airlabs", state=status, details=details)
