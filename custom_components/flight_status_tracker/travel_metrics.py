"""Travel activity metrics tracking for saved flights."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from functools import partial
import math
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_TRAVEL_METRICS_UPDATED
from .directory import get_airport

_STORE_VERSION = 1
_STORE_KEY = f"{DOMAIN}.travel_metrics"
_SAVE_DEBOUNCE = timedelta(seconds=30)
_EARTH_RADIUS_KM = 6371.0


def _default_metrics() -> dict[str, Any]:
    return {
        "total_flights": 0,
        "total_distance_km": 0,
        "day_key": None,
        "daily_flights": 0,
        "daily_distance_km": 0,
        "month_key": None,
        "monthly_flights": 0,
        "monthly_distance_km": 0,
        "year_key": None,
        "yearly_flights": 0,
        "yearly_distance_km": 0,
        "updated_at": None,
        "last_added_flight_key": None,
    }


def _domain_data(hass: HomeAssistant) -> dict[str, Any]:
    return hass.data.setdefault(DOMAIN, {})


def _metrics_data(hass: HomeAssistant) -> dict[str, Any]:
    data = _domain_data(hass)
    metrics = data.get("travel_metrics")
    if not isinstance(metrics, dict):
        metrics = _default_metrics()
        data["travel_metrics"] = metrics
    else:
        defaults = _default_metrics()
        for key, value in defaults.items():
            if key not in metrics:
                metrics[key] = deepcopy(value)
    now = dt_util.utcnow()
    day_key = now.strftime("%Y-%m-%d")
    month_key = now.strftime("%Y-%m")
    year_key = now.strftime("%Y")
    if metrics.get("day_key") != day_key:
        metrics["day_key"] = day_key
        metrics["daily_flights"] = 0
        metrics["daily_distance_km"] = 0
    if metrics.get("month_key") != month_key:
        metrics["month_key"] = month_key
        metrics["monthly_flights"] = 0
        metrics["monthly_distance_km"] = 0
    if metrics.get("year_key") != year_key:
        metrics["year_key"] = year_key
        metrics["yearly_flights"] = 0
        metrics["yearly_distance_km"] = 0
    return metrics


async def async_init_travel_metrics(hass: HomeAssistant) -> None:
    data = _domain_data(hass)
    if data.get("travel_metrics_initialized"):
        return
    store = Store(hass, _STORE_VERSION, _STORE_KEY)
    loaded = await store.async_load()
    metrics = loaded if isinstance(loaded, dict) else _default_metrics()
    data["travel_metrics_store"] = store
    data["travel_metrics"] = metrics
    data["travel_metrics_initialized"] = True


async def _async_save_metrics(hass: HomeAssistant) -> None:
    data = _domain_data(hass)
    store = data.get("travel_metrics_store")
    metrics = data.get("travel_metrics")
    if not isinstance(store, Store) or not isinstance(metrics, dict):
        return
    await store.async_save(metrics)


def _schedule_save(hass: HomeAssistant) -> None:
    data = _domain_data(hass)
    if data.get("travel_metrics_save_unsub"):
        return

    @callback
    def _save_later(_now) -> None:
        data["travel_metrics_save_unsub"] = None
        hass.add_job(_async_save_metrics(hass))

    data["travel_metrics_save_unsub"] = async_call_later(
        hass,
        _SAVE_DEBOUNCE.total_seconds(),
        _save_later,
    )


async def async_flush_travel_metrics(hass: HomeAssistant) -> None:
    data = _domain_data(hass)
    unsub = data.get("travel_metrics_save_unsub")
    if unsub:
        try:
            unsub()
        except Exception:
            pass
        data["travel_metrics_save_unsub"] = None
    await _async_save_metrics(hass)


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return int(round(_EARTH_RADIUS_KM * c))


async def _resolve_airport_coords(
    hass: HomeAssistant,
    airport: dict[str, Any] | None,
) -> tuple[float | None, float | None]:
    data = dict(airport or {})
    lat = _to_float(data.get("lat"))
    lon = _to_float(data.get("lon"))
    if lat is not None and lon is not None:
        return lat, lon

    iata = str(data.get("iata") or "").strip().upper()
    if not iata:
        return None, None

    resolved = await get_airport(hass, {}, iata)
    if not isinstance(resolved, dict):
        return None, None
    return _to_float(resolved.get("lat")), _to_float(resolved.get("lon"))


async def _compute_distance_km(hass: HomeAssistant, flight: dict[str, Any]) -> int:
    dep_air = ((flight.get("dep") or {}).get("airport") or {})
    arr_air = ((flight.get("arr") or {}).get("airport") or {})
    dep_lat, dep_lon = await _resolve_airport_coords(hass, dep_air)
    arr_lat, arr_lon = await _resolve_airport_coords(hass, arr_air)
    if None in (dep_lat, dep_lon, arr_lat, arr_lon):
        return 0
    return _haversine_km(dep_lat, dep_lon, arr_lat, arr_lon)


async def async_record_saved_flight(hass: HomeAssistant, flight: dict[str, Any]) -> None:
    """Record a newly saved flight in aggregate activity metrics."""
    if not isinstance(flight, dict):
        return
    distance_km = await _compute_distance_km(hass, flight)
    flight_key = str(flight.get("flight_key") or "").strip() or None
    hass.add_job(partial(_record_saved_flight_on_loop, hass, distance_km=distance_km, flight_key=flight_key))


@callback
def _record_saved_flight_on_loop(
    hass: HomeAssistant,
    *,
    distance_km: int = 0,
    flight_key: str | None = None,
) -> None:
    metrics = _metrics_data(hass)
    now = dt_util.utcnow()
    day_key = now.strftime("%Y-%m-%d")
    month_key = now.strftime("%Y-%m")
    year_key = now.strftime("%Y")

    if metrics.get("day_key") != day_key:
        metrics["day_key"] = day_key
        metrics["daily_flights"] = 0
        metrics["daily_distance_km"] = 0
    if metrics.get("month_key") != month_key:
        metrics["month_key"] = month_key
        metrics["monthly_flights"] = 0
        metrics["monthly_distance_km"] = 0
    if metrics.get("year_key") != year_key:
        metrics["year_key"] = year_key
        metrics["yearly_flights"] = 0
        metrics["yearly_distance_km"] = 0

    metrics["total_flights"] = int(metrics.get("total_flights") or 0) + 1
    metrics["daily_flights"] = int(metrics.get("daily_flights") or 0) + 1
    metrics["monthly_flights"] = int(metrics.get("monthly_flights") or 0) + 1
    metrics["yearly_flights"] = int(metrics.get("yearly_flights") or 0) + 1

    distance_km = max(0, int(distance_km or 0))
    metrics["total_distance_km"] = int(metrics.get("total_distance_km") or 0) + distance_km
    metrics["daily_distance_km"] = int(metrics.get("daily_distance_km") or 0) + distance_km
    metrics["monthly_distance_km"] = int(metrics.get("monthly_distance_km") or 0) + distance_km
    metrics["yearly_distance_km"] = int(metrics.get("yearly_distance_km") or 0) + distance_km

    now_iso = now.isoformat()
    metrics["updated_at"] = now_iso
    metrics["last_added_flight_key"] = flight_key

    _schedule_save(hass)
    async_dispatcher_send(hass, SIGNAL_TRAVEL_METRICS_UPDATED)


def get_travel_metrics_snapshot(hass: HomeAssistant) -> dict[str, Any]:
    return deepcopy(_metrics_data(hass))
