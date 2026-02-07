"""Directory cache storage for airports and airlines."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY_DIRECTORY

_STORE_VERSION = 1


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except Exception:
        return None


async def _store(hass: HomeAssistant) -> Store:
    return Store(hass, _STORE_VERSION, STORAGE_KEY_DIRECTORY)


async def async_load_cache(hass: HomeAssistant) -> dict[str, Any]:
    st = await _store(hass)
    data = await st.async_load() or {}
    if not isinstance(data, dict):
        return {"airports": {}, "airlines": {}, "meta": {}}
    data.setdefault("airports", {})
    data.setdefault("airlines", {})
    data.setdefault("meta", {})
    return data


async def async_save_cache(hass: HomeAssistant, cache: dict[str, Any]) -> None:
    st = await _store(hass)
    await st.async_save(cache)


async def async_get_airport(hass: HomeAssistant, iata: str) -> dict[str, Any] | None:
    cache = await async_load_cache(hass)
    return cache.get("airports", {}).get(iata)


async def async_get_airline(hass: HomeAssistant, iata: str) -> dict[str, Any] | None:
    cache = await async_load_cache(hass)
    return cache.get("airlines", {}).get(iata)


async def async_set_airport(hass: HomeAssistant, iata: str, data: dict[str, Any]) -> None:
    cache = await async_load_cache(hass)
    airports = cache.setdefault("airports", {})
    airports[iata] = {**data, "fetched_at": _utcnow_iso()}
    await async_save_cache(hass, cache)


async def async_set_airline(hass: HomeAssistant, iata: str, data: dict[str, Any]) -> None:
    cache = await async_load_cache(hass)
    airlines = cache.setdefault("airlines", {})
    # ensure a stable logo_url is cached for local lookups
    logo = data.get("logo") or data.get("logo_url") or data.get("airline_logo_url")
    if not logo:
        code = str(iata).strip().upper()
        if code:
            logo = f"https://pics.avs.io/64/64/{code}.png"
    airlines[iata] = {**data, "logo_url": logo, "fetched_at": _utcnow_iso()}
    await async_save_cache(hass, cache)


def is_fresh(entry: dict[str, Any] | None, ttl_days: int) -> bool:
    if not entry:
        return False
    fetched_at = entry.get("fetched_at")
    dt = _parse_dt(fetched_at) if isinstance(fetched_at, str) else None
    if not dt:
        return False
    age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    return age.total_seconds() <= ttl_days * 86400


async def async_is_initialized(hass: HomeAssistant) -> bool:
    cache = await async_load_cache(hass)
    meta = cache.get("meta") or {}
    return bool(meta.get("initialized_at"))


async def async_mark_initialized(hass: HomeAssistant) -> None:
    cache = await async_load_cache(hass)
    meta = cache.setdefault("meta", {})
    meta["initialized_at"] = _utcnow_iso()
    await async_save_cache(hass, cache)
