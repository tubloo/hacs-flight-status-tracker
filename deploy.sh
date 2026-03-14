#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$REPO_ROOT/custom_components/flight_status_tracker"
DEST="/Users/sumitghosh/dev/ha-flight-dashboard/config/custom_components/flight_status_tracker"
PKG_SRC="$REPO_ROOT/packages/flight_status_tracker_add_flow.yaml"
PKG_DEST="/Users/sumitghosh/dev/ha-flight-dashboard/config/packages/flight_status_tracker_add_flow.yaml"

if [ ! -d "$SRC" ]; then
  echo "Source not found: $SRC" >&2
  exit 1
fi

mkdir -p "$(dirname "$DEST")"
rsync -av --delete "$SRC/" "$DEST/"

echo "Deployed to $DEST"

if [ -f "$PKG_SRC" ]; then
  mkdir -p "$(dirname "$PKG_DEST")"
  rsync -av "$PKG_SRC" "$PKG_DEST"
  echo "Deployed package to $PKG_DEST"
fi
if [ -n "${HA_CONTAINER:-}" ]; then
  CONTAINER="$HA_CONTAINER"
elif docker ps --format '{{.Names}}' | grep -Fxq "ha-flight-dashboard-dev"; then
  CONTAINER="ha-flight-dashboard-dev"
elif docker ps --format '{{.Names}}' | grep -Fxq "ha-dev-test"; then
  CONTAINER="ha-dev-test"
else
  echo "No Home Assistant container found. Set HA_CONTAINER env var or start one of: ha-flight-dashboard-dev, ha-dev-test" >&2
  exit 1
fi

echo "Restarting Home Assistant container: $CONTAINER"
docker restart "$CONTAINER" >/dev/null
echo "Restarted."

echo "Waiting for Home Assistant to start..."
sleep 10

echo "Recent Docker logs:"
docker logs --tail 200 "$CONTAINER" || true

HASS_LOG="/Users/sumitghosh/dev/ha-flight-dashboard/config/home-assistant.log"
if [ -f "$HASS_LOG" ]; then
  echo "Recent Home Assistant log file:"
  tail -n 200 "$HASS_LOG" || true
else
  echo "Home Assistant log file not found at $HASS_LOG"
fi
