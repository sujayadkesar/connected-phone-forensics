"""PDF summary: a short, readable account of the phones found, to draft a report from.

Page 1 gives the case, the numbers at a glance, every phone in one table and the computers
examined. Each phone then gets a page: a drawing of it, who and what it is, its identifiers
with what they tell, how it connected and how much of each kind of data was recovered. A last
page states the method and limits. The detailed, record-by-record account stays in
report.html, workbook.xlsx and the timeline.

Drawn with Qt's PDF writer: text stays selectable, any script is shaped and falls back to an
installed font that has it (device and contact names are often not in Latin script), and the
logo and phone drawings are vector graphics.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__, content
from ..casedata import CaseData, os_label
from ..content import CATEGORIES

NAVY, NAVY_2, BRAND, BRAND_LIGHT, SKY, YELLOW = "#0B1630", "#172C55", "#2456DE", "#5C95FF", "#7CCBFF", "#FFC23D"
INK, MUTED, FAINT, LINE, SOFT, ZEBRA = "#0E1A33", "#56657F", "#93A0B8", "#E1E7F1", "#F4F7FC", "#F8FAFD"
OK, WARN = "#1A8F3C", "#B26B00"
SKY_DARK = "#0E8FB5"
CATEGORY_COLOR = {"messages": "#2456DE", "calls": "#1A9E70", "contacts": "#7C5CE0", "media": "#E5671E",
                  "notifications": "#D9A000", "apps": "#0E8FB5", "connections": "#5B6B88"}

_APP = None


def _ensure_app():
    """Qt needs an application object for fonts; the GUI has one, the command line gets one here."""
    global _APP
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        if sys.platform == "win32":
            os.environ.setdefault("QT_QPA_PLATFORM", "windows")
        elif sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _APP = app = QApplication([sys.argv[0] if sys.argv else "cpf"])
    return app


# Windows font files for Qt's "offscreen" platform, which does not see the installed fonts: the
# UI and code fonts, and fonts for scripts other than Latin (Indic, CJK, Thai, Ethiopic...).
_WINDOWS_FONT_FILES = ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "segoeuii.ttf", "consola.ttf", "consolab.ttf",
                       "CascadiaMono.ttf", "Nirmala.ttc", "msyh.ttc", "malgun.ttf", "YuGothR.ttc", "LeelawUI.ttf",
                       "ebrima.ttf", "gadugi.ttf", "mmrtext.ttf", "himalaya.ttf", "seguisym.ttf", "arial.ttf")
_FONTS_REGISTERED = False


def _register_fonts() -> None:
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    _FONTS_REGISTERED = True
    from PySide6.QtGui import QFontDatabase, QGuiApplication

    if sys.platform != "win32" or QGuiApplication.platformName() != "offscreen":
        return
    fonts = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
    for name in _WINDOWS_FONT_FILES:
        if (fonts / name).is_file():
            QFontDatabase.addApplicationFont(str(fonts / name))


def _family(candidates: list[str], fallback: str) -> str:
    from PySide6.QtGui import QFontDatabase

    _register_fonts()
    available = set(QFontDatabase.families())
    return next((f for f in candidates if f in available), fallback)


@dataclass
class Style:
    size: float
    weight: str = "normal"   # normal | medium | semibold | bold
    color: str = INK
    mono: bool = False
    italic: bool = False
    caps: bool = False       # letter-spaced capitals (overlines)


S = {
    "h1": Style(20, "semibold"), "h2": Style(13, "semibold"), "h3": Style(10.5, "semibold"),
    "body": Style(9), "body_b": Style(9, "semibold"), "small": Style(8, color=MUTED),
    "small_b": Style(8, "semibold"), "tiny": Style(7, color=MUTED), "mono": Style(8, mono=True),
    "mono_s": Style(7.4, mono=True, color=MUTED), "overline": Style(7, "bold", MUTED, caps=True),
    "th": Style(7.6, "bold", "#FFFFFF"), "kpi": Style(19, "semibold"), "kpi_label": Style(8, "medium", MUTED),
    "cover_over": Style(7.5, "bold", SKY, caps=True), "cover_title": Style(22, "semibold", "#FFFFFF"),
    "cover_sub": Style(9, color="#C9D6F0"), "foot": Style(7, color=FAINT), "chip": Style(7, "bold"),
}


@dataclass
class Cell:
    lines: list[tuple[str, str]] = field(default_factory=list)   # (text, style key)

    @classmethod
    def of(cls, *parts) -> Cell:
        return cls([(str(t), s) for t, s in parts if t not in (None, "")])


class Doc:
    """A4 pages drawn top to bottom on a PDF writer (or on images, for previews).

    Positions are in points; ``U`` device units make a point. ``dry`` runs only measure, to
    count the pages before the real run writes "page X of Y".
    """

    def __init__(self, device, *, U: float, dry: bool, total_pages: int, footer: Callable[[Doc], None],
                 next_device: Callable[[], object] | None = None):

        self.device, self.U, self.dry, self.total = device, U, dry, total_pages
        self.next_device = next_device
        self.W, self.H = 595.28, 841.89
        self.M = 42.0                      # side margin
        self.top, self.bottom = 46.0, self.H - 52.0
        self.y = self.top
        self.page = 1
        self.footer = footer
        self.ui = _family(["Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue", "Noto Sans", "DejaVu Sans",
                           "Arial"], "Sans Serif")
        self.mono = _family(["Cascadia Mono", "Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono",
                             "Liberation Mono", "Courier New"], "Monospace")
        self.p = None
        if not dry:
            # No painter scaling: Qt sizes fonts from the device's resolution, so coordinates (in
            # points) are converted to device units instead, and text lays out at full resolution.
            self._begin(device)

    def _begin(self, device) -> None:
        from PySide6.QtGui import QPainter

        self.p = QPainter(device)
        self.p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # -- fonts and text -------------------------------------------------------------------

    def font(self, key: str):
        from PySide6.QtGui import QFont

        st = S[key] if isinstance(key, str) else key
        f = QFont(self.mono if st.mono else self.ui)
        f.setPointSizeF(st.size)
        f.setWeight({"normal": QFont.Weight.Normal, "medium": QFont.Weight.Medium,
                     "semibold": QFont.Weight.DemiBold, "bold": QFont.Weight.Bold}[st.weight])
        f.setItalic(st.italic)
        f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        if st.caps:
            f.setCapitalization(QFont.Capitalization.AllUppercase)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.9 * self.U)
        return f

    def _flags(self, align: str, wrap: str):
        from PySide6.QtCore import Qt

        a = {"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter,
             "right": Qt.AlignmentFlag.AlignRight}[align] | Qt.AlignmentFlag.AlignTop
        w = {"word": Qt.TextFlag.TextWordWrap, "any": Qt.TextFlag.TextWrapAnywhere, "none": 0, "elide": 0}[wrap]
        return int(a) | int(w)

    def measure(self, text: str, key: str, width: float, wrap: str = "word") -> float:
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QFontMetricsF

        if not text:
            return 0.0
        fm = QFontMetricsF(self.font(key), self.device)  # device units
        r = fm.boundingRect(QRectF(0, 0, width * self.U, 1e8), self._flags("left", wrap), text)
        return r.height() / self.U

    def text(self, x: float, y: float, w: float, text: str, key: str = "body", *, align: str = "left",
             wrap: str = "word", color: str | None = None) -> float:
        if wrap == "elide" and text:
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QFontMetricsF

            text = QFontMetricsF(self.font(key), self.device).elidedText(text, Qt.TextElideMode.ElideRight,
                                                                         w * self.U)
        h = self.measure(text, key, w, wrap)
        if self.p is not None and text:
            from PySide6.QtCore import QRectF
            from PySide6.QtGui import QColor

            st = S[key]
            self.p.setFont(self.font(key))
            self.p.setPen(QColor(color or st.color))
            u = self.U
            self.p.drawText(QRectF(x * u, y * u, w * u, (h + 2) * u), self._flags(align, wrap), text)
        return h

    def width_of(self, text: str, key: str) -> float:
        from PySide6.QtGui import QFontMetricsF

        return QFontMetricsF(self.font(key), self.device).horizontalAdvance(text) / self.U

    # -- shapes ---------------------------------------------------------------------------

    def rect(self, x, y, w, h, fill: str | None = None, *, radius: float = 0, stroke: str | None = None,
             width: float = 0.6, alpha: int = 255) -> None:
        if self.p is None:
            return
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QColor, QPen

        c = QColor(fill) if fill else None
        if c is not None:
            c.setAlpha(alpha)
        u = self.U
        self.p.setBrush(c if c is not None else Qt.BrushStyle.NoBrush)
        self.p.setPen(QPen(QColor(stroke), width * u) if stroke else Qt.PenStyle.NoPen)
        if radius:
            self.p.drawRoundedRect(QRectF(x * u, y * u, w * u, h * u), radius * u, radius * u)
        else:
            self.p.drawRect(QRectF(x * u, y * u, w * u, h * u))

    def line(self, x1, y1, x2, y2, color: str = LINE, width: float = 0.6) -> None:
        if self.p is None:
            return
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QColor, QPen

        u = self.U
        self.p.setPen(QPen(QColor(color), width * u))
        self.p.drawLine(QPointF(x1 * u, y1 * u), QPointF(x2 * u, y2 * u))

    def svg(self, name: str, x: float, y: float, w: float) -> float:
        """Draw a packaged SVG ``w`` points wide; returns its height."""
        from PySide6.QtCore import QRectF
        from PySide6.QtSvg import QSvgRenderer

        r = QSvgRenderer(str(Path(__file__).resolve().parents[1] / "assets" / name))
        if not r.isValid():
            return 0.0
        box = r.viewBoxF()
        h = w * box.height() / box.width() if box.width() else w
        if self.p is not None:
            u = self.U
            r.render(self.p, QRectF(x * u, y * u, w * u, h * u))
        return h

    def chip(self, x: float, y: float, text: str, color: str) -> float:
        tw = self.width_of(text, "chip")
        w, h = tw + 12, 13
        if self.p is not None:
            self.rect(x, y, w, h, color, radius=6.5, alpha=30)
            self.rect(x, y, w, h, None, radius=6.5, stroke=color, width=0.5)
        self.text(x, y + 2.6, w, text, "chip", align="center", wrap="none", color=color)
        return w

    # -- pages ----------------------------------------------------------------------------

    @property
    def inner(self) -> float:
        return self.W - 2 * self.M

    def new_page(self) -> None:
        self.footer(self)
        if self.p is not None:
            if self.next_device is not None:  # one image per page
                self.p.end()
                self.device = self.next_device()
                self._begin(self.device)
            else:
                self.device.newPage()
        self.page += 1
        self.y = self.top

    def need(self, h: float) -> None:
        if self.y + h > self.bottom:
            self.new_page()

    def finish(self) -> None:
        self.footer(self)
        if self.p is not None:
            self.p.end()

    # -- blocks ---------------------------------------------------------------------------

    def section(self, title: str, note: str = "", keep: float = 64) -> None:
        """A section heading with its note; ``keep``: room to leave for what follows, so a heading never
        ends a page alone."""
        note_h = self.measure(note, "small", self.inner) + 4 if note else 0
        self.need(max(60.0, 28 + note_h + keep))
        self.y += 6
        self.rect(self.M, self.y + 1.5, 3, 12, YELLOW, radius=1.5)
        self.text(self.M + 10, self.y, self.inner - 10, title, "h2")
        self.y += 19
        if note:
            self.y += self.text(self.M, self.y, self.inner, note, "small") + 4
        self.y += 3

    def paragraph(self, text: str, key: str = "body", gap: float = 5) -> None:
        h = self.measure(text, key, self.inner)
        self.need(h + gap)
        self.text(self.M, self.y, self.inner, text, key)
        self.y += h + gap

    def bullets(self, items: list[str], key: str = "body") -> None:
        for item in items:
            h = self.measure(item, key, self.inner - 14)
            self.need(h + 4)
            self.rect(self.M + 3, self.y + 4.2, 3.2, 3.2, BRAND, radius=1.6)
            self.text(self.M + 14, self.y, self.inner - 14, item, key)
            self.y += h + 4

    def table(self, columns: list[tuple[str, float, str]], rows: list[list[Cell]], *, zebra: bool = True,
              row_fill: Callable[[int], str | None] | None = None) -> None:
        """``columns``: (title, share of the width, alignment). Rows split across pages; the header repeats."""
        total = sum(c[1] for c in columns)
        widths = [self.inner * c[1] / total for c in columns]
        pad_x, pad_y = 6.0, 4.6

        def header() -> None:
            hh = max(self.measure(t, "th", w - 2 * pad_x) for (t, _s, _a), w in zip(columns, widths, strict=True))
            h = hh + 2 * pad_y + 1
            self.rect(self.M, self.y, self.inner, h, NAVY_2, radius=3)
            x = self.M
            for (title, _s, align), w in zip(columns, widths, strict=True):
                self.text(x + pad_x, self.y + pad_y + 0.5, w - 2 * pad_x, title, "th", align=align)
                x += w
            self.y += h

        def cell_height(cell: Cell, w: float) -> float:
            return sum(self.measure(t, k, w - 2 * pad_x, "any" if S[k].mono else "word") + 1.2
                       for t, k in cell.lines)

        self.need(60)
        header()
        for i, row in enumerate(rows):
            h = max((cell_height(c, w) for c, w in zip(row, widths, strict=True)), default=10) + 2 * pad_y
            if self.y + h > self.bottom:
                self.new_page()
                header()
            fill = row_fill(i) if row_fill else None
            if fill:
                self.rect(self.M, self.y, self.inner, h, fill)
            elif zebra and i % 2 == 1:
                self.rect(self.M, self.y, self.inner, h, ZEBRA)
            x = self.M
            for (_t, _s, align), c, w in zip(columns, row, widths, strict=True):
                yy = self.y + pad_y
                for text, key in c.lines:
                    yy += self.text(x + pad_x, yy, w - 2 * pad_x, text, key, align=align,
                                    wrap="any" if S[key].mono else "word") + 1.2
                x += w
            self.y += h
            self.line(self.M, self.y, self.M + self.inner, self.y, LINE, 0.5)
        self.y += 10


# -- the document --------------------------------------------------------------------------


def _fmt(value: str | None) -> str:
    return content.format_time(value) or "—"


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


_CAT_WORD = {"messages": ("message", "messages"), "calls": ("call", "calls"), "contacts": ("contact", "contacts"),
             "media": ("photo or file", "photos and files"), "notifications": ("notification", "notifications"),
             "apps": ("app", "apps"), "connections": ("connection record", "connection records")}


def _recovered_line(c: Counter) -> str:
    parts = [f"{c[k]:,} {_CAT_WORD[k][0] if c[k] == 1 else _CAT_WORD[k][1]}"
             for k in ("messages", "calls", "contacts", "media", "notifications", "apps") if c.get(k)]
    return " · ".join(parts) or "connection records only"


def write_pdf(case: CaseData, path: Path, items: dict[str, list[dict]] | None = None) -> Path:
    """Write the PDF summary of ``case`` to ``path``."""
    _ensure_app()
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    items = items if items is not None else content.extract(case.records, case.devices)

    def render(dry: bool, total: int) -> int:
        target = path.with_suffix(".tmp.pdf") if dry else path
        writer = QPdfWriter(str(target))
        writer.setResolution(720)
        writer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
                                         QMarginsF(0, 0, 0, 0)))
        writer.setTitle(f"Phone connection summary — {case.title}")
        writer.setCreator(f"Connected Phone Forensics {__version__}")
        doc = Doc(writer, U=10.0, dry=dry, total_pages=total, footer=lambda d: _footer(d, case))
        pages = _document(doc, case, items)
        del doc, writer
        if dry:
            with contextlib.suppress(OSError):
                target.unlink()
        return pages

    total = render(True, 0)
    render(False, total)
    return path


def _document(doc: Doc, case: CaseData, items: dict[str, list[dict]]) -> int:
    _cover(doc, case, items)
    phones = case.phones
    for n, dev in enumerate(phones, 1):
        doc.new_page()
        _phone_page(doc, case, dev, items, n, len(phones))
    doc.new_page()
    _method_page(doc, case)
    doc.finish()
    return doc.page


def preview_pages(case: CaseData, dpi: int = 110, items: dict[str, list[dict]] | None = None) -> list:
    """The summary's pages as images (QImage), for previews and screenshots."""
    _ensure_app()
    from PySide6.QtGui import QColor, QImage

    items = items if items is not None else content.extract(case.records, case.devices)
    U = dpi / 72

    def page_image(pages: list) -> QImage:
        img = QImage(round(595.28 * U), round(841.89 * U), QImage.Format.Format_ARGB32)
        img.setDotsPerMeterX(round(dpi / 0.0254))
        img.setDotsPerMeterY(round(dpi / 0.0254))
        img.fill(QColor("#FFFFFF"))
        pages.append(img)
        return img

    scratch: list = []
    total = _document(Doc(page_image(scratch), U=U, dry=True, total_pages=0, footer=lambda d: _footer(d, case)),
                      case, items)
    pages: list = []
    _document(Doc(page_image(pages), U=U, dry=False, total_pages=total, footer=lambda d: _footer(d, case),
                  next_device=lambda: page_image(pages)), case, items)
    return pages


def _footer(d: Doc, case: CaseData) -> None:
    y = d.H - 34
    d.line(d.M, y, d.W - d.M, y, LINE, 0.5)
    d.text(d.M, y + 6, 220, f"Connected Phone Forensics {case.tool_version or __version__}", "foot")
    d.text(d.M + 140, y + 6, d.inner - 280, case.title, "foot", align="center", wrap="elide")
    d.text(d.W - d.M - 160, y + 6, 160, f"Page {d.page} of {d.total or d.page}", "foot", align="right")


def _cover(d: Doc, case: CaseData, items: dict[str, list[dict]]) -> None:
    band = 168.0
    d.rect(0, 0, d.W, band, NAVY)
    d.rect(0, band - 3, d.W, 3, YELLOW)
    if d.p is not None:  # soft circles, echoing the logo's screen
        d.rect(d.W - 150, -60, 230, 230, BRAND, radius=115, alpha=40)
        d.rect(d.W - 70, 70, 120, 120, SKY, radius=60, alpha=22)
    d.svg("lockup-two-line-dark.svg", d.M - 4, 30, 176)
    d.text(d.M, 104, 300, "Phone connection summary", "cover_over")
    d.text(d.M, 116, d.inner - 40, case.title, "cover_title", wrap="elide")
    bits = []
    if case.case.get("examiner"):
        bits.append(f"Examiner {case.case['examiner']}")
    if case.started:
        bits.append(f"scanned {_fmt(case.started)} UTC")
    bits.append(f"written {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    d.text(d.M, 145, d.inner, " · ".join(bits), "cover_sub", wrap="elide")
    d.y = band + 22

    per = content.counts(items)
    phones = case.phones
    confirmed = sum(1 for p in phones if p.get("confirmed"))
    deleted = content.recovered_deleted(items)
    d.section("At a glance")
    hotspots = case.hotspots
    sessions = sum(len(n["sessions"]) for n in hotspots)
    cards = [("Phones found", len(phones), f"{confirmed} confirmed by a strong identifier", BRAND),
             ("Hotspot networks", len(hotspots), f"{_plural(sessions, 'connection')} to phone hotspots"
              if hotspots else "none joined", SKY_DARK),
             ("Computers examined", len(case.systems), ", ".join(s["label"] for s in case.systems[:3])
              + (" …" if len(case.systems) > 3 else ""), NAVY_2)]
    for cat in CATEGORIES[:6]:
        rows = items[cat.key]
        rec = sum(1 for r in rows if r.get("status"))
        phones_with = len({r["device"] for r in rows if r["device"]})
        sub = (f"{rec} recovered after deletion" if rec else
               f"from {_plural(phones_with, 'phone')}" if phones_with else
               "not tied to a phone" if rows else "none recovered")
        cards.append((cat.title, len(rows), sub, CATEGORY_COLOR[cat.key]))
    cards.append(("Recovered after deletion", deleted, "deleted records found again" if deleted else
                  "none found", WARN))
    cols, gap, ch = 5, 8.0, 62.0
    cw = (d.inner - gap * (cols - 1)) / cols
    for i, (title, n, sub, color) in enumerate(cards):
        r, c = divmod(i, cols)
        x, y = d.M + c * (cw + gap), d.y + r * (ch + gap)
        d.rect(x, y, cw, ch, "#FFFFFF", radius=7, stroke=LINE, width=0.7)
        d.rect(x, y + 10, 3, ch - 20, color, radius=1.5)
        d.text(x + 12, y + 8, cw - 18, f"{n:,}", "kpi")
        d.text(x + 12, y + 35, cw - 18, title, "kpi_label", wrap="elide")
        d.text(x + 12, y + 47, cw - 18, sub, "tiny", wrap="elide")
    d.y += ((len(cards) + cols - 1) // cols) * (ch + gap) + 6
    if deleted:
        d.paragraph(f"{deleted:,} of the items were recovered from deleted or overwritten database records; "
                    "they are marked in the workbook and the report.", "small", 8)

    d.section("Phones", "Each phone found, the identifier that names it best, where and when it was seen, and "
                        "what was recovered. Phones are kept apart unless a strong identifier ties them together.")
    rows = []
    for n, dev in enumerate(phones, 1):
        f = content.phone_facts(dev)
        rows.append([
            Cell.of((str(n), "small_b")),
            Cell.of((f["label"], "body_b"), (" · ".join(x for x in (f["os"], f["model"]) if x), "small"),
                    (dev["id"] + ("" if dev.get("confirmed") else " · unconfirmed"), "tiny")),
            Cell.of((f["primary_label"], "small_b"), (f["primary_value"], "mono")),
            Cell.of((", ".join(f["computers"]) or "—", "small")),
            Cell.of((_fmt(dev.get("first_seen")), "small"), (_fmt(dev.get("last_seen")), "small")),
            Cell.of((", ".join(f["connections"]) or "—", "small")),
            Cell.of((_recovered_line(per.get(dev["id"], Counter())), "small")),
        ])
    if rows:
        d.table([("#", 0.48, "center"), ("Phone", 2.3, "left"), ("Main identifier", 2.25, "left"),
                 ("Seen on", 1.35, "left"), ("First / last seen (UTC)", 1.55, "left"),
                 ("Connected by", 1.3, "left"), ("Recovered", 1.95, "left")], rows)
    else:
        d.paragraph("No phones were identified in this evidence.", "body", 10)
    if hotspots:
        _hotspot_section(d, hotspots)
    unlinked = per.get(None)
    if unlinked:
        d.paragraph(f"{sum(unlinked.values()):,} recovered item(s) could not be tied to a particular phone "
                    "(for example Phone Link notifications); they are listed in the workbook.", "small", 8)

    d.section("Computers examined")
    d.table([("Computer", 1.4, "left"), ("System", 2.6, "left"), ("User profiles", 2.0, "left")],
            [[Cell.of((s.get("label") or "—", "body_b")), Cell.of((_system_text(s), "small")),
              Cell.of((", ".join(s.get("users") or []) or "—", "small"))] for s in case.systems])
    if case.acquisition:
        a = case.acquisition
        d.paragraph(f"Acquired live from {a.get('computer')} ({a.get('windows') or 'Windows'}) on "
                    f"{_fmt(a.get('started'))} UTC: {a.get('files_acquired')} files and {a.get('hives_saved')} "
                    f"registry hives copied and hashed, {a.get('errors')} error(s). See the last page.", "small", 8)


def _hotspot_section(d: Doc, hotspots: list[dict]) -> None:
    """Phones known only by their hotspot: one table per network name, a row per access point."""
    d.section("Phone hotspots joined",
              "Phones the computers knew only as Wi-Fi hotspots: a network name and the access point "
              "address (BSSID). Android 11 and later, and recent iPhones, give the hotspot a new random "
              "address each time it is switched on, so one phone's hotspot appears as several access points "
              "with the same name; a phone that was renamed shows as one access point under several names. The "
              "same name does not prove the same phone.", keep=150)
    for net in hotspots:
        n = net["access_points"]
        likely = net["likely_phone"]
        os_name = {"ios": "iPhone (iOS)", "android": "Android"}.get(net["os"], "")
        head = f"“{net['ssid']}”"
        bits = [f"{_plural(n, 'access point')}"]
        if len(net["names"]) > 1:
            bits.append("also named " + ", ".join(f"“{x}”" for x in net["names"] if x != net["ssid"])
                        + (" (the phone was renamed)" if n == 1 else ""))
        if net["tethered"]:
            bits.append("also reached by cable or Bluetooth")
        if likely:
            bits.append(f"likely {likely} (from the network name)")
        elif os_name:
            bits.append(os_name)
        if net["randomised"] == n:
            bits.append("every address randomised: no maker can be named")
        bits.append(f"first {_fmt(net['first'])}, last {_fmt(net['last'])} UTC")
        h = d.measure(" · ".join(bits), "small", d.inner) + 26
        d.need(h + 60)
        d.y += 2
        d.text(d.M, d.y, d.inner, head, "h3")
        d.y += 15
        d.y += d.text(d.M, d.y, d.inner, " · ".join(bits), "small") + 5
        rows = []
        for s in net["sessions"]:
            rows.append([
                Cell.of((s["name"], "small"), (s["link"], "tiny")),
                Cell.of((s["bssid"] or "—", "mono"), (s["maker"], "tiny")),
                Cell.of((_fmt(s["first"]), "small")),
                Cell.of((_fmt(s["last"]), "small")),
                Cell.of((s["profiles"] or "—", "small"), (s["dhcp"], "tiny")),
            ])
        d.table([("Network name · joined by", 1.75, "left"), ("Access point (BSSID)", 1.75, "left"),
                 ("First connected (UTC)", 1.35, "left"), ("Last connected (UTC)", 1.35, "left"),
                 ("Windows profile · addresses", 1.9, "left")], rows)


def _system_text(s: dict) -> str:
    det = s.get("details") or {}
    product = det.get("product") or det.get("pretty_name") or (
        f"macOS {det['product_version']}" if det.get("product_version") else "")
    return " ".join(x for x in (os_label(s.get("os")), product if product and os_label(s.get("os")) not in product
                                else product.replace(os_label(s.get("os")), "").strip()) if x) or "—"


def _wallpaper(case: CaseData, dev: dict, items: dict[str, list[dict]]) -> Path | None:
    if case.outdir is None:
        return None
    for r in items["media"]:
        if r["device"] == dev["id"] and r["kind"] == "Wallpaper" and r.get("file"):
            from ..evidence import long_path

            p = Path(case.outdir) / "exports" / r["file"]
            if long_path(p).is_file():
                return p
    return None


def _phone_page(d: Doc, case: CaseData, dev: dict, items: dict[str, list[dict]], n: int, total: int) -> None:
    from PySide6.QtCore import QRectF

    from .. import phoneart

    f = content.phone_facts(dev)
    d.text(d.M, d.y, d.inner, f"PHONE {n} OF {total}", "overline")
    d.y += 12
    title_h = d.text(d.M, d.y, d.inner - 120, f["label"], "h1")
    d.chip(d.W - d.M - d.width_of("UNCONFIRMED", "chip") - 12, d.y + 5,
           "CONFIRMED" if dev.get("confirmed") else "UNCONFIRMED", OK if dev.get("confirmed") else WARN)
    d.y += title_h + 2
    d.y += d.text(d.M, d.y, d.inner, " · ".join(x for x in (f["os"], f["model"], dev["id"]) if x), "small") + 12

    # the phone, and the identity beside it
    art_w, art_h = 150.0, 300.0
    top = d.y
    if d.p is not None:
        u = d.U
        look = phoneart.look_for(dev, _wallpaper(case, dev, items), caption=f["model"] or f["os"])
        phoneart.paint_phone(d.p, QRectF((d.M - 6) * u, top * u, art_w * u, art_h * u), look, font_family=d.ui)
    x = d.M + art_w + 18
    w = d.inner - art_w - 18
    facts = [("Operating system", f["os"]), ("Model", f["model"] or "not recorded"),
             ("Name", ", ".join(f["names"]) or "not recorded"),
             ("Phone number", ", ".join(f["numbers"]) or "not recorded"),
             (f["primary_label"] or "Main identifier", f["primary_value"] or "—"),
             ("What it tells", f["primary_summary"] or "—"),
             ("Seen on", ", ".join(f["computers"]) or "—"),
             ("Local users", ", ".join(f["users"]) or "—"),
             ("First seen", f"{f['first_seen']} UTC" if f["first_seen"] else "no time recorded"),
             ("Last seen", f"{f['last_seen']} UTC" if f["last_seen"] else "no time recorded"),
             ("Connected by", ", ".join(f["connections"]) or "—")]
    label_w = 92.0
    laid = []
    for k, v in facts:
        mono = k == (f["primary_label"] or "Main identifier")
        laid.append((k, v, mono, d.measure(v, "mono" if mono else "body", w - label_w - 28,
                                          "any" if mono else "word")))
    panel_h = 12 + 14 + sum(max(h, 11) + 5 for *_r, h in laid) + 8
    d.rect(x, top, w, panel_h, SOFT, radius=8)
    yy = top + 12
    d.text(x + 14, yy, w - 28, "Identity", "overline")
    yy += 14
    for k, v, mono, h in laid:
        d.text(x + 14, yy + 0.6, label_w, k, "small")
        d.text(x + 14 + label_w, yy, w - label_w - 28, v, "mono" if mono else "body",
               wrap="any" if mono else "word")
        yy += max(h, 11) + 5
    d.y = top + max(art_h, panel_h) + 16

    d.section("Identifiers", "Every identifier recorded for this phone, decoded where its format tells more.")
    rows = []
    for i in f["identifiers"]:
        detail = "; ".join(f"{k}: {v}" for k, v in i["fields"][:4])
        rows.append([Cell.of((i["label"], "body_b"), (i["strength"], "tiny")),
                     Cell.of((i["value"], "mono")),
                     Cell.of((i["meaning"], "small"), (detail, "tiny"))])
    d.table([("Identifier", 1.3, "left"), ("Value", 2.0, "left"), ("What it tells", 3.2, "left")], rows)

    d.section("How it connected")
    d.table([("Connection", 1.5, "left"), ("First seen (UTC)", 1.5, "left"), ("Last seen (UTC)", 1.5, "left"),
             ("Records", 0.8, "right"), ("Found by", 2.4, "left")],
            [[Cell.of((content.CHANNEL_LABEL.get(c["channel"], c["channel"]), "body_b")),
              Cell.of((_fmt(c.get("first_seen")), "small")), Cell.of((_fmt(c.get("last_seen")), "small")),
              Cell.of((f"{c.get('observations', 0):,}", "small")),
              Cell.of((", ".join(c.get("artifacts") or []), "tiny"))] for c in dev.get("channels") or []])

    d.section("Recovered data", "What the computers held of this phone's own data. Each item, with the file and "
                                "record it came from, is in the workbook and the report.")
    rows = []
    for cat in CATEGORIES:
        mine = [r for r in items[cat.key] if r["device"] == dev["id"]]
        if not mine:
            continue
        times = sorted(r["time"] for r in mine if r.get("time") and r.get("time_source") != "filesystem")
        rec = sum(1 for r in mine if r.get("status"))
        rows.append([Cell.of((cat.title, "body_b"), (cat.description, "tiny")),
                     Cell.of((f"{len(mine):,}", "body_b")),
                     Cell.of((f"{rec:,}" if rec else "—", "small")),
                     Cell.of((_fmt(times[0]) if times else "—", "small")),
                     Cell.of((_fmt(times[-1]) if times else "—", "small"))])
    if rows:
        d.table([("Kind", 2.4, "left"), ("Items", 0.7, "right"), ("Recovered after deletion", 1.2, "right"),
                 ("Earliest (UTC)", 1.4, "left"), ("Latest (UTC)", 1.4, "left")], rows)
    else:
        d.paragraph("Nothing beyond the connection records was recovered for this phone.", "small", 8)

    matches = case.matches_for(dev["id"])
    if matches:
        others = []
        for m in matches:
            other = next((x for x in m.get("devices", []) if x != dev["id"]), "")
            od = case.device(other) or {}
            others.append(f"{od.get('label') or other} ({other}): {m.get('independent_signals', 0)} independent "
                          f"signal(s) — " + "; ".join(f"{s.get('kind')} {s.get('value')}" for s in m.get("signals", [])))
        d.section("May be the same phone as", "Not merged: shared names, numbers or related addresses only. "
                                              "Verify before relying on it.")
        d.bullets(others, "small")


def _method_page(d: Doc, case: CaseData) -> None:
    d.text(d.M, d.y, d.inner, "METHOD AND LIMITS", "overline")
    d.y += 12
    d.y += d.text(d.M, d.y, d.inner, "How this summary was made", "h1") + 10
    d.section("Evidence")
    lines = [f"Evidence examined: {case.input_path}.",
             "The evidence was read without changing it. Databases were copied with their journal files (-wal, "
             "-shm) and the copies opened, so recent records still in the journal are included; deleted records "
             "were recovered from free space in those databases and are marked as such.",
             "Registry hives with pending transaction logs were brought up to date on a copy. Every evidence file "
             "that was read is listed with its SHA-256 in manifest.json."]
    d.bullets(lines, "body")
    if case.acquisition:
        a = case.acquisition
        drive = a.get("destination_drive") or {}
        d.section("Live acquisition")
        d.bullets([
            f"Computer {a.get('computer')} ({a.get('windows') or 'Windows'}), acquired by {a.get('user')} "
            f"{'as administrator' if a.get('elevated') else 'without administrator rights'}, from "
            f"{_fmt(a.get('started'))} to {_fmt(a.get('finished'))} UTC.",
            "Registry hives Windows keeps locked were saved through the registry API (RegSaveKeyEx, as reg save "
            "does). Other files were read with the backup privilege, hashed while copied and given their original "
            "times.",
            f"{a.get('files_acquired')} files ({(a.get('bytes_acquired') or 0) / 1e6:,.1f} MB) and "
            f"{a.get('hives_saved')} hives acquired, {a.get('errors')} error(s). Output on {drive.get('root', '?')} "
            f"({drive.get('kind', '')} {drive.get('filesystem', '')}, serial {drive.get('serial', '')}). Every item "
            "and its SHA-256 is in acquisition.json and acquisition.sha256.",
            "Running a program leaves traces of its own on the examined computer (for example a Prefetch entry "
            "and event-log entries for the administrator prompt); they do not affect the files acquired.",
        ], "body")
    d.section("Identification")
    d.bullets([
        "Records are joined into one phone only through strong identifiers (UDID, serial number, IMEI, Bluetooth "
        "and Wi-Fi addresses, USB serial, IDs apps give the phone) recorded together in a single record.",
        "Names, models, phone numbers and accounts never join records: phones sharing them are listed as possible "
        "matches to verify. A phone identified only by such values is marked unconfirmed.",
        "Identifiers are decoded from their own structure and public tables (chip in an iPhone UDID, check "
        "digit and model code of an IMEI, maker of a MAC address); a randomised MAC address names no maker.",
    ], "body")
    d.section("Times")
    d.bullets([
        "All times are UTC. Each time states what it means (for example 'Last connected' or 'Message received') "
        "in the report and workbook.",
        "Times taken from file-system metadata depend on how the evidence was copied; they are marked and not "
        "used for a phone's first or last seen.",
    ], "body")
    d.section("Companion files")
    d.bullets([
        "report.html: the complete report, with every record, its source file, the record inside it and the "
        "file's SHA-256.",
        "workbook.xlsx: a summary sheet and one sheet per phone, with every recovered item in sortable tables.",
        "timeline.csv and timeline.jsonl (every timed record), devices.json (the phones and how they were "
        "identified), coverage.csv (every file examined) and manifest.json (hashes and run details).",
    ], "body")
    d.y += 6
    d.paragraph(f"Generated by Connected Phone Forensics {case.tool_version or __version__}. This summary supports, "
                "and does not replace, the examiner's own verification of the underlying records.", "small")
