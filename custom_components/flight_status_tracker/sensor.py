"""Flight Status Tracker sensor: exposes canonical flight timeline fields.

Sensor rebuilds automatically when manual flights change (dispatcher signal).
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import inspect
import json
import logging
from typing import Any, Callable
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_interval, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    DATA_UPCOMING_FLIGHTS,
    DOMAIN,
    SIGNAL_MANUAL_FLIGHTS_UPDATED,
    SIGNAL_API_METRICS_UPDATED,
    SIGNAL_TRAVEL_METRICS_UPDATED,
    EVENT_UPDATED,
)
from .coordinator_agg import merge_segments
from .providers.manual.itinerary import ManualItineraryProvider
from .manual_store import async_remove_manual_flight, async_update_manual_flight
from .status_manager import async_update_statuses
from .status_resolver import _normalize_iso_in_tz
from .tz_short import tz_short_name
from .directory import get_airport, get_airline, normalize_airline_name, warm_directory_cache
from .rate_limit import get_blocks, is_blocked, get_block_until, get_block_reason, set_block
from .selected import get_selected_flight, get_flight_position
from .api_metrics import get_api_metrics_snapshot, record_api_call
from .travel_metrics import get_travel_metrics_snapshot


SCHEMA_VERSION = 3
_LOGGER = logging.getLogger(__name__)
_DIR_ENRICH_STATE_KEY = "dir_enrich_state"
_FLIGHT_ENTITY_STATE_KEY = "flight_entities"
_API_FLOW_KEYS = ("status", "schedule", "position", "directory", "usage", "other")


def _dir_enrich_state(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """In-memory guard to avoid repeated directory lookups for unchanged identifiers."""
    return hass.data.setdefault(DOMAIN, {}).setdefault(_DIR_ENRICH_STATE_KEY, {})


def _parse_dt(val: Any):
    if not isinstance(val, str):
        return None
    return dt_util.parse_datetime(val)


def _stable_signature(value: Any) -> str:
    """Build a stable hash for change detection across rebuilds."""
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    except Exception:
        raw = repr(value)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


_VOLATILE_FLIGHT_KEYS = {
    "status_updated_at",
}

_VOLATILE_UI_KEYS = {
    "updated_ago_min",
    "updated_abs",
    "position_age_min",
    "position_line",
    "route_progress_at_poll_pct",
    "plane_x_at_poll_pct",
}


def _signature_payload_flight(flight: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop volatile fields so no-op polls do not trigger rerenders."""
    if not isinstance(flight, dict):
        return None
    out = dict(flight)
    for key in _VOLATILE_FLIGHT_KEYS:
        out.pop(key, None)
    # Raw provider payload may include timestamps/noise that change without
    # altering card-visible status/timing fields.
    out.pop("status", None)
    ui = out.get("ui")
    if isinstance(ui, dict):
        ui_out = dict(ui)
        for key in _VOLATILE_UI_KEYS:
            ui_out.pop(key, None)
        out["ui"] = ui_out
    return out


def _format_hm_local(ts: Any, tzname: str | None) -> tuple[Any | None, str | None]:
    if not isinstance(ts, str):
        return None, None
    dt = dt_util.parse_datetime(ts)
    if not dt:
        return None, None
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=dt_util.UTC)
    if tzname:
        try:
            dt = dt.astimezone(ZoneInfo(tzname))
        except Exception:
            pass
    return dt, dt.strftime("%H:%M")


def _status_label(raw_state: Any) -> str:
    raw = str(raw_state or "unknown").strip().lower()
    if raw in ("active", "en route", "en-route", "enroute"):
        return "En Route"
    if raw == "arrived":
        return "Arrived"
    if raw == "cancelled":
        return "Cancelled"
    if raw == "diverted":
        return "Diverted"
    if raw == "unknown":
        return "Unknown"
    return raw.title() if raw else "Unknown"


def _provider_status_short(raw_state: Any) -> str | None:
    raw = str(raw_state or "").strip().lower()
    if not raw or raw in ("unknown", "n/a", "na"):
        return None
    if raw in ("scheduled", "schedule", "plan", "planned", "expected", "active", "arrival"):
        return None
    mapping = {
        "checkin": "Check-in",
        "check-in": "Check-in",
        "boarding": "Boarding",
        "gateclosed": "Gate closed",
        "gate_closed": "Gate closed",
        "gate closed": "Gate closed",
        "delayed": "Delayed",
        "enroute": "En route",
        "en route": "En route",
        "en-route": "En route",
        "in air": "In air",
        "in-air": "In air",
        "airborne": "Airborne",
        "departed": "Departed",
        "cruising": "Cruising",
        "approaching": "Approaching",
        "landed": "Landed",
        "arrived": "Arrived",
        "arrived_gate": "At gate",
        "cancelled": "Cancelled",
        "canceled": "Cancelled",
        "diverted": "Diverted",
    }
    return mapping.get(raw) or raw.title()


def _term_gate(terminal: Any, gate: Any) -> str:
    term = str(terminal or "").strip()
    g = str(gate or "").strip()
    if term and g:
        return f"Terminal {term} · Gate {g}"
    if term:
        return f"Terminal {term}"
    if g:
        return f"Gate {g}"
    return ""


def _join_nonempty(parts: list[str]) -> str:
    vals = [p.strip() for p in parts if isinstance(p, str) and p.strip()]
    return " · ".join(vals)


def _focused_provider_key(entry, snapshot: dict[str, Any] | None = None) -> str | None:
    """Resolve the single configured provider for diagnostics display."""
    options = dict(getattr(entry, "options", {}) or {})
    status_provider = str(options.get("status_provider") or "").strip().lower()
    schedule_provider = str(options.get("schedule_provider") or "").strip().lower()
    position_provider = str(options.get("position_provider") or "").strip().lower()
    if position_provider == "same_as_status":
        position_provider = status_provider

    providers = {
        provider
        for provider in (status_provider, schedule_provider, position_provider)
        if provider and provider not in ("none", "unknown")
    }
    if len(providers) == 1:
        return next(iter(providers))

    snapshot_providers = (snapshot or {}).get("providers") if isinstance(snapshot, dict) else {}
    if isinstance(snapshot_providers, dict) and len(snapshot_providers) == 1:
        only = next(iter(snapshot_providers.keys()), None)
        return str(only) if only else None

    return status_provider or schedule_provider or None


def _fmt_event_local(ts: Any, tzname: str | None, label: str) -> str:
    if not isinstance(ts, str) or not ts.strip():
        return ""
    dt, hm = _format_hm_local(ts, tzname)
    if hm:
        return f"{label} {hm}"
    # Keep raw value only as a last resort.
    return f"{label} {ts}"


def _as_utc_dt(val: datetime | None) -> datetime | None:
    if val is None:
        return None
    if val.tzinfo:
        return dt_util.as_utc(val)
    return dt_util.as_utc(dt_util.as_local(val))


def _best_dt_utc(flight: dict[str, Any], side: str, keys: list[str]) -> datetime | None:
    block = flight.get(side) or {}
    if not isinstance(block, dict):
        return None
    for k in keys:
        dt = _parse_dt(block.get(k))
        if dt:
            return _as_utc_dt(dt)
    return None


def _segment_key_for_ui(flight: dict[str, Any], now_utc: datetime, options: dict[str, Any]) -> str:
    def _opt_int(key: str, default: int) -> int:
        try:
            return int(options.get(key, default))
        except Exception:
            return default

    far_thr_hours = max(0, _opt_int("far_before_dep_threshold_hours", 6))
    dep_pre_minutes = max(0, _opt_int("dep_window_pre_minutes", 10))
    dep_post_minutes = max(0, _opt_int("dep_window_post_minutes", 10))
    arr_pre_minutes = max(0, _opt_int("arr_window_pre_minutes", 10))
    arr_post_minutes = max(0, _opt_int("arr_window_post_minutes", 10))
    stop_after_arr_minutes = max(0, _opt_int("stop_refresh_after_arrival_minutes", 60))

    now_u = _as_utc_dt(now_utc) or now_utc
    dep = _best_dt_utc(flight, "dep", ["actual", "estimated", "scheduled"])
    arr = _best_dt_utc(flight, "arr", ["actual", "estimated", "scheduled"])

    if dep:
        dep_window_start = dep - timedelta(minutes=dep_pre_minutes)
        dep_window_end = dep + timedelta(minutes=dep_post_minutes)
        if dep_window_start <= now_u <= dep_window_end:
            return "takeoff"

    if arr:
        arr_window_start = arr - timedelta(minutes=arr_pre_minutes)
        arr_window_end = arr + timedelta(minutes=arr_post_minutes)
        if arr_window_start <= now_u <= arr_window_end:
            return "landing"

    if dep and arr:
        mid_start = dep + timedelta(minutes=dep_post_minutes)
        mid_end = arr - timedelta(minutes=arr_pre_minutes)
        if mid_start <= now_u <= mid_end:
            return "mid_flight"

    if dep and now_u < dep:
        delta = dep - now_u
        if delta > timedelta(hours=far_thr_hours):
            return "far_future"
        return "prepare_to_travel"

    if arr and now_u <= arr + timedelta(minutes=stop_after_arr_minutes):
        return "post_arrival"

    return "unknown"


def _build_ui_block(flight: dict[str, Any], now_utc, options: dict[str, Any]) -> dict[str, Any]:
    dep = flight.get("dep") or {}
    arr = flight.get("arr") or {}
    dep_air = dep.get("airport") or {}
    arr_air = arr.get("airport") or {}

    dep_tz = dep_air.get("tz")
    arr_tz = arr_air.get("tz")
    dep_tz_short = dep_air.get("tz_short") or ""
    arr_tz_short = arr_air.get("tz_short") or ""
    viewer_tz_short = dt_util.as_local(now_utc).strftime("%Z")

    dep_sched_dt, dep_sched = _format_hm_local(dep.get("scheduled"), dep_tz)
    dep_est_dt, dep_est = _format_hm_local(dep.get("estimated"), dep_tz)
    dep_act_dt, dep_act = _format_hm_local(dep.get("actual"), dep_tz)
    arr_sched_dt, arr_sched = _format_hm_local(arr.get("scheduled"), arr_tz)
    arr_est_dt, arr_est = _format_hm_local(arr.get("estimated"), arr_tz)
    arr_act_dt, arr_act = _format_hm_local(arr.get("actual"), arr_tz)

    dep_primary = dep_act or dep_est or dep_sched
    arr_primary = arr_act or arr_est or arr_sched
    dep_changed = bool(dep_primary and dep_sched and dep_primary != dep_sched)
    arr_changed = bool(arr_primary and arr_sched and arr_primary != arr_sched)

    raw_state = (flight.get("status_state") or "unknown")
    state_label = _status_label(raw_state)
    route_state = "Scheduled" if str(raw_state).strip().lower() == "unknown" else state_label
    status = flight.get("status") if isinstance(flight.get("status"), dict) else {}
    provider_state_label = _provider_status_short(status.get("provider_state") or status.get("state"))
    if provider_state_label and provider_state_label.lower() == state_label.lower():
        provider_state_label = None

    delay_key = (
        flight.get("delay_status_key")
        or str(flight.get("delay_status") or "unknown").lower().replace(" ", "_")
    )
    is_delayed = delay_key == "delayed"
    is_on_time = delay_key in ("on_time", "early")

    segment_key = _segment_key_for_ui(flight, now_utc, options)

    badge_key = "neutral"
    if state_label in ("Cancelled", "Diverted") or is_delayed:
        badge_key = "critical"
    elif state_label == "En Route":
        badge_key = "ok"
    elif state_label == "Arrived" and is_on_time:
        badge_key = "ok"
    elif state_label == "Arrived" and is_delayed:
        badge_key = "critical"
    elif state_label == "Scheduled" and segment_key in ("prepare_to_travel", "takeoff", "mid_flight", "landing"):
        badge_key = "ok"

    dep_label_raw = dep_air.get("city") or dep_air.get("name") or (dep_air.get("iata") or "—").upper()
    arr_label_raw = arr_air.get("city") or arr_air.get("name") or (arr_air.get("iata") or "—").upper()
    dep_label = str(dep_label_raw).title()
    arr_label = str(arr_label_raw).title()
    dep_date = dep_sched_dt.strftime("%d %b (%a)") if dep_sched_dt else "—"
    arr_date = arr_sched_dt.strftime("%d %b (%a)") if arr_sched_dt else "—"

    dep_viewer = None
    arr_viewer = None
    dep_dt_utc = _parse_dt(dep.get("actual") or dep.get("estimated") or dep.get("scheduled"))
    arr_dt_utc = _parse_dt(arr.get("actual") or arr.get("estimated") or arr.get("scheduled"))
    if dep_dt_utc:
        d = dt_util.as_local(dep_dt_utc if dep_dt_utc.tzinfo else dep_dt_utc.replace(tzinfo=dt_util.UTC))
        dep_viewer = d.strftime("%H:%M")
    if arr_dt_utc:
        d = dt_util.as_local(arr_dt_utc if arr_dt_utc.tzinfo else arr_dt_utc.replace(tzinfo=dt_util.UTC))
        arr_viewer = d.strftime("%H:%M")
    show_dep_viewer = bool(dep_viewer and dep_tz_short != viewer_tz_short and dep_viewer != dep_primary)
    show_arr_viewer = bool(arr_viewer and arr_tz_short != viewer_tz_short and arr_viewer != arr_primary)
    show_viewer_row = not (dep_tz_short == viewer_tz_short and arr_tz_short == viewer_tz_short)

    dep_term_gate = _term_gate(dep.get("terminal"), dep.get("gate"))
    arr_term_gate = _term_gate(arr.get("terminal"), arr.get("gate"))
    show_term_gate_row = bool(dep_term_gate or arr_term_gate)

    dep_ops_line = _join_nonempty(
        [
            f"Check-in {dep.get('check_in_counters')}" if dep.get("check_in_counters") else "",
            _fmt_event_local(dep.get("boarding_time"), dep_tz, "Boarding"),
            _fmt_event_local(dep.get("door_time"), dep_tz, "Door"),
        ]
    )
    arr_ops_line = _join_nonempty(
        [
            f"Baggage {arr.get('baggage_claim')}" if arr.get("baggage_claim") else "",
            f"Belt {arr.get('belt')}" if arr.get("belt") else "",
        ]
    )
    show_ops_row = bool(dep_ops_line or arr_ops_line)

    dep_movement_line = _join_nonempty(
        [
            _fmt_event_local(dep.get("off_block_time"), dep_tz, "Off-block"),
            _fmt_event_local(dep.get("takeoff_time"), dep_tz, "Takeoff"),
        ]
    )
    arr_movement_line = _join_nonempty(
        [
            _fmt_event_local(arr.get("landing_time"), arr_tz, "Landing"),
            _fmt_event_local(arr.get("on_block_time"), arr_tz, "On-block"),
        ]
    )
    show_movement_row = bool(dep_movement_line or arr_movement_line)

    position = flight.get("position") if isinstance(flight.get("position"), dict) else {}
    pos_line = None
    pos_is_stale = False
    pos_age_min = None
    if position:
        alt = position.get("altitude_ft")
        gs = position.get("ground_speed_kt")
        hdg = position.get("heading_deg")
        pos_parts: list[str] = []
        if isinstance(alt, (int, float)):
            pos_parts.append(f"FL{int(round(alt / 100.0))}")
        if isinstance(gs, (int, float)):
            pos_parts.append(f"{int(round(gs))} kt")
        if isinstance(hdg, (int, float)):
            pos_parts.append(f"HDG {int(round(hdg))}\u00b0")
        pos_line = " \u00b7 ".join(pos_parts) if pos_parts else None
        pts = _parse_dt(position.get("timestamp"))
        if pts:
            pts_u = dt_util.as_utc(pts) if pts.tzinfo else dt_util.as_utc(dt_util.as_local(pts))
            pos_age_min = int(max(0, round((dt_util.as_utc(now_utc) - pts_u).total_seconds() / 60)))
            pos_is_stale = pos_age_min >= 15
            if pos_line:
                if pos_is_stale:
                    pos_line = f"{pos_line} \u00b7 Position stale ({pos_age_min}m)"
                else:
                    pos_line = f"{pos_line} \u00b7 Updated {pos_age_min}m ago"

    diverted_air = flight.get("diverted_to_airport") or {}
    diverted_iata = (flight.get("diverted_to_iata") or diverted_air.get("iata") or "").strip().upper()
    has_diverted = state_label == "Diverted" and bool(diverted_iata)
    route_arr_code = diverted_iata if has_diverted else (arr_air.get("iata") or "—").upper()

    progress_start_ts = dep.get("actual") or dep.get("estimated") or dep.get("scheduled")
    progress_end_ts = arr.get("actual") or arr.get("estimated") or arr.get("scheduled")
    arr_target_ts = progress_end_ts

    progress_pct = 0
    if dep_dt_utc and arr_dt_utc:
        dep_u = dt_util.as_utc(dep_dt_utc) if dep_dt_utc.tzinfo else dt_util.as_utc(dt_util.as_local(dep_dt_utc))
        arr_u = dt_util.as_utc(arr_dt_utc) if arr_dt_utc.tzinfo else dt_util.as_utc(dt_util.as_local(arr_dt_utc))
        total = (arr_u - dep_u).total_seconds()
        elapsed = (dt_util.as_utc(now_utc) - dep_u).total_seconds()
        if total > 0:
            progress_pct = int(max(0, min(100, round((elapsed / total) * 100))))

    if route_state == "En Route":
        plane_x = 6 if progress_pct < 6 else (94 if progress_pct > 94 else progress_pct)
    elif route_state == "Arrived":
        plane_x = 100
    else:
        plane_x = 2

    updated = _parse_dt(flight.get("status_updated_at"))
    updated_ago_min = None
    updated_abs = None
    if updated:
        upd = dt_util.as_utc(updated) if updated.tzinfo else dt_util.as_utc(dt_util.as_local(updated))
        updated_ago_min = int(max(0, round((dt_util.as_utc(now_utc) - upd).total_seconds() / 60)))
        updated_abs = dt_util.as_local(upd).strftime("%Y-%m-%d %H:%M:%S %Z")

    next_update = _parse_dt(flight.get("next_status_check_at"))
    next_update_in_min = None
    next_update_abs = None
    if next_update:
        nxt = dt_util.as_utc(next_update) if next_update.tzinfo else dt_util.as_utc(dt_util.as_local(next_update))
        next_update_in_min = int(max(0, round((nxt - dt_util.as_utc(now_utc)).total_seconds() / 60)))
        next_update_abs = dt_util.as_local(nxt).strftime("%Y-%m-%d %H:%M:%S %Z")

    status_error_text = status.get("error_message") or status.get("error")

    return {
        "state_label": state_label,
        "route_state": route_state,
        "badge_key": badge_key,
        "poll_segment_key": segment_key,
        "dep_code": (dep_air.get("iata") or "—").upper(),
        "arr_code": (arr_air.get("iata") or "—").upper(),
        "route_arr_code": route_arr_code,
        "dep_label_line": f"{dep_label} · {dep_date}",
        "arr_label_line": f"{arr_label} · {arr_date}",
        "dep_time_primary": dep_primary,
        "arr_time_primary": arr_primary,
        "dep_time_strike": dep_sched if dep_changed else None,
        "arr_time_strike": arr_sched if arr_changed else None,
        "dep_changed": dep_changed,
        "arr_changed": arr_changed,
        "dep_tz_short": dep_tz_short,
        "arr_tz_short": arr_tz_short,
        "viewer_tz_short": viewer_tz_short,
        "dep_viewer_time": dep_viewer,
        "arr_viewer_time": arr_viewer,
        "show_dep_viewer": show_dep_viewer,
        "show_arr_viewer": show_arr_viewer,
        "show_viewer_row": show_viewer_row,
        "dep_term_gate": dep_term_gate,
        "arr_term_gate": arr_term_gate,
        "show_term_gate_row": show_term_gate_row,
        "dep_ops_line": dep_ops_line,
        "arr_ops_line": arr_ops_line,
        "show_ops_row": show_ops_row,
        "dep_movement_line": dep_movement_line,
        "arr_movement_line": arr_movement_line,
        "show_movement_row": show_movement_row,
        "position_line": pos_line,
        "position_is_stale": pos_is_stale,
        "position_age_min": pos_age_min,
        "show_position_row": bool(pos_line),
        "route_progress_at_poll_pct": progress_pct,
        "plane_x_at_poll_pct": plane_x,
        "progress_start_ts": progress_start_ts,
        "progress_end_ts": progress_end_ts,
        "arr_target_ts": arr_target_ts,
        "status_error_text": status_error_text,
        "provider_state_label": provider_state_label,
        "updated_ago_min": updated_ago_min,
        "updated_abs": updated_abs,
        "next_update_in_min": next_update_in_min,
        "next_update_abs": next_update_abs,
        "source": status.get("provider") or "—",
    }

SCHEMA_DOC = """\
Flight Status Tracker schema (v3)

Per flight:
- dep.scheduled/estimated/actual
- arr.scheduled/estimated/actual
- dep.scheduled_local/estimated_local/actual_local (airport local time)
- arr.scheduled_local/estimated_local/actual_local (airport local time)
- dep.scheduled_viewer_local/estimated_viewer_local/actual_viewer_local (Home Assistant local time)
- arr.scheduled_viewer_local/estimated_viewer_local/actual_viewer_local (Home Assistant local time)
- dep.airport.tz + tz_short
- arr.airport.tz + tz_short
- airline_logo_url (optional), aircraft_type (optional)
- delay_status (On Time|Delayed|Cancelled|Unknown)
- delay_status_key (on_time|delayed|cancelled|unknown)
- delay_minutes (minutes vs sched; arrival preferred if available)
- duration_scheduled_minutes / duration_estimated_minutes / duration_actual_minutes
- duration_minutes (best available: actual → estimated → scheduled)
- diverted_to_iata (optional, only when status_state=Diverted)
- diverted_to_airport (optional, only when status_state=Diverted)
""".strip()

SCHEMA_EXAMPLE: dict[str, Any] = {
    "flight_key": "AI-157-DEL-2026-01-30",
    "source": "manual",
    "airline_code": "AI",
    "flight_number": "157",
    "aircraft_type": "B788",
    "travellers": ["Sumit", "Parul"],
    "status_state": "Scheduled",
    "delay_status": "On Time",
    "delay_minutes": 0,
    "duration_scheduled_minutes": 295,
    "duration_estimated_minutes": None,
    "duration_actual_minutes": None,
    "duration_minutes": 295,
    "dep": {"airport": {"iata": "DEL", "tz": "Asia/Kolkata", "tz_short": "IST", "city": "Delhi"}, "scheduled": "2026-01-30T14:00:00+00:00"},
    "arr": {"airport": {"iata": "CPH", "tz": "Europe/Copenhagen", "tz_short": "CET", "city": "Copenhagen"}, "scheduled": "2026-01-30T18:55:00+00:00"},
}

PROVIDER_BLOCK_REFRESH = timedelta(minutes=1)
WATCHDOG_CHECK_INTERVAL = timedelta(minutes=2)
WATCHDOG_STALE_REBUILD = timedelta(minutes=15)
WATCHDOG_OVERDUE_GRACE = timedelta(minutes=3)
WATCHDOG_KICK_DEBOUNCE = timedelta(minutes=5)
MANUAL_FULL_REFRESH_DEBOUNCE = timedelta(seconds=8)
MANUAL_FULL_REFRESH_MIN_INTERVAL = timedelta(seconds=45)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    await _async_migrate_api_monthly_entity_id(hass)
    upcoming = FlightDashboardUpcomingFlightsSensor(hass, entry, async_add_entities)
    entities = [
        upcoming,
        FlightDashboardSelectedFlightSensor(hass, entry),
        FlightDashboardApiDailySensor(hass, entry),
        FlightDashboardApiMetricsSensor(hass, entry),
        FlightDashboardApiMonthlySensor(hass, entry),
        FlightDashboardApiYearlySensor(hass, entry),
        FlightDashboardFlightsDailySensor(hass, entry),
        FlightDashboardFlightsMonthlySensor(hass, entry),
        FlightDashboardFlightsYearlySensor(hass, entry),
        FlightDashboardFlightsLifetimeSensor(hass, entry),
        FlightDashboardDistanceDailySensor(hass, entry),
        FlightDashboardDistanceMonthlySensor(hass, entry),
        FlightDashboardDistanceYearlySensor(hass, entry),
        FlightDashboardDistanceLifetimeSensor(hass, entry),
        FlightDashboardProviderBlockSensor(hass, entry),
    ]
    try:
        from .preview_sensor import FlightDashboardAddPreviewSensor
    except Exception:
        FlightDashboardAddPreviewSensor = None  # type: ignore
    if FlightDashboardAddPreviewSensor:
        entities.append(FlightDashboardAddPreviewSensor(hass, entry))
    # Do not block setup on initial updates; rebuild runs in background.
    async_add_entities(entities, False)
    hass.data.setdefault(DOMAIN, {}).setdefault("upcoming_sensors", {})[entry.entry_id] = upcoming


async def _async_migrate_api_monthly_entity_id(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    old_unique_id = f"{DOMAIN}_api_utility_meter"
    old_entity_id = registry.async_get_entity_id("sensor", DOMAIN, old_unique_id)
    if not old_entity_id:
        return
    desired_entity_id = "sensor.flight_status_tracker_api_calls_this_month"
    if old_entity_id == desired_entity_id:
        return
    existing = registry.async_get(desired_entity_id)
    if existing is not None and existing.platform != DOMAIN:
        return
    try:
        registry.async_update_entity(old_entity_id, new_entity_id=desired_entity_id)
    except ValueError:
        return


def _flight_entity_slug(flight_key: str) -> str:
    base = "".join(ch.lower() if ch.isalnum() else "_" for ch in (flight_key or "").strip())
    while "__" in base:
        base = base.replace("__", "_")
    base = base.strip("_")[:48] or "flight"
    digest = hashlib.sha1((flight_key or "").encode("utf-8")).hexdigest()[:8]
    return f"{base}_{digest}"


class FlightDashboardFlightSensor(SensorEntity):
    _attr_icon = "mdi:airplane"

    def __init__(self, hass: HomeAssistant, entry, flight_key: str) -> None:
        self.hass = hass
        self.entry = entry
        self._flight_key = flight_key
        slug = _flight_entity_slug(flight_key)
        self._attr_unique_id = f"{DOMAIN}_flight_{entry.entry_id}_{slug}"
        self._attr_suggested_object_id = f"{DOMAIN}_flight_{slug}"
        self._flight: dict[str, Any] | None = None
        self._flight_signature: str | None = None
        self._attr_available = False

    @property
    def name(self) -> str:
        f = self._flight or {}
        code = (f.get("airline_code") or "").strip()
        number = (f.get("flight_number") or "").strip()
        dep = (f.get("dep") or {})
        arr = (f.get("arr") or {})
        dep_iata = (((dep.get("airport") or {}).get("iata")) or "").strip().upper()
        arr_iata = (((arr.get("airport") or {}).get("iata")) or "").strip().upper()
        dep_ts = dep.get("scheduled") or dep.get("estimated") or dep.get("actual")
        dep_dt = _parse_dt(dep_ts) if isinstance(dep_ts, str) else None
        dep_date = dep_dt.strftime("%d-%b") if dep_dt else ""

        flight_label = f"{code} {number}".strip() if (code or number) else self._flight_key
        route_label = f"{dep_iata}-{arr_iata}" if dep_iata and arr_iata else ""

        parts = [f"Flight {flight_label}"]
        if route_label:
            parts.append(route_label)
        if dep_date:
            parts.append(dep_date)
        return ", ".join(parts)

    @property
    def native_value(self) -> str:
        if not self._flight:
            return "unknown"
        ui = self._flight.get("ui") or {}
        return str(ui.get("state_label") or self._flight.get("status_state") or "unknown")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        f = self._flight or {}
        dep = f.get("dep") or {}
        arr = f.get("arr") or {}
        return {
            "schema_version": SCHEMA_VERSION,
            "flight_key": self._flight_key,
            "airline_code": f.get("airline_code"),
            "flight_number": f.get("flight_number"),
            "airline_name": f.get("airline_name"),
            "airline_logo_url": f.get("airline_logo_url"),
            "aircraft_type": f.get("aircraft_type"),
            "source": f.get("source"),
            "editable": f.get("editable"),
            "status_state": f.get("status_state"),
            "delay_status": f.get("delay_status"),
            "delay_minutes": f.get("delay_minutes"),
            "duration_minutes": f.get("duration_minutes"),
            "travellers": f.get("travellers") or [],
            "notes": f.get("notes"),
            "dep_scheduled": dep.get("scheduled"),
            "arr_scheduled": arr.get("scheduled"),
            "dep": dep,
            "arr": arr,
            "ui": f.get("ui") if isinstance(f.get("ui"), dict) else {},
            "flight": f,
        }

    async def async_added_to_hass(self) -> None:
        # Ensure newly created dynamic entities publish their initial state.
        if self._flight is not None:
            self.async_write_ha_state()

    @callback
    def async_set_flight(self, flight: dict[str, Any] | None, *, write_state: bool = True) -> None:
        new_flight = flight if isinstance(flight, dict) else None
        new_sig = _stable_signature(_signature_payload_flight(new_flight)) if new_flight is not None else None
        changed = (new_sig != self._flight_signature) or ((new_flight is not None) != self._attr_available)
        self._flight = new_flight
        self._flight_signature = new_sig
        self._attr_available = self._flight is not None
        if write_state and changed:
            self.async_write_ha_state()


class FlightDashboardUpcomingFlightsSensor(SensorEntity):
    _attr_name = "Flight Status Tracker Upcoming Flights"
    _attr_icon = "mdi:airplane-clock"

    def __init__(self, hass: HomeAssistant, entry, async_add_entities_cb: Callable[[list[SensorEntity]], None]) -> None:
        self.hass = hass
        self.entry = entry
        self._async_add_entities_cb = async_add_entities_cb
        self._attr_unique_id = f"{DOMAIN}_upcoming_flights"
        self._flights: list[dict[str, Any]] = []
        self._flight_entities: dict[str, FlightDashboardFlightSensor] = {}
        self._unsub: Callable[[], None] | None = None
        self._next_refresh_unsub: Callable[[], None] | None = None
        self._watchdog_unsub: Callable[[], None] | None = None
        self._last_rebuild_at: datetime | None = None
        self._next_refresh_at: datetime | None = None
        self._last_rebuild_error: str | None = None
        self._watchdog_last_check_at: datetime | None = None
        self._watchdog_last_kick_at: datetime | None = None
        self._last_full_refresh_at: datetime | None = None
        self._rebuild_lock = asyncio.Lock()
        self._rebuild_pending = False
        self._pending_reason: str | None = None
        self._manual_deferred_unsub: Callable[[], None] | None = None
        self._last_render_signature: str | None = None

    async def async_added_to_hass(self) -> None:
        # Rebuild whenever manual flights are updated
        @callback
        def _on_manual_updated() -> None:
            self.hass.async_create_task(self._run_rebuild_safe("manual_update_fast"))

            @callback
            def _manual_deferred(_now) -> None:
                now = dt_util.utcnow()
                if self._last_full_refresh_at and (now - self._last_full_refresh_at) < MANUAL_FULL_REFRESH_MIN_INTERVAL:
                    return
                self.hass.async_create_task(self._run_rebuild_safe("manual_update_full"))

            if self._manual_deferred_unsub:
                try:
                    self._manual_deferred_unsub()
                except Exception:
                    _LOGGER.debug("Failed to clear previous manual deferred refresh", exc_info=True)
                self._manual_deferred_unsub = None
            self._manual_deferred_unsub = async_track_point_in_utc_time(
                self.hass, _manual_deferred, dt_util.utcnow() + MANUAL_FULL_REFRESH_DEBOUNCE
            )

        self._unsub = async_dispatcher_connect(self.hass, SIGNAL_MANUAL_FLIGHTS_UPDATED, _on_manual_updated)

        @callback
        def _watchdog_tick(_now) -> None:
            self.hass.async_create_task(self._watchdog_check())

        self._watchdog_unsub = async_track_time_interval(self.hass, _watchdog_tick, WATCHDOG_CHECK_INTERVAL)

        await self._run_rebuild_safe("startup_fast")

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._next_refresh_unsub:
            self._next_refresh_unsub()
            self._next_refresh_unsub = None
        if self._watchdog_unsub:
            self._watchdog_unsub()
            self._watchdog_unsub = None
        if self._manual_deferred_unsub:
            self._manual_deferred_unsub()
            self._manual_deferred_unsub = None
        sensors = self.hass.data.get(DOMAIN, {}).get("upcoming_sensors") or {}
        if isinstance(sensors, dict):
            sensors.pop(self.entry.entry_id, None)
        self.hass.data.setdefault(DOMAIN, {}).pop(DATA_UPCOMING_FLIGHTS, None)

    @property
    def native_value(self) -> str:
        n = len(self._flights)
        return f"{n} flight" if n == 1 else f"{n} flights"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        active_entity_ids = [
            ent.entity_id
            for fk, ent in self._flight_entities.items()
            if fk
            and ent is not None
            and ent.entity_id
            and any(isinstance(f, dict) and f.get("flight_key") == fk for f in self._flights)
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "flights_total": len(self._flights),
            "flight_keys": [f.get("flight_key") for f in self._flights if isinstance(f, dict) and f.get("flight_key")],
            "flight_entity_ids": active_entity_ids,
            "flights": self._flights,
            "last_rebuild_at": self._last_rebuild_at.isoformat() if self._last_rebuild_at else None,
            "next_refresh_at": self._next_refresh_at.isoformat() if self._next_refresh_at else None,
            "last_rebuild_error": self._last_rebuild_error,
            "watchdog_last_check_at": self._watchdog_last_check_at.isoformat() if self._watchdog_last_check_at else None,
            "watchdog_last_kick_at": self._watchdog_last_kick_at.isoformat() if self._watchdog_last_kick_at else None,
        }

    @callback
    def _sync_flight_entities(self, flights: list[dict[str, Any]]) -> None:
        active_keys: set[str] = set()
        new_entities: list[FlightDashboardFlightSensor] = []

        for flight in flights:
            if not isinstance(flight, dict):
                continue
            flight_key = str(flight.get("flight_key") or "").strip()
            if not flight_key:
                continue
            active_keys.add(flight_key)
            ent = self._flight_entities.get(flight_key)
            if ent is None:
                ent = FlightDashboardFlightSensor(self.hass, self.entry, flight_key)
                self._flight_entities[flight_key] = ent
                new_entities.append(ent)
                ent.async_set_flight(flight, write_state=False)
                continue
            ent.async_set_flight(flight)

        if new_entities:
            try:
                add_result = self._async_add_entities_cb(new_entities, True)
                if inspect.isawaitable(add_result):
                    self.hass.async_create_task(add_result)
            except Exception:
                _LOGGER.exception("Per-flight entity sync: async_add_entities callback failed")

        for flight_key, ent in list(self._flight_entities.items()):
            if flight_key in active_keys:
                continue
            entity_id = ent.entity_id
            if entity_id:
                try:
                    er.async_get(self.hass).async_remove(entity_id)
                except Exception:
                    _LOGGER.debug("Failed removing stale flight entity %s", entity_id, exc_info=True)
                    ent.async_set_flight(None)
            self._flight_entities.pop(flight_key, None)

    async def _run_rebuild_safe(self, reason: str) -> None:
        """Run rebuild and ensure we always retry after an unexpected failure."""
        light_reasons = {"startup_fast", "manual_update_fast"}
        fast_reasons = {"startup_fast", "manual_update_fast"}

        if self._rebuild_lock.locked():
            # Coalesce burst triggers (manual update + scheduled + watchdog) into
            # one additional rebuild once the active run finishes.
            self._rebuild_pending = True
            # Preserve highest-priority pending reason so fast paths stay fast.
            if self._pending_reason in fast_reasons:
                return
            self._pending_reason = reason
            return

        run_reason = reason
        async with self._rebuild_lock:
            while True:
                self._rebuild_pending = False
                self._pending_reason = None
                try:
                    light_mode = run_reason in light_reasons
                    await self._rebuild(light=light_mode)
                    self._last_rebuild_at = dt_util.utcnow()
                    if not light_mode:
                        self._last_full_refresh_at = self._last_rebuild_at
                    self._last_rebuild_error = None
                except Exception:
                    self._last_rebuild_error = f"rebuild_failed:{run_reason}"
                    _LOGGER.exception("Flight list rebuild failed (%s)", run_reason)
                    if self._next_refresh_unsub:
                        try:
                            self._next_refresh_unsub()
                        except Exception:
                            _LOGGER.debug("Failed to clear existing refresh subscription", exc_info=True)
                        self._next_refresh_unsub = None
                    retry_at = dt_util.utcnow() + timedelta(minutes=5)
                    self._next_refresh_at = retry_at

                    @callback
                    def _retry(_now) -> None:
                        self.hass.async_create_task(self._run_rebuild_safe("retry_after_error"))

                    self._next_refresh_unsub = async_track_point_in_utc_time(self.hass, _retry, retry_at)
                    break

                if not self._rebuild_pending:
                    break
                run_reason = self._pending_reason or "coalesced_update"

    async def _watchdog_check(self) -> None:
        self._watchdog_last_check_at = dt_util.utcnow()
        now = dt_util.utcnow()
        if self._rebuild_lock.locked():
            return
        if not self._flights:
            return

        active = False
        for f in self._flights:
            if not isinstance(f, dict):
                continue
            state = (f.get("status_state") or "").strip().lower()
            if state not in ("arrived", "cancelled", "canceled", "landed"):
                active = True
                break
        if not active:
            return

        stalled = False
        stalled_reason = ""
        if self._next_refresh_unsub is None:
            stalled = True
            stalled_reason = "missing_refresh_subscription"
        elif self._next_refresh_at is None:
            stalled = True
            stalled_reason = "missing_refresh_time"
        elif now > (self._next_refresh_at + WATCHDOG_OVERDUE_GRACE):
            stalled = True
            stalled_reason = "refresh_overdue"
        elif self._last_rebuild_at is None:
            stalled = True
            stalled_reason = "never_rebuilt"
        elif now - self._last_rebuild_at > WATCHDOG_STALE_REBUILD:
            stalled = True
            stalled_reason = "stale_rebuild"

        if not stalled:
            return
        if self._watchdog_last_kick_at and (now - self._watchdog_last_kick_at) < WATCHDOG_KICK_DEBOUNCE:
            return

        self._watchdog_last_kick_at = now
        _LOGGER.warning(
            "Watchdog kick: forcing safe rebuild due to stale scheduler state (%s)",
            stalled_reason or "unknown",
        )
        await self._run_rebuild_safe("watchdog_kick")

    async def _warm_directory_cache_safe(self, options: dict[str, Any], flights: list[dict[str, Any]]) -> None:
        try:
            await warm_directory_cache(self.hass, options, flights)
        except Exception:
            _LOGGER.debug("Directory warmup failed", exc_info=True)

    async def _rebuild(self, *, light: bool = False) -> None:
        """Rebuild the flight list and schedule the next smart refresh."""
        now = dt_util.utcnow()
        options = dict(self.entry.options)
        include_past = int(options.get("include_past_hours", 24))
        days_ahead = int(options.get("days_ahead", 120))
        max_flights = int(options.get("max_flights", 50))
        # Defaults should match the Options Flow defaults. If the user has never
        # opened Options, `entry.options` can be empty, so we must still behave
        # sensibly.
        auto_prune = bool(options.get("auto_prune_landed", True))
        prune_minutes_raw = int(options.get("auto_remove_after_arrival_minutes", int(options.get("prune_landed_hours", 1)) * 60))
        prune_minutes = max(0, prune_minutes_raw) if auto_prune else prune_minutes_raw

        start = now - timedelta(hours=include_past)
        end = now + timedelta(days=days_ahead)

        segments: list[dict[str, Any]] = []
        providers = options.get("itinerary_providers") or ["manual"]

        if "manual" in providers:
            segments.extend(await ManualItineraryProvider(self.hass).async_get_segments(start, end))

        flights = merge_segments(segments)

        # Warm directory cache on first run without blocking initial render.
        self.hass.async_create_task(self._warm_directory_cache_safe(options, flights))

        # Filter by include_past_hours using departure local time (airport tz when available)
        def _as_tz(dt, tzname: str | None):
            if not tzname:
                return dt
            try:
                return dt.astimezone(ZoneInfo(tzname))
            except Exception:
                return dt

        def _dep_dt_local(f: dict[str, Any]):
            dep = f.get("dep") or {}
            dep_air = (dep.get("airport") or {})
            dep_time = dep.get("actual") or dep.get("estimated") or dep.get("scheduled")
            if not isinstance(dep_time, str):
                return None
            dt = dt_util.parse_datetime(dep_time)
            if not dt:
                return None
            dt = dt_util.as_utc(dt) if dt.tzinfo else dt_util.as_utc(dt_util.as_local(dt))
            return _as_tz(dt, dep_air.get("tz"))

        if include_past is not None:
            pruned: list[dict[str, Any]] = []
            for f in flights:
                dep_local = _dep_dt_local(f)
                if not dep_local:
                    pruned.append(f)
                    continue
                now_local = _as_tz(now, (f.get("dep") or {}).get("airport", {}).get("tz"))
                if now_local - dep_local <= timedelta(hours=include_past):
                    pruned.append(f)
            flights = pruned

        # Limit number of flights early (oldest first)
        if max_flights and len(flights) > max_flights:
            flights = flights[:max_flights]

        if light:
            # Fast path: hydrate last persisted status without provider calls.
            flights, next_refresh = await async_update_statuses(
                self.hass, options, flights, refresh_due=False
            )
        else:
            flights, next_refresh = await async_update_statuses(self.hass, options, flights)

        # Optional: auto-remove arrived/cancelled flights after arrival time
        if auto_prune:
            cutoff = now - timedelta(minutes=prune_minutes)
            removed_manual = False
            next_prune: datetime | None = None
            pruned: list[dict[str, Any]] = []

            def _dt_utc(val: str, tzname: str | None) -> datetime | None:
                dt = dt_util.parse_datetime(val)
                if not dt:
                    return None
                if dt.tzinfo is not None:
                    return dt_util.as_utc(dt)
                if tzname:
                    try:
                        dt = dt.replace(tzinfo=ZoneInfo(tzname))
                        return dt_util.as_utc(dt)
                    except Exception:
                        pass
                return dt_util.as_utc(dt_util.as_local(dt))

            for f in flights:
                if not isinstance(f, dict):
                    continue
                status = (f.get("status_state") or "").lower()
                arr = (f.get("arr") or {})
                arr_air = (arr.get("airport") or {})
                arr_time = arr.get("actual") or arr.get("estimated") or arr.get("scheduled")
                is_terminal = status in ("arrived", "cancelled", "canceled", "landed")
                if not isinstance(arr_time, str):
                    pruned.append(f)
                    continue
                dt = _dt_utc(arr_time, arr_air.get("tz"))
                if not dt:
                    pruned.append(f)
                    continue
                prune_at = dt + timedelta(minutes=prune_minutes)
                if prune_at > now and (next_prune is None or prune_at < next_prune):
                    next_prune = prune_at
                if dt <= cutoff:
                    if (f.get("source") or "manual") == "manual":
                        if await async_remove_manual_flight(self.hass, f.get("flight_key", "")):
                            removed_manual = True
                    # Always drop from the list (even for non-manual sources)
                    continue
                if not is_terminal:
                    pruned.append(f)
                    continue
                pruned.append(f)
            flights = pruned
            if next_prune and (not next_refresh or next_prune < next_refresh):
                # Ensure we rebuild at the prune boundary even if status refresh is no longer scheduled.
                next_refresh = next_prune

        for flight in flights:
            flight["editable"] = (flight.get("source") or "manual") == "manual"

            dep = (flight.get("dep") or {})
            arr = (flight.get("arr") or {})
            dep_air = (dep.get("airport") or {})
            arr_air = (arr.get("airport") or {})

            dep_sched = dep.get("scheduled")
            arr_sched = arr.get("scheduled")

            # Enrich from directory cache/providers (optional)
            fk = flight.get("flight_key")
            prev = _dir_enrich_state(self.hass).get(fk) if isinstance(fk, str) and fk else {}

            airline_code = flight.get("airline_code")
            prev_airline = (prev or {}).get("airline_code")
            airline_changed = bool(prev_airline and airline_code and prev_airline != airline_code)
            updates: dict[str, Any] = {}
            if light:
                dep["airport"] = dep_air
                arr["airport"] = arr_air
                flight["dep"] = dep
                flight["arr"] = arr
                flight["ui"] = _build_ui_block(flight, now, options)
                continue
            needs_airline = bool(airline_code)
            if needs_airline:
                airline = await get_airline(self.hass, options, airline_code)
                if airline:
                    current_name = flight.get("airline_name")
                    normalized_current_name = normalize_airline_name(airline_code, current_name)
                    if normalized_current_name and current_name != normalized_current_name:
                        flight["airline_name"] = normalized_current_name
                        updates["airline_name"] = normalized_current_name
                    resolved_name = airline.get("name")
                    if resolved_name and not flight.get("airline_name"):
                        flight["airline_name"] = resolved_name
                        updates["airline_name"] = resolved_name

                    logo = airline.get("logo_url") or airline.get("logo")
                    if logo and (airline_changed or not flight.get("airline_logo_url") or flight.get("airline_logo_url") != logo):
                        flight["airline_logo_url"] = logo
                        updates["airline_logo_url"] = logo

            dep_iata = dep_air.get("iata")
            arr_iata = arr_air.get("iata")

            needs_dep = bool(dep_iata)
            if needs_dep:
                airport = await get_airport(self.hass, options, dep_air.get("iata"))
                if airport:
                    if airport.get("name") and dep_air.get("name") != airport.get("name"):
                        dep_air["name"] = airport.get("name")
                        updates["dep_airport_name"] = airport.get("name")
                    if airport.get("city") and dep_air.get("city") != airport.get("city"):
                        dep_air["city"] = airport.get("city")
                        updates["dep_airport_city"] = airport.get("city")
                    if airport.get("tz") and dep_air.get("tz") != airport.get("tz"):
                        dep_air["tz"] = airport.get("tz")
                        dep_air.pop("tz_short", None)
                        updates["dep_airport_tz"] = airport.get("tz")

            needs_arr = bool(arr_iata)
            if needs_arr:
                airport = await get_airport(self.hass, options, arr_air.get("iata"))
                if airport:
                    if airport.get("name") and arr_air.get("name") != airport.get("name"):
                        arr_air["name"] = airport.get("name")
                        updates["arr_airport_name"] = airport.get("name")
                    if airport.get("city") and arr_air.get("city") != airport.get("city"):
                        arr_air["city"] = airport.get("city")
                        updates["arr_airport_city"] = airport.get("city")
                    if airport.get("tz") and arr_air.get("tz") != airport.get("tz"):
                        arr_air["tz"] = airport.get("tz")
                        arr_air.pop("tz_short", None)
                        updates["arr_airport_tz"] = airport.get("tz")

            # Persist directory enrichment for manual flights
            if updates and (flight.get("source") or "manual") == "manual":
                fk = flight.get("flight_key")
                if fk:
                    await async_update_manual_flight(self.hass, fk, updates)

            # Update in-memory enrichment guard.
            if isinstance(fk, str) and fk:
                _dir_enrich_state(self.hass)[fk] = {
                    "airline_code": airline_code,
                    "dep_iata": dep_iata,
                    "arr_iata": arr_iata,
                }

            # No static fallback: only use directory providers / cache

            if dep_air.get("tz") and not dep_air.get("tz_short"):
                dep_air["tz_short"] = tz_short_name(dep_air.get("tz"), dep_sched)
            if arr_air.get("tz") and not arr_air.get("tz_short"):
                arr_air["tz_short"] = tz_short_name(arr_air.get("tz"), arr_sched)

            dep["airport"] = dep_air
            arr["airport"] = arr_air

            def _to_local(ts: Any, tzname: str | None) -> str | None:
                if not ts or not isinstance(ts, str):
                    return None
                dt = dt_util.parse_datetime(ts)
                if not dt:
                    return None
                if tzname:
                    if not dt.tzinfo:
                        # Naive schedule values from providers are usually airport-local.
                        try:
                            dt = dt.replace(tzinfo=ZoneInfo(tzname))
                        except Exception:
                            return dt.isoformat()
                    try:
                        return dt.astimezone(ZoneInfo(tzname)).isoformat()
                    except Exception:
                        return dt.isoformat()
                try:
                    # No airport timezone known yet: keep provider wall-clock value.
                    return dt.isoformat()
                except Exception:
                    return None

            def _to_viewer_local(ts: Any, tzname: str | None) -> str | None:
                if not ts or not isinstance(ts, str):
                    return None
                dt = dt_util.parse_datetime(ts)
                if not dt:
                    return None
                if not dt.tzinfo and tzname:
                    try:
                        dt = dt.replace(tzinfo=ZoneInfo(tzname))
                    except Exception:
                        return dt.isoformat()
                try:
                    return dt_util.as_local(dt).isoformat()
                except Exception:
                    try:
                        return dt.isoformat()
                    except Exception:
                        return None

            dep["scheduled_local"] = _to_local(dep.get("scheduled"), dep_air.get("tz"))
            dep["estimated_local"] = _to_local(dep.get("estimated"), dep_air.get("tz"))
            dep["actual_local"] = _to_local(dep.get("actual"), dep_air.get("tz"))
            dep["scheduled_viewer_local"] = _to_viewer_local(dep.get("scheduled"), dep_air.get("tz"))
            dep["estimated_viewer_local"] = _to_viewer_local(dep.get("estimated"), dep_air.get("tz"))
            dep["actual_viewer_local"] = _to_viewer_local(dep.get("actual"), dep_air.get("tz"))
            arr["scheduled_local"] = _to_local(arr.get("scheduled"), arr_air.get("tz"))
            arr["estimated_local"] = _to_local(arr.get("estimated"), arr_air.get("tz"))
            arr["actual_local"] = _to_local(arr.get("actual"), arr_air.get("tz"))
            arr["scheduled_viewer_local"] = _to_viewer_local(arr.get("scheduled"), arr_air.get("tz"))
            arr["estimated_viewer_local"] = _to_viewer_local(arr.get("estimated"), arr_air.get("tz"))
            arr["actual_viewer_local"] = _to_viewer_local(arr.get("actual"), arr_air.get("tz"))

            flight["dep"] = dep
            flight["arr"] = arr

            # Normalize naive timestamps now that tz info may be available from directory
            dep["scheduled"] = _normalize_iso_in_tz(dep.get("scheduled"), dep_air.get("tz"))
            dep["estimated"] = _normalize_iso_in_tz(dep.get("estimated"), dep_air.get("tz"))
            dep["actual"] = _normalize_iso_in_tz(dep.get("actual"), dep_air.get("tz"))
            arr["scheduled"] = _normalize_iso_in_tz(arr.get("scheduled"), arr_air.get("tz"))
            arr["estimated"] = _normalize_iso_in_tz(arr.get("estimated"), arr_air.get("tz"))
            arr["actual"] = _normalize_iso_in_tz(arr.get("actual"), arr_air.get("tz"))

            # Safety: if still far in the future, do not show En Route/Arrived
            state = (flight.get("status_state") or "unknown").lower()
            dep_time = dep.get("actual") or dep.get("estimated") or dep.get("scheduled")
            if state in ("en route", "arrived", "cancelled", "canceled") and isinstance(dep_time, str):
                dep_dt = dt_util.parse_datetime(dep_time)
                if dep_dt:
                    dep_dt = dt_util.as_utc(dep_dt) if dep_dt.tzinfo else dt_util.as_utc(dt_util.as_local(dep_dt))
                    if dt_util.as_utc(now) < dep_dt - timedelta(minutes=90):
                        flight["status_state"] = "Scheduled"

            # keep output clean (drop old UI-only duplicates if they exist)
            for legacy in (
                "dep_local_str","arr_local_str","dep_viewer_str","arr_viewer_str",
                "dep_local_hm_new","dep_local_hm_sched","dep_viewer_hm_new","dep_viewer_hm_sched",
                "arr_local_hm_new","arr_local_hm_sched","arr_viewer_hm_new","arr_viewer_hm_sched",
                "delay_minutes","gate_dep","gate_arr","terminal_dep","terminal_arr",
                "scheduled_departure","scheduled_arrival",
            ):
                flight.pop(legacy, None)

            flight["ui"] = _build_ui_block(flight, now, options)

        render_signature = _stable_signature(
            [_signature_payload_flight(f) if isinstance(f, dict) else f for f in flights]
        )
        render_changed = render_signature != self._last_render_signature
        self._flights = flights
        self.hass.data.setdefault(DOMAIN, {})[DATA_UPCOMING_FLIGHTS] = list(flights)
        self.hass.data.setdefault(DOMAIN, {})[_FLIGHT_ENTITY_STATE_KEY] = self._flight_entities
        self._sync_flight_entities(flights)
        if render_changed:
            self._last_render_signature = render_signature
            self.async_write_ha_state()
            self.hass.bus.async_fire(EVENT_UPDATED)

        if not next_refresh:
            # Safety net: if any non-terminal flights remain but per-flight
            # scheduling could not produce a next_refresh, keep the scheduler alive.
            has_active = any(
                isinstance(f, dict)
                and (f.get("status_state") or "").strip().lower() not in ("arrived", "cancelled", "canceled", "landed")
                for f in flights
            )
            if has_active:
                next_refresh = now + timedelta(minutes=30)
                _LOGGER.debug(
                    "No smart next_refresh computed for active flights; using fallback refresh at %s",
                    next_refresh.isoformat(),
                )

        if next_refresh:
            if self._next_refresh_unsub:
                try:
                    self._next_refresh_unsub()
                except Exception:
                    _LOGGER.debug("Failed to clear previous refresh schedule", exc_info=True)
                self._next_refresh_unsub = None

            @callback
            def _scheduled_refresh(_now) -> None:
                self.hass.async_create_task(self._run_rebuild_safe("scheduled_refresh"))

            self._next_refresh_unsub = async_track_point_in_utc_time(self.hass, _scheduled_refresh, next_refresh)
            self._next_refresh_at = next_refresh
        else:
            if self._next_refresh_unsub:
                try:
                    self._next_refresh_unsub()
                except Exception:
                    _LOGGER.debug("Failed to clear previous refresh schedule", exc_info=True)
                self._next_refresh_unsub = None
            self._next_refresh_at = None


class FlightDashboardSelectedFlightSensor(SensorEntity):
    _attr_name = "Flight Status Tracker Selected Flight"
    _attr_icon = "mdi:airplane"

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = f"{DOMAIN}_selected_flight"
        self._unsub_state = None
        self._unsub_bus = None
        self._flight: dict[str, Any] | None = None
        self._flight_signature: str | None = None

    async def async_added_to_hass(self) -> None:
        @callback
        def _kick(_event=None) -> None:
            self.hass.async_create_task(self._refresh())

        self._unsub_state = async_track_state_change_event(
            self.hass,
            ["sensor.flight_status_tracker_upcoming_flights", "select.flight_status_tracker_selected_flight"],
            _kick,
        )
        self._unsub_bus = self.hass.bus.async_listen(EVENT_UPDATED, _kick)

        await self._refresh()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._unsub_bus:
            self._unsub_bus()
            self._unsub_bus = None

    @property
    def native_value(self) -> str:
        if not self._flight:
            return "No flight"
        key = self._flight.get("flight_key") or "Selected flight"
        pos = get_flight_position(self._flight) or {}
        ts = pos.get("timestamp") or self._flight.get("status_updated_at")
        return f"{key} | {ts}" if ts else key

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pos = get_flight_position(self._flight)
        return {
            "flight": self._flight,
            "latitude": (pos or {}).get("lat"),
            "longitude": (pos or {}).get("lon"),
            "heading": (pos or {}).get("track") or 0,
            "map_key": (self._flight or {}).get("flight_key"),
        }

    async def _refresh(self) -> None:
        flight = get_selected_flight(self.hass)
        new_sig = _stable_signature(_signature_payload_flight(flight))
        if new_sig == self._flight_signature:
            return
        self._flight_signature = new_sig
        self._flight = flight
        self.async_write_ha_state()


class FlightDashboardApiMetricsSensor(SensorEntity):
    _attr_name = "Flight Status Tracker API Calls"
    _attr_icon = "mdi:api"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_native_unit_of_measurement = "calls"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = f"{DOMAIN}_api_calls"
        self._unsub: Callable[[], None] | None = None
        self._attrs: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        @callback
        def _kick() -> None:
            self._refresh()

        self._unsub = async_dispatcher_connect(self.hass, SIGNAL_API_METRICS_UPDATED, _kick)
        self._refresh()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attrs

    def _refresh(self) -> None:
        snapshot = get_api_metrics_snapshot(self.hass)
        providers = snapshot.get("providers") if isinstance(snapshot, dict) else {}
        per_provider_totals: dict[str, int] = {}
        if isinstance(providers, dict):
            for provider, stats in providers.items():
                if isinstance(stats, dict):
                    per_provider_totals[provider] = int(stats.get("total") or 0)
        focused_provider = _focused_provider_key(self.entry, snapshot)
        focused_stats = providers.get(focused_provider) if isinstance(providers, dict) and focused_provider else {}
        if not isinstance(focused_stats, dict):
            focused_stats = {}

        overall_total = int(snapshot.get("total_calls") or 0) if isinstance(snapshot, dict) else 0
        provider_total = int(focused_stats.get("total") or 0)
        self._attr_native_value = provider_total
        self._attrs = {
            "day_key": snapshot.get("day_key") if isinstance(snapshot, dict) else None,
            "daily_calls": int(snapshot.get("daily_calls") or 0) if isinstance(snapshot, dict) else 0,
            "month_key": snapshot.get("month_key") if isinstance(snapshot, dict) else None,
            "monthly_calls": int(snapshot.get("monthly_calls") or 0) if isinstance(snapshot, dict) else 0,
            "year_key": snapshot.get("year_key") if isinstance(snapshot, dict) else None,
            "yearly_calls": int(snapshot.get("yearly_calls") or 0) if isinstance(snapshot, dict) else 0,
            "overall_total_calls": overall_total,
            "updated_at": snapshot.get("updated_at") if isinstance(snapshot, dict) else None,
            "provider": focused_provider,
            "provider_total": provider_total,
            "provider_flows": {
                "status": int(focused_stats.get("status") or 0),
                "schedule": int(focused_stats.get("schedule") or 0),
                "position": int(focused_stats.get("position") or 0),
                "directory": int(focused_stats.get("directory") or 0),
                "usage": int(focused_stats.get("usage") or 0),
                "other": int(focused_stats.get("other") or 0),
            },
            "provider_outcomes": {
                "success": int(focused_stats.get("success") or 0),
                "error": int(focused_stats.get("error") or 0),
                "rate_limited": int(focused_stats.get("rate_limited") or 0),
                "quota_exceeded": int(focused_stats.get("quota_exceeded") or 0),
                "timeout": int(focused_stats.get("timeout") or 0),
                "network": int(focused_stats.get("network") or 0),
                "auth_error": int(focused_stats.get("auth_error") or 0),
                "unknown": int(focused_stats.get("unknown") or 0),
            },
            "by_provider": per_provider_totals,
            "providers": providers if isinstance(providers, dict) else {},
        }
        self._attr_available = True
        self.async_write_ha_state()


class _FlightDashboardPeriodApiSensor(SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_native_unit_of_measurement = "calls"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    _value_key = ""
    _period_key = ""
    _by_provider_key = ""
    _by_flow_key = ""
    _by_provider_flow_key = ""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._unsub: Callable[[], None] | None = None
        self._attrs: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        @callback
        def _kick() -> None:
            self._refresh()

        self._unsub = async_dispatcher_connect(self.hass, SIGNAL_API_METRICS_UPDATED, _kick)
        self._refresh()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attrs

    def _refresh(self) -> None:
        snapshot = get_api_metrics_snapshot(self.hass)
        period_value = int(snapshot.get(self._value_key) or 0) if isinstance(snapshot, dict) else 0
        by_provider = snapshot.get(self._by_provider_key) if isinstance(snapshot, dict) else {}
        by_flow = snapshot.get(self._by_flow_key) if isinstance(snapshot, dict) else {}
        by_provider_flow = snapshot.get(self._by_provider_flow_key) if isinstance(snapshot, dict) else {}
        focused_provider = _focused_provider_key(self.entry, snapshot)
        provider_calls = 0
        if isinstance(by_provider, dict) and focused_provider:
            provider_calls = int(by_provider.get(focused_provider) or 0)
        provider_flows = {}
        if isinstance(by_provider_flow, dict) and focused_provider:
            provider_flows = by_provider_flow.get(focused_provider) or {}
        flow_totals = by_flow if isinstance(by_flow, dict) else {}
        provider_flow_totals = provider_flows if isinstance(provider_flows, dict) else {}
        self._attr_native_value = provider_calls
        self._attrs = {
            self._period_key: snapshot.get(self._period_key) if isinstance(snapshot, dict) else None,
            "overall_calls": period_value,
            "provider": focused_provider,
            "provider_calls": provider_calls,
            "by_provider": by_provider if isinstance(by_provider, dict) else {},
            "by_flow": flow_totals,
            "provider_flows": provider_flow_totals,
            "updated_at": snapshot.get("updated_at") if isinstance(snapshot, dict) else None,
        }
        for flow_key in _API_FLOW_KEYS:
            self._attrs[f"flow_{flow_key}"] = int(flow_totals.get(flow_key) or 0)
            self._attrs[f"provider_flow_{flow_key}"] = int(provider_flow_totals.get(flow_key) or 0)
        self._attr_available = True
        self.async_write_ha_state()


class FlightDashboardApiDailySensor(_FlightDashboardPeriodApiSensor):
    _attr_name = "Flight Status Tracker API Calls Today"
    _attr_icon = "mdi:calendar-today"
    _attr_suggested_object_id = "flight_status_tracker_api_calls_today"
    _value_key = "daily_calls"
    _period_key = "day_key"
    _by_provider_key = "daily_by_provider"
    _by_flow_key = "daily_by_flow"
    _by_provider_flow_key = "daily_by_provider_flow"

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(hass, entry)
        self._attr_unique_id = f"{DOMAIN}_api_calls_today"


class FlightDashboardApiMonthlySensor(_FlightDashboardPeriodApiSensor):
    _attr_name = "Flight Status Tracker API Calls This Month"
    _attr_icon = "mdi:calendar-month"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_native_unit_of_measurement = "calls"
    _attr_suggested_object_id = "flight_status_tracker_api_calls_this_month"
    _value_key = "monthly_calls"
    _period_key = "month_key"
    _by_provider_key = "monthly_by_provider"
    _by_flow_key = "monthly_by_flow"
    _by_provider_flow_key = "monthly_by_provider_flow"

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(hass, entry)
        self._attr_unique_id = f"{DOMAIN}_api_utility_meter"


class FlightDashboardApiYearlySensor(_FlightDashboardPeriodApiSensor):
    _attr_name = "Flight Status Tracker API Calls This Year"
    _attr_icon = "mdi:calendar"
    _attr_suggested_object_id = "flight_status_tracker_api_calls_this_year"
    _value_key = "yearly_calls"
    _period_key = "year_key"
    _by_provider_key = "yearly_by_provider"
    _by_flow_key = "yearly_by_flow"
    _by_provider_flow_key = "yearly_by_provider_flow"

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(hass, entry)
        self._attr_unique_id = f"{DOMAIN}_api_calls_this_year"


class _FlightDashboardTravelMetricsSensor(SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._unsub: Callable[[], None] | None = None
        self._attrs: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        @callback
        def _kick() -> None:
            self._refresh()

        self._unsub = async_dispatcher_connect(self.hass, SIGNAL_TRAVEL_METRICS_UPDATED, _kick)
        self._refresh()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attrs


class _FlightDashboardTravelPeriodSensor(_FlightDashboardTravelMetricsSensor):
    _value_key = ""
    _period_key = ""
    _kind = "flights"

    def _refresh(self) -> None:
        snapshot = get_travel_metrics_snapshot(self.hass)
        self._attr_native_value = int(snapshot.get(self._value_key) or 0) if isinstance(snapshot, dict) else 0
        self._attrs = {
            self._period_key: snapshot.get(self._period_key) if isinstance(snapshot, dict) else None,
            "updated_at": snapshot.get("updated_at") if isinstance(snapshot, dict) else None,
            "metric_kind": self._kind,
            "last_added_flight_key": snapshot.get("last_added_flight_key") if isinstance(snapshot, dict) else None,
        }
        self._attr_available = True
        self.async_write_ha_state()


class _FlightDashboardTravelLifetimeSensor(_FlightDashboardTravelMetricsSensor):
    _value_key = ""
    _kind = "flights"

    def _refresh(self) -> None:
        snapshot = get_travel_metrics_snapshot(self.hass)
        self._attr_native_value = int(snapshot.get(self._value_key) or 0) if isinstance(snapshot, dict) else 0
        self._attrs = {
            "day_key": snapshot.get("day_key") if isinstance(snapshot, dict) else None,
            "daily_value": int(snapshot.get(f"daily_{self._kind}") or 0) if isinstance(snapshot, dict) else 0,
            "month_key": snapshot.get("month_key") if isinstance(snapshot, dict) else None,
            "monthly_value": int(snapshot.get(f"monthly_{self._kind}") or 0) if isinstance(snapshot, dict) else 0,
            "year_key": snapshot.get("year_key") if isinstance(snapshot, dict) else None,
            "yearly_value": int(snapshot.get(f"yearly_{self._kind}") or 0) if isinstance(snapshot, dict) else 0,
            "updated_at": snapshot.get("updated_at") if isinstance(snapshot, dict) else None,
            "metric_kind": self._kind,
            "last_added_flight_key": snapshot.get("last_added_flight_key") if isinstance(snapshot, dict) else None,
        }
        self._attr_available = True
        self.async_write_ha_state()


class FlightDashboardFlightsDailySensor(_FlightDashboardTravelPeriodSensor):
    _attr_name = "Flight Status Tracker Flights Today"
    _attr_icon = "mdi:airplane-clock"
    _attr_native_unit_of_measurement = "flights"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_object_id = "flight_status_tracker_flights_today"
    _value_key = "daily_flights"
    _period_key = "day_key"
    _kind = "flights"

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(hass, entry)
        self._attr_unique_id = f"{DOMAIN}_flights_today"


class FlightDashboardFlightsMonthlySensor(_FlightDashboardTravelPeriodSensor):
    _attr_name = "Flight Status Tracker Flights This Month"
    _attr_icon = "mdi:airplane-calendar"
    _attr_native_unit_of_measurement = "flights"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_object_id = "flight_status_tracker_flights_this_month"
    _value_key = "monthly_flights"
    _period_key = "month_key"
    _kind = "flights"

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(hass, entry)
        self._attr_unique_id = f"{DOMAIN}_flights_this_month"


class FlightDashboardFlightsYearlySensor(_FlightDashboardTravelPeriodSensor):
    _attr_name = "Flight Status Tracker Flights This Year"
    _attr_icon = "mdi:airplane-check"
    _attr_native_unit_of_measurement = "flights"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_object_id = "flight_status_tracker_flights_this_year"
    _value_key = "yearly_flights"
    _period_key = "year_key"
    _kind = "flights"

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(hass, entry)
        self._attr_unique_id = f"{DOMAIN}_flights_this_year"


class FlightDashboardFlightsLifetimeSensor(_FlightDashboardTravelLifetimeSensor):
    _attr_name = "Flight Status Tracker Flights Lifetime"
    _attr_icon = "mdi:airplane"
    _attr_native_unit_of_measurement = "flights"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_object_id = "flight_status_tracker_flights_lifetime"
    _value_key = "total_flights"
    _kind = "flights"

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(hass, entry)
        self._attr_unique_id = f"{DOMAIN}_flights_lifetime"


class FlightDashboardDistanceDailySensor(_FlightDashboardTravelPeriodSensor):
    _attr_name = "Flight Status Tracker Distance Today"
    _attr_icon = "mdi:map-marker-distance"
    _attr_native_unit_of_measurement = "km"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_object_id = "flight_status_tracker_distance_today"
    _value_key = "daily_distance_km"
    _period_key = "day_key"
    _kind = "distance_km"

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(hass, entry)
        self._attr_unique_id = f"{DOMAIN}_distance_today"


class FlightDashboardDistanceMonthlySensor(_FlightDashboardTravelPeriodSensor):
    _attr_name = "Flight Status Tracker Distance This Month"
    _attr_icon = "mdi:map-marker-distance"
    _attr_native_unit_of_measurement = "km"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_object_id = "flight_status_tracker_distance_this_month"
    _value_key = "monthly_distance_km"
    _period_key = "month_key"
    _kind = "distance_km"

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(hass, entry)
        self._attr_unique_id = f"{DOMAIN}_distance_this_month"


class FlightDashboardDistanceYearlySensor(_FlightDashboardTravelPeriodSensor):
    _attr_name = "Flight Status Tracker Distance This Year"
    _attr_icon = "mdi:map-marker-distance"
    _attr_native_unit_of_measurement = "km"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_object_id = "flight_status_tracker_distance_this_year"
    _value_key = "yearly_distance_km"
    _period_key = "year_key"
    _kind = "distance_km"

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(hass, entry)
        self._attr_unique_id = f"{DOMAIN}_distance_this_year"


class FlightDashboardDistanceLifetimeSensor(_FlightDashboardTravelLifetimeSensor):
    _attr_name = "Flight Status Tracker Distance Lifetime"
    _attr_icon = "mdi:map-marker-distance"
    _attr_native_unit_of_measurement = "km"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_object_id = "flight_status_tracker_distance_lifetime"
    _value_key = "total_distance_km"
    _kind = "distance_km"

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(hass, entry)
        self._attr_unique_id = f"{DOMAIN}_distance_lifetime"


class FlightDashboardProviderBlockSensor(SensorEntity):
    _attr_name = "Flight Status Tracker Provider Blocks"
    _attr_icon = "mdi:shield-alert"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = f"{DOMAIN}_provider_blocks"
        self._unsub: Callable[[], None] | None = None
        self._blocks: dict[str, Any] = {}
        self._last_signature: str | None = None

    async def async_added_to_hass(self) -> None:
        await self._update()

        @callback
        def _on_tick(_now) -> None:
            self.hass.async_create_task(self._update())

        self._unsub = async_track_time_interval(self.hass, _on_tick, PROVIDER_BLOCK_REFRESH)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._blocks

    async def _update(self) -> None:
        now = dt_util.utcnow()
        blocks = get_blocks(self.hass)
        active: dict[str, Any] = {}
        for provider, info in blocks.items():
            until = info.get("until")
            if not until:
                continue
            if now >= until:
                continue
            remaining = int((until - now).total_seconds())
            active[provider] = {
                "until": dt_util.as_local(until).isoformat(),
                "seconds_remaining": remaining,
                "reason": info.get("reason"),
            }

        new_blocks = {"blocked_count": len(active), "providers": active}
        new_sig = _stable_signature(new_blocks)
        if new_sig == self._last_signature:
            return
        self._last_signature = new_sig
        self._blocks = new_blocks
        self._attr_available = True
        self._attr_native_value = "blocked" if active else "ok"
        self.async_write_ha_state()
