"""TripIt itinerary provider (minimal stub)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class TripItItineraryProvider:
    def __init__(self, hass: HomeAssistant, options: dict[str, Any]) -> None:
        self.hass = hass
        self.options = options

    async def async_get_segments(self, start_utc: datetime, end_utc: datetime) -> list[dict[str, Any]]:
        """Return flight segments from TripIt within the time window.

        TODO: Implement full TripIt ingestion. This stub intentionally returns no segments
        if required credentials are missing.
        """
        consumer_key = (self.options.get("tripit_consumer_key") or "").strip()
        consumer_secret = (self.options.get("tripit_consumer_secret") or "").strip()
        access_token = (self.options.get("tripit_access_token") or "").strip()
        access_token_secret = (self.options.get("tripit_access_token_secret") or "").strip()

        if not (consumer_key and consumer_secret and access_token and access_token_secret):
            return []

        _LOGGER.debug("TripIt provider configured but ingestion not yet implemented.")
        return []
