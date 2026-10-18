"""Icons from the Phosphor and Material Design sets (via qtawesome), cached per name and colour."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap

CHANNEL_ICON = {
    "usb": "mdi6.usb", "bluetooth": "ph.bluetooth", "wifi_hotspot": "ph.wifi-high",
    "usb_tethering": "mdi6.usb-port", "pairing": "ph.handshake", "backup": "ph.archive",
    "phone_link": "ph.link", "continuity": "ph.arrows-left-right", "account_sync": "ph.cloud",
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


def app_icon(accent: str) -> QIcon:
    """The window and taskbar icon: a phone glyph on a rounded accent square."""
    out = QIcon()
    glyph = icon("ph.device-mobile-camera-bold", "#ffffff")
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, size, size), size * 0.24, size * 0.24)
        p.fillPath(path, QColor(accent))
        inset = round(size * 0.2)
        glyph.paint(p, inset, inset, size - 2 * inset, size - 2 * inset)
        p.end()
        out.addPixmap(pm)
    return out
