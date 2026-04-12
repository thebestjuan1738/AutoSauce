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

    _BOOT_TIMEOUT = 10.0   # seconds to wait for READY banner
    _LINUX_PORTS  = ['/dev/ttyACM1', '/dev/ttyUSB0']

    @staticmethod
    def _candidate_ports() -> list:
        """Return ordered list of ports to try based on platform."""
        if sys.platform.startswith('win'):
            # On Windows, scan for COM ports with Arduino/CH340/FTDI descriptors first,
            # then fall back to all available COM ports in numerical order.
            arduino_keywords = ('arduino', 'ch340', 'ch341', 'ftdi', 'usb serial')
            priority = []
            others   = []
            for p in serial.tools.list_ports.comports():
                desc = (p.description or '').lower()
                if any(k in desc for k in arduino_keywords):
                    priority.append(p.device)
                else:
                    others.append(p.device)
            # Sort COM ports numerically so COM3 comes before COM10
            def _com_key(name):
                try:
                    return int(name.upper().replace('COM', ''))
                except ValueError:
                    return 999
            return sorted(priority, key=_com_key) + sorted(others, key=_com_key)
        return ArduinoController._LINUX_PORTS

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

    def send_command(self, cmd: str, timeout: float = 15.0) -> bool:
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
                    if response == "DONE":
                        log.info(f"Arduino completed: {cmd}")
                        return True
                    elif response:
                        log.info(f"Arduino log: {response}")
                time.sleep(0.01)
            
            log.error(f"Arduino command {cmd} timed out after {timeout}s")
            return False
