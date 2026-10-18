"""Render the logo and the Windows icon from the app's own drawing code.

    python scripts/make_brand_assets.py

Writes docs/images/logo.png (512 px) and packaging/app.ico (16-256 px, PNG-compressed).
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def png_bytes(pixmap) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buf, "PNG")
    return bytes(data)


def write_ico(path: Path, images: list[tuple[int, bytes]]) -> None:
    """An ICO with one PNG-compressed image per size (supported since Windows Vista)."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    path.write_bytes(header + entries + blobs)


STEPS = [
    ("Evidence folder", "image or collection"),
    ("Find each computer", "Windows · macOS · Linux"),
    ("23 parsers", "run in parallel"),
    ("Records", "file · locator · SHA-256"),
    ("Correlate", "strong identifiers only"),
    ("Device graph", "+ possible matches"),
    ("Outputs", "report · timeline · JSON"),
]


def how_it_works(path: Path) -> None:
    """The README's flow diagram as an image (PyPI cannot render Mermaid)."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import (
        QColor,
        QFont,
        QFontMetrics,
        QImage,
        QPainter,
        QPainterPath,
        QPen,
        QPolygonF,
    )

    from connected_phone_forensics.gui import theme

    p = theme.DARK
    fonts = theme.load_fonts()
    w, h, scale = 1200, 400, 2
    box_w, box_h, gap, top = 240, 104, 52, 44
    img = QImage(w * scale, h * scale, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    g = QPainter(img)
    g.scale(scale, scale)
    g.setRenderHint(QPainter.RenderHint.Antialiasing)
    card = QPainterPath()
    card.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 16, 16)
    g.fillPath(card, QColor(p.surface))
    g.setPen(QPen(QColor(p.border), 1))
    g.drawPath(card)

    def box_rect(i: int) -> QRectF:
        row, col = (0, i) if i < 4 else (1, i - 4 + 0.5)
        x = (w - (4 * box_w + 3 * gap)) / 2 + col * (box_w + gap)
        return QRectF(x, top + row * (box_h + 88), box_w, box_h)

    def arrow(points: list[QPointF]) -> None:
        g.setPen(QPen(QColor(p.accent), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        for a, b in zip(points, points[1:], strict=False):
            g.drawLine(a, b)
        tip, prev = points[-1], points[-2]
        d = tip - prev
        length = max((d.x() ** 2 + d.y() ** 2) ** 0.5, 1)
        ux, uy = d.x() / length, d.y() / length
        head = QPolygonF([tip, QPointF(tip.x() - 9 * ux - 5 * uy, tip.y() - 9 * uy + 5 * ux),
                          QPointF(tip.x() - 9 * ux + 5 * uy, tip.y() - 9 * uy - 5 * ux)])
        g.setBrush(QColor(p.accent))
        g.setPen(Qt.PenStyle.NoPen)
        g.drawPolygon(head)

    title_font = QFont(fonts.ui)
    title_font.setPixelSize(17)
    title_font.setWeight(QFont.Weight.DemiBold)
    sub_font = QFont(fonts.ui)
    sub_font.setPixelSize(13)
    num_font = QFont(fonts.ui)
    num_font.setPixelSize(12)
    num_font.setWeight(QFont.Weight.Bold)
    for i, (title, sub) in enumerate(STEPS):
        r = box_rect(i)
        shape = QPainterPath()
        shape.addRoundedRect(r, 12, 12)
        g.fillPath(shape, QColor(p.raised))
        g.setPen(QPen(QColor(p.border), 1))
        g.setBrush(Qt.BrushStyle.NoBrush)
        g.drawPath(shape)
        badge = QRectF(r.left() + 16, r.top() + 16, 24, 24)
        g.setPen(Qt.PenStyle.NoPen)
        g.setBrush(QColor(theme.mix(p.raised, p.accent, 0.35)))
        g.drawEllipse(badge)
        g.setPen(QColor(p.text))
        g.setFont(num_font)
        g.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(i + 1))
        for font, text in ((title_font, title), (sub_font, sub)):
            if QFontMetrics(font).horizontalAdvance(text) > r.width() - 32:
                raise ValueError(f"{text!r} does not fit its box; widen the boxes or shorten the text")
        g.setFont(title_font)
        g.drawText(QRectF(r.left() + 16, r.top() + 46, r.width() - 32, 26),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        g.setPen(QColor(p.muted))
        g.setFont(sub_font)
        g.drawText(QRectF(r.left() + 16, r.top() + 72, r.width() - 32, 20),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, sub)
        if i in (1, 2, 3, 5, 6):
            prev = box_rect(i - 1)
            y = prev.center().y()
            arrow([QPointF(prev.right() + 8, y), QPointF(r.left() - 8, y)])
        elif i == 4:  # from the end of the first row down to the start of the second
            prev = box_rect(3)
            mid_y = prev.bottom() + 44
            arrow([QPointF(prev.center().x(), prev.bottom() + 8), QPointF(prev.center().x(), mid_y),
                   QPointF(r.center().x(), mid_y), QPointF(r.center().x(), r.top() - 8)])
    g.end()
    img.setDevicePixelRatio(scale)
    if not img.save(str(path), "PNG"):
        raise OSError(f"could not write {path} (image {img.width()}x{img.height()}, null={img.isNull()})")


def main() -> int:
    # Windows' own platform plugin: the offscreen one falls back to an odd font for text.
    os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "offscreen")
    from PySide6.QtWidgets import QApplication

    _app = QApplication(sys.argv[:1])
    from connected_phone_forensics.gui.icons import logo_pixmap

    (ROOT / "docs" / "images").mkdir(parents=True, exist_ok=True)
    logo_pixmap(512).save(str(ROOT / "docs" / "images" / "logo.png"), "PNG")
    write_ico(ROOT / "packaging" / "app.ico", [(s, png_bytes(logo_pixmap(s))) for s in SIZES])
    how_it_works(ROOT / "docs" / "images" / "how-it-works.png")
    print("wrote docs/images/logo.png, docs/images/how-it-works.png and packaging/app.ico")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
