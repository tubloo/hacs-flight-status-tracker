"""Preview sensor entity: sensor.flight_status_tracker_add_preview"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback

from .preview_store import get_preview


@dataclass
class FlightDashboardAddPreviewSensor(SensorEntity):
    hass: HomeAssistant

    _attr_name = "Flight Status Tracker Add Preview"
    _attr_icon = "mdi:airplane-plus"
    _attr_unique_id = "flight_status_tracker_add_preview"

    @property
    def native_value(self) -> str:
        st = get_preview(self.hass)
        if st.error:
            return "error"
        return "ready" if st.ready else "incomplete"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = get_preview(self.hass)
        return {
            "ready": st.ready,
            "error": st.error,
            "hint": st.hint,
            "input": st.input or {},
            "flight": st.flight,
        }

    @callback
    def async_refresh(self) -> None:
        self.async_write_ha_state()
