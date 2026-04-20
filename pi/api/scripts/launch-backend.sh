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
# Mount ttyACM devices (Arduino Mega + Uno) and the gantry with a fixed name.
#   ttyACM*    — Arduino Mega (gripper/extruder) and Uno (conveyor)
#   ttyGANTRY  — CP210x UART bridge (gantry NodeMCU), fixed by udev rule
DEVICE_FLAGS=""
for dev in /dev/ttyACM*; do
    [ -e "$dev" ] && DEVICE_FLAGS="$DEVICE_FLAGS --device $dev:$dev"
done

# Map the gantry's real device path but expose it as /dev/ttyGANTRY inside the
# container. This keeps the container's device name stable even if the host
# enumerates the CP210x as ttyUSB0, ttyUSB1, etc. on different boots.
if [ -e /dev/ttyGANTRY ]; then
    REAL_GANTRY=$(readlink -f /dev/ttyGANTRY)
    DEVICE_FLAGS="$DEVICE_FLAGS --device $REAL_GANTRY:/dev/ttyGANTRY"
else
    # Fallback: map any ttyUSB devices if udev rule hasn't fired yet
    for dev in /dev/ttyUSB*; do
        [ -e "$dev" ] && DEVICE_FLAGS="$DEVICE_FLAGS --device $dev:$dev"
    done
fi

exec docker run --name sauce-backend \
    -p 8080:8080 \
    -v /home/saucemachine/AutoSauce:/app \
    $DEVICE_FLAGS \
    sauce-backend
