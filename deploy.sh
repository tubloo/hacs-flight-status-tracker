#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$REPO_ROOT/custom_components/flight_dashboard"
DEST="/Users/sumitghosh/dev/ha-flight-dashboard/config/custom_components/flight_dashboard"

if [ ! -d "$SRC" ]; then
  echo "Source not found: $SRC" >&2
  exit 1
fi

mkdir -p "$(dirname "$DEST")"
rsync -av --delete "$SRC/" "$DEST/"

echo "Deployed to $DEST"
echo "Restarting Home Assistant container: ha-flight-dashboard-dev"
docker restart ha-flight-dashboard-dev >/dev/null
echo "Restarted."

echo "Waiting for Home Assistant to start..."
sleep 10

echo "Recent Docker logs:"
docker logs --tail 200 ha-flight-dashboard-dev || true

HASS_LOG="/Users/sumitghosh/dev/ha-flight-dashboard/config/home-assistant.log"
if [ -f "$HASS_LOG" ]; then
  echo "Recent Home Assistant log file:"
  tail -n 200 "$HASS_LOG" || true
else
  echo "Home Assistant log file not found at $HASS_LOG"
fi
