# Flight Status Tracker - Detailed Guide

This document is the "long form" companion to `README.md`.

## What This Integration Does

Flight Status Tracker tracks upcoming flights in Home Assistant:

- You add flights using minimal inputs (airline code, flight number, date).
- You can preview a flight before saving it.
- The integration keeps statuses updated with smart polling and rate limiting.
- Airports/airlines are enriched internally using cached data files (no API keys required).

## Key Entities

Common entities you will use in dashboards:

- Flight list: `sensor.flight_status_tracker_upcoming_flights` (attribute `flights`)
- Add/Preview: `sensor.flight_status_tracker_add_preview` (attribute `preview`)
- Add inputs:
  - `text.flight_status_tracker_add_flight_airline`
  - `text.flight_status_tracker_add_flight_number`
  - `date.flight_status_tracker_add_flight_date`
  - `text.flight_status_tracker_add_flight_dep_airport` (optional)
  - `text.flight_status_tracker_add_flight_travellers` (optional)
  - `text.flight_status_tracker_add_flight_notes` (optional)
- Add actions:
  - `button.flight_status_tracker_preview_from_inputs`
  - `button.flight_status_tracker_confirm_add_preview`
  - `button.flight_status_tracker_clear_preview`
- Maintenance:
  - `button.flight_status_tracker_refresh_now`
  - `button.flight_status_tracker_remove_landed`
  - `button.flight_status_tracker_remove_selected_flight`
  - `button.flight_status_tracker_refresh_directory_data` (force refresh airport/airline directory cache; entity_id may vary)

Note: Home Assistant also creates `update.flight_status_tracker_update` (normal update entity).

## End-to-End Flow

1. Set inputs (airline + number + date; optionally departure airport, travellers, notes).
2. Press `button.flight_status_tracker_preview_from_inputs`.
3. Confirm the preview in `sensor.flight_status_tracker_add_preview` looks correct.
4. Press `button.flight_status_tracker_confirm_add_preview` to save the flight.
5. The flight appears in `sensor.flight_status_tracker_upcoming_flights`.

## Providers

The integration separates providers by responsibility:

- Schedule provider: used for preview/add to resolve airports and scheduled times.
- Status provider: used for ongoing updates (status/times/optional gates/terminals).
- Position provider: optional; used for live position. Disabled by default.
- Directory enrichment: airport/airline enrichment is handled internally using data files (no API keys required).

## Options (Common)

These are configured in the integration Options UI (Settings > Devices & Services > Flight Status Tracker > Options).

### List / horizon

- Include past hours: include flights that departed recently.
- Days ahead: how far into the future to include flights.
- Max flights: maximum number of flights kept in the list.
- Merge tolerance (hours): when multiple sources return the “same” flight, this prevents duplicates.

### Providers

- Schedule provider: used for preview/add to find airports + scheduled times.
- Status provider: used for ongoing status updates.
- Position provider: optional; adds live position when supported.

### Auto-removal (cleanup)

- Auto-remove past flights: if enabled, flights with status Arrived/Cancelled/Landed are removed after a delay.
- Auto-remove flight (minutes after arrival): how long after arrival before removing the flight.
  - Note: the manual prune service (`flight_status_tracker.prune_landed`) still takes an `hours` parameter.

### Polling schedule (simple explanation)

Instead of polling constantly, the integration uses a time-based schedule:

- Far from departure: poll infrequently (e.g., once per day).
- Near departure: poll more often.
- Mid-flight: poll at a steady cadence.
- Near arrival: poll more often.
- After arrival: stop polling, and then auto-removal can remove the flight from the list.

The schedule is configured using these options:

- Minimum API poll interval (minutes): a safety floor; polling will never be faster than this (minimum 5 minutes).
- Far-future threshold (hours before departure): when “far future” starts.
- Far-future poll interval (minutes): how often to poll when far in the future.
- Mid-future threshold (hours before departure): when to switch from far-future to mid-future.
- Mid-future poll interval (minutes): how often to poll in mid-future.
- Near-departure poll interval (minutes): how often to poll in the last part before departure.
- Departure focus starts/ends (minutes before/after departure) + departure focus poll interval: extra-frequent polling around takeoff.
- Mid-flight poll interval (minutes): polling between the departure focus window and the arrival focus window.
- Arrival focus starts/ends (minutes before/after arrival) + arrival focus poll interval: extra-frequent polling around landing.
- Stop polling (minutes after arrival): hard stop for status polling after arrival.

Validation rules (to prevent accidental over-polling):
- Minimum API poll interval must be **at least 5 minutes**.
- All poll intervals must be **greater than or equal** to the minimum API poll interval.
- Far-future threshold must be **greater than** the mid-future threshold.
- Stop polling (minutes after arrival) must be **at least** the arrival focus “minutes after arrival”.

Directory caching is handled internally and refreshed about monthly.

## Defaults When Options Were Never Opened

In some HA setups, `entry.options` can be empty until you open the Options UI at least once.
The integration still behaves with these defaults:

- Include past hours: 24
- Days ahead: 120
- Auto-remove past flights: true
- Auto-remove flight (minutes after arrival): 60
- Minimum API poll interval (minutes): 5
- Position provider: disabled

## Status Refresh Policy (High Level)

The integration aims to reduce provider calls:

- Status refresh frequency increases as departure approaches and while in-flight.
- After arrival/cancellation, refresh stops and the flight becomes eligible for pruning.

Exact refresh intervals depend on your configured polling schedule and minimum API poll interval.

## Upgrade Notes

### v0.3.0

- TripIt was removed (manual flights only).
- Polling minimum is now configured via `min_api_poll_minutes` in Options (replaces the old `status_ttl_minutes` key).
- Auto-removal of past flights is configured in **minutes** after arrival (instead of hours).

### v0.3.1

- Performance: reduced repeated directory-cache disk reads and reduced per-flight polling config parsing.

### v0.3.2

- Reliability: startup/reload and refresh scheduling fixes, including safe rebuild retry on errors.
- Startup behavior: manual-flight update listener now attaches before the first rebuild, reducing cases where flights appeared empty until a later trigger.
- Startup latency: directory warmup/refresh no longer blocks initial entity rendering.
- Provider throttling: FR24 block tracking now uses a consistent key across components.
- Services: idempotent service registration plus cleanup when the last integration entry unloads.
- Schedule lookup: mock fixtures no longer preempt configured providers unless explicitly selected.

### v1.0.0

- Added scheduler watchdog self-healing for stale/missed scheduling state.
- Added per-flight `ui` display block so dashboards can use precomputed backend values and reduce heavy template work.
- Watchdog diagnostics are now exposed in flight sensor attributes (`last_rebuild_at`, `next_refresh_at`, `watchdog_last_*`).

## Storage / Data

Stored locally under `/config/.storage/`:

- `flight_status_tracker.manual_flights`: your saved flights
- `flight_status_tracker.add_preview`: the current preview state
- `flight_status_tracker.ui_inputs`: persisted UI inputs (optional convenience)
- `flight_status_tracker.directory_cache`: cached airports/airlines data (refreshed about monthly)

## Privacy Notes

- API requests go directly from your Home Assistant to the configured providers using your keys.
- Travellers/notes are stored locally in Home Assistant storage.

## Troubleshooting

### Arrived flight still shows in the list

Auto-prune only removes flights when:

- `status_state` is Arrived/Cancelled/Landed, and
- arrival time is older than `now - auto_remove_after_arrival_minutes`.

Actions:

- Press `button.flight_status_tracker_refresh_now` to force a rebuild.
- Press `button.flight_status_tracker_remove_landed` (or call `flight_status_tracker.prune_landed`) to prune immediately.

### Preview is empty / never becomes ready

- Check `sensor.flight_status_tracker_add_preview` attribute `preview` for `error` / `hint`.
- Ensure schedule provider is configured and has a valid API key.
- Try adding `dep_airport` for disambiguation (same flight number can exist across routes).

### Entities show with unexpected IDs

If you previously installed older versions, Home Assistant may preserve entity IDs.
The integration works fine, but dashboards must reference the IDs present on your system.

## Uninstall / Cleanup

See `README.md` for the short checklist. If you want to remove all data, also delete the storage
keys listed above and restart Home Assistant.
