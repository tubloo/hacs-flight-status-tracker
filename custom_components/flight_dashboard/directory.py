"""Provider-agnostic directory resolver with local cache."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .directory_store import async_get_cached, async_set_cached
from .providers.directory.aviationstack import AviationstackDirectoryProvider
from .providers.directory.airlabs import AirLabsDirectoryProvider

AIRPORTS_STORE_KEY = "flight_dashboard.airports_cache"
AIRLINES_STORE_KEY = "flight_dashboard.airlines_cache"

DEFAULT_AIRPORT_TTL_DAYS = 180
DEFAULT_AIRLINE_TTL_DAYS = 180


def airline_logo_url(iata: str, *, w: int = 64, h: int = 64) -> str:
    code = (iata or "").strip().upper()
    # URL-based logos (works well for many airlines)
    return f"https://pics.avs.io/{w}/{h}/{code}.png"


def _build_providers(hass: HomeAssistant, options: dict[str, Any]):
    providers = []
    av_key = (options.get("aviationstack_access_key") or "").strip()
    al_key = (options.get("airlabs_api_key") or "").strip()

    if av_key:
        providers.append(AviationstackDirectoryProvider(hass, av_key))
    if al_key:
        providers.append(AirLabsDirectoryProvider(hass, al_key))
    return providers


async def async_get_airport(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    code = (iata or "").strip().upper()
    if not code:
        return None

    ttl = int(options.get("airport_ttl_days", DEFAULT_AIRPORT_TTL_DAYS))
    cached = await async_get_cached(hass, store_key=AIRPORTS_STORE_KEY, code=code, ttl_days=ttl)
    if cached:
        return cached

    for p in _build_providers(hass, options):
        try:
            a = await p.async_get_airport(code)
        except Exception:
            a = None
        if a:
            await async_set_cached(hass, store_key=AIRPORTS_STORE_KEY, code=code, payload=a)
            return a

    return None


async def async_get_airline(hass: HomeAssistant, options: dict[str, Any], iata: str) -> dict[str, Any] | None:
    code = (iata or "").strip().upper()
    if not code:
        return None

    ttl = int(options.get("airline_ttl_days", DEFAULT_AIRLINE_TTL_DAYS))
    cached = await async_get_cached(hass, store_key=AIRLINES_STORE_KEY, code=code, ttl_days=ttl)
    if cached:
        cached.setdefault("logo_url", airline_logo_url(code))
        return cached

    for p in _build_providers(hass, options):
        try:
            al = await p.async_get_airline(code)
        except Exception:
            al = None
        if al:
            al["logo_url"] = airline_logo_url(code)
            await async_set_cached(hass, store_key=AIRLINES_STORE_KEY, code=code, payload=al)
            return al

    # Still give a logo URL even if name lookup fails
    return {"iata": code, "logo_url": airline_logo_url(code), "source": "fallback"}
