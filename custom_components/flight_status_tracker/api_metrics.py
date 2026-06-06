"""Lightweight API call metrics tracking for provider requests."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from functools import partial
from typing import Any

from homeassistant.core import HomeAssistant, callback
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
        "day_key": None,
        "daily_calls": 0,
        "daily_by_provider": {},
        "month_key": None,
        "monthly_calls": 0,
        "monthly_by_provider": {},
        "year_key": None,
        "yearly_calls": 0,
        "yearly_by_provider": {},
        "providers": {},
        "updated_at": None,
    }


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _self_heal_metrics(metrics: dict[str, Any], now=None) -> bool:
    """Normalize persisted counters into a consistent, monotonic shape."""
    changed = False
    now = now or dt_util.utcnow()
    today_key = now.strftime("%Y-%m-%d")
    this_month_key = now.strftime("%Y-%m")
    this_year_key = now.strftime("%Y")

    providers = metrics.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        metrics["providers"] = providers
        changed = True

    provider_total_sum = 0
    for provider, stats in list(providers.items()):
        if not isinstance(stats, dict):
            providers[provider] = _default_provider_stats()
            stats = providers[provider]
            changed = True
        provider_total_sum += _as_int(stats.get("total"))

    total_calls = _as_int(metrics.get("total_calls"))
    if total_calls < provider_total_sum:
        metrics["total_calls"] = provider_total_sum
        total_calls = provider_total_sum
        changed = True

    daily_by_provider = metrics.get("daily_by_provider")
    if not isinstance(daily_by_provider, dict):
        daily_by_provider = {}
        metrics["daily_by_provider"] = daily_by_provider
        changed = True
    monthly_by_provider = metrics.get("monthly_by_provider")
    if not isinstance(monthly_by_provider, dict):
        monthly_by_provider = {}
        metrics["monthly_by_provider"] = monthly_by_provider
        changed = True
    yearly_by_provider = metrics.get("yearly_by_provider")
    if not isinstance(yearly_by_provider, dict):
        yearly_by_provider = {}
        metrics["yearly_by_provider"] = yearly_by_provider
        changed = True

    daily_calls = max(_as_int(metrics.get("daily_calls")), sum(_as_int(v) for v in daily_by_provider.values()))
    monthly_calls = max(_as_int(metrics.get("monthly_calls")), sum(_as_int(v) for v in monthly_by_provider.values()))
    yearly_calls = max(_as_int(metrics.get("yearly_calls")), sum(_as_int(v) for v in yearly_by_provider.values()))

    if metrics.get("day_key") == today_key:
        healed_monthly = max(monthly_calls, daily_calls)
        healed_yearly = max(yearly_calls, healed_monthly)
        if healed_monthly != monthly_calls:
            monthly_calls = healed_monthly
            metrics["monthly_calls"] = monthly_calls
            changed = True
        if healed_yearly != yearly_calls:
            yearly_calls = healed_yearly
            metrics["yearly_calls"] = yearly_calls
            changed = True
    if metrics.get("month_key") == this_month_key:
        healed_yearly = max(yearly_calls, monthly_calls)
        if healed_yearly != yearly_calls:
            yearly_calls = healed_yearly
            metrics["yearly_calls"] = yearly_calls
            changed = True

    if metrics.get("year_key") == this_year_key and yearly_calls > total_calls:
        metrics["total_calls"] = yearly_calls
        total_calls = yearly_calls
        changed = True

    if _as_int(metrics.get("daily_calls")) != daily_calls:
        metrics["daily_calls"] = daily_calls
        changed = True
    if _as_int(metrics.get("monthly_calls")) != monthly_calls:
        metrics["monthly_calls"] = monthly_calls
        changed = True
    if _as_int(metrics.get("yearly_calls")) != yearly_calls:
        metrics["yearly_calls"] = yearly_calls
        changed = True

    provider_keys = set(daily_by_provider) | set(monthly_by_provider) | set(yearly_by_provider)
    for provider in provider_keys:
        day_value = _as_int(daily_by_provider.get(provider))
        month_value = _as_int(monthly_by_provider.get(provider))
        year_value = _as_int(yearly_by_provider.get(provider))

        if metrics.get("day_key") == today_key and month_value < day_value:
            monthly_by_provider[provider] = day_value
            month_value = day_value
            changed = True
        if metrics.get("month_key") == this_month_key and year_value < month_value:
            yearly_by_provider[provider] = month_value
            year_value = month_value
            changed = True
        if metrics.get("day_key") == today_key and metrics.get("month_key") == this_month_key and year_value < day_value:
            yearly_by_provider[provider] = day_value
            changed = True

    return changed


def _domain_data(hass: HomeAssistant) -> dict[str, Any]:
    return hass.data.setdefault(DOMAIN, {})


def _metrics_data(hass: HomeAssistant) -> dict[str, Any]:
    data = _domain_data(hass)
    metrics = data.get("api_metrics")
    if not isinstance(metrics, dict):
        metrics = _default_metrics()
        data["api_metrics"] = metrics
    else:
        defaults = _default_metrics()
        for key, value in defaults.items():
            if key not in metrics:
                metrics[key] = deepcopy(value)
    _self_heal_metrics(metrics)
    return metrics


async def async_init_api_metrics(hass: HomeAssistant) -> None:
    """Initialize persisted API metrics."""
    data = _domain_data(hass)
    if data.get("api_metrics_initialized"):
        return
    store = Store(hass, _STORE_VERSION, _STORE_KEY)
    loaded = await store.async_load()
    metrics = loaded if isinstance(loaded, dict) else _default_metrics()
    healed = _self_heal_metrics(metrics)
    data["api_metrics_store"] = store
    data["api_metrics"] = metrics
    data["api_metrics_initialized"] = True
    if healed:
        await store.async_save(metrics)


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

    @callback
    def _save_later(_now) -> None:
        data["api_metrics_save_unsub"] = None
        # Use add_job to remain thread-safe even if callback is fired off-loop.
        hass.add_job(_async_save_metrics(hass))

    data["api_metrics_save_unsub"] = async_call_later(
        hass,
        _SAVE_DEBOUNCE.total_seconds(),
        _save_later,
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
    """Record a provider API call in a thread-safe way."""
    hass.add_job(partial(_record_api_call_on_loop, hass, provider, flow=flow, outcome=outcome))


@callback
def _record_api_call_on_loop(
    hass: HomeAssistant,
    provider: str | None,
    *,
    flow: str = "other",
    outcome: str | None = "success",
) -> None:
    """Record a provider API call on the HA event loop."""
    metrics = _metrics_data(hass)
    providers = metrics.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        metrics["providers"] = providers

    provider_key = _normalize_provider(provider)
    flow_key = _normalize_flow(flow)
    outcome_key = _normalize_outcome(outcome)
    now = dt_util.utcnow()
    day_key = now.strftime("%Y-%m-%d")
    month_key = now.strftime("%Y-%m")
    year_key = now.strftime("%Y")
    if metrics.get("day_key") != day_key:
        metrics["day_key"] = day_key
        metrics["daily_calls"] = 0
        metrics["daily_by_provider"] = {}
    if metrics.get("month_key") != month_key:
        metrics["month_key"] = month_key
        metrics["monthly_calls"] = 0
        metrics["monthly_by_provider"] = {}
    if metrics.get("year_key") != year_key:
        metrics["year_key"] = year_key
        metrics["yearly_calls"] = 0
        metrics["yearly_by_provider"] = {}

    stats = providers.get(provider_key)
    if not isinstance(stats, dict):
        stats = _default_provider_stats()
        providers[provider_key] = stats

    metrics["total_calls"] = int(metrics.get("total_calls") or 0) + 1
    metrics["daily_calls"] = int(metrics.get("daily_calls") or 0) + 1
    metrics["monthly_calls"] = int(metrics.get("monthly_calls") or 0) + 1
    metrics["yearly_calls"] = int(metrics.get("yearly_calls") or 0) + 1
    daily_by_provider = metrics.get("daily_by_provider")
    if not isinstance(daily_by_provider, dict):
        daily_by_provider = {}
        metrics["daily_by_provider"] = daily_by_provider
    daily_by_provider[provider_key] = int(daily_by_provider.get(provider_key) or 0) + 1
    monthly_by_provider = metrics.get("monthly_by_provider")
    if not isinstance(monthly_by_provider, dict):
        monthly_by_provider = {}
        metrics["monthly_by_provider"] = monthly_by_provider
    monthly_by_provider[provider_key] = int(monthly_by_provider.get(provider_key) or 0) + 1
    yearly_by_provider = metrics.get("yearly_by_provider")
    if not isinstance(yearly_by_provider, dict):
        yearly_by_provider = {}
        metrics["yearly_by_provider"] = yearly_by_provider
    yearly_by_provider[provider_key] = int(yearly_by_provider.get(provider_key) or 0) + 1
    stats["total"] = int(stats.get("total") or 0) + 1

    if flow_key in ("status", "schedule", "position", "directory", "usage"):
        stats[flow_key] = int(stats.get(flow_key) or 0) + 1
    else:
        stats["other"] = int(stats.get("other") or 0) + 1

    if outcome_key in ("success", "error", "rate_limited", "quota_exceeded", "timeout", "network", "auth_error"):
        stats[outcome_key] = int(stats.get(outcome_key) or 0) + 1
    else:
        stats["unknown"] = int(stats.get("unknown") or 0) + 1

    now_iso = now.isoformat()
    stats["last_call_at"] = now_iso
    metrics["updated_at"] = now_iso

    _schedule_save(hass)
    async_dispatcher_send(hass, SIGNAL_API_METRICS_UPDATED)


def get_api_metrics_snapshot(hass: HomeAssistant) -> dict[str, Any]:
    """Return a safe copy for sensor attributes."""
    return deepcopy(_metrics_data(hass))
