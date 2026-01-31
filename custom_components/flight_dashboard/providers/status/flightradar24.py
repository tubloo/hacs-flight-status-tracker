"""Flightradar24 status provider.

Goal: return provider-agnostic status dict that your existing status_resolver.apply_status()
can merge into schema v3.

Returned keys include:
- provider: "flightradar24"
- state: scheduled|active|landed|cancelled|unknown  (best-effort)
- dep_actual, arr_actual (ISO8601 UTC when available)
- aircraft_type (when available, e.g. A20N/B788/etc.)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from ...fr24_client import FR24Client, FR24Error


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


@dataclass
class Flightradar24StatusProvider:
    hass: HomeAssistant
    api_key: str
    use_sandbox: bool = False
    api_version: str = "v1"

    async def async_get_status(self, flight: dict[str, Any]) -> dict[str, Any] | None:
        """Fetch flight status from FR24 and normalize fields."""
        airline = (flight.get("airline_code") or "").strip()
        fnum = (flight.get("flight_number") or "").strip()

        dep = flight.get("dep") or {}
        arr = flight.get("arr") or {}
        dep_iata = ((dep.get("airport") or {}).get("iata") or "").strip()
        arr_iata = ((arr.get("airport") or {}).get("iata") or "").strip()
        dep_sched_iso = dep.get("scheduled")

        if not airline or not fnum or not dep_sched_iso:
            return {"provider": "flightradar24", "error": "missing_required_fields"}

        try:
            dep_sched = datetime.fromisoformat(dep_sched_iso.replace("Z", "+00:00"))
            if dep_sched.tzinfo is None:
                dep_sched = dep_sched.replace(tzinfo=timezone.utc)
            dep_sched = dep_sched.astimezone(timezone.utc)
        except Exception:
            dep_sched = datetime.now(timezone.utc)

        # FR24 query window (wide to avoid TZ/day ambiguity)
        dt_from = (dep_sched - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S")
        dt_to = (dep_sched + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")

        client = FR24Client(
            self.hass, api_key=self.api_key, use_sandbox=self.use_sandbox, api_version=self.api_version
        )

        params = {
            "flight_datetime_from": dt_from,
            "flight_datetime_to": dt_to,
            "flights": f"{airline}{fnum}",  # e.g. AI157
        }

        try:
            data = await client.flight_summary_full(**params)
        except FR24Error as e:
            return {"provider": "flightradar24", "error": "http_error", "detail": str(e)}
        except Exception as e:
            return {"provider": "flightradar24", "error": "unknown_error", "detail": str(e)}

        rows = data.get("data") or data.get("result") or []
        if not isinstance(rows, list) or not rows:
            return {"provider": "flightradar24", "error": "no_match"}

        # Pick best row: prefer matching route when present
        best = None
        for r in rows:
            orig = (r.get("orig_iata") or r.get("origin_iata") or "").strip()
            dest = (r.get("dest_iata") or r.get("destination_iata") or "").strip()
            if dep_iata and arr_iata and orig == dep_iata and dest == arr_iata:
                best = r
                break
        if best is None:
            best = rows[0]

        takeoff = _parse_dt(best.get("datetime_takeoff"))
        landed = _parse_dt(best.get("datetime_landed"))

        # Best-effort state mapping
        if landed:
            state = "landed"
        elif takeoff:
            state = "active"
        else:
            # If it exists but hasn't taken off yet, treat as scheduled
            state = "scheduled"

        aircraft_type = best.get("type") or best.get("aircraft_type")  # often ICAO type

        # Optional: provider may include airport tz info in some plans; keep placeholders
        return {
            "provider": "flightradar24",
            "state": state,
            "dep_actual": _iso(takeoff),
            "arr_actual": _iso(landed),
            "aircraft_type": aircraft_type,
            "dep_iata": orig or None,
            "arr_iata": dest or None,
            "orig_iata": orig or None,
            "dest_iata": dest or None,
            "fr24_id": best.get("fr24_id"),
        }
