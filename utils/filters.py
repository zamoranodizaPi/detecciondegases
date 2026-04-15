from __future__ import annotations

from collections import deque


class MovingAverageFilter:
    def __init__(self, samples: int) -> None:
        self.values: deque[float] = deque(maxlen=max(1, samples))

    def add(self, value: float) -> float:
        self.values.append(value)
        return sum(self.values) / len(self.values)

    def clear(self) -> None:
        self.values.clear()
