#!/bin/bash
# Sauce Dispenser Backend Launcher (Docker only)
# Run this to start ONLY the backend (API server)
#
# Prerequisites (one-time setup on the Pi):
#   curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
#   arduino-cli core install arduino:avr



# Stop and remove any existing backend container
docker stop sauce-backend 2>/dev/null || true
docker rm sauce-backend 2>/dev/null || true

# Start backend container in the foreground so systemd tracks the process.
# All three controllers get fixed names inside the container via udev symlinks,
# so the container mapping is stable regardless of enumeration order.
#
#   ttyGANTRY    — CP210x (gantry NodeMCU ESP8266)
#   ttyPRINTHEAD — Arduino Mega (gripper + extruder)
#   ttyCONVEYOR  — Arduino Uno (conveyor + cylinder + lamp)

_map_fixed_device() {
    local symlink="$1"
    if [ -e "$symlink" ]; then
        local real
        real=$(readlink -f "$symlink")
        echo "--device $real:$symlink"
    fi
}

DEVICE_FLAGS=""
DEVICE_FLAGS="$DEVICE_FLAGS $(_map_fixed_device /dev/ttyGANTRY)"
DEVICE_FLAGS="$DEVICE_FLAGS $(_map_fixed_device /dev/ttyPRINTHEAD)"
DEVICE_FLAGS="$DEVICE_FLAGS $(_map_fixed_device /dev/ttyCONVEYOR)"

exec docker run --name sauce-backend \
    -p 8080:8080 \
    -v /home/saucemachine/AutoSauce:/app \
    $DEVICE_FLAGS \
    sauce-backend
