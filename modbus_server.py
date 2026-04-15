from __future__ import annotations

import ipaddress
import logging
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext
try:
    from pymodbus.datastore import ModbusDeviceContext
except ImportError:  # pymodbus < 3.8 compatibility
    from pymodbus.datastore import ModbusSlaveContext as ModbusDeviceContext
from pymodbus.server import StartTcpServer

from config import ConfigManager, RuntimeConfig
from register_map import (
    HOLDING_REGISTER_COUNT,
    HR_FORCE_CALIBRATION,
    HR_REBOOT_DEVICE,
    HR_RESET_ALARMS,
    build_register_snapshot,
)
from shared_state import SharedState


LOGGER = logging.getLogger(__name__)


class IndustrialRegisterBlock(ModbusSequentialDataBlock):
    def __init__(self, control_handler: Callable[[int, int], None], read_only: bool = True) -> None:
        super().__init__(0, [0] * HOLDING_REGISTER_COUNT)
        self._lock = threading.RLock()
        self._control_handler = control_handler
        self._read_only = read_only

    def set_read_only(self, read_only: bool) -> None:
        with self._lock:
            self._read_only = read_only

    def update_process_values(self, values: list[int]) -> None:
        with self._lock:
            for index, value in enumerate(values[:HOLDING_REGISTER_COUNT]):
                self.values[index] = value & 0xFFFF

    def getValues(self, address: int, count: int = 1) -> list[int]:
        with self._lock:
            return list(super().getValues(address, count))

    def setValues(self, address: int, values: list[int]) -> None:
        with self._lock:
            if self._read_only:
                LOGGER.warning("modbus write rejected at address=%s: read-only mode", address)
                return
            for offset, value in enumerate(values):
                register = address + offset
                if register in (HR_RESET_ALARMS, HR_REBOOT_DEVICE, HR_FORCE_CALIBRATION):
                    if int(value) == 1:
                        LOGGER.info("modbus control write accepted register=%s value=%s", register, value)
                        self._control_handler(register, int(value))
                    else:
                        LOGGER.warning("modbus control write rejected register=%s invalid value=%s", register, value)
                    continue
                LOGGER.warning("modbus write rejected at %s: register is not writable", register)


class IndustrialModbusServer:
    def __init__(self, config_manager: ConfigManager, shared_state: SharedState) -> None:
        self.config_manager = config_manager
        self.shared_state = shared_state
        runtime = config_manager.runtime()
        self.register_block = IndustrialRegisterBlock(self._handle_control_write, runtime.modbus_read_only)
        self._context = self._build_context()
        self._stop_event = threading.Event()
        self._updater: threading.Thread | None = None
        self._connection_lock = threading.Lock()
        self._active_clients = 0

    def serve_forever(self) -> None:
        while not self._stop_event.is_set():
            runtime = self.config_manager.runtime()
            self.register_block.set_read_only(runtime.modbus_read_only)
            self._start_updater()
            if runtime.modbus_whitelist:
                LOGGER.info("modbus whitelist configured: %s", runtime.modbus_whitelist)
            try:
                LOGGER.info(
                    "starting modbus tcp server on %s:%s read_only=%s max_clients=%s timeout=%ss",
                    runtime.modbus_host,
                    runtime.modbus_port,
                    runtime.modbus_read_only,
                    runtime.modbus_max_clients,
                    runtime.modbus_timeout,
                )
                StartTcpServer(
                    context=self._context,
                    address=(runtime.modbus_host, runtime.modbus_port),
                    trace_connect=self._trace_connect,
                )
            except TypeError:
                LOGGER.warning("pymodbus trace_connect unsupported; starting without connection tracing")
                self._serve_without_trace(runtime)
            except Exception as exc:
                LOGGER.exception("modbus server failure: %s", exc)
                time.sleep(5.0)

    def stop(self) -> None:
        self._stop_event.set()

    def _serve_without_trace(self, runtime: RuntimeConfig) -> None:
        try:
            StartTcpServer(context=self._context, address=(runtime.modbus_host, runtime.modbus_port))
        except Exception as exc:
            LOGGER.exception("modbus server failure: %s", exc)
            time.sleep(5.0)

    def _build_context(self) -> ModbusServerContext:
        try:
            device_context = ModbusDeviceContext(hr=self.register_block, zero_mode=True)
        except TypeError:
            device_context = ModbusDeviceContext(hr=self.register_block)
        try:
            return ModbusServerContext(devices=device_context, single=True)
        except TypeError:
            return ModbusServerContext(slaves=device_context, single=True)

    def _start_updater(self) -> None:
        if self._updater and self._updater.is_alive():
            return
        self._updater = threading.Thread(target=self._update_loop, name="modbus-update-loop", daemon=True)
        self._updater.start()

    def _update_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                runtime = self.config_manager.runtime()
                self.register_block.set_read_only(runtime.modbus_read_only)
                snapshot = self.shared_state.snapshot()
                registers = build_register_snapshot(snapshot)
                self.register_block.update_process_values(registers.values)
                if runtime.modbus_debug:
                    LOGGER.info("modbus registers 40001-40008: %s", registers.values[:8])
            except Exception as exc:
                LOGGER.exception("modbus register update failed: %s", exc)
            time.sleep(1.0)

    def _handle_control_write(self, register: int, value: int) -> None:
        if value != 1:
            return
        if register == HR_RESET_ALARMS:
            LOGGER.warning("modbus control: reset alarms/faults requested")
            self.shared_state.clear_all_sensor_faults()
        elif register == HR_REBOOT_DEVICE:
            LOGGER.warning("modbus control: reboot requested")
            try:
                subprocess.Popen(["/bin/systemctl", "reboot"])
            except Exception as exc:
                LOGGER.error("modbus reboot request failed: %s", exc)
        elif register == HR_FORCE_CALIBRATION:
            LOGGER.warning("modbus control: force calibration requested")

    def _trace_connect(self, connected: bool, *args: Any, **kwargs: Any) -> None:
        runtime = self.config_manager.runtime()
        with self._connection_lock:
            self._active_clients = max(0, self._active_clients + (1 if connected else -1))
            active_clients = self._active_clients
        event = "connected" if connected else "disconnected"
        LOGGER.info("modbus client %s active_clients=%s", event, active_clients)
        if active_clients > runtime.modbus_max_clients:
            LOGGER.warning(
                "modbus active client count %s exceeds configured max_clients=%s",
                active_clients,
                runtime.modbus_max_clients,
            )
        peer = self._extract_peer(args, kwargs)
        if peer and runtime.modbus_whitelist and not self._is_allowed(peer, runtime.modbus_whitelist):
            LOGGER.warning("modbus client %s is outside configured whitelist", peer)

    @staticmethod
    def _extract_peer(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
        for item in list(args) + list(kwargs.values()):
            if isinstance(item, tuple) and item:
                return str(item[0])
            if isinstance(item, str) and "." in item:
                return item
        return None

    @staticmethod
    def _is_allowed(peer: str, whitelist: str) -> bool:
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:
            return False
        for entry in [item.strip() for item in whitelist.split(",") if item.strip()]:
            try:
                if "/" in entry and address in ipaddress.ip_network(entry, strict=False):
                    return True
                if address == ipaddress.ip_address(entry):
                    return True
            except ValueError:
                LOGGER.warning("invalid modbus whitelist entry: %s", entry)
        return False
