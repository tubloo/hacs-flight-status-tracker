# Development

## Architecture Overview
- **Services**: `services_preview.py` exposes preview/add/confirm flows.
- **Storage**: `manual_store.py` persists canonical flights, `storage.py` persists preview and cache.
- **Schedule lookup**: `schedule_lookup.py` enriches minimal inputs using provider APIs.
- **Status**: `status_manager.py` applies cached status and schedules smart refreshes.
- **Sensor**: `sensor.py` exposes the unified flight list in attributes.
- **Refresh now**: `flight_dashboard.refresh_now` triggers an immediate rebuild.
- **Auto prune**: optional removal of landed/cancelled manual flights in `sensor.py`.

## Testing
Recommended quick checks:
1) Restart Home Assistant and confirm the integration loads.
2) Call `flight_dashboard.preview_flight` with airline + flight number + date.
3) Call `flight_dashboard.confirm_add` and confirm the sensor updates.
4) Press `button.flight_dashboard_refresh_now` to force a refresh.

### FR24 Sandbox
Enable **Use FR24 sandbox** in the integration options and set the sandbox key.

### Demo package
Copy `testing/flight_dashboard_demo.yaml` into your HA `config/packages/` and restart.

## Developer Workflow
- Symlink the integration into your HA config for rapid iteration.
- Use `./deploy.sh` for clean deploy + restart.

## Notes
- Schedule and status timestamps are stored as ISO strings (typically UTC).
- Manual flights are editable; provider-sourced flights should be treated read-only.
