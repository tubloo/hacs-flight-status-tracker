"""Local (no external API) status provider."""
from __future__ import annotations

from datetime import datetime, timezone

from .base import FlightStatus


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class LocalStatusProvider:
    @property
    def name(self) -> str:
        return "local"

    async def async_get_status(
        self,
        *,
        flight_key: str,
        airline_code: str,
        flight_number: str,
        dep_airport: str,
        arr_airport: str,
        scheduled_departure: datetime,
        scheduled_arrival: datetime | None,
        now: datetime,
        extra=None,
    ) -> FlightStatus:
        now_u = _utc(now)
        dep_u = _utc(scheduled_departure)
        arr_u = _utc(scheduled_arrival) if scheduled_arrival else None

        if now_u < dep_u:
            phase = "upcoming"
            status = "scheduled"
        elif arr_u and now_u < arr_u:
            phase = "in_air"
            status = "active"
        elif arr_u and now_u >= arr_u:
            phase = "arrived"
            status = "landed"
        else:
            phase = "unknown"
            status = "unknown"

        details = {
            "provider": "local",
            "state": status,
            "phase": phase,
            "dep_scheduled": scheduled_departure.isoformat(),
            "arr_scheduled": scheduled_arrival.isoformat() if scheduled_arrival else None,
            "last_updated": now.isoformat(),
        }
        return FlightStatus(provider="local", state=status, details=details)
