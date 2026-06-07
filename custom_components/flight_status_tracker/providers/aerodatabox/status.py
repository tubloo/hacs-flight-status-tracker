"""AeroDataBox status/schedule provider.

Supports both marketplace gateways:
- RapidAPI
- API.Market
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import random
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .._shared.status_base import FlightStatus

_LOGGER = logging.getLogger(__name__)

_MAX_RATE_LIMIT_RETRIES = 2


class AeroDataBoxStatusProvider:
    def __init__(
        self,
        hass: HomeAssistant,
        *,
        gateway: str = "rapidapi",
        rapidapi_key: str = "",
        apimarket_key: str = "",
    ) -> None:
        self.hass = hass
        self.gateway = (gateway or "rapidapi").strip().lower()
        self.rapidapi_key = (rapidapi_key or "").strip()
        self.apimarket_key = (apimarket_key or "").strip()

    def _base_url_and_headers(self) -> tuple[str, dict[str, str]]:
        if self.gateway == "apimarket":
            return (
                "https://prod.api.market/api/v1/aedbx/aerodatabox",
                {"x-api-market-key": self.apimarket_key},
            )
        return (
            "https://aerodatabox.p.rapidapi.com",
            {
                "X-RapidAPI-Key": self.rapidapi_key,
                "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com",
            },
        )

    @staticmethod
    def _pick_first(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
        for key in keys:
            val = d.get(key)
            if val not in (None, "", []):
                return val
        return None

    @classmethod
    def _pick_time_any(cls, block: dict[str, Any], keys: tuple[str, ...]) -> str | None:
        """Pick first available time key as UTC/local ISO string."""
        for key in keys:
            val = block.get(key)
            if isinstance(val, dict):
                got = cls._get_utc(val) or cls._get_local(val)
                if got:
                    return got
            elif isinstance(val, str) and val.strip():
                return val.strip()
        return None

    @staticmethod
    def _get_utc(dt_obj: dict[str, Any] | None) -> str | None:
        if not isinstance(dt_obj, dict):
            return None
        val = dt_obj.get("utc")
        return val if isinstance(val, str) and val else None

    @staticmethod
    def _get_local(dt_obj: dict[str, Any] | None) -> str | None:
        if not isinstance(dt_obj, dict):
            return None
        val = dt_obj.get("local")
        return val if isinstance(val, str) and val else None

    @staticmethod
    def _norm_state(status: str | None) -> str:
        if not status:
            return "Unknown"
        # Keep AeroDataBox enum text to normalize downstream in status_resolver.
        # Example: EnRoute, Arrived, Canceled...
        return str(status).strip() or "Unknown"

    @staticmethod
    def _search_param(airline_code: str, flight_number: str) -> str:
        return f"{airline_code}{flight_number}".replace(" ", "").upper()

    @staticmethod
    def _parse_iso(val: str | None) -> datetime | None:
        if not isinstance(val, str) or not val:
            return None
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except Exception:
            return None

    @classmethod
    def _status_priority(cls, item: dict[str, Any]) -> int:
        status = str(item.get("status") or "").strip().lower()
        if status in ("arrived", "landed"):
            return 0
        if status in ("enroute", "en route", "active", "airborne", "departed", "cruising", "approaching"):
            return 1
        if status in ("delayed", "expected"):
            return 2
        if status in ("boarding", "gateclosed", "gate_closed", "gate closed", "checkin", "check-in"):
            return 3
        if status in ("scheduled", "unknown", ""):
            return 4
        return 5

    @classmethod
    def _latest_signal_dt(cls, item: dict[str, Any]) -> datetime | None:
        dep = item.get("departure") or {}
        arr = item.get("arrival") or {}
        candidates = [
            (((arr.get("runwayTime") or {}).get("utc")) if isinstance(arr.get("runwayTime"), dict) else None),
            (((arr.get("predictedTime") or {}).get("utc")) if isinstance(arr.get("predictedTime"), dict) else None),
            (((arr.get("revisedTime") or {}).get("utc")) if isinstance(arr.get("revisedTime"), dict) else None),
            (((dep.get("runwayTime") or {}).get("utc")) if isinstance(dep.get("runwayTime"), dict) else None),
            (((dep.get("predictedTime") or {}).get("utc")) if isinstance(dep.get("predictedTime"), dict) else None),
            (((dep.get("revisedTime") or {}).get("utc")) if isinstance(dep.get("revisedTime"), dict) else None),
            item.get("lastUpdatedUtc"),
        ]
        for candidate in candidates:
            dt = cls._parse_iso(candidate if isinstance(candidate, str) else None)
            if dt is not None:
                return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return None

    @classmethod
    def _date_yyyy_mm_dd(cls, flight: dict[str, Any]) -> str | None:
        dep = flight.get("dep") or {}
        local = dep.get("scheduled_local")
        if isinstance(local, str) and len(local) >= 10:
            return local[:10]

        sched = dep.get("scheduled")
        dt = cls._parse_iso(sched if isinstance(sched, str) else None)
        if dt is None:
            return None

        # AeroDataBox expects departure LOCAL date. If timezone is known, convert first.
        dep_air = dep.get("airport") or {}
        tzname = dep_air.get("tz")
        if isinstance(tzname, str) and tzname.strip():
            try:
                return dt.astimezone(ZoneInfo(tzname.strip())).date().isoformat()
            except Exception:
                pass
        return dt.date().isoformat()

    @classmethod
    def _requested_dep_utc(cls, flight: dict[str, Any]) -> datetime | None:
        dep = flight.get("dep") or {}
        dt = cls._parse_iso(dep.get("scheduled") if isinstance(dep.get("scheduled"), str) else None)
        if dt is not None:
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        local = dep.get("scheduled_local")
        dep_air = dep.get("airport") or {}
        tzname = dep_air.get("tz")
        if isinstance(local, str) and isinstance(tzname, str) and tzname.strip():
            raw = cls._parse_iso(local)
            if raw is not None:
                try:
                    if raw.tzinfo is None:
                        raw = raw.replace(tzinfo=ZoneInfo(tzname.strip()))
                    return raw.astimezone(timezone.utc)
                except Exception:
                    return None
        return None

    @classmethod
    def _pick_best(
        cls,
        items: list[dict[str, Any]],
        dep_filter: str | None,
        arr_filter: str | None,
        requested_dep_utc: datetime | None,
    ) -> dict[str, Any] | None:
        def iata(m: dict[str, Any], key: str) -> str:
            obj = ((m.get(key) or {}).get("airport") or {})
            v = obj.get("iata")
            return str(v or "").strip().upper()

        filtered = [
            it
            for it in items
            if (not dep_filter or iata(it, "departure") == dep_filter)
            and (not arr_filter or iata(it, "arrival") == arr_filter)
        ]
        pool = filtered if filtered else items

        def sort_key(it: dict[str, Any]) -> tuple[int, int, float, str]:
            dep_sched = (((it.get("departure") or {}).get("scheduledTime") or {}).get("utc") or "").strip()
            status_rank = cls._status_priority(it)
            dep_dt = AeroDataBoxStatusProvider._parse_iso(dep_sched)
            if dep_dt is not None:
                dep_dt = dep_dt.astimezone(timezone.utc) if dep_dt.tzinfo else dep_dt.replace(tzinfo=timezone.utc)
            # Prefer smallest delta to requested departure when available.
            if requested_dep_utc and dep_dt:
                delta_sec = abs((dep_dt - requested_dep_utc).total_seconds())
            else:
                delta_sec = float("inf")
            signal_dt = cls._latest_signal_dt(it)
            latest_rank = 0.0
            if signal_dt is not None:
                latest_rank = -signal_dt.timestamp()
            # Prefer newer sched if all else equal.
            has_sched = 0 if dep_dt else 1
            return (status_rank, has_sched, delta_sec, latest_rank, dep_sched)

        return sorted(pool, key=sort_key)[0] if pool else None

    async def async_get_status(self, flight: dict[str, Any]) -> FlightStatus | None:
        airline = str(flight.get("airline_code") or "").strip().upper()
        number = str(flight.get("flight_number") or "").strip()
        if not airline or not number:
            return None

        date_local = self._date_yyyy_mm_dd(flight)
        if not date_local:
            return None

        dep_filter = str((((flight.get("dep") or {}).get("airport") or {}).get("iata") or "")).strip().upper() or None
        arr_filter = str((((flight.get("arr") or {}).get("airport") or {}).get("iata") or "")).strip().upper() or None

        base_url, headers = self._base_url_and_headers()
        if not any(headers.values()):
            return FlightStatus(provider="aerodatabox", state="unknown", details={"provider": "aerodatabox", "error": "auth_error"})

        url = f"{base_url}/flights/Number/{self._search_param(airline, number)}/{date_local}"
        # For flight-number lookups, use departure-local date role.
        params = {"withLocation": "true", "withAircraftImage": "true", "dateLocalRole": "Departure"}
        session = async_get_clientsession(self.hass)
        payload, status_code, retry_after, req_err = await self._request_json(
            session=session,
            url=url,
            headers=headers,
            params=params,
            log_key=f"{airline}{number} on {date_local}",
        )
        if req_err:
            return FlightStatus(provider="aerodatabox", state="unknown", details={"provider": "aerodatabox", "error": req_err})

        if status_code is None:
            return FlightStatus(provider="aerodatabox", state="unknown", details={"provider": "aerodatabox", "error": "provider_error"})

        if status_code >= 400:
            err = "provider_error"
            if status_code in (401, 403):
                err = "auth_error"
            elif status_code == 429:
                err = "rate_limited"
            details: dict[str, Any] = {"provider": "aerodatabox", "error": err, "status_code": status_code}
            if retry_after and retry_after.isdigit():
                details["retry_after"] = int(retry_after)
            return FlightStatus(provider="aerodatabox", state="unknown", details=details)

        payload_items = [x for x in payload if isinstance(x, dict)] if isinstance(payload, list) else []
        req_dep_utc = self._requested_dep_utc(flight)
        best = self._pick_best(payload_items, dep_filter, arr_filter, req_dep_utc) if payload_items else None

        # Fallback: airport-window lookup for the day to recover cases where number/day lookup misses.
        if not best and dep_filter:
            airport_url = f"{base_url}/flights/airports/iata/{dep_filter}/{date_local}/{date_local}"
            airport_payload, airport_status, _, airport_err = await self._request_json(
                session=session,
                url=airport_url,
                headers=headers,
                params={"withLocation": "true", "withAircraftImage": "true"},
                log_key=f"airport {dep_filter} on {date_local}",
            )
            if airport_err:
                _LOGGER.debug("AeroDataBox airport fallback error: %s", airport_err)
            elif airport_status and airport_status < 400 and isinstance(airport_payload, list):
                flight_num_norm = self._search_param(airline, number)
                matches: list[dict[str, Any]] = []
                for item in airport_payload:
                    if not isinstance(item, dict):
                        continue
                    num = str(item.get("number") or "").replace(" ", "").upper()
                    if num != flight_num_norm:
                        continue
                    arr_iata_item = ((((item.get("arrival") or {}).get("airport") or {}).get("iata") or "")).strip().upper()
                    if arr_filter and arr_iata_item and arr_iata_item != arr_filter:
                        continue
                    matches.append(item)
                if matches:
                    best = self._pick_best(matches, dep_filter, arr_filter, req_dep_utc)

        if not best:
            return None

        dep = best.get("departure") or {}
        arr = best.get("arrival") or {}
        dep_air = dep.get("airport") or {}
        arr_air = arr.get("airport") or {}
        aircraft = best.get("aircraft") or {}
        airline_obj = best.get("airline") or {}
        loc = best.get("location") or {}

        pos = None
        if isinstance(loc, dict) and loc.get("lat") is not None and loc.get("lon") is not None:
            pos = {
                "lat": loc.get("lat"),
                "lon": loc.get("lon"),
                "timestamp": loc.get("reportedAtUtc"),
                "altitude_ft": ((loc.get("altitude") or {}).get("feet") if isinstance(loc.get("altitude"), dict) else None),
                "ground_speed_kt": ((loc.get("groundSpeed") or {}).get("kts") if isinstance(loc.get("groundSpeed"), dict) else None),
                "heading_deg": (loc.get("trueTrack") or {}).get("deg") if isinstance(loc.get("trueTrack"), dict) else None,
                "vertical_speed_fpm": loc.get("vsiFpm"),
                "provider": "aerodatabox",
                "source": "aerodatabox",
            }

        details = {
            "provider": "aerodatabox",
            "state": self._norm_state(best.get("status")),
            "provider_state": best.get("status"),
            "dep_iata": dep_air.get("iata"),
            "arr_iata": arr_air.get("iata"),
            "dep_airport_name": dep_air.get("name"),
            "dep_airport_city": dep_air.get("municipalityName"),
            "arr_airport_name": arr_air.get("name"),
            "arr_airport_city": arr_air.get("municipalityName"),
            "dep_tz": dep_air.get("timeZone"),
            "arr_tz": arr_air.get("timeZone"),
            "dep_scheduled": self._get_utc(dep.get("scheduledTime")),
            "dep_scheduled_local": self._get_local(dep.get("scheduledTime")),
            # AeroDataBox-only preference: predicted time is the best live estimate;
            # fall back to revised time if predicted is unavailable.
            "dep_estimated": self._get_utc(dep.get("predictedTime")) or self._get_utc(dep.get("revisedTime")),
            "dep_actual": self._get_utc(dep.get("runwayTime")),
            "arr_scheduled": self._get_utc(arr.get("scheduledTime")),
            "arr_scheduled_local": self._get_local(arr.get("scheduledTime")),
            "arr_estimated": self._get_utc(arr.get("predictedTime")) or self._get_utc(arr.get("revisedTime")),
            "arr_actual": self._get_utc(arr.get("runwayTime")),
            "terminal_dep": dep.get("terminal"),
            "gate_dep": dep.get("gate"),
            "terminal_arr": arr.get("terminal"),
            "gate_arr": arr.get("gate"),
            "dep_off_block_time": self._pick_time_any(dep, ("offBlockTime", "outGateTime", "outTime")),
            "dep_takeoff_time": self._pick_time_any(dep, ("takeOffTime", "runwayTime", "offTime")),
            "arr_landing_time": self._pick_time_any(arr, ("landingTime", "runwayTime", "onTime")),
            "arr_on_block_time": self._pick_time_any(arr, ("onBlockTime", "inGateTime", "inTime")),
            "dep_check_in_counters": self._pick_first(dep, ("checkInDesk", "checkInDesks", "checkInCounter", "checkInCounters")),
            "dep_boarding_time": self._get_utc(dep.get("boardingTime")) or self._get_local(dep.get("boardingTime")),
            "dep_door_time": self._get_utc(dep.get("doorTime")) or self._get_local(dep.get("doorTime")),
            "arr_baggage_claim": self._pick_first(arr, ("baggageClaim", "baggageBelt", "baggageBelts")),
            "arr_belt": self._pick_first(arr, ("baggageBelt", "belt", "baggageBelts")),
            "airline_name": airline_obj.get("name"),
            "airline_code": airline_obj.get("iata") or airline,
            "aircraft_type": aircraft.get("model"),
            "aircraft_image_url": (
                ((aircraft.get("image") or {}).get("url") if isinstance(aircraft.get("image"), dict) else None)
                or aircraft.get("imageUrl")
                or aircraft.get("photoUrl")
            ),
            "airline_logo_url": None,
            "last_updated": best.get("lastUpdatedUtc") or datetime.utcnow().isoformat(),
        }
        if pos:
            details["position"] = {k: v for k, v in pos.items() if v is not None}
        return FlightStatus(provider="aerodatabox", state=details["state"], details=details)

    @staticmethod
    def _retry_wait_seconds(retry_after: str | None, attempt: int) -> float:
        if isinstance(retry_after, str) and retry_after.strip():
            raw = retry_after.strip()
            if raw.isdigit():
                return max(0.2, float(raw))
            try:
                retry_dt = parsedate_to_datetime(raw)
                if retry_dt.tzinfo is None:
                    retry_dt = retry_dt.replace(tzinfo=timezone.utc)
                delta = (retry_dt - datetime.now(timezone.utc)).total_seconds()
                if delta > 0:
                    return max(0.2, delta)
            except Exception:
                pass
        # 1.5s, 3.0s with 0-0.4s jitter.
        return (1.5 * (2**attempt)) + random.uniform(0.0, 0.4)

    async def _request_json(
        self,
        *,
        session: aiohttp.ClientSession,
        url: str,
        headers: dict[str, str],
        params: dict[str, str],
        log_key: str,
    ) -> tuple[Any, int | None, str | None, str | None]:
        payload: Any = None
        retry_after: str | None = None
        status_code: int | None = None
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            try:
                async with session.get(url, headers=headers, params=params, timeout=25) as resp:
                    if resp.status == 204:
                        return None, 204, None, None
                    payload = await resp.json(content_type=None)
                    retry_after = resp.headers.get("Retry-After")
                    status_code = resp.status
            except aiohttp.ClientError:
                return None, None, None, "network"
            except TimeoutError:
                return None, None, None, "timeout"

            if status_code != 429 or attempt >= _MAX_RATE_LIMIT_RETRIES:
                break
            wait_s = self._retry_wait_seconds(retry_after, attempt)
            _LOGGER.warning(
                "AeroDataBox 429 for %s, retrying in %.2fs (attempt %s/%s)",
                log_key,
                wait_s,
                attempt + 1,
                _MAX_RATE_LIMIT_RETRIES,
            )
            await asyncio.sleep(wait_s)
        return payload, status_code, retry_after, None
