from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont

try:
    from evdev import InputDevice, ecodes, list_devices
except ImportError:  # pragma: no cover - optional Raspberry Pi touchscreen dependency
    InputDevice = None
    ecodes = None
    list_devices = None

from config import ConfigManager


LOGGER = logging.getLogger(__name__)

Color = tuple[int, int, int]


GREEN = (22, 155, 92)
YELLOW = (214, 183, 41)
ORANGE = (223, 113, 30)
RED = (202, 48, 49)
INK = (245, 248, 250)
MUTED = (169, 181, 194)
PANEL = (20, 27, 34)
BLACK = (7, 10, 13)


@dataclass(frozen=True)
class ConfigField:
    section: str
    key: str
    label: str
    kind: str = "text"
    choices: tuple[str, ...] = ()


@dataclass
class Button:
    rect: tuple[int, int, int, int]
    label: str
    action: Callable[[], None]


class TouchInput:
    def __init__(
        self,
        width: int,
        height: int,
        fb_width: int,
        fb_height: int,
        transform: str,
        swap_xy: bool = False,
        invert_x: bool = False,
        invert_y: bool = False,
    ) -> None:
        self.width = width
        self.height = height
        self.fb_width = fb_width
        self.fb_height = fb_height
        self.transform = transform
        self.swap_xy = swap_xy
        self.invert_x = invert_x
        self.invert_y = invert_y
        self.device = self._open_device()
        self.x: int | None = None
        self.y: int | None = None
        self.touching = False
        self.max_x = 4095
        self.max_y = 4095
        if self.device is not None:
            self._load_abs_ranges()

    def read_tap(self) -> tuple[int, int] | None:
        if self.device is None or ecodes is None:
            return None
        tap: tuple[int, int] | None = None
        try:
            for event in self.device.read():
                if event.type == ecodes.EV_ABS:
                    if event.code in (ecodes.ABS_X, ecodes.ABS_MT_POSITION_X):
                        self.x = event.value
                    elif event.code in (ecodes.ABS_Y, ecodes.ABS_MT_POSITION_Y):
                        self.y = event.value
                elif event.type == ecodes.EV_KEY and event.code in (ecodes.BTN_TOUCH, ecodes.BTN_LEFT):
                    if event.value:
                        self.touching = True
                    elif self.touching and self.x is not None and self.y is not None:
                        self.touching = False
                        tap = self._scale(self.x, self.y)
        except BlockingIOError:
            return tap
        except OSError as exc:
            LOGGER.warning("touch read failed: %s", exc)
            self.device = None
        return tap

    def _scale(self, raw_x: int, raw_y: int) -> tuple[int, int]:
        x = int(max(0, min(raw_x, self.max_x)) * (self.fb_width - 1) / max(1, self.max_x))
        y = int(max(0, min(raw_y, self.max_y)) * (self.fb_height - 1) / max(1, self.max_y))
        original_x, original_y = x, y
        if self.swap_xy:
            x, y = y, x
        if self.invert_x:
            x = self.fb_width - 1 - x
        if self.invert_y:
            y = self.fb_height - 1 - y
        if self.transform == "rotate90":
            mapped = (
                int((self.fb_height - 1 - y) * (self.width - 1) / max(1, self.fb_height - 1)),
                int(x * (self.height - 1) / max(1, self.fb_width - 1)),
            )
        elif (self.fb_width, self.fb_height) != (self.width, self.height):
            mapped = (
                int(x * (self.width - 1) / max(1, self.fb_width - 1)),
                int(y * (self.height - 1) / max(1, self.fb_height - 1)),
            )
        else:
            mapped = (x, y)
        LOGGER.info("touch raw=%s,%s fb=%s,%s adjusted=%s,%s mapped=%s,%s", raw_x, raw_y, original_x, original_y, x, y, mapped[0], mapped[1])
        return mapped

    def _load_abs_ranges(self) -> None:
        if self.device is None or ecodes is None:
            return
        caps = self.device.capabilities(absinfo=True)
        for code, info in caps.get(ecodes.EV_ABS, []):
            if code in (ecodes.ABS_X, ecodes.ABS_MT_POSITION_X):
                self.max_x = max(1, info.max)
            elif code in (ecodes.ABS_Y, ecodes.ABS_MT_POSITION_Y):
                self.max_y = max(1, info.max)

    @staticmethod
    def _open_device() -> InputDevice | None:
        if InputDevice is None or list_devices is None:
            LOGGER.info("evdev not installed; touchscreen menus disabled")
            return None
        for path in list_devices():
            try:
                device = InputDevice(path)
                name = device.name.lower()
                if any(token in name for token in ("touch", "ads7846", "xpt2046", "stmpe")):
                    device.grab()
                    os.set_blocking(device.fd, False)
                    LOGGER.info("using touchscreen input %s (%s)", device.path, device.name)
                    return device
            except OSError:
                continue
        LOGGER.info("touchscreen input device not found; menus disabled")
        return None


class FramebufferDisplay:
    FIELDS: tuple[ConfigField, ...] = (
        ConfigField("system", "device_name", "Device name"),
        ConfigField("web", "port", "Web port", "number"),
        ConfigField("web", "username", "Web user"),
        ConfigField("web", "password", "New password"),
        ConfigField("network", "mode", "Network mode", "choice", ("dhcp", "static")),
        ConfigField("network", "static_ip", "Static IP", "numeric_text"),
        ConfigField("network", "gateway", "Gateway", "numeric_text"),
        ConfigField("network", "dns", "DNS", "numeric_text"),
        ConfigField("alarms", "oxygen_low", "Low O2 alarm", "number"),
        ConfigField("alarms", "oxygen_high", "High O2 alarm", "number"),
        ConfigField("alarms", "co_high", "CO alarm", "number"),
    )

    SECTIONS = ("system", "web", "network", "alarms")

    def __init__(
        self,
        framebuffer: str,
        width: int,
        height: int,
        rotate: int = 0,
        config_manager: ConfigManager | None = None,
    ) -> None:
        self.framebuffer = framebuffer
        self.width = width
        self.height = height
        self.rotate = rotate
        self.fb_width, self.fb_height = self._framebuffer_size()
        self.output_transform = self._output_transform()
        if self.fb_width and self.fb_height and (self.fb_width, self.fb_height) != (self.width, self.height):
            LOGGER.info(
                "framebuffer geometry is %sx%s; rendering layout remains %sx%s",
                self.fb_width,
                self.fb_height,
                self.width,
                self.height,
            )
        self.config_manager = config_manager
        runtime = self.config_manager.runtime() if self.config_manager is not None else None
        self.touch = TouchInput(
            width,
            height,
            self.fb_width,
            self.fb_height,
            self.output_transform,
            swap_xy=runtime.touch_swap_xy if runtime is not None else False,
            invert_x=runtime.touch_invert_x if runtime is not None else False,
            invert_y=runtime.touch_invert_y if runtime is not None else False,
        )
        self.view = "home"
        self.section = "alarms"
        self.edit_field: ConfigField | None = None
        self.edit_value = ""
        self.message = ""
        self.buttons: list[Button] = []
        self.font_xl = self._font(42)
        self.font_large = self._font(32)
        self.font_medium = self._font(22)
        self.font_small = self._font(16)

    def render(self, snapshot: dict[str, object]) -> None:
        self._handle_touch()
        if self.framebuffer.lower() == "none":
            return

        image = Image.new("RGB", (self.width, self.height), color=BLACK)
        draw = ImageDraw.Draw(image)
        self.buttons = []

        if self.view == "menu":
            self._draw_menu(draw)
        elif self.view == "form":
            self._draw_form(draw)
        elif self.view == "edit":
            self._draw_editor(draw)
        else:
            self._draw_home(draw, snapshot)

        if self.rotate:
            image = image.rotate(self.rotate, expand=True)
        image = self._fit_framebuffer(image)

        fb_path = Path(self.framebuffer)
        if not fb_path.exists():
            LOGGER.warning("framebuffer %s not available", self.framebuffer)
            return

        try:
            with fb_path.open("wb") as handle:
                handle.write(self._to_rgb565(image))
        except (OSError, ValueError) as exc:
            LOGGER.warning("display render failed: %s", exc)

    def _framebuffer_size(self) -> tuple[int, int]:
        fb_name = Path(self.framebuffer).name
        size_path = Path("/sys/class/graphics") / fb_name / "virtual_size"
        try:
            width, height = size_path.read_text(encoding="utf-8").strip().split(",", 1)
            return int(width), int(height)
        except (OSError, ValueError):
            return self.width, self.height

    def _fit_framebuffer(self, image: Image.Image) -> Image.Image:
        target = (self.fb_width, self.fb_height)
        if image.size == target:
            return image
        if self.output_transform == "rotate90":
            return image.rotate(90, expand=True)
        return image.resize(target, Image.Resampling.BILINEAR)

    def _output_transform(self) -> str:
        if (self.fb_width, self.fb_height) == (self.height, self.width):
            return "rotate90"
        return "scale"

    def _draw_home(self, draw: ImageDraw.ImageDraw, snapshot: dict[str, object]) -> None:
        measurements = snapshot["measurements"]
        if not isinstance(measurements, dict):
            measurements = {}
        alarms = snapshot.get("alarms", {})
        if not isinstance(alarms, dict):
            alarms = {}

        draw.rectangle((0, 0, self.width, 48), fill=(16, 22, 29))
        self._draw_brand_icon(draw, 8, 4, 42)
        draw.text((58, 8), "Gas Monitor", fill=INK, font=self.font_large)
        draw.text((330, 14), str(snapshot.get("status", "BOOT")), fill=self._status_color(str(snapshot.get("status", ""))), font=self.font_medium)

        rows = (
            ("CO", measurements.get("co"), "ppm", self._gas_color("co", measurements.get("co"), alarms)),
            ("Oxygen", measurements.get("oxygen"), "%", self._gas_color("oxygen", measurements.get("oxygen"), alarms)),
            ("NH3", measurements.get("nh3"), "ppm", self._gas_color("nh3", measurements.get("nh3"), alarms)),
            ("NO2", measurements.get("no2"), "ppm", self._gas_color("no2", measurements.get("no2"), alarms)),
        )
        y = 48
        row_h = 54
        for label, value, unit, color in rows:
            draw.rectangle((0, y, self.width, y + row_h - 1), fill=color)
            draw.text((18, y + 10), label, fill=INK, font=self.font_large)
            text = self._format_value(value, unit)
            text_w = self._text_width(draw, text, self.font_xl)
            draw.text((self.width - text_w - 18, y + 4), text, fill=INK, font=self.font_xl)
            y += row_h

        draw.rectangle((0, y, self.width, self.height), fill=(15, 20, 26))
        ip_address = str(snapshot.get("ip_address", "0.0.0.0"))
        draw.text((16, y + 14), f"IP {ip_address}", fill=MUTED, font=self.font_small)
        self._button(draw, (330, y + 8, 466, self.height - 8), "MENU", lambda: self._go("menu"), fill=(35, 87, 125))

    def _draw_menu(self, draw: ImageDraw.ImageDraw) -> None:
        self._title(draw, "Menu")
        y = 62
        for section in self.SECTIONS:
            self._button(draw, (28, y, self.width - 28, y + 44), section.upper(), lambda s=section: self._open_section(s))
            y += 52
        self._button(draw, (28, self.height - 50, 162, self.height - 10), "BACK", lambda: self._go("home"), fill=(70, 77, 85))

    def _draw_form(self, draw: ImageDraw.ImageDraw) -> None:
        self._title(draw, self.section.upper())
        config = self._config()
        fields = [field for field in self.FIELDS if field.section == self.section]
        y = 58
        for field in fields[:4]:
            value = config.get(field.section, {}).get(field.key, "")
            if field.key == "password":
                value = "tap to set"
            label = f"{field.label}: {value}"
            self._button(draw, (16, y, self.width - 16, y + 42), label, lambda f=field: self._open_editor(f), fill=(29, 41, 53))
            y += 48
        self._button(draw, (16, self.height - 48, 150, self.height - 10), "BACK", lambda: self._go("menu"), fill=(70, 77, 85))
        if self.message:
            draw.text((168, self.height - 38), self.message, fill=YELLOW, font=self.font_small)

    def _draw_editor(self, draw: ImageDraw.ImageDraw) -> None:
        field = self.edit_field
        if field is None:
            self._go("form")
            return
        self._title(draw, field.label)
        draw.rectangle((16, 54, self.width - 16, 92), fill=(242, 245, 247))
        draw.text((26, 62), self.edit_value or " ", fill=(8, 12, 16), font=self.font_medium)

        if field.kind == "choice":
            y = 110
            for choice in field.choices:
                self._button(draw, (44, y, self.width - 44, y + 48), choice.upper(), lambda value=choice: self._save_editor(value))
                y += 60
        else:
            keys = self._keyboard_keys(field.kind)
            x = 16
            y = 110
            key_w = 42
            key_h = 32
            for key in keys:
                if key == "\n":
                    x = 16
                    y += key_h + 8
                    continue
                label = "SP" if key == " " else key
                self._button(draw, (x, y, x + key_w, y + key_h), label, lambda k=key: self._add_key(k), fill=(37, 49, 62), font=self.font_small)
                x += key_w + 6
        self._button(draw, (16, self.height - 44, 112, self.height - 8), "BACK", lambda: self._go("form"), fill=(70, 77, 85))
        self._button(draw, (126, self.height - 44, 238, self.height - 8), "DEL", self._delete_key, fill=ORANGE)
        self._button(draw, (self.width - 128, self.height - 44, self.width - 16, self.height - 8), "OK", lambda: self._save_editor(self.edit_value), fill=GREEN)

    def _button(
        self,
        draw: ImageDraw.ImageDraw,
        rect: tuple[int, int, int, int],
        label: str,
        action: Callable[[], None],
        fill: Color = (32, 63, 83),
        font: ImageFont.ImageFont | None = None,
    ) -> None:
        self.buttons.append(Button(rect, label, action))
        draw.rounded_rectangle(rect, radius=6, fill=fill)
        font = font or self.font_medium
        text_w = self._text_width(draw, label, font)
        text_h = self._text_height(draw, label, font)
        x1, y1, x2, y2 = rect
        draw.text((x1 + ((x2 - x1) - text_w) / 2, y1 + ((y2 - y1) - text_h) / 2 - 1), label, fill=INK, font=font)

    def _title(self, draw: ImageDraw.ImageDraw, text: str) -> None:
        draw.rectangle((0, 0, self.width, 46), fill=(16, 22, 29))
        self._draw_brand_icon(draw, 10, 6, 34)
        draw.text((56, 9), text, fill=INK, font=self.font_medium)

    def _draw_brand_icon(self, draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
        left = x + 2
        top = y + 1
        right = x + size - 2
        bottom = y + size - 2
        mid = x + size // 2
        draw.polygon(
            ((mid, top), (right, bottom), (left, bottom)),
            fill=(80, 137, 31),
            outline=(178, 222, 72),
        )
        inset = max(5, size // 5)
        draw.polygon(
            ((mid, top + inset), (right - inset, bottom - inset), (left + inset, bottom - inset)),
            fill=(12, 28, 18),
            outline=(124, 171, 55),
        )
        small = self._font(max(8, size // 5))
        label = "SIEZA" if size >= 40 else "S"
        text_w = self._text_width(draw, label, small)
        text_h = self._text_height(draw, label, small)
        draw.text((mid - text_w / 2, y + size * 0.50 - text_h / 2), label, fill=(238, 242, 220), font=small)

    def _handle_touch(self) -> None:
        tap = self.touch.read_tap()
        if tap is None:
            return
        x, y = tap
        LOGGER.info("touch tap mapped to %s,%s on view %s", x, y, self.view)
        for button in reversed(self.buttons):
            x1, y1, x2, y2 = button.rect
            if x1 <= x <= x2 and y1 <= y <= y2:
                LOGGER.info("touch hit button %s", button.label)
                button.action()
                return
        LOGGER.info("touch missed %s buttons", len(self.buttons))

    def _open_section(self, section: str) -> None:
        self.section = section
        self.message = ""
        self._go("form")

    def _open_editor(self, field: ConfigField) -> None:
        self.edit_field = field
        config = self._config()
        self.edit_value = "" if field.key == "password" else str(config.get(field.section, {}).get(field.key, ""))
        self._go("edit")

    def _save_editor(self, value: str) -> None:
        field = self.edit_field
        if field is None or self.config_manager is None:
            return
        value = value.strip()
        if field.key == "password" and not value:
            self.message = "Password unchanged"
            self._go("form")
            return
        try:
            runtime = self.config_manager.update({field.section: {field.key: value}})
            if field.section == "network":
                self.config_manager.apply_network_profile()
            if field.section == "web" and field.key == "password" and runtime.first_run:
                self.config_manager.set_first_run(False)
            self.message = "Saved"
        except Exception as exc:
            LOGGER.warning("touch config save failed: %s", exc)
            self.message = "Save failed"
        self._go("form")

    def _add_key(self, key: str) -> None:
        self.edit_value = (self.edit_value + key)[:32]

    def _delete_key(self) -> None:
        self.edit_value = self.edit_value[:-1]

    def _go(self, view: str) -> None:
        self.view = view

    def _config(self) -> dict[str, dict[str, Any]]:
        if self.config_manager is None:
            return {}
        return self.config_manager.to_dict(include_secrets=False)

    @staticmethod
    def _keyboard_keys(kind: str) -> tuple[str, ...]:
        if kind in ("number", "numeric_text"):
            return tuple("1234567890.-/") + ("\n",) + tuple("ABCDEFabcdef:")
        return tuple("QWERTYUIOP") + ("\n",) + tuple("ASDFGHJKL") + ("\n",) + tuple("ZXCVBNM0123") + ("\n",) + tuple("456789.-_ ")

    @staticmethod
    def _format_value(value: object, suffix: str) -> str:
        if value is None:
            return "--"
        return f"{value} {suffix}"

    @staticmethod
    def _to_rgb565(image: Image.Image) -> bytes:
        data = bytearray()
        for red, green, blue in image.convert("RGB").getdata():
            value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
            data.append(value & 0xFF)
            data.append((value >> 8) & 0xFF)
        return bytes(data)

    @staticmethod
    def _font(size: int) -> ImageFont.ImageFont:
        for path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ):
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _status_color(status: str) -> Color:
        if status == "NORMAL":
            return GREEN
        if status == "ALARM":
            return RED
        if status == "WAITING":
            return YELLOW
        return ORANGE

    def _gas_color(self, gas: str, value: object, alarms: dict[str, object]) -> Color:
        if value is None:
            return (82, 88, 94)
        numeric = float(value)
        runtime = self.config_manager.runtime() if self.config_manager is not None else None
        if gas == "co" and alarms.get("co_high"):
            return RED
        if gas == "co" and runtime is not None:
            if numeric >= runtime.co_high:
                return RED
            if numeric >= runtime.co_high * 0.85:
                return ORANGE
            if numeric >= runtime.co_high * 0.70:
                return YELLOW
            return GREEN
        if gas == "oxygen":
            if alarms.get("oxygen_low") or alarms.get("oxygen_high"):
                return RED
            if runtime is not None:
                low_span = max(0.1, 20.9 - runtime.oxygen_low)
                high_span = max(0.1, runtime.oxygen_high - 20.9)
                if numeric < runtime.oxygen_low + low_span * 0.35:
                    return ORANGE
                if numeric < runtime.oxygen_low + low_span * 0.60:
                    return YELLOW
                if numeric > runtime.oxygen_high - high_span * 0.35:
                    return ORANGE
                if numeric > runtime.oxygen_high - high_span * 0.60:
                    return YELLOW
            return GREEN
        if gas == "nh3":
            if numeric >= 50:
                return RED
            if numeric >= 25:
                return ORANGE
            if numeric >= 10:
                return YELLOW
        if gas == "no2":
            if numeric >= 5:
                return RED
            if numeric >= 2:
                return ORANGE
            if numeric >= 1:
                return YELLOW
        return GREEN

    @staticmethod
    def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
        return int(draw.textbbox((0, 0), text, font=font)[2])

    @staticmethod
    def _text_height(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return int(bbox[3] - bbox[1])
