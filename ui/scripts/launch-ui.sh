#!/bin/bash
# Sauce Dispenser UI Launcher (Chromium only)
# Run this to start ONLY the UI (Chromium in kiosk mode)

PORT=8080
URL="http://localhost:$PORT/ui"
SITE_DIR="$(cd "$(dirname "$0")" && pwd)"

# Hide mouse cursor if unclutter is installed
if command -v unclutter &> /dev/null; then
    unclutter -idle 0.1 -root &
fi

sleep 2

export DISPLAY=:0
chromium \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --touch-events=enabled \
    --no-first-run \
    "$URL"
