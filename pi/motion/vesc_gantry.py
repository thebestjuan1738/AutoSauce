"""
vesc_gantry.py

Gantry driver for the SauceBot — NEO rev brushless motor controlled by a VESC over USB serial.
Implements the move_to(position_mm) interface expected by OrderManager.

Speaks the VESC serial protocol directly — no pyvesc dependency.

USB port auto-selects by OS:
    Windows → COM4
    Linux   → /dev/ttyACM0

Tune MAX_DUTY_GANTRY and MM_PER_SECOND once you've done a free-run calibration.
"""

import struct
import sys
import time
import serial

from pi.utils.logger import log

# ─── Config ───────────────────────────────────────────────────────────────────

VESC_GANTRY_PORT = "COM4"          if sys.platform == "win32" else "/dev/ttyACM0"
VESC_GANTRY_BAUD = 115200

# Map speed 0–100 → duty 0.0–MAX_DUTY.
# Keep this conservative until you've verified mechanical limits.
MAX_DUTY_GANTRY  = 0.3            # 30% duty cycle ceiling

# Speed used for move_to() calls (0–100 abstract units).
TRAVEL_SPEED = 50

# TODO: calibrate by measuring mm/s at TRAVEL_SPEED after motor detection.
# Run start(TRAVEL_SPEED) for 1 s, measure belt travel, set MM_PER_SECOND accordingly.
MM_PER_SECOND = 100.0             # mm/s at TRAVEL_SPEED — needs calibration

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
        _tach, _tach_abs,
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
        "fault_code":      fault,
        "fault_name":      _FAULT_NAMES.get(fault, f"UNKNOWN({fault})"),
    }


# ─── Driver ───────────────────────────────────────────────────────────────────

class VESCGantry:
    """
    Controls the NEO rev gantry belt motor via VESC over USB.
    Opened once at construction; connection is reused for the lifetime of the process.

    move_to() is the primary interface used by OrderManager.
    start() / reverse() / stop() are available for manual jog or calibration.
    """

    def __init__(self):
        log.info("VESCGantry: connecting to %s @ %d baud", VESC_GANTRY_PORT, VESC_GANTRY_BAUD)
        self._ser = serial.Serial(VESC_GANTRY_PORT, VESC_GANTRY_BAUD, timeout=1)
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

    def move_to(self, position_mm: int) -> None:
        """
        Open-loop move to position_mm (from the dock end of the rail).
        Runs at TRAVEL_SPEED in the correct direction for a time computed from MM_PER_SECOND.

        Calibrate MM_PER_SECOND at the top of this file to match your belt/pulley ratio.
        """
        if position_mm == self._position_mm:
            return

        delta_mm   = position_mm - self._position_mm
        duration_s = abs(delta_mm) / MM_PER_SECOND

        log.info(
            "VESCGantry: move_to %dmm  (from %dmm, Δ%+dmm, %.2fs)",
            position_mm, self._position_mm, delta_mm, duration_s,
        )

        if delta_mm > 0:
            self.start(TRAVEL_SPEED)
        else:
            self.reverse(TRAVEL_SPEED)

        time.sleep(duration_s)
        self.stop()

        self._position_mm = position_mm
        log.info("VESCGantry: arrived at %dmm", position_mm)

    def start(self, speed: int) -> None:
        """
        Drive the gantry forward (away from dock).
        speed: 0–100 abstract unit.
        """
        duty = (max(0, min(100, speed)) / 100.0) * MAX_DUTY_GANTRY
        log.info("VESCGantry: forward  speed=%d → duty=%.3f", speed, duty)
        self._ser.write(_packet_set_duty(duty))

    def reverse(self, speed: int) -> None:
        """Drive the gantry backward (toward dock)."""
        duty = (max(0, min(100, speed)) / 100.0) * MAX_DUTY_GANTRY
        log.info("VESCGantry: reverse  speed=%d → duty=%.3f", speed, duty)
        self._ser.write(_packet_set_duty(-duty))

    def stop(self) -> None:
        """Release motor current — gantry coasts to a stop."""
        log.info("VESCGantry: stop")
        self._ser.write(_packet_set_current(0.0))
