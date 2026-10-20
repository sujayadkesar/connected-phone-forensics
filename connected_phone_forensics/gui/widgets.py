"""Reusable building blocks: cards, chips, stat tiles, search, segmented filters, drop zone, toast.

Widgets read colours from :data:`THEME` when they paint, and icons are re-tinted
when the theme changes, so switching between light and dark needs no rebuild.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .icons import icon
from .theme import Palette

# -- theme state ------------------------------------------------------------------


class _Theme:
    def __init__(self) -> None:
        self.palette: Palette = theme.DARK
        self.fonts: theme.Fonts | None = None
        self._bound: list[tuple[QWidget, str, str, int]] = []
        self._listeners: list[Callable[[], None]] = []

    def color(self, role: str) -> str:
        return getattr(self.palette, role)

    def bind_icon(self, widget: QAbstractButton | QLabel, name: str, role: str = "muted", size: int = 16) -> None:
        """Give ``widget`` an icon that follows the theme."""
        self._apply_icon(widget, name, role, size)
        self._bound.append((widget, name, role, size))
        widget.destroyed.connect(lambda *_: self._forget(widget))

    def _forget(self, widget: QWidget) -> None:
        self._bound = [b for b in self._bound if b[0] is not widget]

    def _apply_icon(self, widget, name: str, role: str, size: int) -> None:
        ic = icon(name, self.color(role))
        if isinstance(widget, QLabel):
            widget.setPixmap(ic.pixmap(QSize(size, size), widget.devicePixelRatioF()))
        else:
            widget.setIcon(ic)
            widget.setIconSize(QSize(size, size))

    def on_change(self, fn: Callable[[], None], owner: QWidget | None = None) -> None:
        """Call ``fn`` after a theme change. With ``owner``, the call goes when the owner is deleted
        (pages recreate widgets as the examiner moves around; a deleted one must not stop a change)."""
        self._listeners.append(fn)
        if owner is not None:
            owner.destroyed.connect(lambda *_: self._drop(fn))

    def _drop(self, fn: Callable[[], None]) -> None:
        with contextlib.suppress(ValueError):
            self._listeners.remove(fn)

    def set(self, p: Palette) -> None:
        self.palette = p
        for widget, name, role, size in list(self._bound):
            try:
                self._apply_icon(widget, name, role, size)
            except RuntimeError:  # widget already deleted
                self._forget(widget)
        for fn in list(self._listeners):
            try:
                fn()
            except RuntimeError:  # its widget is gone
                self._drop(fn)


THEME = _Theme()


def mono_font() -> QFont:
    return THEME.fonts.mono if THEME.fonts else QFont("Consolas")


# -- small helpers --------------------------------------------------------------------


def breakable(text: str) -> str:
    """Let a long path, locator or hash wrap: zero-width spaces after separators and in long runs."""
    out, run = [], 0
    for ch in text:
        out.append(ch)
        run = 0 if ch.isspace() else run + 1
        if ch in "/\\:&_=.,;|" or run >= 24:
            out.append("\u200b")
            run = 0
    return "".join(out)


def label(text: str = "", role: str | None = None, *, wrap: bool = False, select: bool = False,
          mono: bool = False) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setObjectName(role)
    lb.setWordWrap(wrap)
    if select:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lb.setCursor(Qt.CursorShape.IBeamCursor)
    if mono:
        lb.setFont(mono_font())
    return lb


def overline(text: str) -> QLabel:
    lb = label(text.upper(), "Overline")
    f = lb.font()
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
    lb.setFont(f)
    return lb


def hbox(*items, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        _add(lay, it)
    return lay


def vbox(*items, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        _add(lay, it)
    return lay


def _add(lay, it) -> None:
    stretch = 0
    if isinstance(it, tuple):
        it, stretch = it
    if it is None:
        return
    if isinstance(it, QLayout):
        lay.addLayout(it, stretch)
        return
    if isinstance(it, QWidget):
        lay.addWidget(it, stretch)
        return
    if it == "stretch":
        lay.addStretch(1)
    elif isinstance(it, int):
        lay.addSpacing(it)
    elif isinstance(it, QLayout):
        lay.addLayout(it)
    else:
        lay.addWidget(it)


def button(text: str, kind: str | None = None, icon_name: str | None = None,
           on_click: Callable[[], None] | None = None, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if icon_name:
        role = "accent_text" if kind == "Primary" else "bad" if kind == "Danger" else \
            "accent" if kind == "Link" else "text"
        THEME.bind_icon(b, icon_name, role, 15)
    if on_click:
        b.clicked.connect(lambda *_: on_click())
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def tool_button(icon_name: str, tip: str, on_click: Callable[[], None], size: int = 15) -> QToolButton:
    b = QToolButton()
    THEME.bind_icon(b, icon_name, "muted", size)
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(lambda *_: on_click())
    return b


def clear_layout(lay: QLayout) -> None:
    while lay.count():
        item = lay.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


NATIVE_DIALOGS = True  # False in portable mode (see portable.py)


def choose_folder(parent: QWidget, title: str, start: str) -> str:
    """A folder picker: Windows' own dialog normally, Qt's in portable mode."""
    options = QFileDialog.Option.ShowDirsOnly
    if not NATIVE_DIALOGS:
        options |= QFileDialog.Option.DontUseNativeDialog
    return QFileDialog.getExistingDirectory(parent, title, start, options)


def choose_save_file(parent: QWidget, title: str, start: str, filters: str) -> str:
    """A save-as picker, following the same native/portable rule as :func:`choose_folder`."""
    options = QFileDialog.Option(0) if NATIVE_DIALOGS else QFileDialog.Option.DontUseNativeDialog
    return QFileDialog.getSaveFileName(parent, title, start, filters, "", options)[0]


def copy_text(text: str, toast_from: QWidget | None = None, what: str = "Copied") -> None:
    QGuiApplication.clipboard().setText(text)
    if toast_from is not None:
        Toast.show_for(toast_from, what)


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


# -- containers ---------------------------------------------------------------------


class Card(QFrame):
    """A rounded surface with an optional title row; add content to ``body``."""

    clicked = Signal()

    def __init__(self, title: str = "", subtitle: str = "", *, padding: int = 18, spacing: int = 12,
                 tone: str | None = None, clickable: bool = False):
        super().__init__()
        self.setObjectName("Card")
        if tone:
            self.setProperty("tone", tone)
        if clickable:
            self.setProperty("clickable", "true")
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clickable = clickable
        outer = QVBoxLayout(self)
        outer.setContentsMargins(padding, padding - 2, padding, padding)
        outer.setSpacing(spacing)
        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        self.title_label = label(title, "H3") if title else None
        self.subtitle_label = label(subtitle, "Small", wrap=True) if subtitle else None
        if title or subtitle:
            titles = vbox(self.title_label, self.subtitle_label, spacing=2)
            self.header.addLayout(titles, 1)
            outer.addLayout(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(spacing)
        outer.addLayout(self.body)

    def add_action(self, w: QWidget) -> None:
        self.header.addWidget(w, 0, Qt.AlignmentFlag.AlignTop)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802 - Qt API
        if self._clickable and e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class Divider(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("Divider")
        self.setFrameShape(QFrame.Shape.NoFrame)


class PageScroll(QScrollArea):
    """A scrolling page whose content is centred and capped at ``max_width``."""

    def __init__(self, content: QWidget, max_width: int = 1180, margins: tuple[int, int, int, int] = (32, 26, 32, 32)):
        super().__init__()
        self.setObjectName("PageScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        holder.setObjectName("Page")
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(*margins)
        lay.addStretch(0)
        content.setMaximumWidth(max_width)
        lay.addWidget(content, 1)
        lay.addStretch(0)
        self.setWidget(holder)


def panel_scroll(content: QWidget) -> QScrollArea:
    """A scroll area for content inside a card (transparent, so the card's surface shows)."""
    area = QScrollArea()
    area.setObjectName("PanelScroll")
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(content)
    return area


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = ""):
        super().__init__()
        self.title = label(title, "H1")
        self.subtitle = label(subtitle, "Muted", wrap=True)
        self.subtitle.setVisible(bool(subtitle))
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        lay = hbox((vbox(self.title, self.subtitle, spacing=4), 1), self.actions, spacing=16)
        self.setLayout(lay)

    def set(self, title: str, subtitle: str = "") -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.subtitle.setVisible(bool(subtitle))


class FlowLayout(QLayout):
    """Lays out children left to right, wrapping onto new rows."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802 - Qt API
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._layout(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, dry=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        if not self._items:
            return QSize(0, 0)
        hints = [it.sizeHint() for it in self._items]
        return QSize(sum(h.width() for h in hints) + self._spacing * (len(hints) - 1),
                     max(h.height() for h in hints))

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def _layout(self, rect: QRect, dry: bool) -> int:
        x, y, row_h = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() + 1 and row_h > 0:
                x, y, row_h = rect.x(), y + row_h + self._spacing, 0
            if not dry:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            row_h = max(row_h, hint.height())
        return y + row_h - rect.y()


class ChipRow(QWidget):
    def __init__(self, chips: list[QWidget] | None = None, spacing: int = 6):
        super().__init__()
        self._flow = FlowLayout(self, spacing)
        self.set(chips or [])

    def set(self, chips: list[QWidget]) -> None:
        while self._flow.count():
            it = self._flow.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for c in chips:
            self._flow.addWidget(c)
        self.updateGeometry()


# -- chips -----------------------------------------------------------------------------

ColorFn = Callable[[Palette], str]


def channel_color_fn(channel: str) -> ColorFn:
    return lambda p: theme.channel_color(p, channel)


def tone_fn(tone: str) -> ColorFn:
    return lambda p: getattr(p, tone)


def paint_chip(painter: QPainter, rect: QRectF, text: str, color: ColorFn | None, icon_name: str | None,
               font: QFont, filled: bool = True) -> None:
    p = THEME.palette
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if filled:
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), rect.height() / 2, rect.height() / 2)
        painter.fillPath(path, QColor(p.raised))
        painter.setPen(QPen(QColor(p.border), 1))
        painter.drawPath(path)
    x = rect.left() + 9
    cy = rect.center().y()
    if icon_name:
        ic = icon(icon_name, color(p) if color else p.muted)
        ic.paint(painter, QRect(int(x), int(cy - 6.5), 13, 13))
        x += 17
    elif color:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color(p)))
        painter.drawEllipse(QRectF(x, cy - 3.5, 7, 7))
        x += 13
    painter.setFont(font)
    painter.setPen(QColor(p.text))
    painter.drawText(QRectF(x, rect.top(), rect.right() - x - 8, rect.height()),
                     Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                     QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, int(rect.right() - x - 8)))
    painter.restore()


def chip_width(text: str, font: QFont, has_mark: bool, icon_mark: bool = False) -> int:
    return QFontMetrics(font).horizontalAdvance(text) + 18 + (17 if icon_mark else 13 if has_mark else 0)


class Chip(QWidget):
    """A pill with a coloured dot or icon and a label (the label always uses the text colour)."""

    def __init__(self, text: str, color: ColorFn | None = None, icon_name: str | None = None, tip: str = ""):
        super().__init__()
        self.text, self.color, self.icon_name = text, color, icon_name
        f = QFont(self.font())
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1.0))
        f.setWeight(QFont.Weight.Medium)
        self._font = f
        self.setToolTip(tip)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    @classmethod
    def channel(cls, channel: str, text: str | None = None) -> Chip:
        from .casedata import channel_label

        return cls(text or channel_label(channel), channel_color_fn(channel))

    @classmethod
    def status(cls, text: str, tone: str, icon_name: str, tip: str = "") -> Chip:
        return cls(text, tone_fn(tone), icon_name, tip)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(chip_width(self.text, self._font, bool(self.color), bool(self.icon_name)), 22)

    def paintEvent(self, _e) -> None:  # noqa: N802
        painter = QPainter(self)
        paint_chip(painter, QRectF(self.rect()), self.text, self.color, self.icon_name, self._font)


class ChipDelegate(QStyledItemDelegate):
    """Draws a table cell as a chip; the model returns (text, ColorFn | None, icon | None) for ``role``."""

    def __init__(self, role: int, parent=None):
        super().__init__(parent)
        self.role = role

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        value = index.data(self.role)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else None
        if style:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        if not value:
            return
        text, color, icon_name = value
        f = QFont(option.font)
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1.0))
        f.setWeight(QFont.Weight.Medium)
        w = min(chip_width(text, f, bool(color), bool(icon_name)), option.rect.width() - 16)
        r = QRectF(option.rect.left() + 10, option.rect.center().y() - 10.5, max(w, 24), 21)
        paint_chip(painter, r, text, color, icon_name, f)


# -- display widgets --------------------------------------------------------------------


class IconBadge(QLabel):
    """An icon on a softly tinted rounded square."""

    def __init__(self, icon_name: str, tone: str = "accent", size: int = 36):
        super().__init__()
        self.icon_name, self.tone, self._size = icon_name, tone, size
        self.setFixedSize(size, size)
        THEME.on_change(self._refresh, self)
        self._refresh()

    def set_tone(self, tone: str) -> None:
        self.tone = tone
        self._refresh()

    def _refresh(self) -> None:
        try:
            p = THEME.palette
            c = getattr(p, self.tone)
            self.setStyleSheet(f"background: {theme.mix(p.surface, c, 0.16)}; border-radius: {self._size // 4}px;")
            s = int(self._size * 0.52)
            self.setPixmap(icon(self.icon_name, c).pixmap(QSize(s, s), self.devicePixelRatioF()))
            self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        except RuntimeError:
            pass


class StatTile(Card):
    def __init__(self, icon_name: str, title: str, tone: str = "accent"):
        super().__init__(padding=16, spacing=10, clickable=True)
        self.badge = IconBadge(icon_name, tone, 34)
        self.value = label("0", "StatValue")
        self.title = label(title, "Muted")
        self.caption = label("", "Small", wrap=True)
        self.body.addLayout(hbox(self.badge, "stretch"))
        self.body.addLayout(vbox(self.value, self.title, self.caption, spacing=1))

    def set(self, value: str, caption: str = "", tone: str | None = None) -> None:
        self.value.setText(value)
        self.caption.setText(caption)
        self.caption.setVisible(bool(caption))
        if tone:
            self.badge.set_tone(tone)


class KeyValueGrid(QWidget):
    """Rows of a muted key and a selectable value."""

    def __init__(self, columns: int = 1):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(18)
        self.grid.setVerticalSpacing(8)
        self.columns = columns
        self._n = 0

    def add(self, key: str, value: str | QWidget, mono: bool = False) -> None:
        row, col = divmod(self._n, self.columns)
        self._n += 1
        k = label(key, "Small")
        k.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        v = value if isinstance(value, QWidget) else label(value or "—", None, wrap=True, select=True, mono=mono)
        self.grid.addWidget(k, row, col * 2, Qt.AlignmentFlag.AlignTop)
        self.grid.addWidget(v, row, col * 2 + 1)
        self.grid.setColumnStretch(col * 2 + 1, 1)

    def clear(self) -> None:
        clear_layout(self.grid)
        self._n = 0


class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str, text: str = "", action: QPushButton | None = None,
                 tone: str = "muted"):
        super().__init__()
        self.badge = IconBadge(icon_name, tone, 52)
        self.title = label(title, "H2")
        self.text = label(text, "Muted", wrap=True)
        for w in (self.title, self.text):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setMaximumWidth(460)
        lay = vbox("stretch", spacing=10)
        lay.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(4)
        lay.addWidget(self.title, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.text, 0, Qt.AlignmentFlag.AlignHCenter)
        if action:
            lay.addSpacing(8)
            lay.addWidget(action, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)
        self.setLayout(lay)


class Spinner(QWidget):
    """A small rotating arc for work in progress."""

    def __init__(self, size: int = 16):
        super().__init__()
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self, interval=16)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _tick(self) -> None:
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(QPen(QColor(THEME.palette.border), 2.2))
        p.drawEllipse(r)
        pen = QPen(QColor(THEME.palette.accent), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(r, -self._angle * 16, 100 * 16)


class BarList(QWidget):
    """Counts per category as thin horizontal bars: one hue, labels and values in text colours.

    A single series, so no legend; each row has a hover tooltip.
    """

    ROW = 30

    def __init__(self):
        super().__init__()
        self.rows: list[tuple[str, int, str]] = []  # label, value, tooltip
        self.setMouseTracking(True)
        self._hover = -1

    def set(self, rows: list[tuple[str, int, str]]) -> None:
        self.rows = rows
        self.setMinimumHeight(max(1, len(rows)) * self.ROW)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(320, max(1, len(self.rows)) * self.ROW)

    def _layout(self) -> tuple[int, int, int]:
        fm = self.fontMetrics()
        label_w = min(160, max((fm.horizontalAdvance(r[0]) for r in self.rows), default=60) + 12)
        value_w = max((fm.horizontalAdvance(f"{r[1]:,}") for r in self.rows), default=20) + 12
        return label_w, value_w, max(40, self.width() - label_w - value_w)

    def paintEvent(self, _e) -> None:  # noqa: N802
        if not self.rows:
            return
        pal = THEME.palette
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        label_w, value_w, bar_w = self._layout()
        top = max(r[1] for r in self.rows) or 1
        for i, (name, value, _tip) in enumerate(self.rows):
            y = i * self.ROW
            if i == self._hover:
                hp = QPainterPath()
                hp.addRoundedRect(QRectF(0, y + 2, self.width(), self.ROW - 4), 6, 6)
                p.fillPath(hp, QColor(pal.raised))
            p.setPen(QColor(pal.muted))
            p.drawText(QRect(6, y, label_w - 12, self.ROW), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       self.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, label_w - 12))
            track = QPainterPath()
            track.addRoundedRect(QRectF(label_w, y + self.ROW / 2 - 4, bar_w, 8), 4, 4)
            p.fillPath(track, QColor(pal.divider))
            w = max(6.0, bar_w * value / top)
            bar = QPainterPath()
            bar.addRoundedRect(QRectF(label_w, y + self.ROW / 2 - 4, w, 8), 4, 4)
            p.fillPath(bar, QColor(pal.series[0]))
            p.setPen(QColor(pal.text))
            p.drawText(QRect(label_w + bar_w, y, value_w, self.ROW),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{value:,}")

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        i = int(e.position().y() // self.ROW)
        i = i if 0 <= i < len(self.rows) else -1
        if i != self._hover:
            self._hover = i
            self.update()
        if i >= 0:
            QToolTip.showText(e.globalPosition().toPoint(), self.rows[i][2], self)

    def leaveEvent(self, _e) -> None:  # noqa: N802
        self._hover = -1
        self.update()


# -- inputs ----------------------------------------------------------------------------


class SearchBox(QLineEdit):
    """A search field that reports what was typed after a short pause."""

    search = Signal(str)

    def __init__(self, placeholder: str = "Search", delay_ms: int = 180):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self._action = self.addAction(QIcon(), QLineEdit.ActionPosition.LeadingPosition)
        THEME.on_change(self._icon, self)
        self._icon()
        self._timer = QTimer(self, singleShot=True, interval=delay_ms)
        self._timer.timeout.connect(lambda: self.search.emit(self.text()))
        self.textChanged.connect(lambda *_: self._timer.start())

    def _icon(self) -> None:
        with contextlib.suppress(RuntimeError):
            self._action.setIcon(icon("ph.magnifying-glass", THEME.palette.faint))


class Segmented(QFrame):
    """Mutually exclusive options as a compact button group."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]]):
        super().__init__()
        self.setObjectName("SegGroup")
        lay = hbox(spacing=2, margins=(3, 3, 3, 3))
        self.setLayout(lay)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        self._labels = dict(options)
        for key, text in options:
            b = QPushButton(text)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda *_, k=key: self.changed.emit(k))
            self._group.addButton(b)
            self._buttons[key] = b
            lay.addWidget(b)
        if options:
            self._buttons[options[0][0]].setChecked(True)

    def current(self) -> str:
        return next((k for k, b in self._buttons.items() if b.isChecked()), "")

    def select(self, key: str) -> None:
        if key in self._buttons:
            self._buttons[key].setChecked(True)

    def set_count(self, key: str, n: int) -> None:
        if key in self._buttons:
            self._buttons[key].setText(f"{self._labels[key]}  {n:,}")


def _dropped_folder(event) -> Path | None:
    urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
    paths = [Path(u.toLocalFile()) for u in urls if u.isLocalFile()]
    return next((p for p in paths if p.is_dir()), None)


class PathField(QWidget):
    """A folder path with a Browse button; folders can be dropped on it."""

    changed = Signal(str)

    def __init__(self, placeholder: str = "", dialog_title: str = "Choose a folder"):
        super().__init__()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.changed.emit)
        self._title = dialog_title
        browse = button("Browse…", None, "ph.folder-open", self._browse)
        self.setLayout(hbox(self.edit, browse, spacing=8))
        self.setAcceptDrops(True)

    def _browse(self) -> None:
        start = self.edit.text() or str(Path.home())
        chosen = choose_folder(self, self._title, start)
        if chosen:
            self.edit.setText(str(Path(chosen)))

    def path(self) -> Path | None:
        t = self.edit.text().strip().strip('"')
        return Path(t) if t else None

    def set_path(self, p: Path | str | None) -> None:
        self.edit.setText(str(p) if p else "")

    def set_invalid(self, invalid: bool) -> None:
        self.edit.setProperty("invalid", "true" if invalid else "false")
        repolish(self.edit)

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if _dropped_folder(e):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        folder = _dropped_folder(e)
        if folder:
            self.edit.setText(str(folder))


class DropZone(QFrame):
    """The evidence picker: drop a folder or browse; shows the chosen folder once set."""

    chosen = Signal(object)  # Path

    def __init__(self):
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(150)
        self.badge = IconBadge("ph.hard-drives", "accent", 44)
        self.title = label("Drop the evidence folder here", "H3")
        self.hint = label("A mounted image, an extracted drive, or a KAPE, Velociraptor or UAC collection",
                          "Small", wrap=True)
        self.browse = button("Browse…", None, "ph.folder-open", self._browse)
        self.change = button("Change", "Ghost", None, self._browse)
        self.change.hide()
        for w in (self.title, self.hint):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.path_label = label("", "Mono", wrap=True, select=True, mono=True)
        self.path_label.hide()
        lay = vbox(spacing=8, margins=(24, 20, 24, 20))
        lay.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.title)
        lay.addWidget(self.path_label)
        lay.addWidget(self.hint)
        lay.addLayout(hbox("stretch", self.browse, self.change, "stretch"))
        self.setLayout(lay)
        self.path: Path | None = None

    def _browse(self) -> None:
        chosen = choose_folder(self, "Choose the evidence folder", str(self.path or Path.home()))
        if chosen:
            self.set_path(Path(chosen))

    def set_path(self, path: Path | None) -> None:
        self.path = path
        filled = path is not None
        self.setProperty("filled", "true" if filled else "false")
        self.title.setText(path.name or str(path) if filled else "Drop the evidence folder here")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.path_label.setText(str(path) if filled else "")
        self.path_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.path_label.setVisible(filled)
        self.hint.setVisible(not filled)
        self.browse.setVisible(not filled)
        self.change.setVisible(filled)
        self.badge.set_tone("ok" if filled else "accent")
        repolish(self)
        if filled:
            self.chosen.emit(path)

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if _dropped_folder(e):
            e.acceptProposedAction()
            self.setProperty("active", "true")
            repolish(self)

    def dragLeaveEvent(self, _e) -> None:  # noqa: N802
        self.setProperty("active", "false")
        repolish(self)

    def dropEvent(self, e) -> None:  # noqa: N802
        self.setProperty("active", "false")
        folder = _dropped_folder(e)
        if folder:
            self.set_path(folder)


# -- toast -----------------------------------------------------------------------------


class Toast(QLabel):
    """A short message that fades in at the bottom of the window and fades out."""

    _current: Toast | None = None

    @classmethod
    def show_for(cls, widget: QWidget, text: str, ms: int = 2400) -> None:
        win = widget.window()
        if cls._current is not None:
            with contextlib.suppress(RuntimeError):
                cls._current.deleteLater()
        t = cls(text, win)
        cls._current = t
        t._show(ms)

    def __init__(self, text: str, parent: QWidget):
        super().__init__(text, parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.adjustSize()
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)

    def _show(self, ms: int) -> None:
        win = self.parentWidget()
        self.move((win.width() - self.width()) // 2, win.height() - self.height() - 28)
        self.show()
        self.raise_()
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setDuration(180)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()
        QTimer.singleShot(ms, self._hide)

    def _hide(self) -> None:
        try:
            self._anim = QPropertyAnimation(self._fx, b"opacity", self)
            self._anim.setDuration(260)
            self._anim.setStartValue(1.0)
            self._anim.setEndValue(0.0)
            self._anim.finished.connect(self.deleteLater)
            self._anim.start()
        except RuntimeError:
            pass
