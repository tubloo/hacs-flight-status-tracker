"""Simple per-provider rate limit tracking."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN


def _state(hass: HomeAssistant) -> dict[str, Any]:
    data = hass.data.setdefault(DOMAIN, {})
    return data.setdefault("rate_limits", {})


def is_blocked(hass: HomeAssistant, provider: str) -> bool:
    info = _state(hass).get(provider) or {}
    until = info.get("until")
    if not isinstance(until, datetime):
        return False
    return datetime.now(timezone.utc) < until


def set_block(hass: HomeAssistant, provider: str, seconds: int, reason: str | None = None) -> None:
    until = datetime.now(timezone.utc) + timedelta(seconds=max(0, int(seconds)))
    _state(hass)[provider] = {"until": until, "reason": reason}


def get_block_reason(hass: HomeAssistant, provider: str) -> str | None:
    info = _state(hass).get(provider) or {}
    return info.get("reason")


def get_block_until(hass: HomeAssistant, provider: str) -> datetime | None:
    info = _state(hass).get(provider) or {}
    until = info.get("until")
    return until if isinstance(until, datetime) else None


def get_blocks(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    blocks: dict[str, dict[str, Any]] = {}
    for provider, info in _state(hass).items():
        until = info.get("until")
        if isinstance(until, datetime):
            blocks[provider] = {"until": until, "reason": info.get("reason")}
    return blocks
