"""Banners: the New scan page's animated hero and the case banner on the Overview.

:class:`HeroArt` draws a phone and a laptop joined by USB, Bluetooth and Wi-Fi links, with
records travelling along them and filling the laptop's timeline. It animates only while it
is on screen (or not at all, for the Overview's still version).
"""

from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QFrame, QSizePolicy, QWidget

from .. import phoneart
from .icons import CHANNEL_ICON, icon
from .theme import channel_color, mix
from .widgets import THEME

LINKS = ("usb", "bluetooth", "wifi_hotspot")
TIMELINE = ("usb", "bluetooth", "wifi_hotspot", "phone_link", "pairing", "backup")


class Banner(QFrame):
    """A wide card with a soft accent gradient and a faint dot grid, for a page's title area."""

    def __init__(self):
        super().__init__()
        self.setObjectName("Banner")
        THEME.on_change(self.update, self)

    def paintEvent(self, _e) -> None:  # noqa: N802
        pal = THEME.palette
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0.0, QColor(mix(pal.surface, pal.accent, 0.17 if pal.dark else 0.10)))
        g.setColorAt(0.55, QColor(pal.surface))
        g.setColorAt(1.0, QColor(mix(pal.surface, pal.series[2], 0.12 if pal.dark else 0.07)))
        p.fillPath(path, g)
        p.save()
        p.setClipPath(path)
        dot = QColor(pal.text)
        dot.setAlpha(13 if pal.dark else 20)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot)
        step, y = 18.0, 12.0
        while y < r.height():
            x = r.width() * 0.42 + (9 if int(y / step) % 2 else 0)
            while x < r.width():
                p.drawEllipse(QPointF(x, y), 1.1, 1.1)
                x += step
            y += step
        p.restore()
        p.setPen(QPen(QColor(pal.border), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)


class HeroArt(QWidget):
    """A phone and a laptop joined by USB, Bluetooth and Wi-Fi; records travel from one to the other."""

    def __init__(self, animated: bool = True, height: int = 210):
        super().__init__()
        self.animated = animated
        self.setMinimumSize(360, height)
        self.setMaximumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self.update)
        THEME.on_change(self.update, self)

    def showEvent(self, e) -> None:  # noqa: N802
        if self.animated:
            self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(e)

    def _time(self) -> float:
        return self._clock.elapsed() / 1000.0 if self.animated else 1.35

    # -- drawing ---------------------------------------------------------------------------

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = self._time()
        pal = THEME.palette
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())

        ph_h = h * 0.9
        ph_w = ph_h * 0.5
        lap_w = min(w * 0.46, 250.0)
        lap_h = lap_w * 0.66
        gap = w - ph_w - lap_w
        x0 = max(0.0, gap * 0.12)
        phone_bounds = QRectF(x0, (h - ph_h) / 2, ph_w, ph_h)
        lap = QRectF(w - lap_w - max(0.0, gap * 0.08), (h - lap_h) / 2 - 6, lap_w, lap_h)

        glow = QRadialGradient(phone_bounds.center(), ph_h * 0.62)
        c = QColor(pal.accent)
        c.setAlpha(60 if pal.dark else 40)
        glow.setColorAt(0, c)
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(phone_bounds.center(), ph_h * 0.62, ph_h * 0.62)

        body = phoneart.paint_phone(p, phone_bounds, phoneart.PhoneLook(phoneart.ISLAND, "ios", minimal=True),
                                    shadow=True)
        self._phone_screen(p, body, t)
        self._laptop(p, lap, t)
        for i, chan in enumerate(LINKS):
            self._link(p, i, chan, body, lap, t)

    def _phone_screen(self, p: QPainter, body: QRectF, t: float) -> None:
        scr = body.adjusted(body.width() * 0.13, body.height() * 0.16, -body.width() * 0.13, -body.height() * 0.24)
        p.setPen(Qt.PenStyle.NoPen)
        rows = 4
        bh = scr.height() / (rows * 1.45)
        for k in range(rows):
            y = scr.top() + k * bh * 1.45
            bw = scr.width() * (0.74 if k % 2 == 0 else 0.56)
            x = scr.left() if k % 2 == 0 else scr.right() - bw
            pulse = 0.55 + 0.45 * math.sin(t * 2.2 - k * 0.9) if self.animated else 0.8
            a = 30 + int(40 * pulse)
            p.setBrush(QColor(255, 255, 255, a))
            p.drawRoundedRect(QRectF(x, y, bw, bh), bh * 0.42, bh * 0.42)

    def _laptop(self, p: QPainter, r: QRectF, t: float) -> None:
        pal = THEME.palette
        lid = QRectF(r.left() + r.width() * 0.07, r.top(), r.width() * 0.86, r.height() * 0.84)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(6):  # a soft shadow
            spread = 2.0 * (i + 1)
            p.setBrush(QColor(5, 12, 30, 14))
            p.drawRoundedRect(lid.adjusted(-spread * 0.5, spread * 0.5, spread * 0.5, spread * 1.2), 10 + spread,
                              10 + spread)
        frame = QLinearGradient(lid.topLeft(), lid.bottomRight())
        frame.setColorAt(0, QColor("#4A5672"))
        frame.setColorAt(0.5, QColor("#262E40"))
        frame.setColorAt(1, QColor("#3B465E"))
        p.setBrush(frame)
        p.drawRoundedRect(lid, 10, 10)
        screen = lid.adjusted(6, 6, -6, -6)
        g = QLinearGradient(screen.topLeft(), screen.bottomRight())
        g.setColorAt(0, QColor("#16244A"))
        g.setColorAt(1, QColor("#0A1020"))
        p.setBrush(g)
        p.drawRoundedRect(screen, 5, 5)
        for k, col in enumerate(("#F26B6B", "#E3B341", "#3FB950")):
            p.setBrush(QColor(col))
            p.drawEllipse(QPointF(screen.left() + 9 + k * 7, screen.top() + 8), 2.1, 2.1)

        rows = len(TIMELINE)
        top = screen.top() + 18
        row_h = (screen.height() - 24) / rows
        cycle = (t * 0.32) % 1.0
        shown = rows if not self.animated else int(cycle * (rows + 3))
        for k, chan in enumerate(TIMELINE):
            y = top + k * row_h + row_h * 0.25
            alpha = 1.0 if k < shown else 0.16
            dot = QColor(channel_color(pal, chan))
            dot.setAlphaF(alpha)
            p.setBrush(dot)
            p.drawEllipse(QPointF(screen.left() + 13, y + 2.2), 2.6, 2.6)
            bar = QColor("#C9D6F2")
            bar.setAlphaF(0.55 * alpha)
            bw = screen.width() * (0.30 + 0.06 * ((k * 7) % 6))
            p.setBrush(bar)
            p.drawRoundedRect(QRectF(screen.left() + 22, y, bw, 4.4), 2.2, 2.2)
            bar.setAlphaF(0.22 * alpha)
            p.setBrush(bar)
            p.drawRoundedRect(QRectF(screen.left() + 28 + bw, y, screen.width() * 0.16, 4.4), 2.2, 2.2)
        if self.animated:  # the scan line
            sy = screen.top() + 14 + (screen.height() - 18) * cycle
            sweep = QLinearGradient(QPointF(0, sy - 14), QPointF(0, sy))
            a = QColor(pal.accent)
            a.setAlpha(0)
            sweep.setColorAt(0, a)
            a.setAlpha(110)
            sweep.setColorAt(1, a)
            p.fillRect(QRectF(screen.left() + 1, sy - 14, screen.width() - 2, 14), sweep)

        bt = lid.bottom()
        base = QPainterPath()
        base.moveTo(r.left(), bt)
        base.lineTo(r.right(), bt)
        base.lineTo(r.right() - r.width() * 0.035, bt + r.height() * 0.09)
        base.lineTo(r.left() + r.width() * 0.035, bt + r.height() * 0.09)
        base.closeSubpath()
        bg = QLinearGradient(QPointF(0, bt), QPointF(0, bt + r.height() * 0.09))
        bg.setColorAt(0, QColor("#6A7690"))
        bg.setColorAt(1, QColor("#2A3346"))
        p.fillPath(base, bg)
        p.setBrush(QColor("#1A2030"))
        p.drawRoundedRect(QRectF(r.center().x() - r.width() * 0.09, bt, r.width() * 0.18, 3.2), 1.6, 1.6)

    def _link(self, p: QPainter, i: int, chan: str, body: QRectF, lap: QRectF, t: float) -> None:
        pal = THEME.palette
        y0 = body.top() + body.height() * (0.30 + 0.20 * i)
        y1 = lap.top() + lap.height() * (0.22 + 0.24 * i)
        start = QPointF(body.right() + 10, y0)
        end = QPointF(lap.left() + lap.width() * 0.05, y1)
        dx = end.x() - start.x()
        bend = (i - 1) * 30.0
        path = QPainterPath(start)
        path.cubicTo(QPointF(start.x() + dx * 0.38, y0 + bend), QPointF(start.x() + dx * 0.62, y1 + bend), end)
        color = QColor(channel_color(pal, chan))
        line = QColor(color)
        line.setAlpha(120)
        pen = QPen(line, 1.6)
        pen.setDashPattern([2.5, 4.0])
        pen.setDashOffset(-t * 10.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        for k in range(2):  # records travelling, each with a fading tail
            phase = (t * 0.28 + i * 0.29 + k * 0.5) % 1.0
            for j in range(8):
                tt = phase - j * 0.016
                if tt <= 0.0:
                    break
                c = QColor(color)
                c.setAlphaF(max(0.0, 0.9 - j * 0.11))
                p.setBrush(c)
                radius = max(0.8, 3.6 - j * 0.38)
                p.drawEllipse(path.pointAtPercent(tt), radius, radius)
        mid = path.pointAtPercent(0.5)
        br = QRectF(mid.x() - 14, mid.y() - 14, 28, 28)
        p.setPen(QPen(QColor(mix(pal.border, color.name(), 0.55)), 1.2))
        p.setBrush(QColor(pal.surface))
        p.drawEllipse(br)
        icon(CHANNEL_ICON.get(chan, "ph.link"), color.name()).paint(p, br.adjusted(6, 6, -6, -6).toRect())
