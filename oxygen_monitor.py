#!/usr/bin/env python3
"""
Raspberry Pi oxygen monitor for the DFRobot Gravity SEN0322 sensor.

Features:
- Reads oxygen concentration over I2C with smbus2
- Publishes the current value through a Modbus TCP server
- Renders a simple status UI to a framebuffer-backed SPI TFT

The SEN0322 protocol used here is based on DFRobot's published register layout
for the Raspberry Pi / Arduino libraries:
- Oxygen data starts at register 0x03 (3 bytes)
- Calibration key is stored at register 0x0A (1 byte)
"""

from __future__ import annotations

import argparse
import atexit
import inspect
import logging
import os
import signal
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext
try:
    from pymodbus.datastore import ModbusDeviceContext
except ImportError:
    from pymodbus.datastore import ModbusSlaveContext as ModbusDeviceContext
from pymodbus.server import StartTcpServer
from smbus2 import SMBus


LOG_FORMAT = "%(asctime)s [%(levelname)s] %(threadName)s: %(message)s"

DEFAULT_I2C_BUS = 1
DEFAULT_I2C_ADDRESS = 0x73
DEFAULT_MODBUS_HOST = "0.0.0.0"
DEFAULT_MODBUS_PORT = 5020
DEFAULT_MODBUS_REGISTER_ADDRESS = 0
DEFAULT_FB_DEVICE = "/dev/fb1"
DEFAULT_WIDTH = 480
DEFAULT_HEIGHT = 320
SENSOR_POLL_SECONDS = 1.0
DISPLAY_REFRESH_SECONDS = 1.0
I2C_RETRY_COUNT = 3
I2C_RETRY_DELAY_SECONDS = 0.2

OXYGEN_DATA_REGISTER = 0x03
GET_KEY_REGISTER = 0x0A
DEFAULT_KEY = 20.9 / 120.0
MEASUREMENT_CALIBRATION_FACTOR = 0.774
MAX_VALID_OXYGEN_PERCENT = 25.0

STATUS_NORMAL = "NORMAL"
STATUS_LOW = "LOW"
STATUS_HIGH = "HIGH"
STATUS_ERROR = "ERROR"

COLOR_BLACK = (0, 0, 0)
COLOR_WHITE = (255, 255, 255)
COLOR_GREEN = (0, 220, 120)
COLOR_RED = (255, 64, 64)
COLOR_GRAY = (180, 180, 180)


@dataclass
class AppConfig:
    i2c_bus: int = DEFAULT_I2C_BUS
    i2c_address: int = DEFAULT_I2C_ADDRESS
    modbus_host: str = DEFAULT_MODBUS_HOST
    modbus_port: int = DEFAULT_MODBUS_PORT
    modbus_register_address: int = DEFAULT_MODBUS_REGISTER_ADDRESS
    framebuffer: Optional[str] = DEFAULT_FB_DEVICE
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    rotate: int = 0
    samples: int = 10
    log_level: str = "INFO"
    measurement_calibration_factor: float = MEASUREMENT_CALIBRATION_FACTOR
    max_valid_oxygen_percent: float = MAX_VALID_OXYGEN_PERCENT


@dataclass
class SharedState:
    oxygen_percent: Optional[float] = None
    status: str = STATUS_ERROR
    sensor_ok: bool = False
    error_message: str = "Waiting for sensor..."
    updated_at: Optional[datetime] = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def update_success(self, oxygen_percent: float) -> None:
        with self.lock:
            self.oxygen_percent = oxygen_percent
            self.status = classify_status(oxygen_percent)
            self.sensor_ok = True
            self.error_message = ""
            self.updated_at = datetime.now()

    def update_error(self, message: str) -> None:
        with self.lock:
            self.oxygen_percent = None
            self.sensor_ok = False
            self.status = STATUS_ERROR
            self.error_message = message
            self.updated_at = datetime.now()

    def snapshot(self) -> Tuple[Optional[float], str, bool, str, Optional[datetime]]:
        with self.lock:
            return (
                self.oxygen_percent,
                self.status,
                self.sensor_ok,
                self.error_message,
                self.updated_at,
            )


class SEN0322Sensor:
    """Direct SEN0322 reader using the DFRobot I2C register map."""

    def __init__(
        self,
        bus_id: int,
        address: int,
        samples: int,
        measurement_calibration_factor: float,
        max_valid_oxygen_percent: float,
    ) -> None:
        self.bus_id = bus_id
        self.address = address
        self.samples = max(1, min(samples, 100))
        self.measurement_calibration_factor = measurement_calibration_factor
        self.max_valid_oxygen_percent = max_valid_oxygen_percent
        self.history: deque[float] = deque(maxlen=self.samples)
        self._bus_lock = threading.Lock()
        self._bus: Optional[SMBus] = None

    def read_sensor(self) -> float:
        key = self._read_calibration_key()
        raw = self._read_oxygen_raw()
        oxygen = key * raw * self.measurement_calibration_factor
        if oxygen <= 0 or oxygen > self.max_valid_oxygen_percent:
            raise ValueError(f"oxygen reading out of expected range: {oxygen:.2f}%")
        self.history.append(oxygen)
        return sum(self.history) / len(self.history)

    def close(self) -> None:
        with self._bus_lock:
            if self._bus is not None:
                self._bus.close()
                self._bus = None

    def reset_bus(self) -> None:
        self.close()

    def _read_oxygen_raw(self) -> float:
        raw_bytes = self._read_i2c_block(OXYGEN_DATA_REGISTER, 3)
        if len(raw_bytes) != 3:
            raise IOError(f"expected 3 oxygen bytes, got {len(raw_bytes)}")

        raw_value = raw_bytes[0] + (raw_bytes[1] / 10.0) + (raw_bytes[2] / 100.0)
        if raw_value <= 0 or raw_value > 300:
            raise ValueError(f"invalid oxygen raw value: {raw_bytes!r}")
        return raw_value

    def _read_calibration_key(self) -> float:
        key_byte = self._read_i2c_block(GET_KEY_REGISTER, 1)[0]
        if key_byte == 0:
            return DEFAULT_KEY
        return key_byte / 1000.0

    def _read_i2c_block(self, register: int, length: int) -> list[int]:
        last_error: Optional[Exception] = None
        for attempt in range(1, I2C_RETRY_COUNT + 1):
            try:
                with self._bus_lock:
                    return self._get_bus().read_i2c_block_data(self.address, register, length)
            except OSError as exc:
                last_error = exc
                self.reset_bus()
                if attempt < I2C_RETRY_COUNT:
                    logging.warning(
                        "i2c read failed on register 0x%02X (attempt %d/%d): %s",
                        register,
                        attempt,
                        I2C_RETRY_COUNT,
                        exc,
                    )
                    time.sleep(I2C_RETRY_DELAY_SECONDS)
        if last_error is None:
            raise IOError(f"failed to read register 0x{register:02X}")
        raise IOError(f"failed to read register 0x{register:02X} after {I2C_RETRY_COUNT} attempts: {last_error}")

    def _get_bus(self) -> SMBus:
        if self._bus is None:
            self._bus = SMBus(self.bus_id)
        return self._bus


class ModbusRegisterStore:
    """Thread-safe wrapper around a single holding register."""

    def __init__(self, register_address: int = DEFAULT_MODBUS_REGISTER_ADDRESS) -> None:
        self._lock = threading.Lock()
        self.register_address = max(0, register_address)
        self.slave_context = self._create_device_context()
        self.server_context = self._create_server_context()

    def set_oxygen_register(self, oxygen_percent: Optional[float]) -> None:
        scaled_value = 0 if oxygen_percent is None else max(0, int(round(oxygen_percent * 10)))
        with self._lock:
            self.slave_context.setValues(3, self.register_address, [scaled_value])

    def _create_device_context(self):
        block_size = max(10, self.register_address + 1)
        kwargs = {"hr": ModbusSequentialDataBlock(0, [0] * block_size)}
        if "zero_mode" in inspect.signature(ModbusDeviceContext.__init__).parameters:
            kwargs["zero_mode"] = True
        try:
            return ModbusDeviceContext(**kwargs)
        except TypeError as exc:
            if "zero_mode" not in kwargs or "zero_mode" not in str(exc):
                raise
            kwargs.pop("zero_mode", None)
            return ModbusDeviceContext(**kwargs)

    def _create_server_context(self):
        kwargs = {"single": True}
        parameters = inspect.signature(ModbusServerContext.__init__).parameters
        if "slaves" in parameters:
            kwargs["slaves"] = self.slave_context
        else:
            kwargs["devices"] = self.slave_context
        return ModbusServerContext(**kwargs)


class FramebufferDisplay:
    """Minimal framebuffer writer for 16-bit RGB565 TFT displays."""

    def __init__(self, device: str, width: int, height: int, rotate: int = 0) -> None:
        self.device = device
        self.width = width
        self.height = height
        self.rotate = rotate % 360
        self._fonts = self._load_fonts()
        self._fb = None
        self._fb_lock = threading.Lock()

    def _load_fonts(self) -> dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont]:
        font_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]
        fonts: dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

        def load(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
            for path in font_candidates:
                if os.path.exists(path):
                    return ImageFont.truetype(path, size)
            return ImageFont.load_default()

        fonts["title"] = load(28)
        fonts["value"] = load(92)
        fonts["status"] = load(42)
        fonts["small"] = load(20)
        return fonts

    def update_display(
        self,
        oxygen_percent: Optional[float],
        status: str,
        sensor_ok: bool,
        error_message: str,
        updated_at: Optional[datetime],
    ) -> None:
        image = Image.new("RGB", (self.width, self.height), COLOR_BLACK)
        draw = ImageDraw.Draw(image)

        draw.text((20, 18), "Oxygen Monitor", font=self._fonts["title"], fill=COLOR_WHITE)

        value_text = "--.- %" if oxygen_percent is None else f"{oxygen_percent:0.1f} %"
        value_color = COLOR_GREEN if sensor_ok and status == STATUS_NORMAL else COLOR_RED
        draw.text((20, 78), value_text, font=self._fonts["value"], fill=value_color)

        status_text = f"Status: {status}"
        draw.text((20, 205), status_text, font=self._fonts["status"], fill=value_color)

        if sensor_ok:
            draw.text((20, 262), "Sensor: OK", font=self._fonts["small"], fill=COLOR_GREEN)
        else:
            draw.text((20, 262), f"Sensor: {error_message[:34]}", font=self._fonts["small"], fill=COLOR_RED)

        timestamp = "--"
        if updated_at is not None:
            timestamp = updated_at.strftime("%Y-%m-%d %H:%M:%S")
        draw.text((20, 290), f"Updated: {timestamp}", font=self._fonts["small"], fill=COLOR_GRAY)

        if self.rotate:
            image = image.rotate(self.rotate, expand=True)
            image = image.resize((self.width, self.height))

        self._write_framebuffer(image)

    def _write_framebuffer(self, image: Image.Image) -> None:
        image = image.convert("RGB")
        packed = bytearray()
        for red, green, blue in image.getdata():
            rgb565 = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
            packed.extend(struct.pack("<H", rgb565))

        with self._fb_lock:
            fb = self._ensure_framebuffer()
            fb.seek(0)
            fb.write(packed)

    def close(self) -> None:
        with self._fb_lock:
            if self._fb is not None:
                self._fb.close()
                self._fb = None

    def _ensure_framebuffer(self):
        if self._fb is None:
            self._fb = open(self.device, "r+b", buffering=0)
        return self._fb


def classify_status(oxygen_percent: float) -> str:
    if oxygen_percent < 19.5:
        return STATUS_LOW
    if oxygen_percent > 23.5:
        return STATUS_HIGH
    return STATUS_NORMAL


def sensor_loop(
    stop_event: threading.Event,
    state: SharedState,
    sensor: SEN0322Sensor,
    registers: ModbusRegisterStore,
) -> None:
    while not stop_event.is_set():
        start = time.monotonic()
        try:
            oxygen_percent = sensor.read_sensor()
            state.update_success(oxygen_percent)
            registers.set_oxygen_register(oxygen_percent)
            logging.info("oxygen=%.2f%% status=%s", oxygen_percent, classify_status(oxygen_percent))
        except Exception as exc:
            message = str(exc)
            sensor.reset_bus()
            state.update_error(message)
            registers.set_oxygen_register(None)
            logging.exception("sensor read failed")

        sleep_remaining(start, SENSOR_POLL_SECONDS, stop_event)


def display_loop(
    stop_event: threading.Event,
    state: SharedState,
    display: FramebufferDisplay,
) -> None:
    while not stop_event.is_set():
        start = time.monotonic()
        oxygen_percent, status, sensor_ok, error_message, updated_at = state.snapshot()
        try:
            display.update_display(oxygen_percent, status, sensor_ok, error_message, updated_at)
        except Exception:
            logging.exception("display update failed")
        sleep_remaining(start, DISPLAY_REFRESH_SECONDS, stop_event)


def run_modbus(stop_event: threading.Event, registers: ModbusRegisterStore, host: str, port: int) -> None:
    logging.info("starting Modbus TCP server on %s:%d", host, port)
    try:
        StartTcpServer(context=registers.server_context, address=(host, port))
    except Exception:
        if not stop_event.is_set():
            logging.exception("modbus server stopped unexpectedly")


def sleep_remaining(start_time: float, interval: float, stop_event: threading.Event) -> None:
    elapsed = time.monotonic() - start_time
    remaining = max(0.0, interval - elapsed)
    stop_event.wait(remaining)


def parse_framebuffer(value: str) -> Optional[str]:
    normalized = value.strip().lower()
    if normalized in {"", "none", "off", "disabled", "disable", "no"}:
        return None
    return value


def env_or_default(name: str, default):
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def parse_int_env(name: str, default: int, base: int = 10) -> int:
    value = env_or_default(name, None)
    if value is None:
        return default
    return int(value, base)


def parse_float_env(name: str, default: float) -> float:
    value = env_or_default(name, None)
    if value is None:
        return default
    return float(value)


def parse_framebuffer_env(name: str, default: Optional[str]) -> Optional[str]:
    value = env_or_default(name, None)
    if value is None:
        return default
    return parse_framebuffer(value)


def parse_args() -> AppConfig:
    parser = argparse.ArgumentParser(description="Raspberry Pi oxygen monitor")
    parser.add_argument("--i2c-bus", type=int, default=parse_int_env("I2C_BUS", DEFAULT_I2C_BUS))
    parser.add_argument(
        "--i2c-address",
        type=lambda x: int(x, 0),
        default=parse_int_env("I2C_ADDRESS", DEFAULT_I2C_ADDRESS, 0),
    )
    parser.add_argument("--modbus-host", default=env_or_default("MODBUS_HOST", DEFAULT_MODBUS_HOST))
    parser.add_argument("--modbus-port", type=int, default=parse_int_env("MODBUS_PORT", DEFAULT_MODBUS_PORT))
    parser.add_argument(
        "--modbus-register-address",
        type=int,
        default=parse_int_env("MODBUS_REGISTER_ADDRESS", DEFAULT_MODBUS_REGISTER_ADDRESS),
    )
    parser.add_argument("--framebuffer", type=parse_framebuffer, default=parse_framebuffer_env("FRAMEBUFFER", DEFAULT_FB_DEVICE))
    parser.add_argument("--width", type=int, default=parse_int_env("WIDTH", DEFAULT_WIDTH))
    parser.add_argument("--height", type=int, default=parse_int_env("HEIGHT", DEFAULT_HEIGHT))
    parser.add_argument("--rotate", type=int, default=parse_int_env("ROTATE", 0), choices=[0, 90, 180, 270])
    parser.add_argument("--samples", type=int, default=parse_int_env("SAMPLES", 10))
    parser.add_argument("--log-level", default=env_or_default("LOG_LEVEL", "INFO"), choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument(
        "--measurement-calibration-factor",
        type=float,
        default=parse_float_env("MEASUREMENT_CALIBRATION_FACTOR", MEASUREMENT_CALIBRATION_FACTOR),
    )
    parser.add_argument(
        "--max-valid-oxygen-percent",
        type=float,
        default=parse_float_env("MAX_VALID_OXYGEN_PERCENT", MAX_VALID_OXYGEN_PERCENT),
    )
    args = parser.parse_args()
    return AppConfig(
        i2c_bus=args.i2c_bus,
        i2c_address=args.i2c_address,
        modbus_host=args.modbus_host,
        modbus_port=args.modbus_port,
        modbus_register_address=args.modbus_register_address,
        framebuffer=args.framebuffer,
        width=args.width,
        height=args.height,
        rotate=args.rotate,
        samples=args.samples,
        log_level=args.log_level,
        measurement_calibration_factor=args.measurement_calibration_factor,
        max_valid_oxygen_percent=args.max_valid_oxygen_percent,
    )


def main() -> int:
    config = parse_args()
    logging.basicConfig(level=getattr(logging, config.log_level), format=LOG_FORMAT)

    stop_event = threading.Event()
    state = SharedState()
    sensor = SEN0322Sensor(
        config.i2c_bus,
        config.i2c_address,
        config.samples,
        config.measurement_calibration_factor,
        config.max_valid_oxygen_percent,
    )
    registers = ModbusRegisterStore(config.modbus_register_address)
    display = None
    if config.framebuffer:
        display = FramebufferDisplay(config.framebuffer, config.width, config.height, config.rotate)
    else:
        logging.info("framebuffer disabled; display thread will not start")
    atexit.register(sensor.close)
    if display is not None:
        atexit.register(display.close)

    def handle_signal(signum: int, _frame: object) -> None:
        logging.info("received signal %s, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    threads = [
        threading.Thread(
            target=sensor_loop,
            name="sensor-thread",
            args=(stop_event, state, sensor, registers),
            daemon=True,
        ),
        threading.Thread(
            target=run_modbus,
            name="modbus-thread",
            args=(stop_event, registers, config.modbus_host, config.modbus_port),
            daemon=True,
        ),
    ]

    if display is not None:
        threads.append(
            threading.Thread(
                target=display_loop,
                name="display-thread",
                args=(stop_event, state, display),
                daemon=True,
            )
        )

    for thread in threads:
        thread.start()

    try:
        while not stop_event.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop_event.set()

    for thread in threads:
        if thread.name != "modbus-thread":
            thread.join(timeout=2.0)

    sensor.close()
    if display is not None:
        display.close()
    logging.info("oxygen monitor stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
