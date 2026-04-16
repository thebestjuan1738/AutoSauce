# AutoSauce — Dev Update Guide

## Updating the software (Pi)

SSH into the Pi, then pull the latest code and restart the service:

```bash
cd ~/AutoSauce
git pull
sudo systemctl restart sauce-backend
```

That's it. The service will pick up the new Python/config changes automatically.

---

## Arduino firmware changes

Changes to `autosauce_testing.ino` **cannot** be deployed via git pull.  
They require a manual upload from the Windows laptop using the Arduino IDE:

1. Open `autosauce_testing/autosauce_testing.ino` in the Arduino IDE
2. Connect the Arduino via USB
3. Select the correct board and COM port
4. Click **Upload**
