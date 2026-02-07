"""Status provider base types."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class FlightStatus:
    provider: str
    state: str  # scheduled|active|landed|cancelled|unknown
    details: dict[str, Any]


class StatusProvider(Protocol):
    async def async_get_status(self, flight: dict[str, Any]) -> FlightStatus | None:
        """Return status for a flight dict, or None if not available."""
