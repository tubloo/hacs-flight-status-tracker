"""Flightradar24 API client (official FR24 API).

Auth: Bearer token
Base URL (default): https://fr24api.flightradar24.com

Sandbox:
FR24 sandbox uses endpoints prefixed with /sandbox, i.e. /sandbox/api/...
We support a boolean use_sandbox that rewrites /api/... accordingly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession


DEFAULT_BASE_URL = "https://fr24api.flightradar24.com"


class FR24Error(Exception):
    """FR24 API error."""


class FR24RateLimitError(FR24Error):
    """FR24 rate limit or quota error."""

    def __init__(self, status: int, retry_after: int | None = None) -> None:
        super().__init__(f"HTTP {status}: rate_limited")
        self.status = status
        self.retry_after = retry_after


@dataclass
class FR24Client:
    hass: HomeAssistant
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    use_sandbox: bool = False
    api_version: str = "v1"

    def _url(self, path: str) -> str:
        base = self.base_url.rstrip("/")
        p = path if path.startswith("/") else f"/{path}"

        # Rewrite /api/... -> /sandbox/api/... when sandbox enabled
        if self.use_sandbox and p.startswith("/api/"):
            p = "/sandbox" + p

        return base + p

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        session = async_get_clientsession(self.hass)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept-Version": self.api_version,
        }
        url = self._url(path)

        async with session.get(url, headers=headers, params=params, timeout=30) as resp:
            text = await resp.text()
            if resp.status >= 400:
                if resp.status in (402, 429):
                    retry_after = None
                    ra = resp.headers.get("Retry-After")
                    if ra and ra.isdigit():
                        retry_after = int(ra)
                    raise FR24RateLimitError(resp.status, retry_after)
                raise FR24Error(f"HTTP {resp.status}: {text[:300]}")
            return await resp.json()

    async def flight_summary_full(self, **params: Any) -> dict[str, Any]:
        return await self._get("/api/flight-summary/full", params=params)

    async def flight_summary_light(self, **params: Any) -> dict[str, Any]:
        return await self._get("/api/flight-summary/light", params=params)

    async def live_flight_positions_light(self, **params: Any) -> dict[str, Any]:
        return await self._get("/api/live/flight-positions/light", params=params)

    async def airport_full(self, code: str) -> dict[str, Any]:
        return await self._get(f"/api/static/airports/{code}/full")

    async def airport_light(self, code: str) -> dict[str, Any]:
        return await self._get(f"/api/static/airports/{code}/light")

    async def airline_light_by_icao(self, icao: str) -> dict[str, Any]:
        return await self._get(f"/api/static/airlines/{icao}/light")

    async def usage(self) -> dict[str, Any]:
        return await self._get("/api/usage")
