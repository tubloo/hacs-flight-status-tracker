"""Aviationstack status provider."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ...const import DOMAIN
from .._shared.status_base import FlightStatus


def _error_type(code: str, message: str) -> str:
    code_l = (code or "").lower()
    msg_l = (message or "").lower()
    if code_l in {"rate_limit_reached"} or "rate limit" in msg_l:
        return "rate_limited"
    if code_l in {"usage_limit_reached"} or "quota" in msg_l or "limit" in msg_l:
        return "quota_exceeded"
    if code_l in {"invalid_access_key", "missing_access_key", "inactive_user"} or "access key" in msg_l:
        return "auth_error"
    if code_l in {"function_access_restricted"}:
        return "plan_restricted"
    if code_l in {"invalid_api_function", "404_not_found"}:
        return "bad_request"
    if code_l in {"method_not_supported", "validation_error"}:
        return "bad_request"
    return "provider_error"


def _parse_dt(s: str | None) -> str | None:
    if not s:
        return None
    try:
        # Keep ISO string; HA UI will format later if needed
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return s
    except Exception:
        return s


def _is_utc_offset_iso(s: str | None) -> bool:
    if not isinstance(s, str):
        return False
    v = s.strip()
    return v.endswith("Z") or v.endswith("+00:00")


def _is_non_utc_tz(tzname: str | None) -> bool:
    if not isinstance(tzname, str):
        return False
    t = tzname.strip().lower()
    return t not in {"", "utc", "etc/utc", "gmt", "etc/gmt"}


def _aviationstack_wall_time_iso(s: str | None, tzname: str | None) -> str | None:
    """Normalize Aviationstack datetime string.

    Aviationstack docs expose both timezone and RFC3339 strings; in practice many
    records provide +00:00 even when airport timezone is non-UTC. In that case,
    keep wall-clock value and let downstream normalization apply airport tz.
    """
    out = _parse_dt(s)
    if not out:
        return out
    if _is_utc_offset_iso(out) and _is_non_utc_tz(tzname):
        try:
            dt = datetime.fromisoformat(out.replace("Z", "+00:00"))
            return dt.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            return out
    return out


def _parse_http_date_retry_after(raw: str | None) -> int | None:
    if not raw:
        return None
    v = str(raw).strip()
    if v.isdigit():
        return max(0, int(v))
    try:
        dt = datetime.strptime(v, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
        return max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))
    except Exception:
        return None


def _throttle_state(hass: HomeAssistant) -> dict[str, Any]:
    root = hass.data.setdefault(DOMAIN, {})
    return root.setdefault("aviationstack_throttle", {})


async def _wait_for_endpoint_window(hass: HomeAssistant, endpoint: str, min_interval_seconds: int) -> None:
    state = _throttle_state(hass)
    key = endpoint.lower()
    interval_key = f"{key}_interval"
    interval = min_interval_seconds
    saved_interval = state.get(interval_key)
    if isinstance(saved_interval, (int, float)):
        interval = max(int(saved_interval), min_interval_seconds)
    now = datetime.now(timezone.utc).timestamp()
    last_ts = state.get(key)
    if isinstance(last_ts, (int, float)):
        wait_for = float(interval) - (now - float(last_ts))
        if wait_for > 0:
            await asyncio.sleep(wait_for)
    state[key] = datetime.now(timezone.utc).timestamp()


def _remember_endpoint_interval(hass: HomeAssistant, endpoint: str, seconds: int | None) -> None:
    if not isinstance(seconds, int) or seconds <= 0:
        return
    state = _throttle_state(hass)
    key = f"{endpoint.lower()}_interval"
    current = state.get(key)
    if not isinstance(current, (int, float)) or seconds > int(current):
        state[key] = int(seconds)


class AviationstackStatusProvider:
    def __init__(self, hass: HomeAssistant, access_key: str) -> None:
        self.hass = hass
        self.access_key = access_key.strip()

    async def async_get_status(self, flight: dict[str, Any]) -> FlightStatus | None:
        """Fetch flight status from Aviationstack and normalize fields."""
        airline = (flight.get("airline_code") or "").strip()
        number = str(flight.get("flight_number") or "").strip()
        if not airline or not number:
            return None

        flight_iata = f"{airline}{number}"

        # Try with and without flight_date (plans vary)
        flight_date = None
        sd = flight.get("scheduled_departure")
        if isinstance(sd, str) and len(sd) >= 10:
            flight_date = sd[:10]
        requested_date = (flight_date or "").strip()[:10]
        dep_airport = (flight.get("dep_airport") or "").strip().upper()
        arr_airport = (flight.get("arr_airport") or "").strip().upper()

        query_variants: list[dict[str, Any]] = [
            {"flight_iata": flight_iata, "limit": 10},
        ]
        if flight_date:
            query_variants.insert(0, {"flight_iata": flight_iata, "flight_date": flight_date, "limit": 10})

        session = async_get_clientsession(self.hass)

        def _pick(d: dict[str, Any], *keys: str) -> Any:
            for k in keys:
                if k in d and d.get(k) is not None:
                    return d.get(k)
            return None

        def _codeshared(it: dict[str, Any]) -> dict[str, Any]:
            cs = it.get("codeshared")
            return cs if isinstance(cs, dict) else {}

        def _flight_block(it: dict[str, Any]) -> dict[str, Any]:
            csf = _codeshared(it).get("flight")
            if isinstance(csf, dict) and csf:
                return csf
            fl = it.get("flight")
            return fl if isinstance(fl, dict) else {}

        def _airline_block(it: dict[str, Any]) -> dict[str, Any]:
            csa = _codeshared(it).get("airline")
            if isinstance(csa, dict) and csa:
                return csa
            al = it.get("airline")
            return al if isinstance(al, dict) else {}

        def _flight_iata(it: dict[str, Any]) -> str:
            fl = _flight_block(it)
            return str(_pick(fl, "iata", "iataNumber", "iataCode") or "").strip().upper()

        def _flight_number(it: dict[str, Any]) -> str:
            fl = _flight_block(it)
            return str(_pick(fl, "number", "flightNumber") or "").strip()

        def _airline_iata(it: dict[str, Any]) -> str:
            al = _airline_block(it)
            return str(_pick(al, "iata", "iataCode") or "").strip().upper()

        def _dep_iata(it: dict[str, Any]) -> str:
            dep = it.get("departure") or {}
            return str(_pick(dep, "iata", "iataCode") or "").strip().upper()

        def _arr_iata(it: dict[str, Any]) -> str:
            arr = it.get("arrival") or {}
            return str(_pick(arr, "iata", "iataCode") or "").strip().upper()

        def _dep_sched_raw(it: dict[str, Any]) -> str:
            dep = it.get("departure") or {}
            return str(_pick(dep, "scheduled", "scheduledTime") or "").strip()

        def _arr_sched_raw(it: dict[str, Any]) -> str:
            arr = it.get("arrival") or {}
            return str(_pick(arr, "scheduled", "scheduledTime") or "").strip()

        def _dep_est_raw(it: dict[str, Any]) -> str:
            dep = it.get("departure") or {}
            return str(_pick(dep, "estimated", "estimatedTime") or "").strip()

        def _arr_est_raw(it: dict[str, Any]) -> str:
            arr = it.get("arrival") or {}
            return str(_pick(arr, "estimated", "estimatedTime") or "").strip()

        def _dep_act_raw(it: dict[str, Any]) -> str:
            dep = it.get("departure") or {}
            return str(_pick(dep, "actual", "actualTime") or "").strip()

        def _arr_act_raw(it: dict[str, Any]) -> str:
            arr = it.get("arrival") or {}
            return str(_pick(arr, "actual", "actualTime") or "").strip()

        def _matches_identity(it: dict[str, Any]) -> bool:
            it_iata = _flight_iata(it)
            it_num = _flight_number(it)
            it_airline = _airline_iata(it)
            if it_iata and it_iata == flight_iata.upper():
                return True
            if it_num and it_num == number and (not it_airline or it_airline == airline.upper()):
                return True
            return False

        def _candidate_score(it: dict[str, Any]) -> tuple[int, bool]:
            score = 0
            dep = _dep_iata(it)
            arr = _arr_iata(it)
            if dep_airport and dep == dep_airport:
                score += 50
            if arr_airport and arr == arr_airport:
                score += 50

            item_date = str(it.get("flight_date") or "").strip()[:10]
            dep_sched = _dep_sched_raw(it)
            dep_sched_date = dep_sched[:10] if len(dep_sched) >= 10 else ""
            exact = False
            if requested_date:
                if item_date == requested_date:
                    score += 40
                    exact = True
                elif item_date:
                    score -= 30
                if dep_sched_date == requested_date:
                    score += 20
                    exact = True
                elif dep_sched_date:
                    score -= 10
            return score, exact

        def _pick_best(items: list[dict[str, Any]], require_exact: bool) -> dict[str, Any] | None:
            best_item: dict[str, Any] | None = None
            best_score = -10_000
            for it in items:
                if not isinstance(it, dict):
                    continue
                if not _matches_identity(it):
                    continue
                score, exact = _candidate_score(it)
                if require_exact and not exact:
                    continue
                if score > best_score:
                    best_score = score
                    best_item = it
            return best_item

        def _to_details(best: dict[str, Any]) -> dict[str, Any]:
            dep = best.get("departure") or {}
            arr = best.get("arrival") or {}
            dep_tz = _pick(dep, "timezone")
            arr_tz = _pick(arr, "timezone")
            aircraft = best.get("aircraft") or {}
            airline_name = (best.get("airline") or {}).get("name")
            aircraft_type = None
            if isinstance(aircraft, dict):
                aircraft_type = _pick(aircraft, "iata", "icao", "registration", "icaoCode", "regNumber")
            fs = str(_pick(best, "flight_status", "status") or "unknown").lower()
            state = fs if fs else "unknown"
            return {
                "provider": "aviationstack",
                "state": state,
                "dep_scheduled": _aviationstack_wall_time_iso(_dep_sched_raw(best) or None, dep_tz),
                "dep_estimated": _aviationstack_wall_time_iso(_dep_est_raw(best) or None, dep_tz),
                "dep_actual": _aviationstack_wall_time_iso(_dep_act_raw(best) or None, dep_tz),
                "arr_scheduled": _aviationstack_wall_time_iso(_arr_sched_raw(best) or None, arr_tz),
                "arr_estimated": _aviationstack_wall_time_iso(_arr_est_raw(best) or None, arr_tz),
                "arr_actual": _aviationstack_wall_time_iso(_arr_act_raw(best) or None, arr_tz),
                "dep_iata": _dep_iata(best) or None,
                "arr_iata": _arr_iata(best) or None,
                "dep_tz": dep_tz,
                "arr_tz": arr_tz,
                "aircraft_type": aircraft_type,
                "airline_name": airline_name,
                "terminal_dep": _pick(dep, "terminal"),
                "gate_dep": _pick(dep, "gate"),
                "terminal_arr": _pick(arr, "terminal"),
                "gate_arr": _pick(arr, "gate"),
                "delay_minutes": _pick(dep, "delay") or _pick(arr, "delay"),
            }

        def _parse_date_yyyy_mm_dd(s: str | None) -> date | None:
            if not isinstance(s, str) or len(s) < 10:
                return None
            try:
                return datetime.strptime(s[:10], "%Y-%m-%d").date()
            except Exception:
                return None

        def _candidate_requested_delta_days(it: dict[str, Any]) -> int | None:
            if not requested_date:
                return None
            req = _parse_date_yyyy_mm_dd(requested_date)
            if req is None:
                return None
            candidates: list[date] = []
            item_date = _parse_date_yyyy_mm_dd(str(it.get("flight_date") or "").strip())
            if item_date:
                candidates.append(item_date)
            dep_sched = _parse_date_yyyy_mm_dd(_dep_sched_raw(it))
            if dep_sched:
                candidates.append(dep_sched)
            if not candidates:
                return None
            return min(abs((d - req).days) for d in candidates)

        last_error = None
        non_exact_best: dict[str, Any] | None = None
        for url in ("https://api.aviationstack.com/v1/flights",):
            for params_extra in query_variants:
                params = {"access_key": self.access_key, **params_extra}
                try:
                    async with session.get(url, params=params, timeout=25) as resp:
                        payload = await resp.json(content_type=None)
                        retry_after = _parse_http_date_retry_after(resp.headers.get("Retry-After"))
                except Exception:
                    details = {"provider": "aviationstack", "state": "unknown", "error": "network"}
                    return FlightStatus(provider="aviationstack", state="unknown", details=details)

                if isinstance(payload, dict) and "error" in payload:
                    last_error = payload.get("error")
                    if isinstance(last_error, dict):
                        code = str(last_error.get("code") or last_error.get("type") or "")
                        msg = str(last_error.get("info") or last_error.get("message") or "")
                    else:
                        code = ""
                        msg = str(last_error)
                    err_type = _error_type(code, msg)
                    details = {
                        "provider": "aviationstack",
                        "state": "unknown",
                        "error": err_type,
                        "error_code": code or None,
                        "error_message": msg or str(last_error),
                    }
                    if isinstance(retry_after, int):
                        details["retry_after"] = retry_after
                    elif err_type == "rate_limited":
                        details["retry_after"] = 10
                    elif err_type == "quota_exceeded":
                        details["retry_after"] = 24 * 60 * 60
                    return FlightStatus(provider="aviationstack", state="unknown", details=details)
                if resp.status in (429, 402):
                    details = {"provider": "aviationstack", "state": "unknown", "error": "rate_limited"}
                    details["retry_after"] = retry_after if isinstance(retry_after, int) else 10
                    return FlightStatus(provider="aviationstack", state="unknown", details=details)
                    continue

                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, list) or not data:
                    continue

                exact = _pick_best(data, require_exact=bool(requested_date))
                if exact is not None:
                    details = _to_details(exact)
                    return FlightStatus(provider="aviationstack", state=details["state"], details=details)

                best_any = _pick_best(data, require_exact=False)
                if best_any is not None and non_exact_best is None:
                    non_exact_best = best_any

        # Schedule-specific fallback endpoints (airport-based) when exact date
        # match was not found in /flights.
        if requested_date:
            fallback_airports: list[str] = []
            if dep_airport:
                fallback_airports.append(dep_airport)
            if arr_airport and arr_airport not in fallback_airports:
                fallback_airports.append(arr_airport)
            if isinstance(non_exact_best, dict):
                d = _dep_iata(non_exact_best)
                a = _arr_iata(non_exact_best)
                if d and d not in fallback_airports:
                    fallback_airports.append(d)
                if a and a not in fallback_airports:
                    fallback_airports.append(a)
            req_date_obj = _parse_date_yyyy_mm_dd(requested_date)
            days_ahead = (req_date_obj - datetime.utcnow().date()).days if req_date_obj else None

            endpoint_order: list[str] = ["flight_schedules", "timetable", "flightsFuture"]
            if days_ahead is not None and days_ahead >= 1:
                endpoint_order = ["flightsFuture", "flight_schedules", "timetable"]

            for fallback_airport in fallback_airports:
                for ep in endpoint_order:
                    for url in (f"https://api.aviationstack.com/v1/{ep}",):
                        if ep.lower() in {"timetable", "flightsfuture", "flight_schedules"}:
                            # APILayer docs/changelog call out strict short-window limits.
                            await _wait_for_endpoint_window(self.hass, ep, min_interval_seconds=10)
                        params = {
                            "access_key": self.access_key,
                            "iataCode": str(fallback_airport).upper(),
                            "type": "departure",
                            "limit": 100,
                        }
                        if ep.lower() in {"timetable", "flight_schedules", "flightsfuture"}:
                            params["date"] = requested_date
                        try:
                            async with session.get(url, params=params, timeout=25) as resp:
                                payload = await resp.json(content_type=None)
                                retry_after = _parse_http_date_retry_after(resp.headers.get("Retry-After"))
                        except Exception:
                            continue

                        if isinstance(payload, dict) and "error" in payload:
                            err_obj = payload.get("error")
                            if isinstance(err_obj, dict):
                                code = str(err_obj.get("code") or err_obj.get("type") or "")
                                msg = str(err_obj.get("info") or err_obj.get("message") or "")
                            else:
                                code = ""
                                msg = str(err_obj)
                            err_type = _error_type(code, msg)
                            if err_type in {"rate_limited", "quota_exceeded", "auth_error", "plan_restricted"}:
                                if err_type == "rate_limited":
                                    _remember_endpoint_interval(
                                        self.hass,
                                        ep,
                                        retry_after if isinstance(retry_after, int) else 60,
                                    )
                                details = {
                                    "provider": "aviationstack",
                                    "state": "unknown",
                                    "error": err_type,
                                    "error_code": code or None,
                                    "error_message": msg or str(err_obj),
                                }
                                if isinstance(retry_after, int):
                                    details["retry_after"] = retry_after
                                elif err_type == "rate_limited":
                                    details["retry_after"] = 10
                                return FlightStatus(provider="aviationstack", state="unknown", details=details)
                            # method_not_supported / bad_request / validation: ignore endpoint
                            continue

                        data = payload.get("data") if isinstance(payload, dict) else None
                        if not isinstance(data, list) or not data:
                            continue
                        exact = _pick_best(data, require_exact=True)
                        if exact is not None:
                            details = _to_details(exact)
                            return FlightStatus(provider="aviationstack", state=details["state"], details=details)

        # Keep the best identity match even when date fields are slightly off
        # (common around midnight/timezone boundaries and in near-future feeds).
        if isinstance(non_exact_best, dict):
            delta = _candidate_requested_delta_days(non_exact_best)
            if delta is None or delta <= 1:
                details = _to_details(non_exact_best)
                details["matched_non_exact_date"] = True
                return FlightStatus(provider="aviationstack", state=details["state"], details=details)

        # No match. If API error existed, surface it as unknown status.
        if last_error:
            return FlightStatus(
                provider="aviationstack",
                state="unknown",
                details={"provider": "aviationstack", "state": "unknown", "error": last_error},
            )

        return None
