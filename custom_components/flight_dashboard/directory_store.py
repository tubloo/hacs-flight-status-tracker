"""Local cache storage for airlines/airports."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

DOMAIN = "flight_dashboard"


async def _load_store(hass: HomeAssistant, store_key: str) -> dict[str, Any]:
    store = Store(hass, 1, store_key)
    data = await store.async_load()
    return data or {}


async def _save_store(hass: HomeAssistant, store_key: str, data: dict[str, Any]) -> None:
    store = Store(hass, 1, store_key)
    await store.async_save(data)


def _is_stale(entry: dict[str, Any], ttl_days: int) -> bool:
    fetched_at = entry.get("fetched_at")
    if not isinstance(fetched_at, str):
        return True
    dt = dt_util.parse_datetime(fetched_at)
    if not dt:
        return True
    return (dt_util.utcnow() - dt_util.as_utc(dt)) > timedelta(days=ttl_days)


async def async_get_cached(
    hass: HomeAssistant,
    *,
    store_key: str,
    code: str,
    ttl_days: int,
) -> dict[str, Any] | None:
    data = await _load_store(hass, store_key)
    entry = data.get(code)
    if not isinstance(entry, dict):
        return None
    if _is_stale(entry, ttl_days):
        return None
    payload = entry.get("data")
    return payload if isinstance(payload, dict) else None


async def async_set_cached(
    hass: HomeAssistant,
    *,
    store_key: str,
    code: str,
    payload: dict[str, Any],
) -> None:
    data = await _load_store(hass, store_key)
    data[code] = {
        "fetched_at": dt_util.utcnow().isoformat(),
        "data": payload,
    }
    await _save_store(hass, store_key, data)
