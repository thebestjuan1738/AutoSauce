#!/bin/bash
# Sauce Dispenser Backend Launcher (Docker only)
# Run this to start ONLY the backend (API server)

# Stop and remove any existing backend container
sudo docker stop saucebot-backend 2>/dev/null || true
sudo docker rm saucebot-backend 2>/dev/null || true

# Start backend container
sudo docker run --rm -d --name saucebot-backend -p 8080:8080 -v /home/saucemachine/AutoSauce:/app saucebot-backend
