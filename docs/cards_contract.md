# Companion Cards Contract (Source of Truth)

This file defines what the companion frontend repo must track:
- Cards repo: `https://github.com/tubloo/hacs-flight-status-cards`
- Primary repo: `https://github.com/tubloo/hacs-flight-status-tracker`

## Sync Rule
Any change in this integration that affects dashboard entities, service names, semantics, or UX flow MUST trigger a cards review/update before release.

## Required Card Surface
The cards repo must provide these card types:
- `flight-status-tracker-list-card` (Flight List)
- `flight-status-tracker-add-card` (Add Flight)
- `flight-status-tracker-remove-card` (Remove Flight)
- `flight-status-tracker-diagnostics-card` (Diagnostics & Control)

## Default Entity/Action Contract
Cards should default to these integration entities/actions (while allowing overrides where supported):

### Add Flight
- `button.flight_status_tracker_preview_from_inputs`
- `button.flight_status_tracker_confirm_add_preview`
- `button.flight_status_tracker_clear_preview`

### Remove Flight
- `select.flight_status_tracker_remove_flight`
- `button.flight_status_tracker_remove_selected_flight`

### Diagnostics & Control
- `sensor.flight_status_tracker_upcoming_flights`
- `sensor.flight_status_tracker_api_calls`
- `sensor.flight_status_tracker_api_utility_meter`
- `button.flight_status_tracker_refresh_now`
- `button.flight_status_tracker_remove_landed`
- `button.flight_status_tracker_refresh_directory`

## When Cards Update Is Mandatory
Update cards repo when integration changes any of the following:
- Entity IDs used by cards.
- Required service/button behavior.
- Required attributes consumed by cards (for example summary counters).
- Core add/preview/remove/diagnostics workflow.

## Release Gate (Primary Repo)
Before releasing integration:
1. Check this contract.
2. Decide if cards changes are needed.
3. If needed, ship cards changes first or as a coordinated release.
4. Note cards sync status in the release summary.
