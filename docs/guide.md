# Flight Status Tracker - Detailed Guide

This document is the "long form" companion to `README.md`.

## What This Integration Does

Flight Status Tracker tracks upcoming flights in Home Assistant:

- You add flights using minimal inputs (airline code, flight number, date).
- You can preview a flight before saving it.
- The integration keeps statuses updated with smart polling and rate limiting.
- Airports/airlines can be enriched from inbuilt datasets or provider-first lookup (configurable).

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
- Schedule provider is strict: preview/add only queries the provider you selected.
- Status provider: used for ongoing updates (status/times/optional gates/terminals).
- Position provider: optional; used for live position. Disabled by default.
- Directory enrichment: airport/airline metadata can use:
  - `inbuilt` (OpenFlights/Airportsdata), or
  - `provider` (configured providers first: FlightAPI/Aviationstack/AirLabs, then fallback to inbuilt).

## Options (Common)

These are configured in the integration Options UI (Settings > Devices & Services > Flight Status Tracker > Options).

### List / horizon

- Include past hours: include flights that departed recently.
- Days ahead: how far into the future to include flights.
- Max flights: maximum number of flights kept in the list.

### Providers

- Schedule provider: used for preview/add to find airports + scheduled times.
  - There is no schedule `auto` mode. You must select one schedule provider.
  - Preview/add does not cross-fallback to other schedule providers.
- Status provider: used for ongoing status updates.
- Position provider: optional; adds live position when supported.
- Airline/Airport data source is always hybrid: configured providers first (FlightAPI/Aviationstack/AirLabs), then fallback to inbuilt cache.

Provider capability summary:
- `flightapi`: schedule + status; no live position.
- `aviationstack`: schedule + status (depends on plan access); no live position.
- `airlabs`: schedule + status; limited live position fields via status payload.
- `flightradar24`: schedule + status + live position.
- `opensky`: status/position enrichment only (no schedule lookup).
- `local`: status simulation only (no external API).
- `mock`: testing only.

Aviationstack note:
- Current-day schedule queries use `flight_schedules`/`timetable`; future-day queries use `flightsFuture`.
- `flightsFuture` is provider-restricted to sufficiently future dates (provider validates this window).
- Aviationstack documents strict short-window throttling on schedule endpoints; avoid back-to-back calls inside ~10 seconds.
- Timetable/schedule endpoint access depends on your Aviationstack plan.
- If the selected provider does not return a record for the requested date/flight, preview returns `no_match`.

Validation rule:
- Options must include at least one usable external provider with credentials.
- Local/mock-only setups are blocked in Options validation.

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

- Far-Future threshold (hours before departure): when Far-Future starts.
- Far-Future poll interval (minutes): how often to poll in Far-Future.
- Prepare to Travel poll interval (minutes): how often to poll before the Take Off window starts.
- Take Off window starts/ends (minutes before/after departure) + Take Off poll interval: extra-frequent polling around takeoff.
- Mid Flight poll interval (minutes): polling between the Take Off window and the Landing window.
- Landing window starts/ends (minutes before/after arrival) + Landing poll interval: extra-frequent polling around landing.
- Post Arrival stop polling (minutes): hard stop for status polling after arrival.

Validation rules (to prevent accidental over-polling):
- Far-Future / Prepare to Travel / Mid Flight poll intervals must be **at least 5 minutes**.
- Take Off and Landing poll intervals may be set as low as **1 minute**.
- Post Arrival stop polling (minutes) must be **at least** the Landing window “minutes after arrival”.

Directory caching is handled internally and refreshed about monthly.

## Defaults When Options Were Never Opened

In some HA setups, `entry.options` can be empty until you open the Options UI at least once.
The integration still behaves with these defaults:

- Include past hours: 24
- Days ahead: 120
- Auto-remove past flights: true
- Auto-remove flight (minutes after arrival): 60
- Internal minimum API poll floor: 5 (not user-configurable)
- Position provider: disabled

## Status Refresh Policy (High Level)

The integration aims to reduce provider calls:

- Status refresh frequency increases as departure approaches and while in-flight.
- After arrival/cancellation, refresh stops and the flight becomes eligible for pruning.

Exact refresh intervals depend on your configured polling schedule.

## API Call Metrics

The integration exposes a diagnostic sensor:

- `sensor.flight_status_tracker_api_calls`

Sensor state:

- Total provider API calls recorded by the integration (monotonic counter).

Key attributes:

- `by_provider`: compact totals per provider.
- `providers`: detailed per-provider counters split by flow (`status`, `schedule`, `position`, `usage`) and outcomes (`success`, `rate_limited`, `quota_exceeded`, etc.).

This gives a single, consistent attribute model across providers for dashboard cards and helper/template sensors.

## Upgrade Notes

### v0.3.0

- TripIt was removed (manual flights only).
- Polling uses an internal 5-minute floor for non-critical windows.
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

### v1.0.1

- Polling windows renamed/simplified to: Far-Future, Prepare to Travel, Take Off, Mid Flight, Landing, Post Arrival.
- Prepare to Travel now replaces the previous mid/near pre-departure split.
- Take Off and Landing poll intervals now allow a minimum of 1 minute.

### v1.0.3

- Removed schedule provider `auto` mode.
- Schedule lookup now always uses the explicitly selected provider.
- For backward compatibility, legacy saved `schedule_provider: auto` is coerced to `flightapi`.

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
- Ensure the selected schedule provider has data for that date/flight; preview does not fallback to other providers.
- Try adding `dep_airport` for disambiguation (same flight number can exist across routes).

### Entities show with unexpected IDs

If you previously installed older versions, Home Assistant may preserve entity IDs.
The integration works fine, but dashboards must reference the IDs present on your system.

## Uninstall / Cleanup

See `README.md` for the short checklist. If you want to remove all data, also delete the storage
keys listed above and restart Home Assistant.
