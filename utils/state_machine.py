from __future__ import annotations

from enum import StrEnum


class SystemState(StrEnum):
    BOOT = "BOOT"
    WARMUP = "WARMUP"
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    ALARM = "ALARM"
    SENSOR_ERROR = "SENSOR_ERROR"

