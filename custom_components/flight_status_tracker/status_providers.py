"""Status/position provider selection and fetch helpers."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .rate_limit import is_blocked, set_block
from .api_metrics import record_api_call


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
        pos = status.get("track")
    if not isinstance(pos, dict):
        pos = status
    return _normalize_position(pos, provider)


def _to_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def _to_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "y", "on"):
            return True
        if s in ("false", "0", "no", "n", "off"):
            return False
    return None


def _ts_to_iso_and_age(ts: Any) -> tuple[str | None, int | None]:
    dt = _parse_dt(ts)
    if dt is None and isinstance(ts, (int, float)):
        try:
            # OpenSky/FR24 often use unix seconds.
            dt = datetime.fromtimestamp(float(ts), tz=dt_util.UTC)
        except Exception:
            dt = None
    if dt is None:
        return None, None
    dt_u = dt_util.as_utc(dt) if dt.tzinfo else dt_util.as_utc(dt_util.as_local(dt))
    now_u = dt_util.as_utc(dt_util.utcnow())
    age = int(max(0, (now_u - dt_u).total_seconds()))
    return dt_u.isoformat(), age


def _quality_from_age(age_sec: int | None) -> str:
    if age_sec is None:
        return "unknown"
    if age_sec <= 120:
        return "live"
    if age_sec <= 600:
        return "recent"
    return "stale"


def _normalize_position(raw: dict[str, Any], provider: str) -> dict[str, Any] | None:
    lat = _to_float(raw.get("lat"))
    if lat is None:
        lat = _to_float(raw.get("latitude"))
    lon = _to_float(raw.get("lon"))
    if lon is None:
        lon = _to_float(raw.get("lng"))
    if lon is None:
        lon = _to_float(raw.get("longitude"))
    if lat is None or lon is None:
        return None

    altitude_ft = _to_float(raw.get("altitude_ft"))
    if altitude_ft is None:
        altitude_ft = _to_float(raw.get("alt"))
    if altitude_ft is None:
        alt_m = _to_float(raw.get("altitude_m"))
        if alt_m is not None:
            altitude_ft = alt_m * 3.28084

    speed_kt = _to_float(raw.get("ground_speed_kt"))
    if speed_kt is None:
        speed_kt = _to_float(raw.get("gspeed"))
    if speed_kt is None:
        vel_mps = _to_float(raw.get("velocity_mps"))
        if vel_mps is not None:
            speed_kt = vel_mps * 1.94384

    heading_deg = _to_float(raw.get("heading_deg"))
    if heading_deg is None:
        heading_deg = _to_float(raw.get("track"))

    vertical_speed_fpm = _to_float(raw.get("vertical_speed_fpm"))
    if vertical_speed_fpm is None:
        vr_mps = _to_float(raw.get("vertical_rate_mps"))
        if vr_mps is not None:
            vertical_speed_fpm = vr_mps * 196.8504

    on_ground = _to_bool(raw.get("on_ground"))
    icao24 = (raw.get("icao24") or "").strip().lower() if isinstance(raw.get("icao24"), str) else None
    callsign = (raw.get("callsign") or "").strip() if isinstance(raw.get("callsign"), str) else None
    ts_raw = raw.get("timestamp") or raw.get("updated") or raw.get("last_contact") or raw.get("time_position")
    ts_iso, age_sec = _ts_to_iso_and_age(ts_raw)

    out = {
        "lat": lat,
        "lon": lon,
        "timestamp": ts_iso,
        "source": provider,
        "provider": provider,
        "altitude_ft": altitude_ft,
        "ground_speed_kt": speed_kt,
        "heading_deg": heading_deg,
        "vertical_speed_fpm": vertical_speed_fpm,
        "on_ground": on_ground,
        "icao24": icao24,
        "callsign": callsign,
        "age_sec": age_sec,
        "quality": _quality_from_age(age_sec),
    }
    return {k: v for k, v in out.items() if v is not None}


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


def _outcome_from_payload(out: dict[str, Any] | None) -> str:
    if not isinstance(out, dict):
        return "unknown"
    err = str(out.get("error") or "").strip().lower()
    if not err:
        return "success"
    if err in {"rate_limited", "quota_exceeded", "timeout", "network", "auth_error"}:
        return err
    return "error"


def _attach_normalized_position(out: dict[str, Any] | None, provider: str) -> dict[str, Any] | None:
    if not isinstance(out, dict):
        return out
    pos = _extract_position(out, provider)
    if pos:
        out["position"] = pos
    return out


async def async_fetch_status(
    hass: HomeAssistant,
    options: dict[str, Any],
    flight: dict[str, Any],
    *,
    provider_override: str | None = None,
) -> dict[str, Any] | None:
    """Fetch provider status for a flight, honoring configured provider preference."""
    provider = (provider_override or options.get("status_provider") or "flightapi").lower()
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

        try:
            res = await Flightradar24StatusProvider(
                hass, api_key=fr24_active_key, use_sandbox=use_sandbox, api_version=fr24_version
            ).async_get_status(flight)
        except Exception as e:
            out = {"provider": "flightradar24", "error": "network", "detail": str(e)}
            record_api_call(hass, "flightradar24", flow="status", outcome=_outcome_from_payload(out))
            return out
        out = _attach_normalized_position(_unwrap_status(res), "flightradar24")
        record_api_call(hass, "flightradar24", flow="status", outcome=_outcome_from_payload(out))
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

        try:
            res = await AviationstackStatusProvider(hass, av_key).async_get_status(flight)
        except Exception as e:
            out = {"provider": "aviationstack", "error": "network", "detail": str(e)}
            record_api_call(hass, "aviationstack", flow="status", outcome=_outcome_from_payload(out))
            return out
        out = _attach_normalized_position(_unwrap_status(res), "aviationstack")
        record_api_call(hass, "aviationstack", flow="status", outcome=_outcome_from_payload(out))
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

        try:
            res = await AirLabsStatusProvider(hass, al_key).async_get_status(flight)
        except Exception as e:
            out = {"provider": "airlabs", "error": "network", "detail": str(e)}
            record_api_call(hass, "airlabs", flow="status", outcome=_outcome_from_payload(out))
            return out
        out = _attach_normalized_position(_unwrap_status(res), "airlabs")
        record_api_call(hass, "airlabs", flow="status", outcome=_outcome_from_payload(out))
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

        try:
            res = await FlightAPIStatusProvider(hass, fa_key).async_get_status(flight)
        except Exception as e:
            out = {"provider": "flightapi", "error": "network", "detail": str(e)}
            record_api_call(hass, "flightapi", flow="status", outcome=_outcome_from_payload(out))
            return out
        out = _attach_normalized_position(_unwrap_status(res), "flightapi")
        record_api_call(hass, "flightapi", flow="status", outcome=_outcome_from_payload(out))
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
        out = _attach_normalized_position(_unwrap_status(res), "opensky")
        record_api_call(hass, "opensky", flow="status", outcome=_outcome_from_payload(out))
        return out

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
    if fr24_active_key:
        if is_blocked(hass, "fr24"):
            return None
        from .providers.flightradar24.status import Flightradar24StatusProvider

        try:
            res = await Flightradar24StatusProvider(
                hass, api_key=fr24_active_key, use_sandbox=use_sandbox, api_version=fr24_version
            ).async_get_status(flight)
        except Exception as e:
            out = {"provider": "flightradar24", "error": "network", "detail": str(e)}
            record_api_call(hass, "flightradar24", flow="status", outcome=_outcome_from_payload(out))
            return out
        out = _attach_normalized_position(_unwrap_status(res), "flightradar24")
        record_api_call(hass, "flightradar24", flow="status", outcome=_outcome_from_payload(out))
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

        try:
            res = await AviationstackStatusProvider(hass, av_key).async_get_status(flight)
        except Exception as e:
            out = {"provider": "aviationstack", "error": "network", "detail": str(e)}
            record_api_call(hass, "aviationstack", flow="status", outcome=_outcome_from_payload(out))
            return out
        out = _attach_normalized_position(_unwrap_status(res), "aviationstack")
        record_api_call(hass, "aviationstack", flow="status", outcome=_outcome_from_payload(out))
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

        try:
            res = await AirLabsStatusProvider(hass, al_key).async_get_status(flight)
        except Exception as e:
            out = {"provider": "airlabs", "error": "network", "detail": str(e)}
            record_api_call(hass, "airlabs", flow="status", outcome=_outcome_from_payload(out))
            return out
        out = _attach_normalized_position(_unwrap_status(res), "airlabs")
        record_api_call(hass, "airlabs", flow="status", outcome=_outcome_from_payload(out))
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

        try:
            res = await FlightAPIStatusProvider(hass, fa_key).async_get_status(flight)
        except Exception as e:
            out = {"provider": "flightapi", "error": "network", "detail": str(e)}
            record_api_call(hass, "flightapi", flow="status", outcome=_outcome_from_payload(out))
            return out
        out = _attach_normalized_position(_unwrap_status(res), "flightapi")
        record_api_call(hass, "flightapi", flow="status", outcome=_outcome_from_payload(out))
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

        try:
            res = await Flightradar24StatusProvider(
                hass, api_key=fr24_active_key, use_sandbox=use_sandbox, api_version=fr24_version
            ).async_get_status(flight)
        except Exception as e:
            out = {"provider": "flightradar24", "error": "network", "detail": str(e)}
            record_api_call(hass, "flightradar24", flow="position", outcome=_outcome_from_payload(out))
            return None
        out = _unwrap_status(res)
        record_api_call(hass, "flightradar24", flow="position", outcome=_outcome_from_payload(out))
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

        try:
            res = await AirLabsStatusProvider(hass, al_key).async_get_status(flight)
        except Exception as e:
            out = {"provider": "airlabs", "error": "network", "detail": str(e)}
            record_api_call(hass, "airlabs", flow="position", outcome=_outcome_from_payload(out))
            return None
        out = _unwrap_status(res)
        record_api_call(hass, "airlabs", flow="position", outcome=_outcome_from_payload(out))
        if isinstance(out, dict) and out.get("error") in ("rate_limited", "quota_exceeded"):
            reason = out.get("error")
            block_for = out.get("retry_after") or (24 * 60 * 60 if reason == "quota_exceeded" else 900)
            set_block(hass, "airlabs", block_for, reason)
            return None
        return _extract_position(out, provider)

    if provider == "opensky" and (os_user or os_pass):
        from .providers.opensky.status import OpenSkyEnrichmentProvider

        try:
            res = await OpenSkyEnrichmentProvider(hass).async_get_status(flight)
        except Exception as e:
            out = {"provider": "opensky", "error": "network", "detail": str(e)}
            record_api_call(hass, "opensky", flow="position", outcome=_outcome_from_payload(out))
            return None
        out = _unwrap_status(res)
        record_api_call(hass, "opensky", flow="position", outcome=_outcome_from_payload(out))
        return _extract_position(out, provider)

    if provider == "aviationstack" and av_key:
        if is_blocked(hass, "aviationstack"):
            return None
        from .providers.aviationstack.status import AviationstackStatusProvider

        try:
            res = await AviationstackStatusProvider(hass, av_key).async_get_status(flight)
        except Exception as e:
            out = {"provider": "aviationstack", "error": "network", "detail": str(e)}
            record_api_call(hass, "aviationstack", flow="position", outcome=_outcome_from_payload(out))
            return None
        out = _unwrap_status(res)
        record_api_call(hass, "aviationstack", flow="position", outcome=_outcome_from_payload(out))
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
