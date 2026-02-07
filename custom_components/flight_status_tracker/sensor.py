"""Flight Dashboard sensor: exposes canonical flight timeline fields.

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

from .const import DOMAIN, SIGNAL_MANUAL_FLIGHTS_UPDATED, EVENT_UPDATED
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


SCHEMA_VERSION = 3
_LOGGER = logging.getLogger(__name__)

SCHEMA_DOC = """\
Flight Dashboard schema (v3)

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


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    upcoming = FlightDashboardUpcomingFlightsSensor(hass, entry)
    entities = [
        upcoming,
        FlightDashboardSelectedFlightSensor(hass, entry),
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
    _attr_name = "Flight Dashboard Upcoming Flights"
    _attr_icon = "mdi:airplane-clock"

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = f"{DOMAIN}_upcoming_flights"
        self._flights: list[dict[str, Any]] = []
        self._unsub: Callable[[], None] | None = None
        self._next_refresh_unsub: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        # Rebuild now
        await self._rebuild()

        # Rebuild whenever manual flights are updated
        @callback
        def _on_manual_updated() -> None:
            self.hass.async_create_task(self._rebuild())

        self._unsub = async_dispatcher_connect(self.hass, SIGNAL_MANUAL_FLIGHTS_UPDATED, _on_manual_updated)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._next_refresh_unsub:
            self._next_refresh_unsub()
            self._next_refresh_unsub = None
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
        }

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
        prune_hours_raw = int(options.get("prune_landed_hours", 1))
        prune_hours = max(1, prune_hours_raw) if auto_prune else prune_hours_raw

        start = now - timedelta(hours=include_past)
        end = now + timedelta(days=days_ahead)

        segments: list[dict[str, Any]] = []
        providers = options.get("itinerary_providers") or ["manual"]

        if "manual" in providers:
            segments.extend(await ManualItineraryProvider(self.hass).async_get_segments(start, end))

        if "tripit" in providers:
            try:
                from .providers.tripit.itinerary import TripItItineraryProvider
            except Exception as e:
                _LOGGER.debug("TripIt provider not available: %s", e)
            else:
                try:
                    segments.extend(await TripItItineraryProvider(self.hass, options).async_get_segments(start, end))
                except Exception as e:
                    _LOGGER.debug("TripIt provider failed: %s", e)

        flights = merge_segments(segments)

        # Warm directory cache on first run using known flights
        await warm_directory_cache(self.hass, options, flights)

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
            cutoff = now - timedelta(hours=prune_hours)
            removed_manual = False
            pruned: list[dict[str, Any]] = []
            for f in flights:
                if not isinstance(f, dict):
                    continue
                status = (f.get("status_state") or "").lower()
                if status not in ("arrived", "cancelled", "landed"):
                    pruned.append(f)
                    continue
                arr = (f.get("arr") or {})
                arr_time = arr.get("actual") or arr.get("estimated") or arr.get("scheduled")
                if not isinstance(arr_time, str):
                    pruned.append(f)
                    continue
                dt = dt_util.parse_datetime(arr_time)
                if not dt:
                    pruned.append(f)
                    continue
                dt = dt_util.as_utc(dt) if dt.tzinfo else dt_util.as_utc(dt_util.as_local(dt))
                if dt <= cutoff:
                    if (f.get("source") or "manual") == "manual":
                        if await async_remove_manual_flight(self.hass, f.get("flight_key", "")):
                            removed_manual = True
                    # Always drop from the list (even for non-manual sources)
                    continue
                pruned.append(f)
            flights = pruned
            if removed_manual:
                return

        for flight in flights:
            flight["editable"] = (flight.get("source") or "manual") == "manual"

            dep = (flight.get("dep") or {})
            arr = (flight.get("arr") or {})
            dep_air = (dep.get("airport") or {})
            arr_air = (arr.get("airport") or {})

            dep_sched = dep.get("scheduled")
            arr_sched = arr.get("scheduled")

            # Enrich from directory cache/providers (optional)
            airline_code = flight.get("airline_code")
            if airline_code and not flight.get("airline_name"):
                airline = await get_airline(self.hass, options, airline_code)
                if airline:
                    flight["airline_name"] = airline.get("name") or flight.get("airline_name")
                    if not flight.get("airline_logo_url"):
                        flight["airline_logo_url"] = airline.get("logo") or flight.get("airline_logo_url")

            updates: dict[str, Any] = {}
            if dep_air.get("iata") and (not dep_air.get("name") or not dep_air.get("city") or not dep_air.get("tz")):
                airport = await get_airport(self.hass, options, dep_air.get("iata"))
                if airport:
                    if not dep_air.get("name") and airport.get("name"):
                        dep_air["name"] = airport.get("name")
                        updates["dep_airport_name"] = airport.get("name")
                    if not dep_air.get("city") and airport.get("city"):
                        dep_air["city"] = airport.get("city")
                        updates["dep_airport_city"] = airport.get("city")
                    if not dep_air.get("tz") and airport.get("tz"):
                        dep_air["tz"] = airport.get("tz")
                        updates["dep_airport_tz"] = airport.get("tz")

            if arr_air.get("iata") and (not arr_air.get("name") or not arr_air.get("city") or not arr_air.get("tz")):
                airport = await get_airport(self.hass, options, arr_air.get("iata"))
                if airport:
                    if not arr_air.get("name") and airport.get("name"):
                        arr_air["name"] = airport.get("name")
                        updates["arr_airport_name"] = airport.get("name")
                    if not arr_air.get("city") and airport.get("city"):
                        arr_air["city"] = airport.get("city")
                        updates["arr_airport_city"] = airport.get("city")
                    if not arr_air.get("tz") and airport.get("tz"):
                        arr_air["tz"] = airport.get("tz")
                        updates["arr_airport_tz"] = airport.get("tz")

            # Persist directory enrichment for manual flights
            if updates and (flight.get("source") or "manual") == "manual":
                fk = flight.get("flight_key")
                if fk:
                    await async_update_manual_flight(self.hass, fk, updates)

            # No static fallback: only use directory providers / cache

            if dep_air.get("tz") and not dep_air.get("tz_short"):
                dep_air["tz_short"] = tz_short_name(dep_air.get("tz"), dep_sched)
            if arr_air.get("tz") and not arr_air.get("tz_short"):
                arr_air["tz_short"] = tz_short_name(arr_air.get("tz"), arr_sched)

            dep["airport"] = dep_air
            arr["airport"] = arr_air

            def _to_local(ts: Any, tzname: str | None) -> str | None:
                if not ts or not tzname or not isinstance(ts, str):
                    return None
                dt = dt_util.parse_datetime(ts)
                if not dt:
                    return None
                if not dt.tzinfo:
                    # Treat naive timestamps as UTC; schedule/status resolvers should normalize
                    dt = dt.replace(tzinfo=dt_util.UTC)
                try:
                    return dt.astimezone(ZoneInfo(tzname)).isoformat()
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

        self._flights = flights
        self.async_write_ha_state()
        # Notify selects/binary sensors even if state didn't change
        self.hass.bus.async_fire(EVENT_UPDATED)

        if next_refresh:
            @callback
            def _scheduled_refresh(_now) -> None:
                self.hass.async_create_task(self._rebuild())

            self._next_refresh_unsub = async_track_point_in_utc_time(self.hass, _scheduled_refresh, next_refresh)


class FlightDashboardSelectedFlightSensor(SensorEntity):
    _attr_name = "Flight Dashboard Selected Flight"
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


class FlightDashboardFr24UsageSensor(SensorEntity):
    _attr_name = "Flight Dashboard FR24 Usage"
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

        if is_blocked(self.hass, "flightradar24"):
            until = get_block_until(self.hass, "flightradar24")
            reason = get_block_reason(self.hass, "flightradar24")
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
        except FR24RateLimitError as e:
            seconds = int(e.retry_after or 3600)
            set_block(self.hass, "flightradar24", seconds, "rate_limited")
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
            self._attr_available = False
            self._usage = {"error": str(e), "sandbox": use_sandbox}
            self.async_write_ha_state()
            return
        except Exception as e:
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
    _attr_name = "Flight Dashboard Provider Blocks"
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
