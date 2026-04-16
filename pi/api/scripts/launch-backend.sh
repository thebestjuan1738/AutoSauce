#!/bin/bash
# Sauce Dispenser Backend Launcher (Docker only)
# Run this to start ONLY the backend (API server)
#
# Prerequisites (one-time setup on the Pi):
#   curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
#   arduino-cli core install arduino:avr

set -euo pipefail

SKETCH_DIR="/home/saucemachine/AutoSauce/autosauce_testing"
ARDUINO_FQBN="arduino:avr:mega"

# ── Flash Arduino sketch ───────────────────────────────────────────────────────
# Find the Arduino Mega by its stable /dev/serial/by-id symlink (VID 2a03 = Arduino SRL).
# This is independent of plug-in order and survives reboots.
flash_arduino() {
    if ! command -v arduino-cli &>/dev/null; then
        echo "[WARNING] arduino-cli not found — skipping sketch upload. Install it to enable auto-flash." >&2
        return
    fi

    local by_id_entry
    by_id_entry=$(ls /dev/serial/by-id/ 2>/dev/null | grep -iE "2a03|Arduino" | head -1)
    if [ -z "$by_id_entry" ]; then
        echo "[WARNING] Arduino Mega not found in /dev/serial/by-id — skipping sketch upload." >&2
        return
    fi
    local port
    port=$(readlink -f "/dev/serial/by-id/$by_id_entry")
    echo "[INFO] Flashing Arduino sketch to $port ..."

    arduino-cli compile --fqbn "$ARDUINO_FQBN" "$SKETCH_DIR" \
        && arduino-cli upload  --fqbn "$ARDUINO_FQBN" --port "$port" "$SKETCH_DIR" \
        && echo "[INFO] Arduino sketch uploaded successfully." \
        || echo "[WARNING] Arduino flash failed — continuing with existing firmware." >&2
}

flash_arduino

# Stop and remove any existing backend container
set +e
docker stop sauce-backend 2>/dev/null || true
docker rm sauce-backend 2>/dev/null || true
set -e

# Start backend container in the foreground so systemd tracks the process.
# Mount every /dev/ttyACM* device that exists (VESC + Arduino Mega, order-independent).
DEVICE_FLAGS=""
for dev in /dev/ttyACM*; do
    [ -e "$dev" ] && DEVICE_FLAGS="$DEVICE_FLAGS --device $dev:$dev"
done

exec docker run --name sauce-backend \
    -p 8080:8080 \
    -v /home/saucemachine/AutoSauce:/app \
    $DEVICE_FLAGS \
    sauce-backend
