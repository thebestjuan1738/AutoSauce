#!/bin/bash
# Sauce Dispenser Backend Launcher (Docker only)
# Run this to start ONLY the backend (API server)

# Stop and remove any existing backend container
docker stop sauce-backend 2>/dev/null || true
docker rm sauce-backend 2>/dev/null || true

# Start backend container in the foreground so systemd tracks the process.
# Pass the VESC serial port only if it exists — Python falls back to MockConveyor if not.
DEVICE_FLAG=""
if [ -e /dev/ttyACM0 ]; then
    DEVICE_FLAG="--device /dev/ttyACM0:/dev/ttyACM0"
fi

#exec docker run --name sauce-backend \
#    -p 8080:8080 \
#    -v /home/saucemachine/AutoSauce:/app \
#    $DEVICE_FLAG \
#    sauce-backend
