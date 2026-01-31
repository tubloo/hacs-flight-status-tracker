# Flight Dashboard (Home Assistant)

[![HACS](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://hacs.xyz/)
[![Hassfest](https://github.com/tubloo/hass-integration-flight-dashboard/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/tubloo/hass-integration-flight-dashboard/actions/workflows/hassfest.yaml)
[![HACS Action](https://github.com/tubloo/hass-integration-flight-dashboard/actions/workflows/hacs.yaml/badge.svg)](https://github.com/tubloo/hass-integration-flight-dashboard/actions/workflows/hacs.yaml)
[![Lint](https://github.com/tubloo/hass-integration-flight-dashboard/actions/workflows/lint.yaml/badge.svg)](https://github.com/tubloo/hass-integration-flight-dashboard/actions/workflows/lint.yaml)

Flight Dashboard is a Home Assistant integration that tracks upcoming flights and their status.
You can add flights with minimal inputs (airline code, flight number, date) and let the integration enrich the details using provider APIs.

## Features
- Add flights with minimal inputs.
- Preview and confirm before saving.
- Automatic status refresh with smart API call rationing.
- Manual flights are editable; provider-sourced flights are read-only.
- On-demand refresh button/service.
- Schedule provider and status provider can be set independently.
- Optional auto-removal of landed/cancelled manual flights.

## Installation (HACS)
1. Add this repository as a custom repository in HACS.
2. Install **Flight Dashboard**.
3. Restart Home Assistant.
4. Add the integration from **Settings → Devices & Services**.

## Configuration
All configuration is done via the UI (config flow).

Key points:
- **Schedule provider** is used for preview/add (must return scheduled times).
- **Status provider** is used for live status updates.
- FR24 is great for status, but does not always return scheduled times. Use AirLabs or Aviationstack for schedule.
- FR24 sandbox: enable **Use FR24 sandbox** and set the sandbox key.
- **Auto-remove landed flights** is optional and applies only to manual flights.

### Required inputs when adding a flight
```
airline, flight_number, date
```

### Supported providers
**Schedule provider**
- Auto (best available)
- Aviationstack
- AirLabs
- Flightradar24
- Mock

**Status provider**
- Flightradar24 (default)
- Aviationstack
- AirLabs
- OpenSky (tracking-only)
- Local (no API calls)
- Mock

## Lovelace Examples

### Basic sensor card
```yaml
type: entities
title: Flight Dashboard
entities:
  - entity: sensor.flight_dashboard_upcoming_flights
    name: Upcoming Flights
```

### Add Flight Card (with Preview)
```yaml
type: vertical-stack
cards:
  - type: custom:mushroom-title-card
    title: Add a flight
    subtitle: Enter flight + date, preview, then confirm

  - type: entities
    show_header_toggle: false
    entities:
      - entity: input_text.fd_flight_query
        name: Flight (e.g. AI 157)
      - entity: input_datetime.fd_flight_date
        name: Date
      - entity: input_text.fd_travellers
        name: Travellers (optional)
      - entity: input_text.fd_notes
        name: Notes (optional)
      - entity: sensor.flight_dashboard_add_preview
        name: Preview status

  - type: custom:mushroom-template-card
    primary: >
      {% set p = state_attr('sensor.flight_dashboard_add_preview','preview') %}
      {% if p and p.flight %}
      {{ p.flight.airline_code }} {{ p.flight.flight_number }} ·
      {{ p.flight.dep.airport.iata or '—' }} → {{ p.flight.arr.airport.iata or '—' }}
      {% else %} No preview {% endif %}
    secondary: >
      {% set p = state_attr('sensor.flight_dashboard_add_preview','preview') %}
      {% if p and p.flight %}
      Dep {{ p.flight.dep.scheduled or p.flight.dep.estimated or '—' }} ·
      Arr {{ p.flight.arr.scheduled or p.flight.arr.estimated or '—' }}
      {% else %} Run Preview {% endif %}
    picture: >
      {% set p = state_attr('sensor.flight_dashboard_add_preview','preview') %}
      {% if p and p.flight %}
      {{ p.flight.airline_logo_url }}
      {% endif %}
    icon: mdi:airplane
    layout: horizontal
    multiline_secondary: false

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
```

### Remove Flight Card
```yaml
type: entities
title: Remove a flight
show_header_toggle: false
entities:
  - entity: select.flight_dashboard_remove_flight
    name: Select flight to remove
  - entity: button.flight_dashboard_remove_selected_flight
    name: Remove selected flight
```

### Refresh Now Card
```yaml
type: entities
title: Flight Dashboard
show_header_toggle: false
entities:
  - entity: button.flight_dashboard_refresh_now
    name: Refresh now
```

### Remove Landed Flights Card (manual)
```yaml
type: entities
title: Flight Dashboard
show_header_toggle: false
entities:
  - entity: button.flight_dashboard_remove_landed
    name: Remove landed flights
```

### Flight Status List Card (example)
```yaml
type: custom:auto-entities
card:
  type: entities
  title: Flights
  show_header_toggle: false
filter:
  template: >
    {% set flights =
    state_attr('sensor.flight_dashboard_upcoming_flights','flights') or [] %} [
    {%- for f in flights -%}

      {

        "{%- set dep_date = f.dep.scheduled and (as_timestamp(f.dep.scheduled) | timestamp_custom('%d %b', true)) or '—' -%}"
        
        "{%- set dep_sched_dt = f.dep.scheduled and as_datetime(f.dep.scheduled) -%}"
        "{%- set dep_sched = dep_sched_dt and (as_timestamp(dep_sched_dt) | timestamp_custom('%H:%M', false)) -%}"
        "{%- set dep_sched_viewer = dep_sched_dt and (as_timestamp(dep_sched_dt | as_local) | timestamp_custom('%H:%M', false)) -%}"

        "{%- set dep_est_dt = f.dep.estimated and as_datetime(f.dep.estimated) -%}"
        "{%- set dep_est = dep_est_dt and (as_timestamp(dep_est_dt) | timestamp_custom('%H:%M', false)) -%}"
        "{%- set dep_est_viewer = dep_est_dt and (as_timestamp(dep_est_dt | as_local) | timestamp_custom('%H:%M', false)) -%}"

        "{%- set dep_act_dt = f.dep.actual and as_datetime(f.dep.actual) -%}"
        "{%- set dep_act = dep_act_dt and (as_timestamp(dep_act_dt) | timestamp_custom('%H:%M', false)) -%}"
        "{%- set dep_act_viewer = dep_act_dt and (as_timestamp(dep_act_dt | as_local) | timestamp_custom('%H:%M', false)) -%}"


        "{%- set arr_sched_dt = f.arr.scheduled and as_datetime(f.arr.scheduled) -%}"
        "{%- set arr_sched = arr_sched_dt and (as_timestamp(arr_sched_dt) | timestamp_custom('%H:%M', false)) -%}"
        "{%- set arr_sched_viewer = arr_sched_dt and (as_timestamp(arr_sched_dt | as_local) | timestamp_custom('%H:%M', false)) -%}"

        "{%- set arr_est_dt = f.arr.estimated and as_datetime(f.arr.estimated) -%}"
        "{%- set arr_est = arr_est_dt and (as_timestamp(arr_est_dt) | timestamp_custom('%H:%M', false)) -%}"
        "{%- set arr_est_viewer = arr_est_dt and (as_timestamp(arr_est_dt | as_local) | timestamp_custom('%H:%M', false)) -%}"

        "{%- set arr_act_dt = f.arr.actual and as_datetime(f.arr.actual) -%}"
        "{%- set arr_act = arr_act_dt and (as_timestamp(arr_act_dt) | timestamp_custom('%H:%M', false)) -%}"
        "{%- set arr_act_viewer = arr_act_dt and (as_timestamp(arr_act_dt | as_local) | timestamp_custom('%H:%M', false)) -%}"


        "{%- set dep_tz_short = f.dep.airport.tz_short -%}"
        "{%- set arr_tz_short = f.arr.airport.tz_short -%}"
        "{%- set viewer_tz_short = now().strftime('%Z') -%}"

        "{%- set dep_label = f.dep.airport.city or f.dep.airport.name or f.dep.airport.iata -%}"
        "{%- set arr_label = f.arr.airport.city or f.arr.airport.name or f.arr.airport.iata -%}"

        
        "type": "custom:mushroom-template-card",
        "entity": "sensor.flight_dashboard_upcoming_flights",
        "picture": "{{ f.airline_logo_url }}",
        "primary": "{{ f.airline_code }} {{ f.flight_number }} · {{ dep_label }} → {{ arr_label }} · {{ dep_date }} · ({{ (f.status_state or \"unknown\") | title }})",
        "state": "{{ (f.status_state or 'unknown') | title }}",
        "secondary":

          "Dep: "
          "{%- if dep_sched -%} S: {{ dep_sched }} {{ dep_tz_short }} {%- endif -%}"          
          "{%- if dep_tz_short and dep_tz_short != viewer_tz_short and dep_sched -%}"
            " · {{ dep_sched_viewer }} {{ viewer_tz_short }}"
          "{%- endif -%}"
          "{%- if dep_est -%}, E: {{ dep_est }} {{ dep_tz_short }} {%- endif -%}"           
          "{%- if dep_tz_short and dep_tz_short != viewer_tz_short and dep_sched -%}"
            " · {{ dep_est_viewer }} {{ viewer_tz_short }}"
          "{%- endif -%}"
          "{%- if dep_act -%}, A: {{ dep_act }} {{ dep_tz_short }} {%- endif -%}"         
          "{%- if dep_tz_short and dep_tz_short != viewer_tz_short and dep_sched -%}"
            " · {{ dep_act_viewer }} {{ viewer_tz_short }}"
          "{%- endif -%}"

          "\nArr: "
          "{%- if arr_sched -%} S: {{ arr_sched }} {{ arr_tz_short }} {%- endif -%}"          
          "{%- if arr_tz_short and arr_tz_short != viewer_tz_short and arr_sched -%}"
            " · {{ arr_sched_viewer }} {{ viewer_tz_short }}"
          "{%- endif -%}"

          "{%- if arr_est -%}, E: {{ arr_est }} {{ arr_tz_short }} {%- endif -%}"           
          "{%- if arr_tz_short and arr_tz_short != viewer_tz_short and arr_sched -%}"
            " · {{ arr_est_viewer }} {{ viewer_tz_short }}"
          "{%- endif -%}"

          "{%- if arr_act -%}, A: {{ arr_act }} {{ arr_tz_short }} {%- endif -%}"         
          "{%- if arr_tz_short and arr_tz_short != viewer_tz_short and arr_sched -%}"
            " · {{ arr_act_viewer }} {{ viewer_tz_short }}"
          "{%- endif -%}"
       


          "{%- if f.aircraft_type -%}"
          "\nAircraft: {{ f.aircraft_type }}"
          "{%- endif -%}"

          "{%- if f.dep.terminal or f.dep.gate or f.arr.terminal or f.arr.gate -%}"
          "\nT/G: {{ f.dep.airport.iata }} T.{{ f.dep.terminal or '—' }}{% if f.dep.gate %} G.{{ f.dep.gate }}{% endif %}"
          " → {{ f.arr.airport.iata }} T.{{ f.arr.terminal or '—' }}{% if f.arr.gate %} G.{{ f.arr.gate }}{% endif %}"
          "{%- endif -%}"

          "{%- if f.travellers -%}"
          "\nPax: {{ f.travellers | join(', ') }}"
          "{%- endif -%}" ,

        "multiline_secondary": "true",
        "tap_action": { "action": "more-info" }
      }{{ "," if not loop.last else "" }}
    {%- endfor -%} ]
```

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
