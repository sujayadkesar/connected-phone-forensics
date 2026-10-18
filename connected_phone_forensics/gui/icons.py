"""Icons from the Phosphor and Material Design sets (via qtawesome), cached per name and colour."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap

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


LOGO_TOP, LOGO_BOTTOM = "#5f95f5", "#2a5fd6"


def paint_logo(p: QPainter, size: int) -> None:
    """The app logo: a phone on a blue rounded square, with a link badge at larger sizes."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    bg = QPainterPath()
    bg.addRoundedRect(QRectF(0, 0, size, size), size * 0.235, size * 0.235)
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0, QColor(LOGO_TOP))
    grad.setColorAt(1, QColor(LOGO_BOTTOM))
    p.fillPath(bg, grad)
    badge = size >= 40
    g = round(size * (0.60 if badge else 0.66))
    gx = round((size - g) / 2 - (size * 0.06 if badge else 0))
    gy = round((size - g) / 2 - (size * 0.03 if badge else 0))
    icon("ph.device-mobile-bold", "#ffffff").paint(p, QRect(gx, gy, g, g))
    if badge:
        d = size * 0.36
        c = QRectF(size * 0.575, size * 0.575, d, d)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(LOGO_BOTTOM))
        p.drawEllipse(c.adjusted(-size * 0.03, -size * 0.03, size * 0.03, size * 0.03))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(c)
        inner = round(d * 0.64)
        icon("ph.link-bold", LOGO_BOTTOM).paint(
            p, QRect(round(c.center().x() - inner / 2), round(c.center().y() - inner / 2), inner, inner))
    p.restore()


def logo_pixmap(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    paint_logo(p, size)
    p.end()
    return pm


def app_icon(_accent: str | None = None) -> QIcon:
    """The window and taskbar icon (the logo at every common size)."""
    out = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        out.addPixmap(logo_pixmap(size))
    return out
