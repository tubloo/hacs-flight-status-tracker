"""Mock status provider using local fixtures (for testing)."""
from __future__ import annotations

import json
from typing import Any

from importlib import resources

from .base import FlightStatus


def _load_fixtures() -> dict[str, Any]:
    data = resources.files("custom_components.flight_dashboard.fixtures").joinpath("mock_flights.json").read_text()
    return json.loads(data)


def _key(airline_code: str, flight_number: str, date: str) -> str:
    return f"{airline_code}{flight_number}|{date}"


class MockStatusProvider:
    """Return canned status data from fixtures."""

    async def async_get_status(self, flight: dict[str, Any]) -> FlightStatus | None:
        airline = (flight.get("airline_code") or "").strip().upper()
        number = str(flight.get("flight_number") or "").strip()
        dep_sched = (flight.get("dep") or {}).get("scheduled") or flight.get("scheduled_departure") or ""
        date = str(dep_sched)[:10] if dep_sched else ""

        if not airline or not number or not date:
            return None

        fixtures = _load_fixtures()
        rec = fixtures.get(_key(airline, number, date))
        if not isinstance(rec, dict):
            return None

        details = {
            "provider": "mock",
            "state": rec.get("status_state") or "scheduled",
            "dep_scheduled": (rec.get("dep") or {}).get("scheduled"),
            "arr_scheduled": (rec.get("arr") or {}).get("scheduled"),
            "dep_estimated": (rec.get("dep") or {}).get("estimated"),
            "arr_estimated": (rec.get("arr") or {}).get("estimated"),
            "dep_actual": (rec.get("dep") or {}).get("actual"),
            "arr_actual": (rec.get("arr") or {}).get("actual"),
            "terminal_dep": (rec.get("dep") or {}).get("terminal"),
            "gate_dep": (rec.get("dep") or {}).get("gate"),
            "terminal_arr": (rec.get("arr") or {}).get("terminal"),
            "gate_arr": (rec.get("arr") or {}).get("gate"),
            "airline_name": rec.get("airline_name"),
            "airline_logo_url": rec.get("airline_logo_url"),
            "aircraft_type": rec.get("aircraft_type"),
            "dep_airport_name": ((rec.get("dep") or {}).get("airport") or {}).get("name"),
            "dep_airport_city": ((rec.get("dep") or {}).get("airport") or {}).get("city"),
            "arr_airport_name": ((rec.get("arr") or {}).get("airport") or {}).get("name"),
            "arr_airport_city": ((rec.get("arr") or {}).get("airport") or {}).get("city"),
            "dep_tz": ((rec.get("dep") or {}).get("airport") or {}).get("tz"),
            "arr_tz": ((rec.get("arr") or {}).get("airport") or {}).get("tz"),
        }

        return FlightStatus(provider="mock", state=details["state"], details=details)
