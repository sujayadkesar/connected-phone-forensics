"""Build the app's brand assets from the logo kit in docs/brand.

    python scripts/make_brand_assets.py

- connected_phone_forensics/assets/*.svg: the kit's SVGs, with nested <svg> elements turned into
  transformed groups (Qt's SVG renderer, which implements SVG Tiny, silently drops nested <svg>).
- connected_phone_forensics/assets/cpf.ico and packaging/app.ico: the kit's Windows icon.
- docs/images/logo.png, lockup-light.png, lockup-dark.png: README images.
- docs/images/how-it-works.png: the README's flow diagram (PyPI cannot render Mermaid).
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BRAND = ROOT / "docs" / "brand"
ASSETS = ROOT / "connected_phone_forensics" / "assets"
IMAGES = ROOT / "docs" / "images"

SVGS = {
    "cpf-logo.svg": "logo.svg",
    "cpf-logo-small.svg": "logo-small.svg",
    "cpf-app-icon-dark.svg": "app-icon-dark.svg",
    "cpf-app-icon-light.svg": "app-icon-light.svg",
    "cpf-lockup-one-line-on-dark.svg": "lockup-dark.svg",
    "cpf-lockup-one-line-on-light.svg": "lockup-light.svg",
    "cpf-lockup-two-line-on-dark.svg": "lockup-two-line-dark.svg",
    "cpf-lockup-two-line-on-light.svg": "lockup-two-line-light.svg",
}

_NESTED = re.compile(r'<svg\s+x="([\d.]+)"\s+y="([\d.]+)"\s+width="([\d.]+)"\s+height="([\d.]+)"\s+'
                     r'viewBox="([\d.\s-]+)"\s*>')


_ATTR = re.compile(r'([\w-]+)="([^"]*)"')
_CLIP_DEF = re.compile(r'<clipPath id="([^"]+)">\s*<rect ([^>]*?)/?>\s*</clipPath>')
_CLIP_USE = re.compile(r'<g clip-path="url\(#([^)]+)\)">(.*?)</g>', re.S)
_RECT = re.compile(r"<rect ([^>]*?)/>")


def _rect(attrs: str) -> dict[str, str]:
    return dict(_ATTR.findall(attrs))


def _clipped_rect_path(r: dict[str, str], clip: dict[str, str]) -> str:
    """A rounded <rect> clipped to the horizontal span of a rounded clip rect, as a path.

    Valid only where the rect lies in the clip's straight-sided middle band (checked), so the
    clip's own corners never cut it: the result keeps the rect's corners on the side that is
    inside the clip and gets a straight edge where the clip cuts it.
    """
    x, y, w, h = (float(r[k]) for k in ("x", "y", "width", "height"))
    rx = float(r.get("rx", 0))
    cx, cy, cw, ch = (float(clip[k]) for k in ("x", "y", "width", "height"))
    crx = float(clip.get("rx", 0))
    if y < cy + crx or y + h > cy + ch - crx:
        raise ValueError("clipped rect reaches the clip's rounded corners; flattening would be wrong")
    left, right = max(x, cx), min(x + w, cx + cw)
    if right <= left:
        return ""
    keep_left, keep_right = left == x, right == x + w  # sides of the rect not cut by the clip
    d = [f"M {left + (rx if keep_left else 0):g} {y:g}", f"H {right - (rx if keep_right else 0):g}"]
    d += [f"A {rx:g} {rx:g} 0 0 1 {right:g} {y + rx:g}", f"V {y + h - rx:g}",
          f"A {rx:g} {rx:g} 0 0 1 {right - rx:g} {y + h:g}"] if keep_right else [f"H {right:g}", f"V {y + h:g}"]
    d += [f"H {left + (rx if keep_left else 0):g}"]
    d += [f"A {rx:g} {rx:g} 0 0 1 {left:g} {y + h - rx:g}", f"V {y + rx:g}",
          f"A {rx:g} {rx:g} 0 0 1 {left + rx:g} {y:g}"] if keep_left else [f"H {left:g}", f"V {y:g}"]
    extra = " ".join(f'{k}="{v}"' for k, v in r.items() if k not in ("x", "y", "width", "height", "rx"))
    return f'<path d="{" ".join(d)} Z" {extra}/>'


def resolve_clips(text: str) -> str:
    """Replace <g clip-path> groups of rounded rects by explicitly clipped paths (Qt has no clip-path)."""
    clips = {cid: _rect(attrs) for cid, attrs in _CLIP_DEF.findall(text)}

    def replace(m: re.Match) -> str:
        clip = clips[m.group(1)]
        rects = _RECT.findall(m.group(2))
        if _RECT.sub("", m.group(2)).strip():
            raise ValueError("clip group holds something other than rects")
        return "".join(_clipped_rect_path(_rect(a), clip) for a in rects)

    return _CLIP_USE.sub(replace, text)


def flatten_svg(text: str) -> str:
    """Replace each nested <svg x y width height viewBox> by an equivalent <g transform>."""
    text = resolve_clips(text)
    while True:
        m = _NESTED.search(text, 1)  # skip the root <svg> at position 0
        if not m:
            return text
        x, y, w, h = (float(v) for v in m.groups()[:4])
        vx, vy, vw, vh = (float(v) for v in m.group(5).split())
        scale = min(w / vw, h / vh)  # preserveAspectRatio="xMidYMid meet"
        dx = x + (w - vw * scale) / 2 - vx * scale
        dy = y + (h - vh * scale) / 2 - vy * scale
        close = text.index("</svg>", m.end())
        text = (text[:m.start()] + f'<g transform="matrix({scale:.6f} 0 0 {scale:.6f} {dx:.4f} {dy:.4f})">'
                + text[m.end():close] + "</g>" + text[close + len("</svg>"):])


STEPS = [
    ("Evidence folder", "image, collection or live PC"),
    ("Find each computer", "Windows · macOS · Linux"),
    ("23 parsers", "run in parallel"),
    ("Records", "file · locator · SHA-256"),
    ("Correlate", "strong identifiers only"),
    ("Device graph", "+ possible matches"),
    ("Reports", "HTML · PDF · Excel · JSON"),
]


def render_svg(svg: Path, width: int, out: Path) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    r = QSvgRenderer(str(svg))
    size = r.defaultSize()
    img = QImage(width, round(width * size.height() / size.width()), QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(p)
    p.end()
    if not img.save(str(out), "PNG"):
        raise OSError(f"could not write {out}")


def how_it_works(path: Path) -> None:
    """The README's flow diagram as an image, in the app's dark palette."""
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
        g.setBrush(QColor(theme.BRAND_YELLOW if i == 0 else theme.mix(p.raised, p.accent, 0.4)))
        g.drawEllipse(badge)
        g.setPen(QColor(theme.BRAND_NAVY if i == 0 else p.text))
        g.setFont(num_font)
        g.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(i + 1))
        for font, text in ((title_font, title), (sub_font, sub)):
            if QFontMetrics(font).horizontalAdvance(text) > r.width() - 32:
                raise ValueError(f"{text!r} does not fit its box; widen the boxes or shorten the text")
        g.setPen(QColor(p.text))
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
        raise OSError(f"could not write {path}")


def main() -> int:
    # Windows' own platform plugin: the offscreen one falls back to an odd font for text.
    os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "offscreen")
    from PySide6.QtWidgets import QApplication

    _app = QApplication(sys.argv[:1])
    ASSETS.mkdir(parents=True, exist_ok=True)
    for src, dst in SVGS.items():
        flat = flatten_svg((BRAND / src).read_text(encoding="utf-8"))
        if "<svg" in flat[1:]:
            raise ValueError(f"{src}: nested <svg> left after flattening")
        (ASSETS / dst).write_text(flat, encoding="utf-8", newline="\n")
    shutil.copyfile(BRAND / "cpf.ico", ASSETS / "cpf.ico")
    shutil.copyfile(BRAND / "cpf.ico", ROOT / "packaging" / "app.ico")
    IMAGES.mkdir(parents=True, exist_ok=True)
    render_svg(ASSETS / "logo.svg", 512, IMAGES / "logo.png")
    render_svg(ASSETS / "lockup-light.svg", 1600, IMAGES / "lockup-light.png")
    render_svg(ASSETS / "lockup-dark.svg", 1600, IMAGES / "lockup-dark.png")
    how_it_works(IMAGES / "how-it-works.png")
    print("wrote assets/*.svg, cpf.ico, packaging/app.ico, docs/images/{logo,lockup-*,how-it-works}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
