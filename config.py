from __future__ import annotations

import configparser
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

try:
    import bcrypt
except ImportError:  # pragma: no cover - allows service to start before dependency refresh
    bcrypt = None


DEFAULT_CONFIG = {
    "hardware": {
        "i2c_bus": "1",
        "mock_sensors": "true",
        "oxygen_address": "0x73",
        "mics_enabled": "true",
        "mics_address": "0x48",
        "framebuffer": "/dev/fb1",
        "display_width": "320",
        "display_height": "480",
        "display_rotate": "0",
        "touch_rotation": "90",
        "touch_swap_xy": "true",
        "touch_invert_x": "false",
        "touch_invert_y": "true",
        "touch_debug": "false",
        "touch_calibration": "",
        "touch_calibrate_on_start": "true",
        "touch_force_calibration": "false",
    },
    "network": {
        "mode": "dhcp",
        "static_ip": "192.168.1.100",
        "gateway": "192.168.1.1",
        "dns": "8.8.8.8",
    },
    "web": {
        "port": "8080",
        "username": "admin",
        "password": "admin",
    },
    "display": {
        "brightness": "100",
        "theme": "dark",
        "inactivity_timeout": "60",
    },
    "modbus": {
        "enabled": "true",
        "host": "0.0.0.0",
        "port": "5020",
        "max_clients": "5",
        "timeout": "10",
        "read_only": "true",
        "whitelist": "",
        "debug": "false",
    },
    "sampling": {
        "samples": "10",
        "interval": "1",
        "publish_window": "5",
        "oxygen_max_jump": "1.5",
        "co_max_jump": "30",
        "no2_max_jump": "1.0",
        "nh3_max_jump": "8.0",
    },
    "calibration": {
        "oxygen_factor": "0.75",
        "co_factor": "1.0",
        "no2_factor": "1.0",
        "nh3_factor": "1.0",
    },
    "alarms": {
        "oxygen_low": "19.5",
        "oxygen_high": "23.5",
        "co_high": "50",
    },
    "system": {
        "first_run": "true",
        "device_name": "GasMonitor",
        "log_file": "logs/system.log",
        "watchdog_enabled": "true",
        "log_retention_days": "7",
    },
}

PASSWORD_PREFIX = "pbkdf2_sha256"
BCRYPT_PREFIX = "bcrypt"


def ensure_directory(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def hash_password(password: str, iterations: int = 120_000) -> str:
    if bcrypt is not None:
        digest = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
        return f"{BCRYPT_PREFIX}${digest}"
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"{PASSWORD_PREFIX}${iterations}${salt}${digest.hex()}"


def verify_password(stored_value: str, provided_password: str) -> bool:
    if stored_value.startswith(f"{BCRYPT_PREFIX}$"):
        if bcrypt is None:
            return False
        _, digest = stored_value.split("$", 1)
        return bcrypt.checkpw(provided_password.encode("utf-8"), digest.encode("utf-8"))
    if not stored_value.startswith(f"{PASSWORD_PREFIX}$"):
        return hmac.compare_digest(stored_value, provided_password)

    _, iterations, salt, expected_digest = stored_value.split("$", 3)
    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        provided_password.encode("utf-8"),
        salt.encode("utf-8"),
        int(iterations),
    ).hex()
    return hmac.compare_digest(candidate, expected_digest)


@dataclass(frozen=True)
class RuntimeConfig:
    i2c_bus: int
    mock_sensors: bool
    oxygen_address: int
    mics_enabled: bool
    mics_address: int
    framebuffer: str
    display_width: int
    display_height: int
    display_rotate: int
    touch_rotation: int
    touch_swap_xy: bool
    touch_invert_x: bool
    touch_invert_y: bool
    touch_debug: bool
    touch_calibration: str
    touch_calibrate_on_start: bool
    touch_force_calibration: bool
    network_mode: str
    static_ip: str
    gateway: str
    dns: str
    web_port: int
    web_username: str
    web_password: str
    display_brightness: int
    display_theme: str
    display_inactivity_timeout: int
    modbus_enabled: bool
    modbus_host: str
    modbus_port: int
    modbus_max_clients: int
    modbus_timeout: int
    modbus_read_only: bool
    modbus_whitelist: str
    modbus_debug: bool
    samples: int
    interval: float
    publish_window: float
    oxygen_max_jump: float
    co_max_jump: float
    no2_max_jump: float
    nh3_max_jump: float
    oxygen_factor: float
    co_factor: float
    no2_factor: float
    nh3_factor: float
    oxygen_low: float
    oxygen_high: float
    co_high: float
    first_run: bool
    device_name: str
    log_file: str
    watchdog_enabled: bool
    log_retention_days: int


class ConfigManager:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self._parser = configparser.ConfigParser()
        self.load_or_create()

    def load_or_create(self) -> None:
        with self._lock:
            if not self.path.exists():
                self._parser.read_dict(DEFAULT_CONFIG)
                self.save()
                return

            self._parser.read_dict(DEFAULT_CONFIG)
            self._parser.read(self.path)
            changed = False
            for section, values in DEFAULT_CONFIG.items():
                if not self._parser.has_section(section):
                    self._parser.add_section(section)
                    changed = True
                for key, value in values.items():
                    if not self._parser.has_option(section, key):
                        self._parser.set(section, key, value)
                        changed = True
            if self._validate_and_repair():
                changed = True
            if changed:
                self.save()

    def _validate_and_repair(self) -> bool:
        changed = False

        def repair(section: str, key: str, value: object) -> None:
            nonlocal changed
            self._parser.set(section, key, str(value))
            changed = True

        def bounded_int(section: str, key: str, default: int, minimum: int, maximum: int) -> None:
            try:
                value = self._parser.getint(section, key)
            except ValueError:
                repair(section, key, default)
                return
            if value < minimum or value > maximum:
                repair(section, key, min(max(value, minimum), maximum))

        def bounded_float(section: str, key: str, default: float, minimum: float, maximum: float) -> None:
            try:
                value = self._parser.getfloat(section, key)
            except ValueError:
                repair(section, key, default)
                return
            if value < minimum or value > maximum:
                repair(section, key, min(max(value, minimum), maximum))

        for section, key in (
            ("hardware", "mock_sensors"),
            ("hardware", "mics_enabled"),
            ("hardware", "touch_swap_xy"),
            ("hardware", "touch_invert_x"),
            ("hardware", "touch_invert_y"),
            ("hardware", "touch_debug"),
            ("modbus", "enabled"),
            ("modbus", "read_only"),
            ("modbus", "debug"),
            ("system", "first_run"),
            ("system", "watchdog_enabled"),
        ):
            try:
                self._parser.getboolean(section, key)
            except ValueError:
                repair(section, key, DEFAULT_CONFIG[section][key])

        bounded_int("hardware", "i2c_bus", 1, 0, 10)
        bounded_int("hardware", "display_width", 320, 160, 1920)
        bounded_int("hardware", "display_height", 480, 160, 1920)
        bounded_int("hardware", "display_rotate", 0, 0, 270)
        bounded_int("hardware", "touch_rotation", 90, 0, 270)
        if self._parser.getint("hardware", "touch_rotation") not in (0, 90, 180, 270):
            repair("hardware", "touch_rotation", 90)
        if self._parser.getint("hardware", "display_width") == 480 and self._parser.getint("hardware", "display_height") == 320:
            repair("hardware", "display_width", 320)
            repair("hardware", "display_height", 480)
        bounded_int("web", "port", 8080, 1, 65535)
        bounded_int("modbus", "port", 5020, 1, 65535)
        bounded_int("modbus", "max_clients", 5, 1, 64)
        bounded_int("modbus", "timeout", 10, 1, 3600)
        bounded_int("sampling", "samples", 10, 1, 120)
        bounded_int("display", "brightness", 100, 1, 100)
        bounded_int("display", "inactivity_timeout", 60, 10, 600)
        bounded_int("system", "log_retention_days", 7, 1, 365)

        bounded_float("sampling", "interval", 1.0, 0.2, 60.0)
        bounded_float("sampling", "publish_window", 5.0, 1.0, 300.0)
        bounded_float("alarms", "oxygen_low", 19.5, 0.0, 100.0)
        bounded_float("alarms", "oxygen_high", 23.5, 0.0, 100.0)
        bounded_float("alarms", "co_high", 50.0, 0.0, 10000.0)

        if self._parser.getfloat("alarms", "oxygen_low") >= self._parser.getfloat("alarms", "oxygen_high"):
            repair("alarms", "oxygen_low", DEFAULT_CONFIG["alarms"]["oxygen_low"])
            repair("alarms", "oxygen_high", DEFAULT_CONFIG["alarms"]["oxygen_high"])

        if self._parser.get("display", "theme").lower() != "dark":
            repair("display", "theme", "dark")
        return changed

    def save(self) -> None:
        ensure_directory(self.path)
        temp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            self._parser.write(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, self.path)

    def runtime(self) -> RuntimeConfig:
        with self._lock:
            get = self._parser.get
            return RuntimeConfig(
                i2c_bus=self._parser.getint("hardware", "i2c_bus"),
                mock_sensors=self._parser.getboolean("hardware", "mock_sensors"),
                oxygen_address=int(get("hardware", "oxygen_address"), 0),
                mics_enabled=self._parser.getboolean("hardware", "mics_enabled"),
                mics_address=int(get("hardware", "mics_address"), 0),
                framebuffer=get("hardware", "framebuffer"),
                display_width=self._parser.getint("hardware", "display_width"),
                display_height=self._parser.getint("hardware", "display_height"),
                display_rotate=self._parser.getint("hardware", "display_rotate"),
                touch_rotation=self._parser.getint("hardware", "touch_rotation"),
                touch_swap_xy=self._parser.getboolean("hardware", "touch_swap_xy"),
                touch_invert_x=self._parser.getboolean("hardware", "touch_invert_x"),
                touch_invert_y=self._parser.getboolean("hardware", "touch_invert_y"),
                touch_debug=self._parser.getboolean("hardware", "touch_debug"),
                touch_calibration=get("hardware", "touch_calibration"),
                touch_calibrate_on_start=self._parser.getboolean("hardware", "touch_calibrate_on_start"),
                touch_force_calibration=self._parser.getboolean("hardware", "touch_force_calibration"),
                network_mode=get("network", "mode"),
                static_ip=get("network", "static_ip"),
                gateway=get("network", "gateway"),
                dns=get("network", "dns"),
                web_port=self._parser.getint("web", "port"),
                web_username=get("web", "username"),
                web_password=get("web", "password"),
                display_brightness=self._parser.getint("display", "brightness"),
                display_theme=get("display", "theme"),
                display_inactivity_timeout=self._parser.getint("display", "inactivity_timeout"),
                modbus_enabled=self._parser.getboolean("modbus", "enabled"),
                modbus_host=get("modbus", "host"),
                modbus_port=self._parser.getint("modbus", "port"),
                modbus_max_clients=self._parser.getint("modbus", "max_clients"),
                modbus_timeout=self._parser.getint("modbus", "timeout"),
                modbus_read_only=self._parser.getboolean("modbus", "read_only"),
                modbus_whitelist=get("modbus", "whitelist"),
                modbus_debug=self._parser.getboolean("modbus", "debug"),
                samples=max(1, self._parser.getint("sampling", "samples")),
                interval=max(0.2, self._parser.getfloat("sampling", "interval")),
                publish_window=max(1.0, self._parser.getfloat("sampling", "publish_window")),
                oxygen_max_jump=max(0.1, self._parser.getfloat("sampling", "oxygen_max_jump")),
                co_max_jump=max(1.0, self._parser.getfloat("sampling", "co_max_jump")),
                no2_max_jump=max(0.1, self._parser.getfloat("sampling", "no2_max_jump")),
                nh3_max_jump=max(0.1, self._parser.getfloat("sampling", "nh3_max_jump")),
                oxygen_factor=self._parser.getfloat("calibration", "oxygen_factor"),
                co_factor=self._parser.getfloat("calibration", "co_factor"),
                no2_factor=self._parser.getfloat("calibration", "no2_factor"),
                nh3_factor=self._parser.getfloat("calibration", "nh3_factor"),
                oxygen_low=self._parser.getfloat("alarms", "oxygen_low"),
                oxygen_high=self._parser.getfloat("alarms", "oxygen_high"),
                co_high=self._parser.getfloat("alarms", "co_high"),
                first_run=self._parser.getboolean("system", "first_run"),
                device_name=get("system", "device_name"),
                log_file=get("system", "log_file"),
                watchdog_enabled=self._parser.getboolean("system", "watchdog_enabled"),
                log_retention_days=self._parser.getint("system", "log_retention_days"),
            )

    def to_dict(self, include_secrets: bool = False) -> dict[str, dict[str, Any]]:
        with self._lock:
            data = {section: dict(self._parser.items(section)) for section in self._parser.sections()}
        if not include_secrets:
            data["web"]["password"] = ""
            data["web"]["password_set"] = "true"
        return data

    def update(self, payload: dict[str, dict[str, Any]]) -> RuntimeConfig:
        with self._lock:
            for section, values in payload.items():
                if not isinstance(values, dict):
                    continue
                if not self._parser.has_section(section):
                    self._parser.add_section(section)
                for key, value in values.items():
                    if value is None:
                        continue
                    if section == "web" and key == "password":
                        text = str(value).strip()
                        if text:
                            self._parser.set(section, key, hash_password(text))
                        continue
                    self._parser.set(section, key, str(value))
            self._validate_and_repair()
            self.save()
            return self.runtime()

    def set_first_run(self, first_run: bool) -> RuntimeConfig:
        with self._lock:
            self._parser.set("system", "first_run", "true" if first_run else "false")
            self.save()
            return self.runtime()

    def authenticate(self, username: str, password: str) -> bool:
        runtime = self.runtime()
        return username == runtime.web_username and verify_password(runtime.web_password, password)

    def apply_network_profile(self) -> None:
        runtime = self.runtime()
        if runtime.network_mode.lower() != "static":
            return
        dhcpcd_conf = Path("/etc/dhcpcd.conf")
        if not os.access(dhcpcd_conf, os.W_OK):
            return
        managed_block = (
            "\n# gasmonitor-managed\n"
            "interface eth0\n"
            f"static ip_address={runtime.static_ip}/24\n"
            f"static routers={runtime.gateway}\n"
            f"static domain_name_servers={runtime.dns}\n"
        )
        text = dhcpcd_conf.read_text(encoding="utf-8")
        if "# gasmonitor-managed" in text:
            base = text.split("# gasmonitor-managed", 1)[0].rstrip() + "\n"
        else:
            base = text.rstrip() + "\n"
        dhcpcd_conf.write_text(base + managed_block, encoding="utf-8")
