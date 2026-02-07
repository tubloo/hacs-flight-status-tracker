"""Status/position provider selection and fetch helpers."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .rate_limit import is_blocked, set_block


def _unwrap_status(res: Any) -> dict[str, Any] | None:
    if res is None:
        return None
    if isinstance(res, dict):
        return res
    details = getattr(res, "details", None)
    if isinstance(details, dict):
        return details
    return None


def _extract_position(status: dict[str, Any] | None, provider: str) -> dict[str, Any] | None:
    if not isinstance(status, dict):
        return None
    pos = status.get("position")
    if not isinstance(pos, dict):
        return None
    if pos.get("lat") is None or pos.get("lon") is None:
        return None
    out = dict(pos)
    out["provider"] = provider
    return out


def _parse_dt(val: Any) -> datetime | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        dt = dt_util.parse_datetime(val)
        if dt is not None:
            return dt
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


async def async_fetch_status(
    hass: HomeAssistant,
    options: dict[str, Any],
    flight: dict[str, Any],
    *,
    provider_override: str | None = None,
) -> dict[str, Any] | None:
    """Fetch provider status for a flight, honoring configured provider preference."""
    provider = (provider_override or options.get("status_provider") or "flightradar24").lower()
    use_sandbox = bool(options.get("fr24_use_sandbox", False))
    fr24_key = (options.get("fr24_api_key") or "").strip()
    fr24_sandbox_key = (options.get("fr24_sandbox_key") or "").strip()
    fr24_active_key = fr24_sandbox_key if use_sandbox and fr24_sandbox_key else fr24_key
    av_key = (options.get("aviationstack_access_key") or "").strip()
    al_key = (options.get("airlabs_api_key") or "").strip()
    fa_key = (options.get("flightapi_api_key") or "").strip()
    os_user = (options.get("opensky_username") or "").strip()
    os_pass = (options.get("opensky_password") or "").strip()
    fr24_version = (options.get("fr24_api_version") or "v1").strip()

    # Provider preference with fallbacks if missing key
    if provider == "flightradar24" and fr24_active_key:
        if is_blocked(hass, "fr24"):
            return None
        from .providers.flightradar24.status import Flightradar24StatusProvider

        res = await Flightradar24StatusProvider(
            hass, api_key=fr24_active_key, use_sandbox=use_sandbox, api_version=fr24_version
        ).async_get_status(flight)
        out = _unwrap_status(res)
        if isinstance(out, dict) and out.get("error") in ("rate_limited", "quota_exceeded"):
            reason = out.get("error")
            block_for = out.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
            set_block(hass, "fr24", block_for, reason)
            return None
        return out

    if provider == "aviationstack" and av_key:
        if is_blocked(hass, "aviationstack"):
            return None
        from .providers.aviationstack.status import AviationstackStatusProvider

        res = await AviationstackStatusProvider(hass, av_key).async_get_status(flight)
        out = _unwrap_status(res)
        if isinstance(out, dict) and out.get("error") in ("rate_limited", "quota_exceeded"):
            reason = out.get("error")
            block_for = out.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
            set_block(hass, "aviationstack", block_for, reason)
            return None
        return out

    if provider == "airlabs" and al_key:
        if is_blocked(hass, "airlabs"):
            return None
        from .providers.airlabs.status import AirLabsStatusProvider

        res = await AirLabsStatusProvider(hass, al_key).async_get_status(flight)
        out = _unwrap_status(res)
        if isinstance(out, dict) and out.get("error") in ("rate_limited", "quota_exceeded"):
            reason = out.get("error")
            block_for = out.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
            set_block(hass, "airlabs", block_for, reason)
            return None
        return out

    if provider == "flightapi" and fa_key:
        if is_blocked(hass, "flightapi"):
            return None
        from .providers.flightapi.status import FlightAPIStatusProvider

        res = await FlightAPIStatusProvider(hass, fa_key).async_get_status(flight)
        out = _unwrap_status(res)
        if isinstance(out, dict) and out.get("error") in ("rate_limited", "quota_exceeded"):
            reason = out.get("error")
            block_for = out.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
            set_block(hass, "flightapi", block_for, reason)
            return None
        return out

    if provider == "opensky" and (os_user or os_pass):
        # OpenSky can work without auth but is rate-limited; only use if configured
        from .providers.opensky.status import OpenSkyEnrichmentProvider

        res = await OpenSkyEnrichmentProvider(hass).async_get_status(flight)
        return _unwrap_status(res)

    if provider == "local":
        from .providers.local.status import LocalStatusProvider

        dep = _parse_dt((flight.get("dep") or {}).get("scheduled"))
        if dep is None:
            return None
        arr = _parse_dt((flight.get("arr") or {}).get("scheduled"))
        res = await LocalStatusProvider().async_get_status(
            flight_key=flight.get("flight_key") or "",
            airline_code=flight.get("airline_code") or "",
            flight_number=flight.get("flight_number") or "",
            dep_airport=((flight.get("dep") or {}).get("airport") or {}).get("iata") or "",
            arr_airport=((flight.get("arr") or {}).get("airport") or {}).get("iata") or "",
            scheduled_departure=dep,
            scheduled_arrival=arr,
            now=dt_util.utcnow(),
        )
        return _unwrap_status(res)

    if provider == "mock":
        from .providers.mock.status import MockStatusProvider

        res = await MockStatusProvider().async_get_status(flight)
        return _unwrap_status(res)

    # Fallback: try any configured provider in priority order
    if fr24_key:
        if is_blocked(hass, "fr24"):
            return None
        from .providers.flightradar24.status import Flightradar24StatusProvider

        res = await Flightradar24StatusProvider(
            hass, api_key=fr24_key, use_sandbox=use_sandbox, api_version=fr24_version
        ).async_get_status(flight)
        out = _unwrap_status(res)
        if isinstance(out, dict) and out.get("error") in ("rate_limited", "quota_exceeded"):
            reason = out.get("error")
            block_for = out.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
            set_block(hass, "fr24", block_for, reason)
            return None
        return out
    if av_key:
        if is_blocked(hass, "aviationstack"):
            return None
        from .providers.aviationstack.status import AviationstackStatusProvider

        res = await AviationstackStatusProvider(hass, av_key).async_get_status(flight)
        out = _unwrap_status(res)
        if isinstance(out, dict) and out.get("error") in ("rate_limited", "quota_exceeded"):
            reason = out.get("error")
            block_for = out.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
            set_block(hass, "aviationstack", block_for, reason)
            return None
        return out
    if al_key:
        if is_blocked(hass, "airlabs"):
            return None
        from .providers.airlabs.status import AirLabsStatusProvider

        res = await AirLabsStatusProvider(hass, al_key).async_get_status(flight)
        out = _unwrap_status(res)
        if isinstance(out, dict) and out.get("error") in ("rate_limited", "quota_exceeded"):
            reason = out.get("error")
            block_for = out.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
            set_block(hass, "airlabs", block_for, reason)
            return None
        return out

    if fa_key:
        if is_blocked(hass, "flightapi"):
            return None
        from .providers.flightapi.status import FlightAPIStatusProvider

        res = await FlightAPIStatusProvider(hass, fa_key).async_get_status(flight)
        out = _unwrap_status(res)
        if isinstance(out, dict) and out.get("error") in ("rate_limited", "quota_exceeded"):
            reason = out.get("error")
            block_for = out.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
            set_block(hass, "flightapi", block_for, reason)
            return None
        return out

    return None


async def async_fetch_position(
    hass: HomeAssistant, options: dict[str, Any], flight: dict[str, Any], provider: str
) -> dict[str, Any] | None:
    """Fetch live position only using a specific provider."""
    provider = (provider or "").lower()
    if provider in ("", "none"):
        return None

    use_sandbox = bool(options.get("fr24_use_sandbox", False))
    fr24_key = (options.get("fr24_api_key") or "").strip()
    fr24_sandbox_key = (options.get("fr24_sandbox_key") or "").strip()
    fr24_active_key = fr24_sandbox_key if use_sandbox and fr24_sandbox_key else fr24_key
    av_key = (options.get("aviationstack_access_key") or "").strip()
    al_key = (options.get("airlabs_api_key") or "").strip()
    os_user = (options.get("opensky_username") or "").strip()
    os_pass = (options.get("opensky_password") or "").strip()
    fr24_version = (options.get("fr24_api_version") or "v1").strip()

    if provider == "flightradar24" and fr24_active_key:
        if is_blocked(hass, "fr24"):
            return None
        from .providers.flightradar24.status import Flightradar24StatusProvider

        res = await Flightradar24StatusProvider(
            hass, api_key=fr24_active_key, use_sandbox=use_sandbox, api_version=fr24_version
        ).async_get_status(flight)
        out = _unwrap_status(res)
        if isinstance(out, dict) and out.get("error") in ("rate_limited", "quota_exceeded"):
            reason = out.get("error")
            block_for = out.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
            set_block(hass, "fr24", block_for, reason)
            return None
        return _extract_position(out, provider)

    if provider == "airlabs" and al_key:
        if is_blocked(hass, "airlabs"):
            return None
        from .providers.airlabs.status import AirLabsStatusProvider

        res = await AirLabsStatusProvider(hass, al_key).async_get_status(flight)
        out = _unwrap_status(res)
        if isinstance(out, dict) and out.get("error") in ("rate_limited", "quota_exceeded"):
            reason = out.get("error")
            block_for = out.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
            set_block(hass, "airlabs", block_for, reason)
            return None
        return _extract_position(out, provider)

    if provider == "opensky" and (os_user or os_pass):
        from .providers.opensky.status import OpenSkyEnrichmentProvider

        res = await OpenSkyEnrichmentProvider(hass).async_get_status(flight)
        out = _unwrap_status(res)
        return _extract_position(out, provider)

    if provider == "aviationstack" and av_key:
        if is_blocked(hass, "aviationstack"):
            return None
        from .providers.aviationstack.status import AviationstackStatusProvider

        res = await AviationstackStatusProvider(hass, av_key).async_get_status(flight)
        out = _unwrap_status(res)
        if isinstance(out, dict) and out.get("error") in ("rate_limited", "quota_exceeded"):
            reason = out.get("error")
            block_for = out.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
            set_block(hass, "aviationstack", block_for, reason)
            return None
        return _extract_position(out, provider)

    if provider == "flightapi":
        # FlightAPI does not provide live position
        return None

    return None
