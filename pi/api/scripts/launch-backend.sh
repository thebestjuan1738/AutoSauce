#!/bin/bash
# Sauce Dispenser Backend Launcher (Docker only)
# Run this to start ONLY the backend (API server)

# Stop and remove any existing backend container
docker stop sauce-backend 2>/dev/null || true
docker rm sauce-backend 2>/dev/null || true

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
