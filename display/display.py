from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


LOGGER = logging.getLogger(__name__)


class FramebufferDisplay:
    def __init__(self, framebuffer: str, width: int, height: int, rotate: int = 0) -> None:
        self.framebuffer = framebuffer
        self.width = width
        self.height = height
        self.rotate = rotate
        self.font_large = ImageFont.load_default()
        self.font_small = ImageFont.load_default()

    def render(self, snapshot: dict[str, object]) -> None:
        if self.framebuffer.lower() == "none":
            return

        image = Image.new("RGB", (self.width, self.height), color=(10, 14, 18))
        draw = ImageDraw.Draw(image)

        status = str(snapshot["status"])
        measurements = snapshot["measurements"]
        oxygen = measurements.get("oxygen")
        co = measurements.get("co")
        ip_address = snapshot["ip_address"]

        draw.text((20, 20), str(snapshot["device_name"]), fill=(240, 240, 240), font=self.font_large)
        draw.text((20, 70), f"O2: {self._format_value(oxygen, '%')}", fill=(80, 220, 140), font=self.font_large)
        draw.text((20, 110), f"CO: {self._format_value(co, 'ppm')}", fill=(240, 220, 80), font=self.font_large)
        draw.text((20, 160), f"STATUS: {status}", fill=self._status_color(status), font=self.font_large)
        draw.text((20, 200), f"IP: {ip_address}", fill=(220, 220, 220), font=self.font_small)

        if snapshot.get("first_run"):
            draw.text((20, 250), "CONFIG MODE - CHANGE PASSWORD", fill=(255, 120, 120), font=self.font_small)

        if self.rotate:
            image = image.rotate(self.rotate, expand=True)

        fb_path = Path(self.framebuffer)
        if not fb_path.exists():
            LOGGER.warning("framebuffer %s not available", self.framebuffer)
            return

        try:
            with fb_path.open("wb") as handle:
                handle.write(image.convert("RGB").tobytes("raw", "BGR;16"))
        except OSError as exc:
            LOGGER.warning("display render failed: %s", exc)

    @staticmethod
    def _format_value(value: object, suffix: str) -> str:
        if value is None:
            return "--"
        return f"{value} {suffix}"

    @staticmethod
    def _status_color(status: str) -> tuple[int, int, int]:
        if status == "NORMAL":
            return (60, 220, 120)
        if status == "ALARM":
            return (255, 80, 80)
        return (255, 180, 60)
