from __future__ import annotations

import logging
from collections import deque
from pathlib import Path


LOGGER = logging.getLogger(__name__)


class MovingAverage:
    def __init__(self, samples: int) -> None:
        self.values: deque[float] = deque(maxlen=max(1, samples))

    def add(self, value: float) -> float:
        self.values.append(value)
        return sum(self.values) / len(self.values)


class Mics6814Sensor:
    CHANNELS = {"co": 0, "no2": 1, "nh3": 2}
    DEFAULT_SCALE = {"co": 100.0, "no2": 5.0, "nh3": 20.0}

    def __init__(
        self,
        device_path: str,
        samples: int,
        calibration: dict[str, float],
    ) -> None:
        self.device_path = Path(device_path)
        self.calibration = calibration
        self.filters = {name: MovingAverage(samples) for name in self.CHANNELS}

    def read(self) -> dict[str, float]:
        if not self.device_path.exists():
            raise FileNotFoundError(f"MICS device path not found: {self.device_path}")

        readings: dict[str, float] = {}
        for gas, channel in self.CHANNELS.items():
            voltage = self._read_voltage(channel)
            ppm = self._voltage_to_ppm(gas, voltage)
            filtered = self.filters[gas].add(ppm * self.calibration[gas])
            readings[gas] = round(filtered, 2)
        return readings

    def _read_voltage(self, channel: int) -> float:
        raw_path = self.device_path / f"in_voltage{channel}_raw"
        scale_path = self.device_path / f"in_voltage{channel}_scale"
        raw_value = float(raw_path.read_text(encoding="utf-8").strip())
        if scale_path.exists():
            scale = float(scale_path.read_text(encoding="utf-8").strip())
            return raw_value * scale / 1000.0
        return (raw_value / 1023.0) * 3.3

    def _voltage_to_ppm(self, gas: str, voltage: float) -> float:
        normalized = max(0.0, min(voltage / 3.3, 1.0))
        ppm = normalized * self.DEFAULT_SCALE[gas]
        LOGGER.debug("MICS %s voltage %.3fV -> %.3f ppm", gas, voltage, ppm)
        return ppm
