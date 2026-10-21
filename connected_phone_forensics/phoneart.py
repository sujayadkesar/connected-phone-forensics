"""A drawn phone, for the app's device page and the PDF summary (QtGui only, no widgets).

The look follows what is known about the phone: an iPhone with a Dynamic Island, a notch or a
Home button depending on the model (from its model identifier or the chip in its UDID), an
Android phone with a punch-hole camera, or a plain slab when the OS is unknown. The screen
shows the wallpaper recovered from Phone Link when there is one, the time the phone was last
seen, and its name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPaintEngine,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)

ISLAND, NOTCH, HOME_BUTTON, PUNCH_HOLE, PLAIN = "island", "notch", "home", "punch", "plain"

# iPhone model numbers (the first number of "iPhoneN,M") -> front design.
_IPHONE_DESIGN = {7: HOME_BUTTON, 8: HOME_BUTTON, 9: HOME_BUTTON, 10: NOTCH, 11: NOTCH, 12: NOTCH, 13: NOTCH,
                  14: NOTCH, 15: ISLAND, 16: ISLAND, 17: ISLAND, 18: ISLAND}
_SE_MODELS = {"iPhone8,4", "iPhone12,8", "iPhone14,6", "iPhone10,1", "iPhone10,2", "iPhone10,4", "iPhone10,5"}
_NOTCH_14 = {"iPhone14,7", "iPhone14,8"}  # iPhone 14 / 14 Plus kept the notch
_CHIP_DESIGN = {0x8020: NOTCH, 0x8030: NOTCH, 0x8101: NOTCH, 0x8110: NOTCH, 0x8120: ISLAND, 0x8130: ISLAND,
                0x8140: ISLAND, 0x8150: ISLAND}


@dataclass
class PhoneLook:
    design: str = PLAIN
    os: str = ""
    name: str = ""
    caption: str = ""            # small line under the name (model)
    last_seen: str | None = None
    wallpaper: QImage | None = None
    tablet: bool = False
    minimal: bool = False        # no status bar or lock-screen text (small drawings)


def design_for(device: dict) -> str:
    os_name = device.get("phone_os") or ""
    ids = device.get("identifiers") or []
    models = [i["value"] for i in ids if i.get("kind") == "model"]
    for m in models:
        mm = re.fullmatch(r"iPhone(\d+),(\d+)", str(m).strip())
        if mm:
            if m in _SE_MODELS:
                return HOME_BUTTON
            if m in _NOTCH_14:
                return NOTCH
            return _IPHONE_DESIGN.get(int(mm.group(1)), ISLAND if int(mm.group(1)) > 18 else HOME_BUTTON)
    for i in ids:
        if i.get("kind") in ("udid", "usb_serial"):
            v = str(i["value"]).replace("-", "")
            if re.fullmatch(r"0000[0-9A-Fa-f]{20}", v):
                return _CHIP_DESIGN.get(int(v[4:8], 16), ISLAND)
            if re.fullmatch(r"[0-9A-Fa-f]{40}", v):
                return HOME_BUTTON  # pre-2018 UDID: an iPhone with a Home button (or an iPhone X)
    if os_name == "ios":
        return ISLAND
    if os_name == "android":
        return PUNCH_HOLE
    return PLAIN


def look_for(device: dict, wallpaper: Path | None = None, caption: str = "") -> PhoneLook:
    img = None
    if wallpaper is not None:
        candidate = QImage(str(wallpaper))
        img = candidate if not candidate.isNull() and candidate.width() > 8 else None
    return PhoneLook(design=design_for(device), os=device.get("phone_os") or "", name=device.get("label") or "",
                     caption=caption, last_seen=device.get("last_seen"), wallpaper=img,
                     tablet=device.get("device_class") == "tablet")


def phone_rect(bounds: QRectF, tablet: bool = False) -> QRectF:
    """The largest phone shape (19.5:9-ish, or 4:3 for tablets) centred in ``bounds``."""
    ratio = 0.72 if tablet else 0.49
    h = bounds.height()
    w = h * ratio
    if w > bounds.width():
        w = bounds.width()
        h = w / ratio
    return QRectF(bounds.center().x() - w / 2, bounds.center().y() - h / 2, w, h)


def _rounded(rect: QRectF, r: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, r, r)
    return path


def _screen_gradient(rect: QRectF, os_name: str) -> QLinearGradient:
    g = QLinearGradient(rect.topLeft(), rect.bottomRight())
    if os_name == "ios":
        stops = ("#5C95FF", "#2456DE", "#0B1630")
    elif os_name == "android":
        stops = ("#3FD0B5", "#1B8A8F", "#0E2A3A")
    else:
        stops = ("#7CCBFF", "#4A6FA5", "#172C55")
    for pos, c in zip((0.0, 0.55, 1.0), stops, strict=True):
        g.setColorAt(pos, QColor(c))
    return g


_OS_WORD = {"ios": "iPhone", "android": "Android"}


def _when(value: str | None, os_name: str) -> tuple[str, str]:
    """(big text, small line) for the lock screen: the time the phone was last seen."""
    try:
        dt = datetime.fromisoformat(value) if value else None
    except ValueError:
        dt = None
    if dt is None:
        return _OS_WORD.get(os_name, "Phone"), "no time recorded"
    return dt.strftime("%H:%M"), f"last seen {dt.day} {dt:%b %Y} · UTC"


def paint_phone(p: QPainter, bounds: QRectF, look: PhoneLook, *, font_family: str = "", shadow: bool = True) -> QRectF:
    """Draw the phone fitted into ``bounds``; returns the phone's rectangle."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    body = phone_rect(bounds.adjusted(bounds.width() * 0.06, 0, -bounds.width() * 0.06, 0), look.tablet)
    w = body.width()
    radius = w * (0.09 if look.tablet else 0.17)

    if shadow:  # a soft contact shadow, built from translucent layers
        for i in range(8):
            spread = w * 0.012 * (i + 1)
            r = body.adjusted(-spread * 0.4, spread * 0.6 + w * 0.02, spread * 0.4, spread * 0.9 + w * 0.02)
            p.fillPath(_rounded(r, radius + spread), QColor(5, 12, 30, 16))

    # side buttons
    btn = QColor("#2A3346")
    bw = w * 0.022
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(btn)
    if not look.tablet:
        for top, length in ((0.20, 0.06), (0.29, 0.10), (0.41, 0.10)):
            p.drawRoundedRect(QRectF(body.left() - bw * 0.8, body.top() + body.height() * top, bw * 1.6,
                                     body.height() * length), bw * 0.7, bw * 0.7)
        p.drawRoundedRect(QRectF(body.right() - bw * 0.8, body.top() + body.height() * 0.30, bw * 1.6,
                                 body.height() * 0.14), bw * 0.7, bw * 0.7)

    # frame: a metallic edge around a near-black body
    edge = QLinearGradient(body.topLeft(), body.bottomRight())
    for pos, c in ((0.0, "#8E9AB3"), (0.35, "#3B465E"), (0.65, "#262E40"), (1.0, "#7F8BA4")):
        edge.setColorAt(pos, QColor(c))
    p.fillPath(_rounded(body, radius), edge)
    inner = body.adjusted(w * 0.012, w * 0.012, -w * 0.012, -w * 0.012)
    p.fillPath(_rounded(inner, radius * 0.94), QColor("#0A0E17"))

    # screen
    bezel = w * (0.045 if look.design != HOME_BUTTON else 0.06)
    screen = inner.adjusted(bezel, bezel, -bezel, -bezel)
    if look.design == HOME_BUTTON:
        screen = QRectF(screen.left(), body.top() + body.height() * 0.12, screen.width(), body.height() * 0.76)
    s_radius = 0.0 if look.design == HOME_BUTTON else radius * 0.78
    clip = _rounded(screen, max(s_radius, w * 0.01))
    p.save()
    p.setClipPath(clip)
    if p.paintEngine().type() == QPaintEngine.Type.Pdf:
        # PDF viewers built on PDFium (Chrome, Edge, Android) paint a gradient's transparency as solid
        # colour, which blanked the screen. On a PDF it is one opaque image, drawn straight after the
        # opaque body: Qt's PDF writer paints an image with the transparency of the last fill.
        p.drawImage(screen, _screen_image(screen, look, p))
    else:
        _paint_screen(p, screen, look)

    if look.minimal:
        p.restore()  # screen clip
        p.restore()
        return body
    # status bar and lock-screen text
    fam = font_family or QFont().family()
    white = QColor(255, 255, 255)
    soft = QColor(255, 255, 255, 215)
    sb_h = screen.height() * 0.045
    sb_top = screen.top() + sb_h * 0.55
    f = QFont(fam)
    f.setPixelSize(max(5, int(sb_h * 0.62)))
    f.setWeight(QFont.Weight.DemiBold)
    p.setFont(f)
    p.setPen(white)
    hhmm, seen = _when(look.last_seen, look.os)
    p.drawText(QRectF(screen.left() + screen.width() * 0.08, sb_top, screen.width() * 0.3, sb_h),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "9:41" if look.design != PLAIN else "")
    _status_icons(p, QRectF(screen.right() - screen.width() * 0.3, sb_top, screen.width() * 0.24, sb_h))

    f.setPixelSize(max(8, int(screen.width() * (0.25 if ":" in hhmm else 0.16))))
    f.setWeight(QFont.Weight.DemiBold if look.os == "ios" else QFont.Weight.Normal)
    p.setFont(f)
    clock = QRectF(screen.left(), screen.top() + screen.height() * 0.13, screen.width(), screen.height() * 0.17)
    p.setPen(white)
    p.drawText(clock, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, hhmm)
    f.setPixelSize(max(5, int(screen.width() * 0.058)))
    f.setWeight(QFont.Weight.Medium)
    p.setFont(f)
    p.setPen(soft)
    p.drawText(QRectF(screen.left(), clock.top() - screen.height() * 0.035, screen.width(), screen.height() * 0.05),
               Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, seen)

    # the name in a frosted pill near the bottom
    if look.name:
        f.setPixelSize(max(6, int(screen.width() * 0.068)))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        pill = QRectF(screen.left() + screen.width() * 0.08, screen.bottom() - screen.height() * 0.2,
                      screen.width() * 0.84, screen.height() * (0.115 if look.caption else 0.075))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 48))
        p.drawRoundedRect(pill, pill.height() * 0.3, pill.height() * 0.3)
        p.setPen(white)
        name_rect = QRectF(pill.left() + pill.width() * 0.06, pill.top(), pill.width() * 0.88,
                           pill.height() * (0.58 if look.caption else 1.0))
        metrics_name = p.fontMetrics().elidedText(look.name, Qt.TextElideMode.ElideRight, int(name_rect.width()))
        p.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, metrics_name)
        if look.caption:
            f.setPixelSize(max(5, int(screen.width() * 0.05)))
            f.setWeight(QFont.Weight.Normal)
            p.setFont(f)
            p.setPen(soft)
            cap_rect = QRectF(name_rect.left(), pill.top() + pill.height() * 0.52, name_rect.width(),
                              pill.height() * 0.4)
            p.drawText(cap_rect, Qt.AlignmentFlag.AlignCenter,
                       p.fontMetrics().elidedText(look.caption, Qt.TextElideMode.ElideRight, int(cap_rect.width())))
    if look.design in (ISLAND, NOTCH, PUNCH_HOLE, PLAIN) and not look.tablet:
        bar = QRectF(screen.center().x() - screen.width() * 0.17, screen.bottom() - screen.height() * 0.022,
                     screen.width() * 0.34, max(1.5, screen.height() * 0.007))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 200))
        p.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)
    p.restore()  # screen clip

    # front camera / sensors
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#05070C"))
    if look.design == ISLAND:
        island = QRectF(screen.center().x() - screen.width() * 0.16, screen.top() + screen.height() * 0.018,
                        screen.width() * 0.32, screen.height() * 0.042)
        p.drawRoundedRect(island, island.height() / 2, island.height() / 2)
        _lens(p, QPointF(island.right() - island.height() * 0.55, island.center().y()), island.height() * 0.24)
    elif look.design == NOTCH:
        notch = QRectF(screen.center().x() - screen.width() * 0.25, screen.top() - 1, screen.width() * 0.5,
                       screen.height() * 0.045)
        path = QPainterPath()
        path.addRoundedRect(notch, notch.height() * 0.45, notch.height() * 0.45)
        path.addRect(QRectF(notch.left(), notch.top(), notch.width(), notch.height() / 2))
        p.drawPath(path.simplified())
        _lens(p, QPointF(notch.center().x() + notch.width() * 0.22, notch.center().y()), notch.height() * 0.2)
    elif look.design == PUNCH_HOLE:
        _lens(p, QPointF(screen.center().x(), screen.top() + screen.height() * 0.035), screen.width() * 0.035,
              ring=True)
    elif look.design == HOME_BUTTON:
        speaker = QRectF(body.center().x() - w * 0.1, body.top() + body.height() * 0.055, w * 0.2, w * 0.025)
        p.setBrush(QColor("#1A2030"))
        p.drawRoundedRect(speaker, speaker.height() / 2, speaker.height() / 2)
        _lens(p, QPointF(body.center().x() - w * 0.17, speaker.center().y()), w * 0.022)
        button = QRectF(body.center().x() - w * 0.085, body.bottom() - body.height() * 0.105, w * 0.17, w * 0.17)
        p.setBrush(QColor("#10141F"))
        p.setPen(QPen(QColor("#5B667F"), max(0.6, w * 0.008)))
        p.drawEllipse(button)
    p.restore()
    return body


def _paint_screen(p: QPainter, screen: QRectF, look: PhoneLook) -> None:
    """The screen's background: the wallpaper under a veil, or a default-looking one."""
    if look.wallpaper is not None:
        img = look.wallpaper
        scale = max(screen.width() / img.width(), screen.height() / img.height())
        tw, th = img.width() * scale, img.height() * scale
        p.drawImage(QRectF(screen.center().x() - tw / 2, screen.center().y() - th / 2, tw, th), img)
        veil = QLinearGradient(screen.topLeft(), screen.bottomLeft())
        veil.setColorAt(0.0, QColor(0, 0, 0, 90))
        veil.setColorAt(0.45, QColor(0, 0, 0, 20))
        veil.setColorAt(1.0, QColor(0, 0, 0, 140))
        p.fillRect(screen, veil)
        return
    p.fillRect(screen, _screen_gradient(screen, look.os))
    glow = QRadialGradient(QPointF(screen.left() + screen.width() * 0.25, screen.top() + screen.height() * 0.3),
                           screen.width() * 0.9)
    glow.setColorAt(0.0, QColor(255, 255, 255, 46))
    glow.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillRect(screen, glow)
    # a few soft shapes, like a default wallpaper
    p.setPen(Qt.PenStyle.NoPen)
    for cx, cy, rr, alpha in ((0.85, 0.72, 0.55, 26), (0.1, 0.95, 0.45, 20), (0.6, 0.15, 0.3, 18)):
        p.setBrush(QColor(255, 255, 255, alpha))
        p.drawEllipse(QPointF(screen.left() + screen.width() * cx, screen.top() + screen.height() * cy),
                      screen.width() * rr, screen.width() * rr)


def _screen_image(screen: QRectF, look: PhoneLook, p: QPainter, dpi: float = 300) -> QImage:
    """The screen's background painted by the raster engine, opaque, at ``dpi`` on ``p``'s device."""
    size = p.worldTransform().mapRect(screen).size()  # in device units
    per_unit = dpi / max(1, p.device().logicalDpiX())
    img = QImage(max(1, round(size.width() * per_unit)), max(1, round(size.height() * per_unit)),
                 QImage.Format.Format_RGB32)
    img.fill(QColor("#0A0E17"))
    q = QPainter(img)
    q.setRenderHint(QPainter.RenderHint.Antialiasing)
    q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    q.scale(img.width() / screen.width(), img.height() / screen.height())
    q.translate(-screen.left(), -screen.top())
    _paint_screen(q, screen, look)
    q.end()
    return img


def _lens(p: QPainter, c: QPointF, r: float, ring: bool = False) -> None:
    if ring:
        p.setBrush(QColor("#05070C"))
        p.drawEllipse(c, r * 1.35, r * 1.35)
    g = QRadialGradient(QPointF(c.x() - r * 0.3, c.y() - r * 0.3), r)
    g.setColorAt(0.0, QColor("#4A5B8C"))
    g.setColorAt(0.6, QColor("#151C30"))
    g.setColorAt(1.0, QColor("#05070C"))
    p.setBrush(g)
    p.drawEllipse(c, r, r)


def _status_icons(p: QPainter, r: QRectF) -> None:
    """Signal bars, Wi-Fi and battery, drawn small at the right of the status bar."""
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 235))
    h = r.height() * 0.62
    y0 = r.center().y() + h / 2
    x = r.left()
    bw = h * 0.22
    for i in range(4):
        bh = h * (0.35 + 0.65 * i / 3)
        p.drawRoundedRect(QRectF(x + i * bw * 1.45, y0 - bh, bw, bh), bw * 0.3, bw * 0.3)
    x += bw * 1.45 * 4 + h * 0.4
    pen = QPen(QColor(255, 255, 255, 235), max(0.5, h * 0.14))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx, cy = x + h * 0.6, y0 - h * 0.05
    for k in (0.95, 0.62):
        rr = h * k
        p.drawArc(QRectF(cx - rr, cy - rr, rr * 2, rr * 2), 45 * 16, 90 * 16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 235))
    p.drawEllipse(QPointF(cx, cy - h * 0.1), h * 0.12, h * 0.12)
    x = cx + h * 1.1
    batt = QRectF(x, r.center().y() - h * 0.42, h * 1.75, h * 0.84)
    p.setPen(QPen(QColor(255, 255, 255, 170), max(0.4, h * 0.08)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(batt, h * 0.22, h * 0.22)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 235))
    p.drawRoundedRect(batt.adjusted(h * 0.14, h * 0.14, -h * 0.55, -h * 0.14), h * 0.12, h * 0.12)
    p.drawRoundedRect(QRectF(batt.right() + h * 0.06, batt.center().y() - h * 0.18, h * 0.12, h * 0.36),
                      h * 0.05, h * 0.05)
    p.restore()
