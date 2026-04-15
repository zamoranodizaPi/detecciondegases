from __future__ import annotations

import logging
import random
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
from sensors import Mics6814Sensor, NoisyOxygenReading, OxygenSensor
from shared_state import SharedState
from web_server import WebServerThread
from utils.state_machine import SystemState


LOGGER = logging.getLogger(__name__)
WARMUP_SECONDS = 5.0


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
            int(round((measurements.get("co") or 0) * 10)),
            int(round((measurements.get("no2") or 0) * 10)),
            int(round((measurements.get("nh3") or 0) * 10)),
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
        self._last_published = time.monotonic() - publish_window

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
        self.started_at = time.monotonic()
        self.oxygen_sensor = self._build_oxygen_sensor(self.runtime)
        self.mics_sensor = self._build_mics_sensor(self.runtime)
        self.measurement_window = MeasurementWindow(self.runtime.publish_window)
        self.last_stable_measurements: dict[str, float | None] = {
            "oxygen": None,
            "co": None,
            "no2": None,
            "nh3": None,
        }
        self.sensor_heartbeat = time.monotonic()
        self.last_alarm_state = ""

    def run(self) -> None:
        self._start_thread("sensor-loop", self._sensor_loop)
        self._start_thread("display-loop", self._display_loop)
        self._start_thread("web-api", WebServerThread(self.config_manager, self.state, self.token_store).run)
        if self.runtime.watchdog_enabled:
            self._start_thread("watchdog-loop", self._watchdog_loop)
        if self.runtime.modbus_enabled:
            self._start_thread("modbus-server", lambda: self.modbus.serve(self.runtime.modbus_port))

        while not self.stop_event.is_set():
            time.sleep(1.0)

    def stop(self) -> None:
        self.stop_event.set()
        if self.oxygen_sensor is not None:
            self.oxygen_sensor.close()
        if self.mics_sensor is not None:
            self.mics_sensor.close()

    def _start_thread(self, name: str, target) -> None:
        thread = threading.Thread(name=name, target=target, daemon=True)
        thread.start()

    def _sensor_loop(self) -> None:
        while not self.stop_event.is_set():
            runtime = self.config_manager.runtime()
            self._apply_runtime_changes(runtime)
            self.state.refresh_config(runtime)
            self.state.set_ip_address(self._get_ip_address())
            warming_up = time.monotonic() - self.started_at < WARMUP_SECONDS
            if warming_up:
                self.state.set_status(SystemState.WARMUP)

            if runtime.mock_sensors:
                measurements = self._mock_measurements()
                self.state.clear_sensor_fault("oxygen")
                self.state.clear_sensor_fault("mics6814")
            else:
                measurements: dict[str, float | None] = {}
                try:
                    if self.oxygen_sensor is not None:
                        measurements.update(self.oxygen_sensor.read())
                    self.state.clear_sensor_fault("oxygen")
                except NoisyOxygenReading as exc:
                    LOGGER.warning("oxygen noisy sample ignored: %s", exc)
                    self.state.clear_sensor_fault("oxygen")
                except Exception as exc:
                    LOGGER.warning("oxygen sensor read ignored: %s", exc)
                    self.state.set_sensor_fault("oxygen", str(exc))

                if self.mics_sensor is not None:
                    try:
                        measurements.update(self.mics_sensor.read())
                        self.state.clear_sensor_fault("mics6814")
                    except Exception as exc:
                        LOGGER.warning("mics6814 read ignored: %s", exc)
                        self.state.set_sensor_fault("mics6814", str(exc))
                else:
                    self.state.clear_sensor_fault("mics6814")

            filtered = self._filter_measurements(measurements)
            for gas, value in filtered.items():
                self.measurement_window.add_sample(gas, value)

            if self.measurement_window.ready() and not warming_up:
                published = self.measurement_window.publish(self.last_stable_measurements)
                self.last_stable_measurements.update(published)
                self.state.update_measurements(self.last_stable_measurements)
                self.modbus.update(self.last_stable_measurements)
                self.sensor_heartbeat = time.monotonic()
                self.state.mark_sensor_heartbeat(self.sensor_heartbeat)
                self._log_alarm_transition()
            time.sleep(runtime.interval)

    def _display_loop(self) -> None:
        while not self.stop_event.is_set():
            self.display.render(self.state.snapshot())
            time.sleep(1.0)

    def _watchdog_loop(self) -> None:
        while not self.stop_event.is_set():
            runtime = self.config_manager.runtime()
            max_age = max(10.0, runtime.interval * 5.0, runtime.publish_window * 2.0)
            if time.monotonic() - self.sensor_heartbeat > max_age:
                LOGGER.error("sensor watchdog timeout after %.1fs", max_age)
                self.state.set_sensor_fault("watchdog", "sensor update timeout")
                self._trigger_buzzer("watchdog")
            time.sleep(2.0)

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
            runtime.mock_sensors != self.runtime.mock_sensors
            or runtime.i2c_bus != self.runtime.i2c_bus
            or runtime.oxygen_address != self.runtime.oxygen_address
            or runtime.oxygen_factor != self.runtime.oxygen_factor
            or runtime.samples != self.runtime.samples
        ):
            if self.oxygen_sensor is not None:
                self.oxygen_sensor.close()
            self.oxygen_sensor = self._build_oxygen_sensor(runtime)
        if (
            runtime.mock_sensors != self.runtime.mock_sensors
            or runtime.mics_enabled != self.runtime.mics_enabled
            or runtime.i2c_bus != self.runtime.i2c_bus
            or runtime.mics_address != self.runtime.mics_address
            or runtime.co_factor != self.runtime.co_factor
            or runtime.no2_factor != self.runtime.no2_factor
            or runtime.nh3_factor != self.runtime.nh3_factor
            or runtime.samples != self.runtime.samples
        ):
            if self.mics_sensor is not None:
                self.mics_sensor.close()
            self.mics_sensor = self._build_mics_sensor(runtime)
        if runtime.publish_window != self.runtime.publish_window:
            self.measurement_window.publish_window = runtime.publish_window
        self.runtime = runtime

    def _mock_measurements(self) -> dict[str, float]:
        return {
            "oxygen": round(random.uniform(20.4, 21.3), 2),
            "co": round(random.uniform(0.0, 12.0), 2),
            "no2": round(random.uniform(0.0, 0.8), 2),
            "nh3": round(random.uniform(0.0, 8.0), 2),
        }

    def _log_alarm_transition(self) -> None:
        snapshot = self.state.snapshot()
        status = str(snapshot["status"])
        if status != self.last_alarm_state:
            LOGGER.info("system state changed to %s", status)
            if status in (SystemState.ALARM.value, SystemState.SENSOR_ERROR.value):
                LOGGER.error("alarm state %s measurements=%s faults=%s", status, snapshot["measurements"], snapshot["sensor_faults"])
                self._trigger_buzzer(status)
            self.last_alarm_state = status

    @staticmethod
    def _trigger_buzzer(reason: str) -> None:
        LOGGER.warning("buzzer trigger hook: %s", reason)

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

    def _build_display(self, runtime) -> FramebufferDisplay:
        return FramebufferDisplay(
            framebuffer=runtime.framebuffer,
            width=runtime.display_width,
            height=runtime.display_height,
            rotate=runtime.display_rotate,
            config_manager=self.config_manager,
        )

    @staticmethod
    def _build_oxygen_sensor(runtime) -> OxygenSensor | None:
        if runtime.mock_sensors:
            return None
        return OxygenSensor(
            bus_id=runtime.i2c_bus,
            address=runtime.oxygen_address,
            calibration_factor=runtime.oxygen_factor,
            samples=runtime.samples,
        )

    @staticmethod
    def _build_mics_sensor(runtime):
        if runtime.mock_sensors or not runtime.mics_enabled:
            return None
        return Mics6814Sensor(
            bus_id=runtime.i2c_bus,
            address=runtime.mics_address,
            samples=runtime.samples,
            calibration={
                "co": runtime.co_factor,
                "no2": runtime.no2_factor,
                "nh3": runtime.nh3_factor,
            },
        )
