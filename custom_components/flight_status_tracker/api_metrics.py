"""Lightweight API call metrics tracking for provider requests."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_API_METRICS_UPDATED

_STORE_VERSION = 1
_STORE_KEY = f"{DOMAIN}.api_metrics"
_SAVE_DEBOUNCE = timedelta(seconds=30)


def _normalize_provider(provider: str | None) -> str:
    p = (provider or "").strip().lower()
    if p == "fr24":
        return "flightradar24"
    return p or "unknown"


def _normalize_outcome(outcome: str | None) -> str:
    out = (outcome or "").strip().lower()
    return out or "unknown"


def _normalize_flow(flow: str | None) -> str:
    f = (flow or "").strip().lower()
    return f or "unknown"


def _default_provider_stats() -> dict[str, Any]:
    return {
        "total": 0,
        "status": 0,
        "schedule": 0,
        "position": 0,
        "directory": 0,
        "usage": 0,
        "other": 0,
        "success": 0,
        "error": 0,
        "rate_limited": 0,
        "quota_exceeded": 0,
        "timeout": 0,
        "network": 0,
        "auth_error": 0,
        "unknown": 0,
        "last_call_at": None,
    }


def _default_metrics() -> dict[str, Any]:
    return {
        "total_calls": 0,
        "providers": {},
        "updated_at": None,
    }


def _merge_loaded(data: dict[str, Any]) -> dict[str, Any]:
    out = _default_metrics()
    out["total_calls"] = int(data.get("total_calls") or 0)
    out["updated_at"] = data.get("updated_at")
    raw = data.get("providers")
    if isinstance(raw, dict):
        providers: dict[str, Any] = {}
        for name, stats in raw.items():
            if not isinstance(stats, dict):
                continue
            p = _default_provider_stats()
            for k in p:
                if k == "last_call_at":
                    p[k] = stats.get(k)
                else:
                    p[k] = int(stats.get(k) or 0)
            providers[str(name)] = p
        out["providers"] = providers
    return out


def _domain_data(hass: HomeAssistant) -> dict[str, Any]:
    return hass.data.setdefault(DOMAIN, {})


def _metrics_data(hass: HomeAssistant) -> dict[str, Any]:
    data = _domain_data(hass)
    metrics = data.get("api_metrics")
    if not isinstance(metrics, dict):
        metrics = _default_metrics()
        data["api_metrics"] = metrics
    return metrics


async def async_init_api_metrics(hass: HomeAssistant) -> None:
    """Initialize persisted API metrics."""
    data = _domain_data(hass)
    if data.get("api_metrics_initialized"):
        return
    store = Store(hass, _STORE_VERSION, _STORE_KEY)
    loaded = await store.async_load()
    metrics = _merge_loaded(loaded) if isinstance(loaded, dict) else _default_metrics()
    data["api_metrics_store"] = store
    data["api_metrics"] = metrics
    data["api_metrics_initialized"] = True


async def _async_save_metrics(hass: HomeAssistant) -> None:
    data = _domain_data(hass)
    store = data.get("api_metrics_store")
    metrics = data.get("api_metrics")
    if not isinstance(store, Store) or not isinstance(metrics, dict):
        return
    await store.async_save(metrics)


def _schedule_save(hass: HomeAssistant) -> None:
    data = _domain_data(hass)
    if data.get("api_metrics_save_unsub"):
        return

    async def _save_later(_now) -> None:
        data["api_metrics_save_unsub"] = None
        await _async_save_metrics(hass)

    data["api_metrics_save_unsub"] = async_call_later(
        hass,
        _SAVE_DEBOUNCE.total_seconds(),
        lambda now: hass.async_create_task(_save_later(now)),
    )


async def async_flush_api_metrics(hass: HomeAssistant) -> None:
    """Persist pending metric changes immediately."""
    data = _domain_data(hass)
    unsub = data.get("api_metrics_save_unsub")
    if unsub:
        try:
            unsub()
        except Exception:
            pass
        data["api_metrics_save_unsub"] = None
    await _async_save_metrics(hass)


def record_api_call(
    hass: HomeAssistant,
    provider: str | None,
    *,
    flow: str = "other",
    outcome: str | None = "success",
) -> None:
    """Record a provider API call."""
    metrics = _metrics_data(hass)
    providers = metrics.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        metrics["providers"] = providers

    provider_key = _normalize_provider(provider)
    flow_key = _normalize_flow(flow)
    outcome_key = _normalize_outcome(outcome)

    stats = providers.get(provider_key)
    if not isinstance(stats, dict):
        stats = _default_provider_stats()
        providers[provider_key] = stats

    metrics["total_calls"] = int(metrics.get("total_calls") or 0) + 1
    stats["total"] = int(stats.get("total") or 0) + 1

    if flow_key in ("status", "schedule", "position", "directory", "usage"):
        stats[flow_key] = int(stats.get(flow_key) or 0) + 1
    else:
        stats["other"] = int(stats.get("other") or 0) + 1

    if outcome_key in ("success", "error", "rate_limited", "quota_exceeded", "timeout", "network", "auth_error"):
        stats[outcome_key] = int(stats.get(outcome_key) or 0) + 1
    else:
        stats["unknown"] = int(stats.get("unknown") or 0) + 1

    now_iso = dt_util.utcnow().isoformat()
    stats["last_call_at"] = now_iso
    metrics["updated_at"] = now_iso

    _schedule_save(hass)
    async_dispatcher_send(hass, SIGNAL_API_METRICS_UPDATED)


def get_api_metrics_snapshot(hass: HomeAssistant) -> dict[str, Any]:
    """Return a safe copy for sensor attributes."""
    return deepcopy(_metrics_data(hass))
