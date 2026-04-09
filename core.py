from __future__ import annotations

import logging
import socket
import threading
import time
from collections import defaultdict

from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext
try:
    from pymodbus.datastore import ModbusDeviceContext
except ImportError:
    from pymodbus.datastore import ModbusSlaveContext as ModbusDeviceContext
from pymodbus.server import StartTcpServer

from auth import TokenStore
from config import ConfigManager
from display import FramebufferDisplay
from sensors import Mics6814Sensor, OxygenSensor
from shared_state import SharedState
from web_server import WebServerThread


LOGGER = logging.getLogger(__name__)


class ModbusBridge:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._device_context = ModbusDeviceContext(hr=ModbusSequentialDataBlock(0, [0] * 10))
        try:
            self._context = ModbusServerContext(devices=self._device_context, single=True)
        except TypeError:
            self._context = ModbusServerContext(slaves=self._device_context, single=True)

    def update(self, measurements: dict[str, float | None]) -> None:
        values = [
            int(round((measurements.get("oxygen") or 0) * 10)),
            int(round(measurements.get("co") or 0)),
            int(round((measurements.get("no2") or 0) * 10)),
            int(round(measurements.get("nh3") or 0)),
        ]
        with self._lock:
            self._device_context.setValues(3, 0, values)

    def serve(self, port: int) -> None:
        LOGGER.info("starting modbus tcp server on port %s", port)
        StartTcpServer(context=self._context, address=("0.0.0.0", port))


class MeasurementWindow:
    def __init__(self, publish_window: float) -> None:
        self._samples: dict[str, list[float]] = defaultdict(list)
        self.publish_window = publish_window
        self._last_published = time.monotonic()

    def add_sample(self, gas: str, value: float | None) -> None:
        if value is None:
            return
        self._samples[gas].append(value)

    def ready(self) -> bool:
        return (time.monotonic() - self._last_published) >= self.publish_window

    def publish(self, fallback: dict[str, float | None]) -> dict[str, float | None]:
        published: dict[str, float | None] = dict(fallback)
        for gas, values in self._samples.items():
            if values:
                published[gas] = round(sum(values) / len(values), 2)
        self._samples.clear()
        self._last_published = time.monotonic()
        return published


class GasMonitorCore:
    def __init__(self, config_manager: ConfigManager) -> None:
        self.config_manager = config_manager
        self.runtime = config_manager.runtime()
        self.state = SharedState(
            config=self.runtime,
            config_mode=self.runtime.first_run,
            require_password_change=self.runtime.first_run,
        )
        self.stop_event = threading.Event()
        self.modbus = ModbusBridge()
        self.token_store = TokenStore(config_manager)
        self.display = self._build_display(self.runtime)
        self.oxygen_sensor = self._build_oxygen_sensor(self.runtime)
        self.mics_sensor = self._build_mics_sensor(self.runtime)
        self.measurement_window = MeasurementWindow(self.runtime.publish_window)
        self.last_stable_measurements: dict[str, float | None] = {
            "oxygen": None,
            "co": None,
            "no2": None,
            "nh3": None,
        }

    def run(self) -> None:
        self._start_thread("sensor-loop", self._sensor_loop)
        self._start_thread("display-loop", self._display_loop)
        self._start_thread("web-api", WebServerThread(self.config_manager, self.state, self.token_store).run)
        if self.runtime.modbus_enabled:
            self._start_thread("modbus-server", lambda: self.modbus.serve(self.runtime.modbus_port))

        while not self.stop_event.is_set():
            time.sleep(1.0)

    def stop(self) -> None:
        self.stop_event.set()
        self.oxygen_sensor.close()

    def _start_thread(self, name: str, target) -> None:
        thread = threading.Thread(name=name, target=target, daemon=True)
        thread.start()

    def _sensor_loop(self) -> None:
        while not self.stop_event.is_set():
            runtime = self.config_manager.runtime()
            self._apply_runtime_changes(runtime)
            self.state.refresh_config(runtime)
            self.state.set_ip_address(self._get_ip_address())

            measurements: dict[str, float | None] = {}
            try:
                measurements.update(self.oxygen_sensor.read())
            except Exception as exc:
                LOGGER.warning("oxygen sensor read ignored: %s", exc)

            if self.mics_sensor is not None:
                try:
                    measurements.update(self.mics_sensor.read())
                except Exception as exc:
                    LOGGER.warning("mics6814 read ignored: %s", exc)

            filtered = self._filter_measurements(measurements)
            for gas, value in filtered.items():
                self.measurement_window.add_sample(gas, value)

            if self.measurement_window.ready():
                published = self.measurement_window.publish(self.last_stable_measurements)
                self.last_stable_measurements.update(published)
                self.state.update_measurements(self.last_stable_measurements)
                self.modbus.update(self.last_stable_measurements)
            time.sleep(runtime.interval)

    def _display_loop(self) -> None:
        while not self.stop_event.is_set():
            self.display.render(self.state.snapshot())
            time.sleep(1.0)

    @staticmethod
    def _get_ip_address() -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return "0.0.0.0"
        finally:
            sock.close()

    def _apply_runtime_changes(self, runtime) -> None:
        if runtime == self.runtime:
            return
        if (
            runtime.framebuffer != self.runtime.framebuffer
            or runtime.display_width != self.runtime.display_width
            or runtime.display_height != self.runtime.display_height
            or runtime.display_rotate != self.runtime.display_rotate
        ):
            self.display = self._build_display(runtime)
        if (
            runtime.i2c_bus != self.runtime.i2c_bus
            or runtime.oxygen_address != self.runtime.oxygen_address
            or runtime.oxygen_factor != self.runtime.oxygen_factor
            or runtime.samples != self.runtime.samples
        ):
            self.oxygen_sensor.close()
            self.oxygen_sensor = self._build_oxygen_sensor(runtime)
        if (
            runtime.mics_enabled != self.runtime.mics_enabled
            or runtime.mics_path != self.runtime.mics_path
            or runtime.co_factor != self.runtime.co_factor
            or runtime.no2_factor != self.runtime.no2_factor
            or runtime.nh3_factor != self.runtime.nh3_factor
            or runtime.samples != self.runtime.samples
        ):
            self.mics_sensor = self._build_mics_sensor(runtime)
        if runtime.publish_window != self.runtime.publish_window:
            self.measurement_window.publish_window = runtime.publish_window
        self.runtime = runtime

    def _filter_measurements(self, measurements: dict[str, float | None]) -> dict[str, float | None]:
        max_allowed_jump = {
            "oxygen": self.runtime.oxygen_max_jump,
            "co": self.runtime.co_max_jump,
            "no2": self.runtime.no2_max_jump,
            "nh3": self.runtime.nh3_max_jump,
        }
        filtered: dict[str, float | None] = {}
        for gas, value in measurements.items():
            previous = self.last_stable_measurements.get(gas)
            if value is None:
                continue
            if previous is not None and abs(value - previous) > max_allowed_jump[gas]:
                LOGGER.warning(
                    "ignoring %s jump from %.2f to %.2f",
                    gas,
                    previous,
                    value,
                )
                continue
            filtered[gas] = value
        return filtered

    @staticmethod
    def _build_display(runtime) -> FramebufferDisplay:
        return FramebufferDisplay(
            framebuffer=runtime.framebuffer,
            width=runtime.display_width,
            height=runtime.display_height,
            rotate=runtime.display_rotate,
        )

    @staticmethod
    def _build_oxygen_sensor(runtime) -> OxygenSensor:
        return OxygenSensor(
            bus_id=runtime.i2c_bus,
            address=runtime.oxygen_address,
            calibration_factor=runtime.oxygen_factor,
            samples=runtime.samples,
        )

    @staticmethod
    def _build_mics_sensor(runtime):
        if not runtime.mics_enabled:
            return None
        return Mics6814Sensor(
            device_path=runtime.mics_path,
            samples=runtime.samples,
            calibration={
                "co": runtime.co_factor,
                "no2": runtime.no2_factor,
                "nh3": runtime.nh3_factor,
            },
        )
