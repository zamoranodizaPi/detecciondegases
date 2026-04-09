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


DEFAULT_CONFIG = {
    "hardware": {
        "i2c_bus": "1",
        "oxygen_address": "0x73",
        "mics_enabled": "true",
        "mics_path": "/sys/bus/iio/devices/iio:device0",
        "framebuffer": "/dev/fb1",
        "display_width": "480",
        "display_height": "320",
        "display_rotate": "0",
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
    "modbus": {
        "enabled": "true",
        "port": "5020",
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
    },
}

PASSWORD_PREFIX = "pbkdf2_sha256"


def ensure_directory(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def hash_password(password: str, iterations: int = 120_000) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"{PASSWORD_PREFIX}${iterations}${salt}${digest.hex()}"


def verify_password(stored_value: str, provided_password: str) -> bool:
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
    oxygen_address: int
    mics_enabled: bool
    mics_path: str
    framebuffer: str
    display_width: int
    display_height: int
    display_rotate: int
    network_mode: str
    static_ip: str
    gateway: str
    dns: str
    web_port: int
    web_username: str
    web_password: str
    modbus_enabled: bool
    modbus_port: int
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
            if changed:
                self.save()

    def save(self) -> None:
        ensure_directory(self.path)
        with self.path.open("w", encoding="utf-8") as handle:
            self._parser.write(handle)

    def runtime(self) -> RuntimeConfig:
        with self._lock:
            get = self._parser.get
            return RuntimeConfig(
                i2c_bus=self._parser.getint("hardware", "i2c_bus"),
                oxygen_address=int(get("hardware", "oxygen_address"), 0),
                mics_enabled=self._parser.getboolean("hardware", "mics_enabled"),
                mics_path=get("hardware", "mics_path"),
                framebuffer=get("hardware", "framebuffer"),
                display_width=self._parser.getint("hardware", "display_width"),
                display_height=self._parser.getint("hardware", "display_height"),
                display_rotate=self._parser.getint("hardware", "display_rotate"),
                network_mode=get("network", "mode"),
                static_ip=get("network", "static_ip"),
                gateway=get("network", "gateway"),
                dns=get("network", "dns"),
                web_port=self._parser.getint("web", "port"),
                web_username=get("web", "username"),
                web_password=get("web", "password"),
                modbus_enabled=self._parser.getboolean("modbus", "enabled"),
                modbus_port=self._parser.getint("modbus", "port"),
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
