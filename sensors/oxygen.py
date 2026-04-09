from __future__ import annotations

import logging
import time
from collections import deque
from typing import Optional

from smbus2 import SMBus


LOGGER = logging.getLogger(__name__)
OXYGEN_DATA_REGISTER = 0x03
GET_KEY_REGISTER = 0x0A
DEFAULT_KEY = 20.9 / 120.0


class OxygenSensor:
    def __init__(self, bus_id: int, address: int, calibration_factor: float, samples: int) -> None:
        self.bus_id = bus_id
        self.address = address
        self.calibration_factor = calibration_factor
        self.history: deque[float] = deque(maxlen=max(1, samples))
        self._bus: Optional[SMBus] = None

    def read(self) -> dict[str, float]:
        key = self._read_calibration_key()
        raw = self._read_oxygen_raw()
        value = key * raw * self.calibration_factor
        if value <= 0 or value > 25:
            raise ValueError(f"oxygen reading out of range: {value:.2f}")
        self.history.append(value)
        return {"oxygen": round(sum(self.history) / len(self.history), 2)}

    def close(self) -> None:
        if self._bus is not None:
            self._bus.close()
            self._bus = None

    def _read_oxygen_raw(self) -> float:
        data = self._read_block(OXYGEN_DATA_REGISTER, 3)
        return data[0] + (data[1] / 10.0) + (data[2] / 100.0)

    def _read_calibration_key(self) -> float:
        value = self._read_block(GET_KEY_REGISTER, 1)[0]
        return DEFAULT_KEY if value == 0 else value / 1000.0

    def _read_block(self, register: int, length: int) -> list[int]:
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                return self._get_bus().read_i2c_block_data(self.address, register, length)
            except OSError as exc:
                last_error = exc
                LOGGER.warning("oxygen i2c read failed on attempt %s: %s", attempt + 1, exc)
                self.close()
                time.sleep(0.2)
        raise IOError(f"oxygen sensor read failed: {last_error}")

    def _get_bus(self) -> SMBus:
        if self._bus is None:
            self._bus = SMBus(self.bus_id)
        return self._bus
