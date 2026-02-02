# Flight Dashboard (Home Assistant)

> This project was created with the assistance of OpenAI Codex.

Flight Dashboard is a Home Assistant integration that tracks upcoming flights and their status.

## Privacy & Data Handling

Flight Dashboard is **per‑user and BYO‑API‑keys**. It does **not** operate any shared
backend and does **not** transmit your travellers/notes to third parties. Flight
status and schedule lookups are performed directly from your Home Assistant
instance to the configured provider APIs using your own keys.

## Installation

### HACS (recommended)
1. Open **HACS → Integrations**.
2. Click **Explore & Download Repositories** and search for **Flight Dashboard**.
3. Download and restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration** and search **Flight Dashboard**.

### Manual
1. Copy `custom_components/flight_dashboard` into `/config/custom_components/flight_dashboard`.
2. Restart Home Assistant.
3. Add the integration in **Settings → Devices & Services**.

## Setup Package (Helpers + Scripts)

The easiest way to create the required helpers/scripts is to include the
package file in your configuration and restart HA:

1) Ensure `packages:` is enabled in `configuration.yaml`:
```yaml
homeassistant:
  packages: !include_dir_named packages
```

2) Copy the package file:
```
/config/packages/flight_dashboard_add_flow.yaml
```

3) Restart Home Assistant.

This will create:
- `input_text.fd_airline`
- `input_text.fd_flight_number`
- `input_text.fd_dep_airport` (optional, for disambiguation)
- `input_text.fd_travellers`
- `input_text.fd_notes`
- `input_datetime.fd_flight_date`
- `script.fd_preview_flight`
- `script.fd_confirm_add`
- `script.fd_clear_preview`

### Manual Helper + Script Creation (UI)

If you prefer the UI instead of packages:

**Helpers**
1. Settings → Devices & Services → Helpers → **Create Helper**
2. Create these:
   - **Text**: `input_text.fd_airline`
   - **Text**: `input_text.fd_flight_number`
   - **Text**: `input_text.fd_travellers`
   - **Text**: `input_text.fd_notes`
   - **Date & Time**: `input_datetime.fd_flight_date`

**Scripts**
1. Settings → Automations & Scenes → **Scripts** → Create Script
2. Create:
   - `script.fd_preview_flight`
   - `script.fd_confirm_add`
   - `script.fd_clear_preview`

You can copy the script YAML directly from
`/config/packages/flight_dashboard_add_flow.yaml` if you want exact parity.

## Required / Recommended Frontend Components

These Lovelace examples use the following custom cards:

**Required for the examples below**
- **Mushroom** cards (`custom:mushroom-*`)
- **auto-entities** (`custom:auto-entities`)
- **map-card** (`custom:map-card`)

If you don’t have these, install them via HACS → Frontend, then restart HA.

## Helpers & Scripts (Add Flight Flow)

The Add Flight card expects these helpers/scripts to exist. Use the provided
`/config/packages/flight_dashboard_add_flow.yaml` or create them in UI:

- `input_text.fd_airline`
- `input_text.fd_flight_number`
- `input_text.fd_travellers`
- `input_text.fd_notes`
- `input_datetime.fd_flight_date`
- `script.fd_preview_flight`
- `script.fd_confirm_add`
- `script.fd_clear_preview`

## Post‑Install Checklist

1) **Configure the integration**  
   Settings → Devices & Services → Add Integration → Flight Dashboard  
   Add API keys, choose providers, set cache/refresh options.

2) **Install required frontend cards** (via HACS → Frontend)  
   - Mushroom  
   - auto‑entities  
   - map-card

3) **Create helpers + scripts**  
   Use the **Setup Package** above or create them in UI.

4) **Add Lovelace dashboards/cards**  
   Copy the Flight Status and Manage Flights dashboards from the examples below.

## Status Refresh Policy

By default, **no status polling is performed until the flight is within 6 hours
of scheduled departure**. After that, refresh intervals tighten as departure
approaches and while in‑flight, and stop several hours after arrival.
You can add flights with minimal inputs (airline code, flight number, date) and let the integration enrich the details using provider APIs.

## Features
- Add flights with minimal inputs.
- Preview and confirm before saving.
- Automatic status refresh with smart API call rationing.
- Manual flights are editable; provider-sourced flights are read-only.
- On-demand refresh button/service.
- Schedule provider and status provider can be set independently.
- Optional auto-removal of landed/cancelled manual flights.
- Optional airport/airline directory cache (default 90 days).

## Installation (Manual)
1. Copy `custom_components/flight_dashboard` into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Add the integration from **Settings → Devices & Services**.

## Configuration
All configuration is done via the UI (config flow).

Key points:
- **Schedule provider** is used for preview/add (must return scheduled times).
- **Status provider** is used for live status updates.
- FR24 is great for status, but does not always return scheduled times. Use AirLabs or Aviationstack for schedule.
- FR24 sandbox: enable **Use FR24 sandbox** and set the sandbox key.
- **Auto-remove landed flights** is optional and applies only to manual flights.
- **Delay grace (minutes)** controls when a flight is considered delayed (default 10 min).
- **Preview displays scheduled times only** (est/act appear after the flight is added).

### Required inputs when adding a flight
```
airline, flight_number, date
```
Optional:
```
dep_airport
```

### Delay Status Logic
Computed field: `delay_status` (on_time | delayed | cancelled | arrived | unknown)  
Computed field: `delay_minutes` (minutes vs sched; arrival preferred if available)

Logic:
- If status_state == cancelled → `cancelled`
- If status_state == landed → `arrived`
- If arrival estimated/actual is available:
  - arrival_delay = arrival_est_or_act − arrival_scheduled
  - delayed if arrival_delay > grace
  - otherwise on_time
- Else if departure estimated/actual is available:
  - departure_delay = dep_est_or_act − dep_scheduled
  - delayed if departure_delay > grace
  - otherwise on_time
- If no sched/est/act → `unknown`

### Supported providers
**Schedule provider**
- Auto (best available)
- Aviationstack
- AirLabs
- FlightAPI.io
- Flightradar24
- Mock

**Status provider**
- Flightradar24 (default)
- Aviationstack
- AirLabs
- FlightAPI.io
- OpenSky (tracking-only)
- Local (no API calls)
- Mock

## Directory Cache
- Enable **Cache airports/airlines** to store directory lookups in `.storage`.
- **Directory cache TTL (days)** controls refresh age (default 90).
- Cache is only used when a field is missing (name, city, tz, airline name/logo).
- If the cache and provider miss, the integration falls back to OpenFlights datasets
  (airlines.dat / airports.dat) on-demand and stores results in the cache.

## Lovelace Examples

Below are **full dashboard YAMLs** and **individual card snippets**.

### Flight Status Dashboard (full)
```yaml
title: Flight Status
views:
  - title: Flights
    path: flights
    cards:
      - type: custom:auto-entities
        card:
          type: entities
          title: Flights
          show_header_toggle: false
        filter:
          template: >
            {% set flights = state_attr('sensor.flight_dashboard_upcoming_flights','flights') or [] %} [
            {%- for f in flights -%}

              {

                "{%- set dep_date = f.dep.scheduled and (as_timestamp(f.dep.scheduled) | timestamp_custom('%d %b', true)) or '—' -%}"

                "{%- set dep_sched_local_dt = f.dep.scheduled_local and as_datetime(f.dep.scheduled_local) -%}"
                "{%- set dep_est_local_dt = f.dep.estimated_local and as_datetime(f.dep.estimated_local) -%}"
                "{%- set dep_act_local_dt = f.dep.actual_local and as_datetime(f.dep.actual_local) -%}"

                "{%- set arr_sched_local_dt = f.arr.scheduled_local and as_datetime(f.arr.scheduled_local) -%}"
                "{%- set arr_est_local_dt = f.arr.estimated_local and as_datetime(f.arr.estimated_local) -%}"
                "{%- set arr_act_local_dt = f.arr.actual_local and as_datetime(f.arr.actual_local) -%}"

                "{%- set dep_sched_local = dep_sched_local_dt and (dep_sched_local_dt.strftime('%H:%M')) -%}"
                "{%- set dep_est_local = dep_est_local_dt and (dep_est_local_dt.strftime('%H:%M')) -%}"
                "{%- set dep_act_local = dep_act_local_dt and (dep_act_local_dt.strftime('%H:%M')) -%}"

                "{%- set arr_sched_local = arr_sched_local_dt and (arr_sched_local_dt.strftime('%H:%M')) -%}"
                "{%- set arr_est_local = arr_est_local_dt and (arr_est_local_dt.strftime('%H:%M')) -%}"
                "{%- set arr_act_local = arr_act_local_dt and (arr_act_local_dt.strftime('%H:%M')) -%}"

                "{%- set dep_sched_viewer_dt = f.dep.scheduled and (as_datetime(f.dep.scheduled) | as_local) -%}"
                "{%- set dep_est_viewer_dt = f.dep.estimated and (as_datetime(f.dep.estimated) | as_local) -%}"
                "{%- set dep_act_viewer_dt = f.dep.actual and (as_datetime(f.dep.actual) | as_local) -%}"

                "{%- set arr_sched_viewer_dt = f.arr.scheduled and (as_datetime(f.arr.scheduled) | as_local) -%}"
                "{%- set arr_est_viewer_dt = f.arr.estimated and (as_datetime(f.arr.estimated) | as_local) -%}"
                "{%- set arr_act_viewer_dt = f.arr.actual and (as_datetime(f.arr.actual) | as_local) -%}"

                "{%- set dep_sched_viewer = dep_sched_viewer_dt and (dep_sched_viewer_dt.strftime('%H:%M')) -%}"
                "{%- set dep_est_viewer = dep_est_viewer_dt and (dep_est_viewer_dt.strftime('%H:%M')) -%}"
                "{%- set dep_act_viewer = dep_act_viewer_dt and (dep_act_viewer_dt.strftime('%H:%M')) -%}"

                "{%- set arr_sched_viewer = arr_sched_viewer_dt and (arr_sched_viewer_dt.strftime('%H:%M')) -%}"
                "{%- set arr_est_viewer = arr_est_viewer_dt and (arr_est_viewer_dt.strftime('%H:%M')) -%}"
                "{%- set arr_act_viewer = arr_act_viewer_dt and (arr_act_viewer_dt.strftime('%H:%M')) -%}"

                "{%- set dep_sched_local_date = dep_sched_local_dt and dep_sched_local_dt.strftime('%d %b') -%}"
                "{%- set dep_est_local_date = dep_est_local_dt and dep_est_local_dt.strftime('%d %b') -%}"
                "{%- set dep_act_local_date = dep_act_local_dt and dep_act_local_dt.strftime('%d %b') -%}"

                "{%- set arr_sched_local_date = arr_sched_local_dt and arr_sched_local_dt.strftime('%d %b') -%}"
                "{%- set arr_est_local_date = arr_est_local_dt and arr_est_local_dt.strftime('%d %b') -%}"
                "{%- set arr_act_local_date = arr_act_local_dt and arr_act_local_dt.strftime('%d %b') -%}"

                "{%- set dep_sched_viewer_date = dep_sched_viewer_dt and dep_sched_viewer_dt.strftime('%d %b') -%}"
                "{%- set dep_est_viewer_date = dep_est_viewer_dt and dep_est_viewer_dt.strftime('%d %b') -%}"
                "{%- set dep_act_viewer_date = dep_act_viewer_dt and dep_act_viewer_dt.strftime('%d %b') -%}"

                "{%- set arr_sched_viewer_date = arr_sched_viewer_dt and arr_sched_viewer_dt.strftime('%d %b') -%}"
                "{%- set arr_est_viewer_date = arr_est_viewer_dt and arr_est_viewer_dt.strftime('%d %b') -%}"
                "{%- set arr_act_viewer_date = arr_act_viewer_dt and arr_act_viewer_dt.strftime('%d %b') -%}"

                "{%- set dep_base_local_dt = dep_act_local_dt or dep_est_local_dt or dep_sched_local_dt -%}"
                "{%- set dep_base_local_date = dep_base_local_dt and dep_base_local_dt.strftime('%d %b') -%}"

                "{%- set dep_tz_short = f.dep.airport.tz_short -%}"
                "{%- set arr_tz_short = f.arr.airport.tz_short -%}"
                "{%- set viewer_tz_short = now().strftime('%Z') -%}"

                "{%- set dep_label = f.dep.airport.iata -%}"
                "{%- set arr_label = f.arr.airport.iata -%}"

                "type": "custom:mushroom-template-card",
                "entity": "sensor.flight_dashboard_upcoming_flights",
                "picture": "{{ f.airline_logo_url }}",
                "primary": "{{ f.airline_code }} {{ f.flight_number }} · {{ dep_label }} → {{ arr_label }} · {{ dep_date }} · {{ (f.get('delay_status') or "unknown") | title }} · ({{ (f.status_state or "unknown") | title }})",
                "state": "{{ (f.status_state or 'unknown') | title }}",
                "secondary":

                  "Dep: "
                  "{%- if dep_sched_local -%} S: {{ dep_sched_local }} {{ dep_tz_short }} {%- endif -%}"
                  "{%- if dep_tz_short and dep_tz_short != viewer_tz_short and dep_sched_viewer -%}"
                    " · {{ dep_sched_viewer }} {{ viewer_tz_short }}{% if dep_sched_viewer_date and dep_sched_local_date and dep_sched_viewer_date != dep_sched_local_date %} ({{ dep_sched_viewer_date }}){% endif %}"
                  "{%- endif -%}"
                  "{%- if dep_est_local -%}, E: {{ dep_est_local }} {{ dep_tz_short }} {%- endif -%}"
                  "{%- if dep_tz_short and dep_tz_short != viewer_tz_short and dep_est_viewer -%}"
                    " · {{ dep_est_viewer }} {{ viewer_tz_short }}{% if dep_est_viewer_date and dep_est_local_date and dep_est_viewer_date != dep_est_local_date %} ({{ dep_est_viewer_date }}){% endif %}"
                  "{%- endif -%}"
                  "{%- if dep_act_local -%}, A: {{ dep_act_local }} {{ dep_tz_short }} {%- endif -%}"
                  "{%- if dep_tz_short and dep_tz_short != viewer_tz_short and dep_act_viewer -%}"
                    " · {{ dep_act_viewer }} {{ viewer_tz_short }}{% if dep_act_viewer_date and dep_act_local_date and dep_act_viewer_date != dep_act_local_date %} ({{ dep_act_viewer_date }}){% endif %}"
                  "{%- endif -%}"

                  "\nArr: "
                  "{%- if arr_sched_local -%} S: {{ arr_sched_local }} {{ arr_tz_short }}{% if arr_sched_local_date and dep_base_local_date and arr_sched_local_date != dep_base_local_date %} ({{ arr_sched_local_date }}){% endif %}{%- endif -%}"
                  "{%- if arr_tz_short and arr_tz_short != viewer_tz_short and arr_sched_viewer -%}"
                    " · {{ arr_sched_viewer }} {{ viewer_tz_short }}{% if arr_sched_viewer_date and arr_sched_local_date and arr_sched_viewer_date != arr_sched_local_date %} ({{ arr_sched_viewer_date }}){% endif %}"
                  "{%- endif -%}"
                  "{%- if arr_est_local -%}, E: {{ arr_est_local }} {{ arr_tz_short }}{% if arr_est_local_date and dep_base_local_date and arr_est_local_date != dep_base_local_date %} ({{ arr_est_local_date }}){% endif %}{%- endif -%}"
                  "{%- if arr_tz_short and arr_tz_short != viewer_tz_short and arr_est_viewer -%}"
                    " · {{ arr_est_viewer }} {{ viewer_tz_short }}{% if arr_est_viewer_date and arr_est_local_date and arr_est_viewer_date != arr_est_local_date %} ({{ arr_est_viewer_date }}){% endif %}"
                  "{%- endif -%}"
                  "{%- if arr_act_local -%}, A: {{ arr_act_local }} {{ arr_tz_short }}{% if arr_act_local_date and dep_base_local_date and arr_act_local_date != dep_base_local_date %} ({{ arr_act_local_date }}){% endif %}{%- endif -%}"
                  "{%- if arr_tz_short and arr_tz_short != viewer_tz_short and arr_act_viewer -%}"
                    " · {{ arr_act_viewer }} {{ viewer_tz_short }}{% if arr_act_viewer_date and arr_act_local_date and arr_act_viewer_date != arr_act_local_date %} ({{ arr_act_viewer_date }}){% endif %}"
                  "{%- endif -%}"

                  "{%- if f.aircraft_type -%}"
                  "\nAircraft: {{ f.aircraft_type }}"
                  "{%- endif -%}"

                  "{%- if f.travellers -%}"
                  "\nPax: {{ f.travellers | join(', ') }}"
                  "{%- endif -%}" ,

                "multiline_secondary": "true",
                "tap_action": {
                  "action": "call-service",
                  "service": "select.select_option",
                  "target": { "entity_id": "select.flight_dashboard_selected_flight" },
                  "data": { "option": "{{ f.flight_key }} | {{ f.airline_code }} {{ f.flight_number }} {{ f.dep.airport.iata }}→{{ f.arr.airport.iata }} {{ f.dep.scheduled }}" }
                }
              }{{ "," if not loop.last else "" }}
            {%- endfor -%} ]

      - type: vertical-stack
        cards:
          - type: custom:mushroom-template-card
            entity: sensor.flight_dashboard_selected_flight
            picture: >-
              {{ (state_attr('sensor.flight_dashboard_selected_flight','flight') or {}).get('airline_logo_url') }}
            primary: >
              {% set f = state_attr('sensor.flight_dashboard_selected_flight','flight') or {} %}
              {{ f.get('airline_code','—') }} {{ f.get('flight_number','—') }}
              · {{ (f.get('delay_status') or 'unknown') | title }}
            secondary: >
              {% set f = state_attr('sensor.flight_dashboard_selected_flight','flight') or {} %}
              {% set dep = f.get('dep') or {} %}
              {% set arr = f.get('arr') or {} %}
              {% set dep_air = dep.get('airport') or {} %}
              {% set arr_air = arr.get('airport') or {} %}
              {{ dep_air.get('city') or dep_air.get('name') or dep_air.get('iata') or '—' }}
              → {{ arr_air.get('city') or arr_air.get('name') or arr_air.get('iata') or '—' }}
              · {{ f.get('aircraft_type','') }}
              · {{ (f.get('travellers') | join(', ')) if f.get('travellers') else '—' }}
            multiline_secondary: true

          - type: markdown
            content: >
              {% set f = state_attr('sensor.flight_dashboard_selected_flight','flight') or {} %}
              {% set dep = f.get('dep') or {} %}
              {% set arr = f.get('arr') or {} %}
              {% set dep_air = dep.get('airport') or {} %}
              {% set arr_air = arr.get('airport') or {} %}
              {% set viewer_tz = now().strftime('%Z') %}

              {% set dep_sched_local_dt = dep.get('scheduled_local') and as_datetime(dep.get('scheduled_local')) %}
              {% set dep_est_local_dt = dep.get('estimated_local') and as_datetime(dep.get('estimated_local')) %}
              {% set dep_act_local_dt = dep.get('actual_local') and as_datetime(dep.get('actual_local')) %}
              {% set dep_est_or_act_local_dt = dep_act_local_dt or dep_est_local_dt %}

              {% set arr_sched_local_dt = arr.get('scheduled_local') and as_datetime(arr.get('scheduled_local')) %}
              {% set arr_est_local_dt = arr.get('estimated_local') and as_datetime(arr.get('estimated_local')) %}
              {% set arr_act_local_dt = arr.get('actual_local') and as_datetime(arr.get('actual_local')) %}
              {% set arr_est_or_act_local_dt = arr_act_local_dt or arr_est_local_dt %}

              {% set dep_sched_viewer_dt = dep.get('scheduled') and (as_datetime(dep.get('scheduled')) | as_local) %}
              {% set dep_est_viewer_dt = dep.get('estimated') and (as_datetime(dep.get('estimated')) | as_local) %}
              {% set dep_act_viewer_dt = dep.get('actual') and (as_datetime(dep.get('actual')) | as_local) %}
              {% set dep_est_or_act_viewer_dt = dep_act_viewer_dt or dep_est_viewer_dt %}

              {% set arr_sched_viewer_dt = arr.get('scheduled') and (as_datetime(arr.get('scheduled')) | as_local) %}
              {% set arr_est_viewer_dt = arr.get('estimated') and (as_datetime(arr.get('estimated')) | as_local) %}
              {% set arr_act_viewer_dt = arr.get('actual') and (as_datetime(arr.get('actual')) | as_local) %}
              {% set arr_est_or_act_viewer_dt = arr_act_viewer_dt or arr_est_viewer_dt %}

              {% set dep_sched_local_date = dep_sched_local_dt and dep_sched_local_dt.strftime('%d %b') %}
              {% set dep_est_or_act_local_date = dep_est_or_act_local_dt and dep_est_or_act_local_dt.strftime('%d %b') %}
              {% set arr_sched_local_date = arr_sched_local_dt and arr_sched_local_dt.strftime('%d %b') %}
              {% set arr_est_or_act_local_date = arr_est_or_act_local_dt and arr_est_or_act_local_dt.strftime('%d %b') %}

              |   | **Dep** | **Arr** |
              |---|---|---|
              | **Airport ({{ dep_air.get('tz_short','—') }}/{{ arr_air.get('tz_short','—') }})** | S: {{ dep_sched_local_dt and dep_sched_local_dt.strftime('%H:%M') or '—' }}, E/A: {{ dep_est_or_act_local_dt and dep_est_or_act_local_dt.strftime('%H:%M') or '—' }} | S: {{ arr_sched_local_dt and arr_sched_local_dt.strftime('%H:%M') or '—' }}, E/A: {{ arr_est_or_act_local_dt and arr_est_or_act_local_dt.strftime('%H:%M') or '—' }} |
              | **Viewer ({{ viewer_tz }})** | S: {{ dep_sched_viewer_dt and dep_sched_viewer_dt.strftime('%H:%M') or '—' }}{% if dep_sched_viewer_dt and dep_sched_local_date and dep_sched_viewer_dt.strftime('%d %b') != dep_sched_local_date %} ({{ dep_sched_viewer_dt.strftime('%d %b') }}){% endif %}, E/A: {{ dep_est_or_act_viewer_dt and dep_est_or_act_viewer_dt.strftime('%H:%M') or '—' }}{% if dep_est_or_act_viewer_dt and dep_est_or_act_local_date and dep_est_or_act_viewer_dt.strftime('%d %b') != dep_est_or_act_local_date %} ({{ dep_est_or_act_viewer_dt.strftime('%d %b') }}){% endif %} | S: {{ arr_sched_viewer_dt and arr_sched_viewer_dt.strftime('%H:%M') or '—' }}{% if arr_sched_viewer_dt and arr_sched_local_date and arr_sched_viewer_dt.strftime('%d %b') != arr_sched_local_date %} ({{ arr_sched_viewer_dt.strftime('%d %b') }}){% endif %}, E/A: {{ arr_est_or_act_viewer_dt and arr_est_or_act_viewer_dt.strftime('%H:%M') or '—' }}{% if arr_est_or_act_viewer_dt and arr_est_or_act_local_date and arr_est_or_act_viewer_dt.strftime('%d %b') != arr_est_or_act_local_date %} ({{ arr_est_or_act_viewer_dt.strftime('%d %b') }}){% endif %} |

          - type: conditional
            conditions:
              - condition: state
                entity: binary_sensor.flight_dashboard_selected_has_position
                state: "on"
            card:
              type: custom:map-card
              auto_fit: true
              auto_fit_padding: 0.1
              zoom: 5
              focus_entity: sensor.flight_dashboard_selected_flight
              entities:
                - entity: sensor.flight_dashboard_selected_flight
                  latitude: latitude
                  longitude: longitude
                  label: Aircraft
                  icon: mdi:airplane
                  size: 40
                  rotate: "{{ state_attr('sensor.flight_dashboard_selected_flight','heading') | int(0) }}"

### Manage Flights Dashboard (full)
```yaml
title: Manage Flights
views:
  - title: Manage
    path: manage
    cards:
      - type: vertical-stack
        cards:
          - type: custom:mushroom-title-card
            title: Add a flight
            subtitle: Enter airline + number, preview, then confirm
          - type: entities
            show_header_toggle: false
            entities:
              - entity: input_text.fd_airline
                name: Airline code (e.g. EK)
              - entity: input_text.fd_flight_number
                name: Flight number (e.g. 236)
              - entity: input_datetime.fd_flight_date
                name: Date
              - entity: input_text.fd_dep_airport
                name: Departure airport (optional, e.g. AMD)
              - entity: input_text.fd_travellers
                name: Travellers (optional)
              - entity: input_text.fd_notes
                name: Notes (optional)
          - type: custom:mushroom-template-card
            picture: >
              {% set p = state_attr('sensor.flight_dashboard_add_preview','preview') or {} %}
              {% set f = p.get('flight') or {} %}
              {% if f.get('airline_logo_url') %}
                {{ f.get('airline_logo_url') }}
              {% else %}
                https://pics.avs.io/64/64/{{ (f.get('airline_code') or '') | upper }}.png
              {% endif %}
            primary: >
              {% set p = state_attr('sensor.flight_dashboard_add_preview','preview') or {} %}
              {% set f = p.get('flight') or {} %}
              {% set dep = f.get('dep') or {} %}
              {% set arr = f.get('arr') or {} %}
              {% set dep_air = dep.get('airport') or {} %}
              {% set arr_air = arr.get('airport') or {} %}
              {% set dep_t = dep.get('scheduled_local') or dep.get('scheduled') %}
              {% set arr_t = arr.get('scheduled_local') or arr.get('scheduled') %}
              {% set dep_hm = dep_t and dep_t[11:16] %}
              {% set arr_hm = arr_t and arr_t[11:16] %}
              {% set dep_date = dep_t and dep_t[0:10] %}
              {% set arr_date = arr_t and arr_t[0:10] %}
              {% set dep_date_fmt = dep_date and (as_datetime(dep_date) | timestamp_custom('%d %b', false)) %}
              {% set arr_date_fmt = arr_date and (as_datetime(arr_date) | timestamp_custom('%d %b', false)) %}
              {{ f.get('airline_code','—') }} {{ f.get('flight_number','—') }} ·
              {{ dep_air.get('iata') or '—' }} ({{ dep_hm or '—' }} {{ dep_air.get('tz_short') or '' }})
              → {{ arr_air.get('iata') or '—' }} ({{ arr_hm or '—' }} {{ arr_air.get('tz_short') or '' }}{% if arr_date_fmt and dep_date_fmt and arr_date_fmt != dep_date_fmt %} · {{ arr_date_fmt }}{% endif %})
            secondary: >
              {% set p = state_attr('sensor.flight_dashboard_add_preview','preview') or {} %}
              {% set f = p.get('flight') or {} %}
              {% set airline_name = f.get('airline_name') or f.get('airline_code') or '—' %}
              {% if p.get('hint') %}
                ❗ {{ p.get('hint') }}
              {% elif p.get('warning') %}
                ⚠️ {{ p.get('warning') }}
              {% elif p.get('error') %}
                ❌ {{ p.get('error') }}
              {% else %}
                Run Preview
              {% endif %} · Airline: {{ airline_name }} · Ready: {{ p.get('ready') }}
            icon: mdi:airplane
            layout: horizontal
            multiline_secondary: true
          - type: horizontal-stack
            cards:
              - type: custom:mushroom-entity-card
                entity: script.fd_preview_flight
                name: Preview
                icon: mdi:magnify
                tap_action:
                  action: call-service
                  service: script.turn_on
                  target:
                    entity_id: script.fd_preview_flight
              - type: custom:mushroom-entity-card
                entity: script.fd_confirm_add
                name: Add
                icon: mdi:content-save
                tap_action:
                  action: call-service
                  service: script.turn_on
                  target:
                    entity_id: script.fd_confirm_add
              - type: custom:mushroom-entity-card
                entity: script.fd_clear_preview
                name: Clear
                icon: mdi:close-circle
                tap_action:
                  action: call-service
                  service: script.turn_on
                  target:
                    entity_id: script.fd_clear_preview

      - type: entities
        title: Remove a flight
        show_header_toggle: false
        entities:
          - entity: select.flight_dashboard_remove_flight
            name: Select flight to remove
          - entity: button.flight_dashboard_remove_selected_flight
            name: Remove selected flight

      - type: vertical-stack
        cards:
          - type: entities
            title: Flight Dashboard Diagnostics
            show_header_toggle: false
            entities:
              - entity: sensor.flight_dashboard_fr24_usage
                name: FR24 usage (credits in last period)
              - entity: sensor.flight_dashboard_upcoming_flights
                name: Upcoming flights (summary)
              - entity: sensor.flight_dashboard_provider_blocks
                name: Provider blocks
              - entity: button.flight_dashboard_refresh_now
                name: Refresh now
              - entity: button.flight_dashboard_remove_landed_flights
                name: Remove landed flights
          - type: markdown
            title: Provider Blocks Detail
            content: >
              {% set p = state_attr('sensor.flight_dashboard_provider_blocks','providers') or {} %}
              {% if p %}
              {% for name, info in p.items() %}
              {% set until_dt = info.until and as_datetime(info.until) %}
              - **{{ name }}** blocked until
                {{ until_dt and (as_timestamp(until_dt) | timestamp_custom('%d %b %H:%M', true)) or info.until }}
                ({{ info.seconds_remaining }}s) · {{ info.reason }}
              {% endfor %}
              {% else %}
              No providers blocked ✅
              {% endif %}

### Individual Cards

#### Add Flight (preview + confirm)
Use the **Add Flight** card from the Manage Flights dashboard above.

#### Flight Status List
Use the **Flight Status list** card from the Flight Status dashboard above.

#### Detailed Flight View (selected flight + map)
Use the **Selected Flight** stack from the Flight Status dashboard above.

#### Diagnostics
Use the **Diagnostics** stack from the Manage Flights dashboard above.
## Services
### `flight_dashboard.preview_flight`
Preview a flight before saving it.

### `flight_dashboard.confirm_add`
Confirm and save the current preview.

### `flight_dashboard.add_flight`
Add a flight directly using minimal inputs.

### `flight_dashboard.clear_preview`
Clear the preview.

### `flight_dashboard.add_manual_flight`
Add a flight with full manual inputs.

### `flight_dashboard.remove_manual_flight`
Remove a manual flight by flight_key.

### `flight_dashboard.clear_manual_flights`
Clear all manual flights.

### `flight_dashboard.refresh_now`
Force a refresh of upcoming flights and status updates.

### `flight_dashboard.prune_landed`
Remove landed/cancelled manual flights. Optional `hours` delay after arrival.

## Notes
- Schedule and status timestamps are stored as ISO strings (typically UTC). Convert at display time.
- Manual flights are editable; provider-sourced flights are read-only.
