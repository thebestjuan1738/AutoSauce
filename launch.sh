#!/bin/bash
# ============================================================
#  Sauce Dispenser – Raspberry Pi Launcher
#  Run automatically at boot via systemd (instructions below).
#
#  Starts a Python HTTP server and opens Chromium in kiosk
#  (fullscreen, no mouse cursor) on the 7-inch touchscreen.
# ============================================================

PORT=8080
URL="http://localhost:$PORT"
# Path to the sauce website files (update if needed)
SITE_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo " Sauce Dispenser – Pi Launcher"
echo " ================================"
echo " Port : $PORT"
echo " URL  : $URL"
echo " Dir  : $SITE_DIR"
echo ""

# ── Hide the mouse cursor (requires unclutter) ──────────────
# Install once with: sudo apt install unclutter
if command -v unclutter &> /dev/null; then
    unclutter -idle 0.1 -root &
fi

# ── Start Python HTTP server in the background ──────────────
cd "$SITE_DIR"
python3 -m http.server $PORT &
SERVER_PID=$!
echo " HTTP server PID: $SERVER_PID"

# Give the server a moment to start
sleep 2

# ── Open Chromium in kiosk / fullscreen mode ────────────────
# --kiosk            : true fullscreen, no chrome/titlebar
# --noerrdialogs     : suppress crash dialogs
# --disable-infobars : hide "Chrome is being controlled" bar
# --touch-events     : enable touch input
# --no-first-run     : skip first-run wizard
chromium-browser \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --touch-events=enabled \
    --no-first-run \
    "$URL"

# ── Cleanup: kill the server when Chromium closes ───────────
echo " Chromium closed – stopping HTTP server..."
kill $SERVER_PID
wait $SERVER_PID 2>/dev/null
echo " Done."

# ============================================================
#  AUTO-START AT BOOT (systemd)
#  1. Copy this file to the Pi, e.g. /home/pi/sauceWebsite/
#  2. Make it executable:
#       chmod +x /home/pi/sauceWebsite/launch.sh
#  3. Create a systemd service:
#       sudo nano /etc/systemd/system/sauce-dispenser.service
#
#  Paste this into the service file:
#  ---
#  [Unit]
#  Description=Sauce Dispenser UI
#  After=graphical.target
#
#  [Service]
#  Type=simple
#  User=pi
#  Environment=DISPLAY=:0
#  ExecStart=/home/pi/sauceWebsite/launch.sh
#  Restart=on-failure
#
#  [Install]
#  WantedBy=graphical.target
#  ---
#
#  4. Enable and start the service:
#       sudo systemctl daemon-reload
#       sudo systemctl enable sauce-dispenser
#       sudo systemctl start sauce-dispenser
# ============================================================
