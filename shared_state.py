from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from config import RuntimeConfig


def classify_alarm(measurements: dict[str, float | None], config: RuntimeConfig) -> str:
    oxygen = measurements.get("oxygen")
    co = measurements.get("co")
    if oxygen is None:
        return "FAULT"
    if oxygen < config.oxygen_low or oxygen > config.oxygen_high:
        return "ALARM"
    if co is not None and co > config.co_high:
        return "ALARM"
    return "NORMAL"


@dataclass
class SharedState:
    config: RuntimeConfig
    measurements: dict[str, float | None] = field(
        default_factory=lambda: {"oxygen": None, "co": None, "no2": None, "nh3": None}
    )
    status: str = "BOOT"
    alarms: dict[str, bool] = field(default_factory=dict)
    sensor_faults: dict[str, str] = field(default_factory=dict)
    ip_address: str = "0.0.0.0"
    last_update: str | None = None
    config_mode: bool = False
    require_password_change: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)

    def refresh_config(self, config: RuntimeConfig) -> None:
        with self.lock:
            self.config = config
            self.config_mode = config.first_run
            self.require_password_change = config.first_run

    def update_measurements(self, measurements: dict[str, float | None]) -> None:
        with self.lock:
            self.measurements.update(measurements)
            self.alarms = {
                "oxygen_low": self.measurements["oxygen"] is not None and self.measurements["oxygen"] < self.config.oxygen_low,
                "oxygen_high": self.measurements["oxygen"] is not None and self.measurements["oxygen"] > self.config.oxygen_high,
                "co_high": self.measurements["co"] is not None and self.measurements["co"] > self.config.co_high,
            }
            self.status = "FAULT" if self.sensor_faults else classify_alarm(self.measurements, self.config)
            self.last_update = datetime.utcnow().isoformat() + "Z"

    def set_sensor_fault(self, sensor_name: str, message: str) -> None:
        with self.lock:
            self.sensor_faults[sensor_name] = message
            self.status = "FAULT"
            self.last_update = datetime.utcnow().isoformat() + "Z"

    def clear_sensor_fault(self, sensor_name: str) -> None:
        with self.lock:
            self.sensor_faults.pop(sensor_name, None)
            self.status = "FAULT" if self.sensor_faults else classify_alarm(self.measurements, self.config)

    def set_ip_address(self, ip_address: str) -> None:
        with self.lock:
            self.ip_address = ip_address

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "device_name": self.config.device_name,
                "status": self.status,
                "measurements": dict(self.measurements),
                "alarms": dict(self.alarms),
                "sensor_faults": dict(self.sensor_faults),
                "ip_address": self.ip_address,
                "last_update": self.last_update,
                "first_run": self.config.first_run,
                "config_mode": self.config_mode,
                "require_password_change": self.require_password_change,
            }
