"""
vesc_gantry.py

Gantry driver for the SauceBot — NEO rev brushless motor controlled by a VESC over USB serial.
Implements the move_to(position_mm) interface expected by OrderManager.

Speaks the VESC serial protocol directly — no pyvesc dependency.
Uses tachometer_abs from COMM_GET_VALUES for closed-loop position control.

USB port auto-selects by OS:
    Windows → COM4
    Linux   → /dev/ttyACM0

Tune MAX_DUTY_GANTRY and TICKS_PER_MM once you've done a free-run calibration:
  1. Position gantry at one end with 300+ mm of clear travel.
  2. Run: python calibrate_gantry.py
  3. Enter the measured distance each run until readings converge.
  4. Paste the output values for TICKS_PER_MM and POSITION_TOLERANCE_TICKS here.

Current calibration: 3.67 ticks/mm (runs 2–3 average, April 2026).
"""

import struct
import sys
import time
import serial
import serial.tools.list_ports

from pi.utils.logger import log

# ─── Config ───────────────────────────────────────────────────────────────────

VESC_GANTRY_PORT = "COM4"          if sys.platform == "win32" else "/dev/ttyACM0"
VESC_GANTRY_BAUD = 115200

# Map speed 0–100 → duty 0.0–MAX_DUTY.
MAX_DUTY_GANTRY  = 0.7           # 70% duty ceiling — raise only after verifying mechanics
# Minimum duty applied even at low speeds — needed to overcome sticky/noisy sections.
MIN_DUTY_GANTRY  = 0.5           # never go below this when the motor is running
# Speed used for move_to() calls (0–100 abstract units).
# 80 × 0.5 = 0.40 effective duty — enough torque to drive a loaded gantry.
TRAVEL_SPEED = 80

# Calibrated: average of runs 2–3 from calibrate_gantry.py (3.56, 3.78 ticks/mm).
TICKS_PER_MM = 3.67               # encoder ticks per mm of belt travel

# How close (in ticks) counts as "arrived".
# 3.67 × 2 ≈ 7 ticks = ±1.9 mm
POSITION_TOLERANCE_TICKS = 7

# Raise TimeoutError if the gantry doesn't reach the target within this many seconds.
TRAVEL_TIMEOUT_S = 30

# P-controller deceleration zone.
# Duty ramps linearly from MAX_DUTY_GANTRY down to MIN_DUTY_GANTRY over the last
# DECEL_ZONE_MM millimetres of travel.  Increase to start braking earlier;
# decrease if the gantry stalls in the ramp zone.
DECEL_ZONE_MM = 50   # mm before target where ramp begins

# Stall detection — if tachometer_abs doesn't advance for this long, fire a torque kick.
STALL_DETECT_S   = 0.6   # seconds of no progress before a kick
STALL_KICK_DUTY  = MAX_DUTY_GANTRY   # kick at the duty ceiling (0.70)
STALL_KICK_S     = 0.35  # how long to hold the kick
STALL_MAX_KICKS  = 3     # give up after this many failed kicks

# Launch kick — applied at the very start of each move to overcome static friction.
# If the gantry stalls on the first movement then moves once the stall kick fires,
# increase LAUNCH_KICK_DUTY or LAUNCH_KICK_S until it breaks away cleanly.
LAUNCH_KICK_DUTY = MAX_DUTY_GANTRY   # duty for the initial breakaway burst
LAUNCH_KICK_S    = 0.3               # how long to hold the burst (seconds)

# USB VID/PID used to identify the VESC regardless of plug-in order.
# Run `python -m serial.tools.list_ports -v` to verify your device's VID and PID.
# VESC 4.x / 6.x on STM32 hardware is typically VID=0x0483, PID=0x5740.
_VESC_VID = 0x0483   # STMicroelectronics
_VESC_PID = 0x5740   # USB Serial (CDC)

# VESC command IDs
_COMM_GET_VALUES  = 4
_COMM_SET_DUTY    = 5
_COMM_SET_CURRENT = 6

# Boot-check safety thresholds
_MIN_VIN_V      = 8.0    # below this → battery dead / not connected
_MAX_VIN_V      = 65.0   # above this → over-voltage
_MAX_FET_TEMP_C = 80.0   # MOSFET too hot to start
_MAX_MOT_TEMP_C = 100.0  # motor too hot to start (if sensor fitted)
_MAX_IDLE_RPM   = 100    # motor should be stationary at boot

_FAULT_NAMES = {
    0: "NONE",
    1: "OVER_VOLTAGE",
    2: "UNDER_VOLTAGE",
    3: "DRV",
    4: "ABS_OVER_CURRENT",
    5: "OVER_TEMP_FET",
    6: "OVER_TEMP_MOTOR",
}


# ─── VESC packet helpers ──────────────────────────────────────────────────────

def _crc16_ccitt(data: bytes) -> int:
    """CRC-CCITT (XModem, poly=0x1021, init=0x0000)."""
    crc = 0x0000
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc


def _build_packet(payload: bytes) -> bytes:
    """Wrap payload in a VESC short-frame packet."""
    crc = _crc16_ccitt(payload)
    return bytes([0x02, len(payload)]) + payload + bytes([crc >> 8, crc & 0xFF, 0x03])


def _packet_set_duty(duty: float) -> bytes:
    """COMM_SET_DUTY: duty ∈ [-1.0, 1.0] → int32 scaled by 1e5."""
    value = int(duty * 100_000)
    return _build_packet(bytes([_COMM_SET_DUTY]) + struct.pack('>i', value))


def _packet_set_current(amps: float) -> bytes:
    """COMM_SET_CURRENT: amps → int32 scaled by 1e3."""
    value = int(amps * 1000)
    return _build_packet(bytes([_COMM_SET_CURRENT]) + struct.pack('>i', value))


def _packet_get_values() -> bytes:
    """COMM_GET_VALUES: no arguments — requests full telemetry from VESC."""
    return _build_packet(bytes([_COMM_GET_VALUES]))


def _read_packet(ser: serial.Serial) -> bytes:
    """Read one VESC response frame and return the validated payload."""
    start = ser.read(1)
    if not start:
        raise RuntimeError("VESC did not respond (timeout during boot check)")
    if start[0] == 0x02:                          # short frame
        length = ser.read(1)[0]
    elif start[0] == 0x03:                        # long frame
        hi, lo = ser.read(1)[0], ser.read(1)[0]
        length = (hi << 8) | lo
    else:
        raise RuntimeError(f"Unexpected VESC start byte: 0x{start[0]:02X}")

    payload   = ser.read(length)
    crc_bytes = ser.read(2)
    end       = ser.read(1)

    if len(payload) != length:
        raise RuntimeError("VESC response truncated")
    if not end or end[0] != 0x03:
        raise RuntimeError("VESC packet missing end byte")

    crc_rx   = (crc_bytes[0] << 8) | crc_bytes[1]
    crc_calc = _crc16_ccitt(payload)
    if crc_rx != crc_calc:
        raise RuntimeError(f"VESC CRC mismatch: rx=0x{crc_rx:04X} calc=0x{crc_calc:04X}")

    return payload


def _parse_get_values(payload: bytes) -> dict:
    """
    Parse a COMM_GET_VALUES response payload (starts with command byte).
    Returns a dict of human-readable telemetry values.

    VESC firmware response layout (big-endian, after command byte):
        int16  temp_fet        / 10  → °C
        int16  temp_motor      / 10  → °C
        int32  avg_motor_curr  / 100 → A
        int32  avg_input_curr  / 100 → A
        int32  avg_id          / 100 → A
        int32  avg_iq          / 100 → A
        int16  duty_cycle      / 1000
        int32  rpm
        int16  v_in            / 10  → V
        int32  amp_hours       / 10000
        int32  amp_hours_chg   / 10000
        int32  watt_hours      / 10000
        int32  watt_hours_chg  / 10000
        int32  tachometer
        int32  tachometer_abs
        uint8  fault_code
    """
    # skip leading command byte
    data = payload[1:]
    if len(data) < 53:
        raise RuntimeError(f"COMM_GET_VALUES response too short: {len(data)} bytes")

    (
        raw_temp_fet, raw_temp_mot,
        raw_motor_curr, raw_input_curr, raw_id, raw_iq,
        raw_duty, raw_rpm,
        raw_vin,
        _ah, _ahc, _wh, _whc,
        _tach, tach_abs,
        fault,
    ) = struct.unpack_from('>hhiiiihihiiiiiiB', data)

    return {
        "temp_fet_c":      raw_temp_fet   / 10.0,
        "temp_motor_c":    raw_temp_mot   / 10.0,
        "motor_current_a": raw_motor_curr / 100.0,
        "input_current_a": raw_input_curr / 100.0,
        "duty_cycle":      raw_duty       / 1000.0,
        "rpm":             raw_rpm,
        "v_in":            raw_vin        / 10.0,
        "tachometer_abs":  tach_abs,
        "fault_code":      fault,
        "fault_name":      _FAULT_NAMES.get(fault, f"UNKNOWN({fault})"),
    }


def _find_vesc_port() -> str:
    """
    Scan serial ports for the VESC by USB VID/PID so it is found regardless of
    USB hub plug-in order.  Falls back to VESC_GANTRY_PORT if no match is found.
    Run `python -m serial.tools.list_ports -v` to verify _VESC_VID / _VESC_PID.
    """
    for p in serial.tools.list_ports.comports():
        if p.vid == _VESC_VID and p.pid == _VESC_PID:
            log.info("VESCGantry: matched VESC on %s (%s)", p.device, p.description)
            return p.device
    log.warning(
        "VESCGantry: no port matched VID=0x%04X PID=0x%04X — falling back to %s",
        _VESC_VID, _VESC_PID, VESC_GANTRY_PORT,
    )
    return VESC_GANTRY_PORT


# ─── Driver ───────────────────────────────────────────────────────────────────

class VESCGantry:
    """
    Controls the NEO rev gantry belt motor via VESC over USB.
    Opened once at construction; connection is reused for the lifetime of the process.

    move_to() is the primary interface used by OrderManager.
    start() / reverse() / stop() are available for manual jog or calibration.
    """

    def __init__(self):
        port = _find_vesc_port()
        log.info("VESCGantry: connecting to %s @ %d baud", port, VESC_GANTRY_BAUD)
        self._ser = serial.Serial(port, VESC_GANTRY_BAUD, timeout=1)
        # Assumes the gantry is physically at the dock (0 mm) when the program starts.
        self._position_mm = 0
        log.info("VESCGantry: connected")

    def boot_check(self) -> None:
        """
        Request telemetry from the VESC and verify the motor is safe to run.
        Raises RuntimeError with a clear message if any check fails.
        Called once at startup before accepting orders.
        """
        log.info("VESCGantry: running boot check...")
        self._ser.reset_input_buffer()
        self._ser.write(_packet_get_values())

        payload = _read_packet(self._ser)
        v = _parse_get_values(payload)

        log.info(
            "VESCGantry: telemetry — VIN=%.1fV  FET=%.1f°C  MOT=%.1f°C  "
            "RPM=%d  duty=%.3f  fault=%s",
            v["v_in"], v["temp_fet_c"], v["temp_motor_c"],
            v["rpm"], v["duty_cycle"], v["fault_name"],
        )

        errors = []
        if v["fault_code"] != 0:
            errors.append(f"active fault: {v['fault_name']}")
        if v["v_in"] < _MIN_VIN_V:
            errors.append(f"input voltage too low: {v['v_in']:.1f}V (min {_MIN_VIN_V}V)")
        if v["v_in"] > _MAX_VIN_V:
            errors.append(f"input voltage too high: {v['v_in']:.1f}V (max {_MAX_VIN_V}V)")
        if v["temp_fet_c"] > _MAX_FET_TEMP_C:
            errors.append(f"FET temperature too high: {v['temp_fet_c']:.1f}°C (max {_MAX_FET_TEMP_C}°C)")
        if v["temp_motor_c"] > _MAX_MOT_TEMP_C:
            errors.append(f"motor temperature too high: {v['temp_motor_c']:.1f}°C (max {_MAX_MOT_TEMP_C}°C)")
        if abs(v["rpm"]) > _MAX_IDLE_RPM:
            errors.append(f"motor already spinning at boot: {v['rpm']} RPM")

        if errors:
            raise RuntimeError("VESCGantry boot check FAILED:\n  " + "\n  ".join(errors))

        log.info("VESCGantry: boot check passed ✓")

    def _get_encoder_position(self) -> int:
        """Request telemetry and return the absolute tachometer tick count."""
        self._ser.reset_input_buffer()
        self._ser.write(_packet_get_values())
        payload = _read_packet(self._ser)
        return _parse_get_values(payload)["tachometer_abs"]

    def calibrate(self, duration_s: float = 5.0) -> None:
        """
        Calibration utility — measures TICKS_PER_MM for your belt/pulley.

        Usage (run once from a Python shell or a throw-away script):
            from pi.motion.vesc_gantry import VESCGantry
            g = VESCGantry()
            g.boot_check()
            g.calibrate(duration_s=5)
            # then measure how far the carriage physically moved and set:
            # TICKS_PER_MM = printed_ticks / actual_mm
            # POSITION_TOLERANCE_TICKS = round(TICKS_PER_MM * 2)  # ±2 mm

        Ensure at least 300 mm of clear travel before calling.
        """
        log.info("VESCGantry calibrate: running %.1fs at speed=%d...", duration_s, TRAVEL_SPEED)
        t_start = self._get_encoder_position()
        self.start(TRAVEL_SPEED)
        time.sleep(duration_s)
        self.stop()
        t_end = self._get_encoder_position()
        delta = abs(t_end - t_start)
        log.info(
            "VESCGantry calibrate: %d ticks in %.1fs",
            delta, duration_s,
        )
        print(f"\nCalibration result: {delta} ticks in {duration_s}s")
        print(f"Measure how far the carriage moved, then set:")
        print(f"    TICKS_PER_MM = {delta} / <actual_mm_travelled>")
        print(f"    POSITION_TOLERANCE_TICKS = round(TICKS_PER_MM * 2)  # ±2 mm")

    def _p_duty(self, ticks_remaining: int, delta_ticks: int) -> float:
        """
        P-controller ramp: returns the duty to apply given how far is left.
        Full duty during cruise; linearly tapers to MIN_DUTY over the decel zone.
        Short moves entirely within the decel zone use the full zone for the ramp
        so they don't stall on very small hops.
        """
        decel_zone_ticks = int(DECEL_ZONE_MM * TICKS_PER_MM)
        effective_zone   = min(decel_zone_ticks, delta_ticks)
        if ticks_remaining >= effective_zone or effective_zone == 0:
            return MAX_DUTY_GANTRY
        ramp = ticks_remaining / effective_zone          # 1.0 → 0.0 as target approaches
        return MIN_DUTY_GANTRY + ramp * (MAX_DUTY_GANTRY - MIN_DUTY_GANTRY)

    def move_to(self, position_mm: int) -> None:
        """
        Closed-loop move to position_mm (from the dock end of the rail).
        Drives at TRAVEL_SPEED and polls tachometer_abs at 20 Hz until the
        target tick count is reached within POSITION_TOLERANCE_TICKS.

        Calibrate TICKS_PER_MM at the top of this file — see module docstring.
        """
        if position_mm == self._position_mm:
            return

        delta_mm    = position_mm - self._position_mm
        delta_ticks = int(abs(delta_mm) * TICKS_PER_MM)
        direction   = 1 if delta_mm > 0 else -1

        start_ticks = self._get_encoder_position()

        log.info(
            "VESCGantry: move_to %dmm  (from %dmm, Δ%+d ticks needed)",
            position_mm, self._position_mm, direction * delta_ticks,
        )

        # Launch kick — brief full-duty burst to break static friction before the
        # P-controller takes over.  Avoids waiting for stall detection to fire.
        log.debug("VESCGantry: launch kick duty=%.2f for %.2fs", LAUNCH_KICK_DUTY, LAUNCH_KICK_S)
        self._ser.write(_packet_set_duty(-direction * LAUNCH_KICK_DUTY))
        time.sleep(LAUNCH_KICK_S)

        deadline        = time.monotonic() + TRAVEL_TIMEOUT_S
        last_tick_time  = time.monotonic()
        last_ticks_seen = start_ticks
        kicks           = 0

        while True:
            # tachometer_abs always increases — measure distance travelled from start
            current_ticks   = self._get_encoder_position()
            ticks_travelled = abs(current_ticks - start_ticks)
            ticks_remaining = delta_ticks - ticks_travelled

            if ticks_remaining <= POSITION_TOLERANCE_TICKS:
                break

            if time.monotonic() > deadline:
                self.stop()
                actual_mm = int(ticks_travelled / TICKS_PER_MM)
                self._position_mm += direction * actual_mm
                raise TimeoutError(
                    f"VESCGantry timed out after {TRAVEL_TIMEOUT_S}s "
                    f"moving to {position_mm}mm "
                    f"(~{int(ticks_remaining / TICKS_PER_MM)}mm remaining)"
                )

            # ── P ramp — update duty every poll cycle ──────────────────────
            duty = self._p_duty(ticks_remaining, delta_ticks)
            self._ser.write(_packet_set_duty(-direction * duty))

            # Stall detection — no tachometer progress for STALL_DETECT_S
            if current_ticks != last_ticks_seen:
                last_ticks_seen = current_ticks
                last_tick_time  = time.monotonic()
            elif time.monotonic() - last_tick_time > STALL_DETECT_S:
                kicks += 1
                if kicks > STALL_MAX_KICKS:
                    self.stop()
                    actual_mm = int(ticks_travelled / TICKS_PER_MM)
                    self._position_mm += direction * actual_mm
                    raise RuntimeError(
                        f"VESCGantry stalled at ~{self._position_mm}mm after "
                        f"{STALL_MAX_KICKS} torque kicks — check for obstruction"
                    )
                log.warning(
                    "VESCGantry: stall detected (%d/%d) — torque kick at duty=%.2f",
                    kicks, STALL_MAX_KICKS, STALL_KICK_DUTY,
                )
                # Brief high-duty kick to break through the sticky section
                kick_duty = STALL_KICK_DUTY if direction > 0 else -STALL_KICK_DUTY
                self._ser.write(_packet_set_duty(-kick_duty))
                time.sleep(STALL_KICK_S)
                last_tick_time = time.monotonic()  # reset stall clock; P ramp resumes next iter

            time.sleep(0.05)  # poll at 20 Hz

        self.stop()
        self._position_mm = position_mm
        log.info("VESCGantry: arrived at %dmm", position_mm)

    def start(self, speed: int) -> None:
        """
        Drive the gantry forward (away from dock).
        speed: 0–100 abstract unit.
        """
        duty = (max(0, min(100, speed)) / 100.0) * MAX_DUTY_GANTRY
        duty = max(duty, MIN_DUTY_GANTRY)
        log.info("VESCGantry: forward  speed=%d → duty=%.3f", speed, -duty)
        self._ser.write(_packet_set_duty(-duty))

    def reverse(self, speed: int) -> None:
        """Drive the gantry backward (toward dock)."""
        duty = (max(0, min(100, speed)) / 100.0) * MAX_DUTY_GANTRY
        duty = max(duty, MIN_DUTY_GANTRY)
        log.info("VESCGantry: reverse  speed=%d → duty=%.3f", speed, duty)
        self._ser.write(_packet_set_duty(duty))

    def stop(self) -> None:
        """Release motor current — gantry coasts to a stop."""
        log.info("VESCGantry: stop")
        self._ser.write(_packet_set_current(0.0))
