import sys
import serial
import serial.tools.list_ports
import threading
import time
from pi.utils.logger import log

class ArduinoController:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ArduinoController, cls).__new__(cls)
                cls._instance._init_serial()
            return cls._instance

    _BOOT_TIMEOUT = 15.0   # seconds to wait for READY banner (ESC arming takes ~6 s)

    # Known Arduino/clone USB VIDs — matched before falling back to description keywords.
    _ARDUINO_VIDS = frozenset({
        0x2341,  # Arduino SA (Uno, Mega, …)
        0x2A03,  # Arduino SRL
        0x1A86,  # WCH CH340 / CH341 clones
        0x0403,  # FTDI FT232
        0x10C4,  # Silicon Labs CP210x
    })
    # VID used by most VESC hardware — ports with this VID are never probed as Arduino.
    _VESC_VID = 0x0483   # STMicroelectronics
    # Devices that are NOT the gripper/extruder Mega — never probe these.
    #   10c4:ea60  CP210x UART Bridge → gantry NodeMCU
    #   2341:0043  Arduino Uno R3     → conveyor belt
    _SKIP_DEVICES = frozenset({
        (0x10C4, 0xEA60),
        (0x2341, 0x0043),
    })

    @staticmethod
    def _candidate_ports() -> list:
        """
        Return an ordered list of ports to try, skipping known VESC ports.
        Works regardless of USB hub plug-in order.
        Priority: exact VID match → description keyword → everything else.
        """
        arduino_keywords = ('arduino', 'ch340', 'ch341', 'ftdi', 'usb serial')

        def _sort_key(name):
            nums = ''.join(c for c in name if c.isdigit())
            return int(nums) if nums else 999

        by_vid     = []
        by_keyword = []
        others     = []

        for p in serial.tools.list_ports.comports():
            if p.vid == ArduinoController._VESC_VID:
                continue  # skip VESC — never try it as an Arduino
            if (p.vid, p.pid) in ArduinoController._SKIP_DEVICES:
                continue  # skip gantry CP210x and conveyor Uno
            desc = (p.description or '').lower()
            if p.vid in ArduinoController._ARDUINO_VIDS:
                by_vid.append(p.device)
            elif any(k in desc for k in arduino_keywords):
                by_keyword.append(p.device)
            else:
                others.append(p.device)

        # On Linux, restrict the last-resort fallback to ttyACM/ttyUSB paths only
        if not sys.platform.startswith('win'):
            others = [p for p in others if '/dev/ttyACM' in p or '/dev/ttyUSB' in p]

        return (sorted(by_vid, key=_sort_key) +
                sorted(by_keyword, key=_sort_key) +
                sorted(others, key=_sort_key))

    def _init_serial(self):
        self.serial_lock = threading.Lock()
        self.port = None
        candidates = self._candidate_ports()
        if not candidates:
            log.error("ArduinoController: no serial ports found on this system")
            return
        for port_name in candidates:
            try:
                log.info(f"ArduinoController: attempting to connect to {port_name}")
                self.port = serial.Serial(port_name, 115200, timeout=1)
                self._wait_for_ready(port_name)
                return
            except Exception as e:
                log.error(f"ArduinoController: failed on {port_name}: {e}")
                if self.port and self.port.is_open:
                    self.port.close()
                self.port = None
        log.error("ArduinoController: could not connect to Arduino on any port")

    def _wait_for_ready(self, port_name: str) -> None:
        """Wait for the READY banner, then verify with a PING/PONG."""
        log.info(f"ArduinoController: waiting for READY banner on {port_name}...")
        deadline = time.time() + self._BOOT_TIMEOUT
        while time.time() < deadline:
            if self.port.in_waiting > 0:
                line = self.port.readline().decode('utf-8').strip()
                log.info(f"ArduinoController boot: {line}")
                if 'READY' in line:
                    log.info("ArduinoController: READY received, sending PING...")
                    self._ping()
                    return
            time.sleep(0.05)
        raise RuntimeError(
            f"ArduinoController: timed out waiting for READY from {port_name}"
        )

    def _ping(self) -> None:
        """Send PING and expect PONG to confirm two-way communication."""
        self.port.reset_input_buffer()
        self.port.write(b'PING\n')
        self.port.flush()
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if self.port.in_waiting > 0:
                response = self.port.readline().decode('utf-8').strip()
                if response == 'PONG':
                    log.info("ArduinoController: PONG received — Arduino is ready")
                    return
        raise RuntimeError("ArduinoController: no PONG received — Arduino may be unresponsive")

    def send_command(self, cmd: str, timeout: float = 15.0, done_marker: str = None) -> bool:
        """
        Send a command to the Arduino and wait for completion.

        Args:
            cmd: Command string to send
            timeout: Max seconds to wait for completion
            done_marker: Expected completion string (e.g., "GRAB_DONE", "PLUNGER_DONE").
                         If None, looks for any line ending with "_DONE" or "READY".

        Returns:
            True if command completed successfully, False otherwise.
        """
        if not self.port or not self.port.is_open:
            log.error("Cannot send command, Arduino not connected.")
            return False

        with self.serial_lock:
            self.port.reset_input_buffer()
            full_cmd = f"{cmd}\n"
            self.port.write(full_cmd.encode('utf-8'))
            self.port.flush()
            log.info(f"Sent to Arduino: {cmd}")

            start_time = time.time()
            while time.time() - start_time < timeout:
                if self.port.in_waiting > 0:
                    response = self.port.readline().decode('utf-8').strip()
                    if not response:
                        continue

                    # Check for specific done marker
                    if done_marker and response == done_marker:
                        log.info(f"Arduino completed: {cmd} -> {response}")
                        return True

                    # Check for generic completion patterns
                    if done_marker is None:
                        if response.endswith("_DONE") or response == "READY" or response == "STOPPED":
                            log.info(f"Arduino completed: {cmd} -> {response}")
                            return True

                    # Check for error responses
                    if response == "ERROR" or response.startswith("[ERR]"):
                        log.error(f"Arduino error for {cmd}: {response}")
                        return False

                    # Log intermediate messages
                    log.info(f"Arduino: {response}")
                time.sleep(0.01)

            log.error(f"Arduino command {cmd} timed out after {timeout}s")
            return False

    def send_command_async(self, cmd: str) -> bool:
        """
        Send a command without waiting for completion.
        Useful for commands like EXTRUDESLOW that run continuously.

        Returns:
            True if command was sent, False if not connected.
        """
        if not self.port or not self.port.is_open:
            log.error("Cannot send command, Arduino not connected.")
            return False

        with self.serial_lock:
            full_cmd = f"{cmd}\n"
            self.port.write(full_cmd.encode('utf-8'))
            self.port.flush()
            log.info(f"Sent to Arduino (async): {cmd}")
            return True

    def wait_for_response(self, marker: str, timeout: float = 15.0) -> bool:
        """
        Wait for a specific response marker from the Arduino.

        Args:
            marker: The response string to wait for
            timeout: Max seconds to wait

        Returns:
            True if marker was received, False on timeout.
        """
        if not self.port or not self.port.is_open:
            return False

        with self.serial_lock:
            start_time = time.time()
            while time.time() - start_time < timeout:
                if self.port.in_waiting > 0:
                    response = self.port.readline().decode('utf-8').strip()
                    if not response:
                        continue
                    log.info(f"Arduino: {response}")
                    if response == marker:
                        return True
                    if response == "ERROR" or response.startswith("[ERR]"):
                        log.error(f"Arduino error: {response}")
                        return False
                time.sleep(0.01)
            return False
