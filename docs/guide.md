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

- Include past hours: include flights that departed recently (helps with timezones and near-term flights).
- Days ahead: horizon window for upcoming flights.
- Auto-remove past flights: automatically removes Arrived/Cancelled/Landed manual flights after the cutoff.
- Remove past flights after (hours): delay after arrival before removal (minimum 1 hour).
Directory caching is handled internally and refreshed about monthly.

## Defaults When Options Were Never Opened

In some HA setups, `entry.options` can be empty until you open the Options UI at least once.
The integration still behaves with these defaults:

- Include past hours: 24
- Days ahead: 120
- Auto-remove past flights: true
- Remove past flights after (hours): 1
- Position provider: disabled

## Status Refresh Policy (High Level)

The integration aims to reduce provider calls:

- Status refresh frequency increases as departure approaches and while in-flight.
- After arrival/cancellation, refresh stops and the flight becomes eligible for pruning.

Exact refresh intervals can vary by provider and configured TTL.

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
- arrival time is older than `now - prune_landed_hours`.

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
