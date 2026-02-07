"""Button entities for Flight Dashboard."""
from __future__ import annotations

import asyncio
from datetime import datetime
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN, EVENT_UPDATED
from .services import SERVICE_CLEAR, SERVICE_REMOVE
from .const import SERVICE_REFRESH_NOW, SERVICE_PRUNE_LANDED
from .preview_store import async_get_preview, async_set_preview
from .manual_store import async_add_manual_flight_record

SELECT_ENTITY_ID = "select.flight_status_tracker_remove_flight"
UPCOMING_SENSOR = "sensor.flight_status_tracker_upcoming_flights"

_LOGGER = logging.getLogger(__name__)


async def _notify(_hass: HomeAssistant, title: str, message: str) -> None:
    _LOGGER.info("%s: %s", title, message)


def _extract_flight_key(selected: str) -> str:
    if " | " not in selected:
        return ""
    return selected.split(" | ", 1)[0].strip()


def _is_visible_in_upcoming(hass: HomeAssistant, flight_key: str) -> bool:
    st = hass.states.get(UPCOMING_SENSOR)
    if not st:
        return False
    flights = st.attributes.get("flights") or []
    return any(isinstance(f, dict) and f.get("flight_key") == flight_key for f in flights)


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _get_include_past_hours(hass: HomeAssistant) -> int:
    # Read current options from the first config entry
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return 6
    opts = dict(entries[0].options or {})
    try:
        return int(opts.get("include_past_hours", 6))
    except Exception:
        return 6


class FlightDashboardRemoveSelectedFlightButton(ButtonEntity):
    _attr_name = "Flight Dashboard Remove Selected Flight"
    _attr_unique_id = "flight_status_tracker_remove_selected_flight"
    _attr_icon = "mdi:airplane-remove"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_press(self) -> None:
        st = self.hass.states.get(SELECT_ENTITY_ID)
        if not st or st.state == "No flights":
            return

        flight_key = _extract_flight_key(st.state)
        if not flight_key:
            return

        await self.hass.services.async_call(
            DOMAIN, SERVICE_REMOVE, {"flight_key": flight_key}, blocking=True
        )


class FlightDashboardClearManualFlightsButton(ButtonEntity):
    _attr_name = "Flight Dashboard Clear Manual Flights"
    _attr_unique_id = "flight_status_tracker_clear_manual_flights"
    _attr_icon = "mdi:delete-sweep"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_press(self) -> None:
        await self.hass.services.async_call(DOMAIN, SERVICE_CLEAR, {}, blocking=True)


class FlightDashboardRefreshNowButton(ButtonEntity):
    _attr_name = "Flight Dashboard Refresh Now"
    _attr_unique_id = "flight_status_tracker_refresh_now"
    _attr_icon = "mdi:refresh"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_press(self) -> None:
        await self.hass.services.async_call(DOMAIN, SERVICE_REFRESH_NOW, {}, blocking=True)


class FlightDashboardPruneLandedButton(ButtonEntity):
    _attr_name = "Flight Dashboard Remove Landed Flights"
    _attr_unique_id = "flight_status_tracker_remove_landed"
    _attr_icon = "mdi:airplane-off"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_press(self) -> None:
        await self.hass.services.async_call(DOMAIN, SERVICE_PRUNE_LANDED, {}, blocking=True)


class FlightDashboardConfirmAddPreviewButton(ButtonEntity):
    _attr_name = "Flight Dashboard Confirm Add Preview"
    _attr_unique_id = "flight_status_tracker_confirm_add_preview"
    _attr_icon = "mdi:check-circle-outline"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_press(self) -> None:
        preview = await async_get_preview(self.hass)

        if not preview:
            await _notify(self.hass, "Flight Dashboard", "No preview found. Run Preview Flight first.")
            return

        if not preview.get("ready"):
            await _notify(self.hass, "Flight Dashboard", "Preview is incomplete. Fix it before confirming.")
            return

        flight = preview.get("flight")
        if not isinstance(flight, dict):
            await _notify(self.hass, "Flight Dashboard", "Preview data is invalid. Clear and retry.")
            return

        # Ensure travellers always exists (empty allowed)
        if "travellers" not in flight or flight["travellers"] is None:
            flight["travellers"] = []

        try:
            flight_key = await async_add_manual_flight_record(self.hass, flight)
        except Exception as e:
            await _notify(self.hass, "Flight Dashboard — Add failed", str(e))
            return
        added = {"flight_key": flight_key, **flight}
        await async_set_preview(self.hass, None)

        # Trigger rebuild of upcoming flights sensor
        self.hass.bus.async_fire(EVENT_UPDATED)

        # Wait a bit for sensor rebuild
        visible = False
        for _ in range(10):  # ~2 seconds
            await asyncio.sleep(0.2)
            if flight_key and _is_visible_in_upcoming(self.hass, flight_key):
                visible = True
                break

        if visible:
            await _notify(
                self.hass,
                "Flight Dashboard — Added ✅",
                f"Now visible:\n{added.get('airline_code')} {added.get('flight_number')} "
                f"{added.get('dep_airport')} → {added.get('arr_airport')}\n"
                f"Flight key: {flight_key}",
            )
            return

        # Not visible: check if filtered out by include_past_hours
        include_past_hours = _get_include_past_hours(self.hass)
        dep = _parse_iso(added.get("scheduled_departure"))
        if dep:
            dep_utc = dt_util.as_utc(dep) if dep.tzinfo else dt_util.as_utc(dt_util.as_local(dep))
            now = dt_util.utcnow()
            hours_ago = (now - dep_utc).total_seconds() / 3600.0
            if hours_ago > include_past_hours:
                await _notify(
                    self.hass,
                    "Flight Dashboard — Saved (filtered) ⚠️",
                    "Flight was saved, but it's not shown because it departed too long ago.\n"
                    f"Departed ~{hours_ago:.1f}h ago, include_past_hours={include_past_hours}.\n"
                    "Fix: increase 'Include past hours' in Flight Dashboard options.\n"
                    f"Flight key: {flight_key}",
                )
                return

        await _notify(
            self.hass,
            "Flight Dashboard — Saved ⚠️",
            "Flight was saved, but it didn't appear in Upcoming Flights yet.\n"
            "If it's not filtered, then the sensor/provider wiring needs checking.\n"
            f"Flight key: {flight_key}",
        )


class FlightDashboardClearAddPreviewButton(ButtonEntity):
    _attr_name = "Flight Dashboard Clear Add Preview"
    _attr_unique_id = "flight_status_tracker_clear_add_preview"
    _attr_icon = "mdi:close-circle-outline"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_press(self) -> None:
        await async_set_preview(self.hass, None)
        self.hass.bus.async_fire(EVENT_UPDATED)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    async_add_entities(
        [
            FlightDashboardRemoveSelectedFlightButton(hass),
            FlightDashboardClearManualFlightsButton(hass),
            FlightDashboardRefreshNowButton(hass),
            FlightDashboardPruneLandedButton(hass),
            FlightDashboardConfirmAddPreviewButton(hass),
            FlightDashboardClearAddPreviewButton(hass),
        ]
    )
