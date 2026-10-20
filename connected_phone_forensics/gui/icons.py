"""Icons from the Phosphor and Material Design sets (via qtawesome), cached per name and colour."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap

CHANNEL_ICON = {
    "usb": "mdi6.usb", "bluetooth": "ph.bluetooth", "wifi_hotspot": "ph.wifi-high",
    "usb_tethering": "mdi6.usb-port", "pairing": "mdi6.cellphone-key", "backup": "ph.archive",
    "phone_link": "mdi6.cellphone-link", "continuity": "ph.arrows-left-right", "account_sync": "ph.cloud",
    "messaging_client": "ph.chat-text", "companion_app": "ph.app-window", "developer": "ph.terminal-window",
    "other": "ph.dots-three-circle",
}
OS_ICON = {"windows": "ph.windows-logo", "macos": "ph.apple-logo", "linux": "mdi6.linux",
           "ios": "ph.apple-logo", "android": "ph.android-logo"}
CLASS_ICON = {"phone": "ph.device-mobile", "tablet": "ph.device-tablet", "computer": "ph.desktop",
              "audio": "ph.headphones", "wearable": "ph.watch", "peripheral": "ph.mouse-simple",
              "other": "ph.cpu"}


@lru_cache(maxsize=512)
def icon(name: str, color: str) -> QIcon:
    import qtawesome as qta

    try:
        return qta.icon(name, color=color)
    except Exception:  # noqa: BLE001 - an unknown name must not break a screen
        return qta.icon("ph.circle", color=color)


def device_icon(device_class: str | None) -> str:
    return CLASS_ICON.get(device_class or "phone", "ph.device-mobile")


def main_channel(d: dict) -> str | None:
    """The connection that recorded most about a device (it picks the device's badge)."""
    channels = sorted(d.get("channels") or [], key=lambda c: -int(c.get("observations") or 0))
    return channels[0]["channel"] if channels else None


def device_badge(d: dict) -> tuple[str, str | None]:
    """(icon, channel) for a device in a list: headphones, computers... by their class; phones by
    how they connected (USB, Bluetooth, Phone Link, hotspot...)."""
    cls = d.get("device_class")
    if cls not in (None, "phone", "tablet"):
        return CLASS_ICON.get(cls, "ph.cpu"), None
    ch = main_channel(d)
    return CHANNEL_ICON.get(ch or "", "ph.device-mobile"), ch


ASSETS = Path(__file__).resolve().parents[1] / "assets"


def asset(name: str) -> Path:
    """A file from the brand kit in connected_phone_forensics/assets (works when frozen too)."""
    return ASSETS / name


@lru_cache(maxsize=64)
def svg_pixmap(name: str, width: int, height: int | None = None, ratio: float = 1.0) -> QPixmap:
    """Render an asset SVG to a pixmap ``width`` (logical) pixels wide, sharp at ``ratio``."""
    from PySide6.QtSvg import QSvgRenderer

    r = QSvgRenderer(str(asset(name)))
    size = r.defaultSize()
    h = height or round(width * size.height() / size.width())
    pm = QPixmap(round(width * ratio), round(h * ratio))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    r.render(p, QRectF(0, 0, pm.width(), pm.height()))
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def logo_pixmap(size: int, ratio: float = 1.0) -> QPixmap:
    """The logo mark; sizes up to 48 px use the kit's simplified mark, as the kit specifies."""
    return svg_pixmap("logo-small.svg" if size <= 48 else "logo.svg", size, size, ratio)


@lru_cache(maxsize=1)
def app_icon(_accent: str | None = None) -> QIcon:
    """The window and taskbar icon: the kit's Windows icon plus sharp large sizes from the SVG."""
    out = QIcon(str(asset("cpf.ico")))
    for size in (512, 1024):
        out.addPixmap(logo_pixmap(size))
    return out
