from __future__ import annotations

import math
from dataclasses import dataclass


HOLDING_REGISTER_COUNT = 128

HR_OXYGEN_X10 = 0       # 40001
HR_CO_PPM = 1           # 40002
HR_NO2_X100 = 2         # 40003
HR_NH3_PPM = 3          # 40004
HR_DEVICE_STATUS = 4    # 40005
HR_ALARM_STATUS = 5     # 40006
HR_SYSTEM_STATE = 6     # 40007
HR_ERROR_CODE = 7       # 40008

HR_RESET_ALARMS = 99        # 40100
HR_REBOOT_DEVICE = 100      # 40101
HR_FORCE_CALIBRATION = 101  # 40102

DEVICE_OK = 0
DEVICE_WARNING = 1
DEVICE_ALARM = 2
DEVICE_SENSOR_FAULT = 3

STATE_CODES = {
    "BOOT": 0,
    "WARMUP": 1,
    "NORMAL": 2,
    "WARNING": 3,
    "ALARM": 4,
    "SENSOR_ERROR": 5,
    "FAULT": 5,
}

ERROR_NONE = 0
ERROR_SENSOR_FAILURE = 1
ERROR_WATCHDOG_TIMEOUT = 2
ERROR_INVALID_READING = 3

ALARM_OXYGEN_LOW = 1 << 0
ALARM_OXYGEN_HIGH = 1 << 1
ALARM_CO_HIGH = 1 << 2
ALARM_SENSOR_FAILURE = 1 << 3


@dataclass(frozen=True)
class RegisterSnapshot:
    values: list[int]


def clamp_u16(value: float | int | None) -> int:
    if value is None:
        return 0
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(numeric):
        return 0
    return max(0, min(int(round(numeric)), 0xFFFF))


def build_register_snapshot(snapshot: dict[str, object]) -> RegisterSnapshot:
    measurements = snapshot.get("measurements", {})
    alarms = snapshot.get("alarms", {})
    faults = snapshot.get("sensor_faults", {})
    status = str(snapshot.get("status", "BOOT"))

    if not isinstance(measurements, dict):
        measurements = {}
    if not isinstance(alarms, dict):
        alarms = {}
    if not isinstance(faults, dict):
        faults = {}

    values = [0] * HOLDING_REGISTER_COUNT
    values[HR_OXYGEN_X10] = clamp_u16((measurements.get("oxygen") or 0) * 10)
    values[HR_CO_PPM] = clamp_u16(measurements.get("co"))
    values[HR_NO2_X100] = clamp_u16((measurements.get("no2") or 0) * 100)
    values[HR_NH3_PPM] = clamp_u16(measurements.get("nh3"))
    values[HR_DEVICE_STATUS] = device_status_code(status)
    values[HR_ALARM_STATUS] = alarm_bitmask(alarms, faults)
    values[HR_SYSTEM_STATE] = STATE_CODES.get(status, STATE_CODES["SENSOR_ERROR"])
    values[HR_ERROR_CODE] = error_code(faults)
    return RegisterSnapshot(values=values)


def device_status_code(status: str) -> int:
    if status == "NORMAL":
        return DEVICE_OK
    if status == "WARNING" or status == "WARMUP" or status == "BOOT":
        return DEVICE_WARNING
    if status == "ALARM":
        return DEVICE_ALARM
    return DEVICE_SENSOR_FAULT


def alarm_bitmask(alarms: dict[str, object], faults: dict[str, object]) -> int:
    mask = 0
    if alarms.get("oxygen_low"):
        mask |= ALARM_OXYGEN_LOW
    if alarms.get("oxygen_high"):
        mask |= ALARM_OXYGEN_HIGH
    if alarms.get("co_high"):
        mask |= ALARM_CO_HIGH
    if faults:
        mask |= ALARM_SENSOR_FAILURE
    return mask


def error_code(faults: dict[str, object]) -> int:
    if not faults:
        return ERROR_NONE
    if "watchdog" in faults:
        return ERROR_WATCHDOG_TIMEOUT
    return ERROR_SENSOR_FAILURE
