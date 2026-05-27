"""Constants for Flight Status Tracker."""
from __future__ import annotations

DOMAIN = "flight_status_tracker"
STORAGE_KEY_DIRECTORY = f"{DOMAIN}.directory_cache"
DATA_UPCOMING_FLIGHTS = "upcoming_flights"

# Platforms
PLATFORMS: list[str] = ["sensor", "binary_sensor", "select", "button", "text", "date"]

# Schema
SCHEMA_VERSION = 3

# --- Dispatcher signals (used internally to notify updates) ---
SIGNAL_MANUAL_FLIGHTS_UPDATED = "flight_status_tracker_manual_flights_updated"
SIGNAL_PREVIEW_UPDATED = "flight_status_tracker_preview_updated"
SIGNAL_OPTIONS_UPDATED = "flight_status_tracker_options_updated"
SIGNAL_API_METRICS_UPDATED = "flight_status_tracker_api_metrics_updated"

# --- Events (optional; safe if unused) ---
EVENT_UPDATED = "flight_status_tracker_updated"
EVENT_PREVIEW_UPDATED = "flight_status_tracker_preview_updated"

# --- Service names: manual flights ---
SERVICE_ADD_MANUAL_FLIGHT = "add_manual_flight"
SERVICE_REMOVE_MANUAL_FLIGHT = "remove_manual_flight"
SERVICE_CLEAR_MANUAL_FLIGHTS = "clear_manual_flights"
SERVICE_REFRESH_NOW = "refresh_now"
SERVICE_PRUNE_LANDED = "prune_landed"

# --- Service names: preview flow ---
SERVICE_PREVIEW_FLIGHT = "preview_flight"
SERVICE_CONFIRM_ADD = "confirm_add"
SERVICE_CLEAR_PREVIEW = "clear_preview"
SERVICE_ADD_FLIGHT = "add_flight"

# --- Storage keys (if any modules reference them) ---
STORAGE_KEY_MANUAL = "manual_flights"
STORAGE_KEY_CACHE = "static_cache"
