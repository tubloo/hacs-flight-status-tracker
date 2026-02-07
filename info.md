# Flight Status Tracker

Track upcoming flights in Home Assistant with minimal input.

- Add flights by airline code + flight number + date (optional departure airport to disambiguate)
- Preview before saving
- Schedule provider and status provider can be set independently
- Position provider can be set independently for live position/route updates
- Merge tolerance prevents duplicate flights when schedule/status overlap (default 6 hours)
- Computed duration fields: duration_minutes and scheduled/estimated/actual minutes
- Raw provider status stored as status.provider_state (status block is a normalized subset)
- Smart status refresh with API call rationing
- On-demand refresh button/service
- Optional auto-removal of arrived/cancelled manual flights
- Optional airport/airline directory cache (default 180 days)
- Provider times are normalized to UTC (offset-aware timestamps are treated as authoritative; naive times are localized via airport TZ when available)
