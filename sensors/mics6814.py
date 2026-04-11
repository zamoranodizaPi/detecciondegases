from __future__ import annotations

import logging
import time
from collections import deque
from typing import Optional

from smbus2 import SMBus


LOGGER = logging.getLogger(__name__)


class MovingAverage:
    def __init__(self, samples: int) -> None:
        self.values: deque[float] = deque(maxlen=max(1, samples))

    def add(self, value: float) -> float:
        self.values.append(value)
        return sum(self.values) / len(self.values)


class Mics6814Sensor:
    CHANNELS = {"co": 0, "no2": 2, "nh3": 1}
    DEFAULT_SCALE = {"co": 100.0, "no2": 5.0, "nh3": 20.0}
    CONFIG_REGISTER = 0x01
    CONVERSION_REGISTER = 0x00
    PGA_4_096V = 0x0200
    SINGLE_SHOT = 0x0100
    DATA_RATE_128SPS = 0x0080
    DISABLE_COMPARATOR = 0x0003
    FULL_SCALE_VOLTAGE = 4.096
    CONVERSION_READY = 0x8000

    def __init__(
        self,
        bus_id: int,
        address: int,
        samples: int,
        calibration: dict[str, float],
    ) -> None:
        self.bus_id = bus_id
        self.address = address
        self.calibration = calibration
        self.filters = {name: MovingAverage(samples) for name in self.CHANNELS}
        self._bus: Optional[SMBus] = None

    def read(self) -> dict[str, float]:
        readings: dict[str, float] = {}
        for gas, channel in self.CHANNELS.items():
            voltage = self._read_voltage(channel)
            ppm = self._voltage_to_ppm(gas, voltage)
            filtered = self.filters[gas].add(ppm * self.calibration[gas])
            readings[gas] = round(filtered, 2)
        return readings

    def close(self) -> None:
        if self._bus is not None:
            self._bus.close()
            self._bus = None

    def _read_voltage(self, channel: int) -> float:
        if channel not in range(4):
            raise ValueError(f"ADS1115 channel out of range: {channel}")

        mux = 0x4000 | (channel << 12)
        config = (
            0x8000
            | mux
            | self.PGA_4_096V
            | self.SINGLE_SHOT
            | self.DATA_RATE_128SPS
            | self.DISABLE_COMPARATOR
        )
        bus = self._get_bus()
        bus.write_i2c_block_data(
            self.address,
            self.CONFIG_REGISTER,
            [(config >> 8) & 0xFF, config & 0xFF],
        )
        self._wait_for_conversion()
        data = bus.read_i2c_block_data(self.address, self.CONVERSION_REGISTER, 2)
        raw_value = (data[0] << 8) | data[1]
        if raw_value & 0x8000:
            raw_value -= 0x10000
        return max(0.0, raw_value * self.FULL_SCALE_VOLTAGE / 32768.0)

    def _wait_for_conversion(self) -> None:
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            data = self._get_bus().read_i2c_block_data(self.address, self.CONFIG_REGISTER, 2)
            config = (data[0] << 8) | data[1]
            if config & self.CONVERSION_READY:
                return
            time.sleep(0.002)
        raise TimeoutError("ADS1115 conversion timed out")

    def _get_bus(self) -> SMBus:
        if self._bus is None:
            self._bus = SMBus(self.bus_id)
        return self._bus

    def _voltage_to_ppm(self, gas: str, voltage: float) -> float:
        normalized = max(0.0, min(voltage / self.FULL_SCALE_VOLTAGE, 1.0))
        ppm = normalized * self.DEFAULT_SCALE[gas]
        LOGGER.debug("MICS %s voltage %.3fV -> %.3f ppm", gas, voltage, ppm)
        return ppm
