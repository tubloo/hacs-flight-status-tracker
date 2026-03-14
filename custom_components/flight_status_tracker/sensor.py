"""Flight Status Tracker sensor: exposes canonical flight timeline fields.

Sensor rebuilds automatically when manual flights change (dispatcher signal).
"""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any, Callable
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_interval, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_MANUAL_FLIGHTS_UPDATED, SIGNAL_API_METRICS_UPDATED, EVENT_UPDATED
from .coordinator_agg import merge_segments
from .providers.manual.itinerary import ManualItineraryProvider
from .manual_store import async_remove_manual_flight, async_update_manual_flight
from .status_manager import async_update_statuses
from .status_resolver import _normalize_iso_in_tz
from .tz_short import tz_short_name
from .directory import get_airport, get_airline, warm_directory_cache
from .providers.flightradar24.client import FR24Client, FR24RateLimitError, FR24Error
from .rate_limit import get_blocks, is_blocked, get_block_until, get_block_reason, set_block
from .selected import get_selected_flight, get_flight_position
from .api_metrics import get_api_metrics_snapshot, record_api_call


SCHEMA_VERSION = 3
_LOGGER = logging.getLogger(__name__)
_DIR_ENRICH_STATE_KEY = "dir_enrich_state"


def _dir_enrich_state(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """In-memory guard to avoid repeated directory lookups for unchanged identifiers."""
    return hass.data.setdefault(DOMAIN, {}).setdefault(_DIR_ENRICH_STATE_KEY, {})


def _parse_dt(val: Any):
    if not isinstance(val, str):
        return None
    return dt_util.parse_datetime(val)


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

    status = flight.get("status") if isinstance(flight.get("status"), dict) else {}
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
        "route_progress_at_poll_pct": progress_pct,
        "plane_x_at_poll_pct": plane_x,
        "progress_start_ts": progress_start_ts,
        "progress_end_ts": progress_end_ts,
        "arr_target_ts": arr_target_ts,
        "status_error_text": status_error_text,
        "updated_ago_min": updated_ago_min,
        "updated_abs": updated_abs,
        "source": status.get("provider") or "—",
    }

SCHEMA_DOC = """\
Flight Status Tracker schema (v3)

Per flight:
- dep.scheduled/estimated/actual
- arr.scheduled/estimated/actual
- dep.scheduled_local/estimated_local/actual_local (airport local time)
- arr.scheduled_local/estimated_local/actual_local (airport local time)
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

# Options keys (keep local to avoid config_flow import)
CONF_FR24_API_KEY = "fr24_api_key"
CONF_FR24_SANDBOX_KEY = "fr24_sandbox_key"
CONF_FR24_USE_SANDBOX = "fr24_use_sandbox"
CONF_FR24_API_VERSION = "fr24_api_version"

FR24_USAGE_REFRESH = timedelta(minutes=30)
PROVIDER_BLOCK_REFRESH = timedelta(minutes=1)
WATCHDOG_CHECK_INTERVAL = timedelta(minutes=2)
WATCHDOG_STALE_REBUILD = timedelta(minutes=15)
WATCHDOG_OVERDUE_GRACE = timedelta(minutes=3)
WATCHDOG_KICK_DEBOUNCE = timedelta(minutes=5)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    upcoming = FlightDashboardUpcomingFlightsSensor(hass, entry)
    entities = [
        upcoming,
        FlightDashboardSelectedFlightSensor(hass, entry),
        FlightDashboardApiMetricsSensor(hass, entry),
        FlightDashboardFr24UsageSensor(hass, entry),
        FlightDashboardProviderBlockSensor(hass, entry),
    ]
    try:
        from .preview_sensor import FlightDashboardAddPreviewSensor
    except Exception:
        FlightDashboardAddPreviewSensor = None  # type: ignore
    if FlightDashboardAddPreviewSensor:
        entities.append(FlightDashboardAddPreviewSensor(hass, entry))
    async_add_entities(entities, True)
    hass.data.setdefault(DOMAIN, {}).setdefault("upcoming_sensors", {})[entry.entry_id] = upcoming


class FlightDashboardUpcomingFlightsSensor(SensorEntity):
    _attr_name = "Flight Status Tracker Upcoming Flights"
    _attr_icon = "mdi:airplane-clock"

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = f"{DOMAIN}_upcoming_flights"
        self._flights: list[dict[str, Any]] = []
        self._unsub: Callable[[], None] | None = None
        self._next_refresh_unsub: Callable[[], None] | None = None
        self._watchdog_unsub: Callable[[], None] | None = None
        self._last_rebuild_at: datetime | None = None
        self._next_refresh_at: datetime | None = None
        self._last_rebuild_error: str | None = None
        self._watchdog_last_check_at: datetime | None = None
        self._watchdog_last_kick_at: datetime | None = None

    async def async_added_to_hass(self) -> None:
        # Rebuild whenever manual flights are updated
        @callback
        def _on_manual_updated() -> None:
            self.hass.async_create_task(self._run_rebuild_safe("manual_update"))

        self._unsub = async_dispatcher_connect(self.hass, SIGNAL_MANUAL_FLIGHTS_UPDATED, _on_manual_updated)

        @callback
        def _watchdog_tick(_now) -> None:
            self.hass.async_create_task(self._watchdog_check())

        self._watchdog_unsub = async_track_time_interval(self.hass, _watchdog_tick, WATCHDOG_CHECK_INTERVAL)

        # Rebuild now
        await self._run_rebuild_safe("startup")

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
        sensors = self.hass.data.get(DOMAIN, {}).get("upcoming_sensors") or {}
        if isinstance(sensors, dict):
            sensors.pop(self.entry.entry_id, None)

    @property
    def native_value(self) -> str:
        n = len(self._flights)
        return f"{n} flight" if n == 1 else f"{n} flights"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "schema_doc": SCHEMA_DOC,
            "schema_example": SCHEMA_EXAMPLE,
            "flights": self._flights,
            "last_rebuild_at": self._last_rebuild_at.isoformat() if self._last_rebuild_at else None,
            "next_refresh_at": self._next_refresh_at.isoformat() if self._next_refresh_at else None,
            "last_rebuild_error": self._last_rebuild_error,
            "watchdog_last_check_at": self._watchdog_last_check_at.isoformat() if self._watchdog_last_check_at else None,
            "watchdog_last_kick_at": self._watchdog_last_kick_at.isoformat() if self._watchdog_last_kick_at else None,
        }

    async def _run_rebuild_safe(self, reason: str) -> None:
        """Run rebuild and ensure we always retry after an unexpected failure."""
        try:
            await self._rebuild()
            self._last_rebuild_at = dt_util.utcnow()
            self._last_rebuild_error = None
        except Exception:
            self._last_rebuild_error = f"rebuild_failed:{reason}"
            _LOGGER.exception("Flight list rebuild failed (%s)", reason)
            if self._next_refresh_unsub:
                return
            retry_at = dt_util.utcnow() + timedelta(minutes=5)
            self._next_refresh_at = retry_at

            @callback
            def _retry(_now) -> None:
                self.hass.async_create_task(self._run_rebuild_safe("retry_after_error"))

            self._next_refresh_unsub = async_track_point_in_utc_time(self.hass, _retry, retry_at)

    async def _watchdog_check(self) -> None:
        self._watchdog_last_check_at = dt_util.utcnow()
        now = dt_util.utcnow()
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
        if self._next_refresh_at is not None and now > (self._next_refresh_at + WATCHDOG_OVERDUE_GRACE):
            stalled = True
        elif self._next_refresh_at is not None and self._next_refresh_unsub is None:
            stalled = True
        elif self._last_rebuild_at is None:
            stalled = True
        elif now - self._last_rebuild_at > WATCHDOG_STALE_REBUILD:
            stalled = True

        if not stalled:
            return
        if self._watchdog_last_kick_at and (now - self._watchdog_last_kick_at) < WATCHDOG_KICK_DEBOUNCE:
            return

        self._watchdog_last_kick_at = now
        _LOGGER.warning("Watchdog kick: forcing safe rebuild due to stale scheduler state")
        await self._run_rebuild_safe("watchdog_kick")

    async def _warm_directory_cache_safe(self, options: dict[str, Any], flights: list[dict[str, Any]]) -> None:
        try:
            await warm_directory_cache(self.hass, options, flights)
        except Exception:
            _LOGGER.debug("Directory warmup failed", exc_info=True)

    async def _rebuild(self) -> None:
        """Rebuild the flight list and schedule the next smart refresh."""
        if self._next_refresh_unsub:
            self._next_refresh_unsub()
            self._next_refresh_unsub = None

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
                if status not in ("arrived", "cancelled", "canceled", "landed"):
                    pruned.append(f)
                    continue
                arr = (f.get("arr") or {})
                arr_air = (arr.get("airport") or {})
                arr_time = arr.get("actual") or arr.get("estimated") or arr.get("scheduled")
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
            needs_airline = bool(airline_code)
            if needs_airline:
                airline = await get_airline(self.hass, options, airline_code)
                if airline:
                    resolved_name = airline.get("name")
                    if resolved_name and (airline_changed or flight.get("airline_name") != resolved_name):
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

            dep["scheduled_local"] = _to_local(dep.get("scheduled"), dep_air.get("tz"))
            dep["estimated_local"] = _to_local(dep.get("estimated"), dep_air.get("tz"))
            dep["actual_local"] = _to_local(dep.get("actual"), dep_air.get("tz"))
            arr["scheduled_local"] = _to_local(arr.get("scheduled"), arr_air.get("tz"))
            arr["estimated_local"] = _to_local(arr.get("estimated"), arr_air.get("tz"))
            arr["actual_local"] = _to_local(arr.get("actual"), arr_air.get("tz"))

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

        self._flights = flights
        self.async_write_ha_state()
        # Notify selects/binary sensors even if state didn't change
        self.hass.bus.async_fire(EVENT_UPDATED)

        if next_refresh:
            @callback
            def _scheduled_refresh(_now) -> None:
                self.hass.async_create_task(self._run_rebuild_safe("scheduled_refresh"))

            self._next_refresh_unsub = async_track_point_in_utc_time(self.hass, _scheduled_refresh, next_refresh)
            self._next_refresh_at = next_refresh
        else:
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
        self._flight = get_selected_flight(self.hass)
        self.async_write_ha_state()


class FlightDashboardApiMetricsSensor(SensorEntity):
    _attr_name = "Flight Status Tracker API Calls"
    _attr_icon = "mdi:api"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_native_unit_of_measurement = "calls"

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

        self._attr_native_value = int(snapshot.get("total_calls") or 0) if isinstance(snapshot, dict) else 0
        self._attrs = {
            "updated_at": snapshot.get("updated_at") if isinstance(snapshot, dict) else None,
            "by_provider": per_provider_totals,
            "providers": providers if isinstance(providers, dict) else {},
        }
        self._attr_available = True
        self.async_write_ha_state()


class FlightDashboardFr24UsageSensor(SensorEntity):
    _attr_name = "Flight Status Tracker FR24 Usage"
    _attr_icon = "mdi:chart-box"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_native_unit_of_measurement = "credits"

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = f"{DOMAIN}_fr24_usage"
        self._unsub: Callable[[], None] | None = None
        self._usage: dict[str, Any] | None = None

    async def async_added_to_hass(self) -> None:
        await self._update()

        @callback
        def _on_tick(_now) -> None:
            self.hass.async_create_task(self._update())

        self._unsub = async_track_time_interval(self.hass, _on_tick, FR24_USAGE_REFRESH)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._usage or {}

    async def _update(self) -> None:
        options = dict(self.entry.options)
        use_sandbox = bool(options.get(CONF_FR24_USE_SANDBOX, False))
        fr24_key = (options.get(CONF_FR24_API_KEY) or "").strip()
        fr24_sandbox_key = (options.get(CONF_FR24_SANDBOX_KEY) or "").strip()
        api_version = (options.get(CONF_FR24_API_VERSION) or "v1").strip() or "v1"

        key = fr24_sandbox_key if use_sandbox and fr24_sandbox_key else fr24_key
        if not key:
            self._attr_available = False
            self._usage = {"error": "missing_api_key", "sandbox": use_sandbox}
            self.async_write_ha_state()
            return

        if is_blocked(self.hass, "fr24"):
            until = get_block_until(self.hass, "fr24")
            reason = get_block_reason(self.hass, "fr24")
            self._attr_available = True
            self._usage = {
                "blocked": True,
                "blocked_until": dt_util.as_local(until).isoformat() if until else None,
                "blocked_reason": reason,
                "sandbox": use_sandbox,
            }
            self._attr_native_value = None
            self.async_write_ha_state()
            return

        client = FR24Client(self.hass, key, use_sandbox=use_sandbox, api_version=api_version)
        try:
            data = await client.usage()
            record_api_call(self.hass, "flightradar24", flow="usage", outcome="success")
        except FR24RateLimitError as e:
            record_api_call(self.hass, "flightradar24", flow="usage", outcome="rate_limited")
            seconds = int(e.retry_after or 3600)
            set_block(self.hass, "fr24", seconds, "rate_limited")
            self._attr_available = True
            self._usage = {
                "blocked": True,
                "blocked_until": (dt_util.utcnow() + timedelta(seconds=seconds)).isoformat(),
                "blocked_reason": "rate_limited",
                "sandbox": use_sandbox,
            }
            self._attr_native_value = None
            self.async_write_ha_state()
            return
        except FR24Error as e:
            record_api_call(self.hass, "flightradar24", flow="usage", outcome="error")
            self._attr_available = False
            self._usage = {"error": str(e), "sandbox": use_sandbox}
            self.async_write_ha_state()
            return
        except Exception as e:
            record_api_call(self.hass, "flightradar24", flow="usage", outcome="error")
            self._attr_available = False
            self._usage = {"error": str(e), "sandbox": use_sandbox}
            self.async_write_ha_state()
            return

        items = data.get("data") or []
        total_credits = 0
        total_requests = 0
        by_endpoint: list[dict[str, Any]] = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                total_credits += int(item.get("credits") or 0)
                total_requests += int(item.get("request_count") or 0)
                by_endpoint.append(
                    {
                        "endpoint": item.get("endpoint"),
                        "credits": int(item.get("credits") or 0),
                        "requests": int(item.get("request_count") or 0),
                    }
                )

        self._attr_available = True
        self._attr_native_value = total_credits
        self._usage = {
            "credits_used": total_credits,
            "requests": total_requests,
            "endpoints": by_endpoint,
            "sandbox": use_sandbox,
            "api_version": api_version,
        }
        self.async_write_ha_state()


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

        self._blocks = {"blocked_count": len(active), "providers": active}
        self._attr_available = True
        self._attr_native_value = "blocked" if active else "ok"
        self.async_write_ha_state()
