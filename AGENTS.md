# AI Agent Guidelines (Repository Root)

This file defines **default instructions for AI coding assistants** working in this repository. It is intended to help an agent make changes that match the project’s conventions and avoid common Home Assistant integration pitfalls.

## Goals
- Make **small, focused** changes that address the requested behavior.
- Prefer **root-cause fixes** over band-aids.
- Keep changes consistent with existing patterns in `custom_components/flight_status_tracker/`.
- Update docs when behavior or configuration changes.

## Repository Map
- `custom_components/flight_status_tracker/`: Home Assistant integration code.
- `docs/`: documentation for users.
- `testing/`: demo package and testing helpers (see `testing/flight_status_tracker_demo.yaml`).
- `DEVELOPMENT.md`: architecture overview + manual testing checklist.

## Architectural Notes
- **Canonical flight schema**: a plain `dict` (Schema v3) with `dep`/`arr` blocks and ISO timestamps (generally normalized to UTC). See `custom_components/flight_status_tracker/sensor.py` for the schema doc + example.
- **Stable identity**: `flight_key` is the primary identifier (used for merges, status cache, and manual store upserts). Keep it stable and avoid changing how keys are constructed unless you also provide a migration.
- **Preview → confirm flow**: `preview_flight` stores a preview object immediately, then optionally enriches via schedule providers; `confirm_add` persists the canonical flight record into the manual store.
- **Status pipeline**: provider status is normalized via `status_resolver.apply_status()`; computed fields like `delay_status`, `delay_minutes`, and durations are derived in `status_manager.py`.
- **Smart refresh**: status refresh uses per-flight `next_check` scheduling and an in-memory `status_cache` in `hass.data[DOMAIN]` (not a fixed global polling loop).
- **Directory caching**: airport/airline directory data is cached with TTL and refreshed periodically; prefer cached lookups and avoid repeated provider calls.
- **Compatibility mindset**: keep imports/option keys tolerant (see `const.py` “compat superset”) and preserve legacy timestamp fields where required for stored manual flights.

## Development Workflow
- Prefer using `./deploy.sh` for clean deploys (if you have a local HA dev environment wired to it).
- For quick verification, follow the checklist in `DEVELOPMENT.md`.

## Home Assistant Integration Conventions
- Treat this as an **async-first** codebase (Home Assistant runs on an asyncio event loop).
  - Don’t add blocking I/O in async code paths.
  - Use Home Assistant patterns for scheduling updates and coordinator-like behavior if applicable.
- Be careful with **time and timezones**:
  - Assume timestamps may be stored/transported as ISO strings (often UTC).
  - Avoid mixing naive and timezone-aware datetimes.
- Storage expectations:
  - Manual flights are user-editable and should remain stable across upgrades.
  - Provider-sourced/enriched fields should generally be treated as read-only outputs.
  - If you change any stored schema, include a safe migration path.
- Logging:
  - Keep logs useful but not noisy; avoid logging secrets/keys.

## Code Style
- Match the existing style in the integration.
- Prefer clear names over abbreviations.
- Add type hints where it improves clarity, but don’t churn types across unrelated files.

## Testing / Validation (Practical)
When you change behavior, try to validate via at least one of:
- Restart Home Assistant and confirm the integration loads without errors.
- Exercise the preview/add/confirm flow:
  - `flight_status_tracker.preview_flight`
  - `flight_status_tracker.confirm_add`
- Force refresh:
  - `button.flight_status_tracker_refresh_now`

If adding logic that’s easy to unit test, add/extend tests in `testing/` (only if the repo already uses that style).

## Debugging (Home Assistant)
- When asking the user for Home Assistant state/attributes/log context, prefer providing a **copy/paste script** runnable in **Developer Tools → Template** to extract exactly the needed info.

## Safety Rules
- Don’t commit secrets or add real API keys to docs/examples.
- Avoid touching `.storage` or real HA configuration outside this repo.
- Don’t introduce new network calls in tests unless they are explicitly mocked/stubbed.

## Releases
- **Do not create a release automatically.** Always ask for confirmation before tagging/publishing a release or running release tooling.
- Before any release, perform a **release review**:
  - Verify the integration loads and key flows work (see **Testing / Validation** above).
  - Ensure user-facing docs are updated (`README.md`, `docs/`, `info.md`, `DEVELOPMENT.md` as applicable).
  - Ensure `AGENTS.md` is updated if any workflows/conventions changed during the work.
  - Provide a short **release summary** (what changed, any breaking changes/migration notes, and how you validated).
  - Include an **agent-instructions summary** if `AGENTS.md` changed (what you updated and why).

## Commits
- When preparing to commit, check whether `AGENTS.md` should be updated to reflect any new conventions/workflows introduced by the changes.
- If `AGENTS.md` changed, include a brief summary of those instruction changes alongside the code change summary.

## How to Use This Document
When starting a task:
1) Read `README.md` and `DEVELOPMENT.md` for context.
2) Locate the relevant module(s) in `custom_components/flight_status_tracker/`.
3) Implement the smallest change that satisfies the request.
4) Validate using the checklist above and update docs if needed.
