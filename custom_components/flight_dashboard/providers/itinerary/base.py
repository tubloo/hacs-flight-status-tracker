"""Base classes for itinerary providers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Any


@dataclass(frozen=True)
class FlightSegment:
    """Provider-agnostic normalized flight segment.

    NOTE: This is a *segment* (one flight leg), before aggregation/merging.
    """
    source: str                 # e.g. "manual", "tripit"
    source_uid: str             # stable id within that provider
    travellers: tuple[str, ...] # people travelling on this segment

    airline_code: str           # IATA preferred
    flight_number: str
    dep_airport: str            # IATA
    arr_airport: str            # IATA

    scheduled_departure: datetime  # tz-aware recommended
    scheduled_arrival: datetime | None = None

    notes: str | None = None
    raw: dict[str, Any] | None = None


class ItineraryProvider(Protocol):
    """Interface for any itinerary provider."""

    @property
    def name(self) -> str: ...

    async def async_get_segments(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> list[FlightSegment]:
        """Return normalized segments within [start, end]."""
        raise NotImplementedError
