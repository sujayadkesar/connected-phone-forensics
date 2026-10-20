"""Spreadsheet-style column filters for the app's tables.

Each column header gets a filter button. It opens a panel to sort the column,
keep the rows that meet a condition (contains, does not contain, begins with,
a regular expression; greater or less than for numbers; on, before or after for
times) and tick the values to keep, as in Excel. Right-clicking a cell offers
"show only this value" and "hide this value". The filters in force show as
chips above the table, each with a button to remove it.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHeaderView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QTableView,
    QWidget,
)

from .icons import icon
from .models import NO_TEXT, ColumnFilter, RecordModel, ops_for, value_key
from .widgets import THEME, FlowLayout, button, hbox, label, repolish, vbox

MAX_VALUES = 2000  # more different values than this: the panel offers the condition only
_ZONE = 22         # the filter button's width in a header section


def _model(view: QTableView) -> RecordModel | None:
    m = view.model()
    return m if isinstance(m, RecordModel) else None


class FilterHeader(QHeaderView):
    """A horizontal header with a filter button at the right of each filterable column."""

    filter_clicked = Signal(int)  # logical column

    def __init__(self, view: QTableView):
        super().__init__(Qt.Orientation.Horizontal, view)
        self.view = view
        self.setSectionsClickable(True)
        self.setMouseTracking(True)
        self._hover = -1
        self._pressed = -1
        self.filter_clicked.connect(lambda col: open_filter(view, col))
        THEME.on_change(self.viewport().update, self)

    def _filterable(self, col: int) -> bool:
        m = self.model()
        return isinstance(m, RecordModel) and 0 <= col < len(m.columns) and m.columns[col].filterable

    def _zone(self, col: int) -> QRect:
        right = self.sectionViewportPosition(col) + self.sectionSize(col)
        return QRect(right - _ZONE - 5, (self.height() - _ZONE) // 2, _ZONE, _ZONE)

    def _shows_button(self, col: int) -> bool:
        return self._filterable(col) and self.sectionSize(col) >= 64

    def paintSection(self, painter: QPainter, rect: QRect, col: int) -> None:  # noqa: N802
        if not self._shows_button(col):
            super().paintSection(painter, rect, col)
            return
        painter.save()
        super().paintSection(painter, rect.adjusted(0, 0, -(_ZONE + 6), 0), col)
        painter.restore()
        p = THEME.palette
        painter.save()
        strip = QRect(rect.right() - _ZONE - 6, rect.top(), _ZONE + 7, rect.height())
        painter.fillRect(strip, QColor(p.surface))
        painter.fillRect(QRect(strip.left(), rect.bottom(), strip.width(), 1), QColor(p.border))
        m = self.model()
        active = isinstance(m, RecordModel) and col in m.column_filters()
        zone = QRect(rect.right() - _ZONE - 4, rect.top() + (rect.height() - _ZONE) // 2, _ZONE, _ZONE)
        if active or col == self._hover:
            from .theme import mix

            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(mix(p.surface, p.accent, 0.2) if active else p.raised))
            painter.drawRoundedRect(zone, 6, 6)
        color = p.accent if active else (p.text if col == self._hover else p.faint)
        icon("ph.funnel-simple-fill" if active else "ph.funnel-simple", color).paint(painter,
                                                                                    zone.adjusted(4, 4, -4, -4))
        painter.restore()

    def _hit(self, pos: QPoint) -> int:
        col = self.logicalIndexAt(pos)
        return col if col >= 0 and self._shows_button(col) and self._zone(col).contains(pos) else -1

    def mousePressEvent(self, e) -> None:  # noqa: N802
        hit = self._hit(e.position().toPoint()) if e.button() == Qt.MouseButton.LeftButton else -1
        self._pressed = hit
        if hit >= 0:
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._pressed >= 0:
            col, self._pressed = self._pressed, -1
            if self._hit(e.position().toPoint()) == col:
                self.filter_clicked.emit(col)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        hit = self._hit(e.position().toPoint())
        if hit != self._hover:
            self._hover = hit
            self.viewport().update()
        super().mouseMoveEvent(e)
        if hit >= 0:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(f"Filter “{self.model().columns[hit].label}”")
        elif self.cursor().shape() == Qt.CursorShape.PointingHandCursor:
            self.unsetCursor()
            self.setToolTip("")

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = -1
        self.viewport().update()
        super().leaveEvent(e)

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        col = self.logicalIndexAt(e.pos())
        if col >= 0 and self._filterable(col):
            self.filter_clicked.emit(col)


class FilterPopup(QWidget):
    """The panel a header's filter button opens: sort, a condition, and the values to keep."""

    def __init__(self, view: QTableView, col: int):
        super().__init__(view, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.view, self.col = view, col
        self.model = _model(view)
        c = self.model.columns[col]
        self.kind = c.kind
        current = self.model.column_filters().get(col, ColumnFilter())

        frame = QFrame()
        frame.setObjectName("FilterPopup")
        outer = vbox(frame, margins=(0, 0, 0, 0))
        self.setLayout(outer)

        asc, desc = {"number": ("Smallest first", "Largest first"), "time": ("Oldest first", "Newest first")}.get(
            c.kind, ("Sort A to Z", "Sort Z to A"))
        b_asc = button(asc, "Ghost", "ph.sort-ascending", lambda: self._sort(Qt.SortOrder.AscendingOrder))
        b_desc = button(desc, "Ghost", "ph.sort-descending", lambda: self._sort(Qt.SortOrder.DescendingOrder))

        self.op = QComboBox()
        self.op.addItem("Any value", "")
        for key, text in ops_for(c.kind):
            self.op.addItem(text, key)
        i = self.op.findData(current.op)
        self.op.setCurrentIndex(max(0, i))
        self.text = QLineEdit(current.text)
        self.text.setPlaceholderText({"time": "YYYY-MM-DD or YYYY-MM-DD HH:MM", "number": "A number"}.get(
            c.kind, "Text (not case-sensitive)"))
        self.text.returnPressed.connect(self._apply)
        self.op.currentIndexChanged.connect(lambda *_: self._check())
        self.text.textChanged.connect(lambda *_: self._check())
        self.error = label("", "Faint", wrap=True)

        counts = self.model.column_values(col)
        self.values = QListWidget()
        self.values.setObjectName("FilterValues")
        self.values.setMinimumHeight(150)
        self.values.setMaximumHeight(240)
        self.values.setUniformItemSizes(True)
        self.values.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.values.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.find = QLineEdit()
        self.find.setPlaceholderText("Search the values")
        self.find.setClearButtonEnabled(True)
        self.find.textChanged.connect(self._find)
        self.all_box = QPushButton()
        self.all_box.setObjectName("Link")
        self.all_box.setCursor(Qt.CursorShape.PointingHandCursor)
        self.all_box.clicked.connect(self._toggle_all)
        self.too_many = len(counts) > MAX_VALUES
        if not self.too_many:
            for key in sorted(counts, key=lambda k: (k == "", k.lower())):
                shown = key if len(key) <= 90 else key[:89] + "…"
                item = QListWidgetItem(f"{shown or '(blank)'}    {counts[key]:,}")
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setToolTip(key if len(key) > 90 else "")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                checked = current.values is None or key in current.values
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                self.values.addItem(item)
        self.values.itemChanged.connect(lambda *_: self._check())

        self.apply_btn = button("Apply", "Primary", None, self._apply)
        clear = button("Clear filter", "Ghost", "ph.x", self._clear)
        cancel = button("Cancel", None, None, self.close)

        body = vbox(spacing=8, margins=(14, 12, 14, 12))
        body.addWidget(label(c.label[:1].upper() + c.label[1:], "H3"))
        body.addLayout(hbox(b_asc, b_desc, "stretch", spacing=6))
        body.addWidget(label("Show rows where the value", "Faint"))
        body.addWidget(self.op)
        body.addWidget(self.text)
        body.addWidget(self.error)
        if self.too_many:
            body.addWidget(label(f"{len(counts):,} different values: too many to list; use a condition above.",
                                 "Faint", wrap=True))
        else:
            n = len(counts)
            head = label(("Days" if c.kind == "time" else "Values") + f" ({n:,})", "Faint")
            body.addLayout(hbox(head, "stretch", self.all_box))
            body.addWidget(self.find)
            body.addWidget(self.values)
        body.addLayout(hbox(clear, "stretch", cancel, self.apply_btn, spacing=6))
        frame.setLayout(body)
        self.setFixedWidth(340)
        self._check()

    # -- behaviour -----------------------------------------------------------------------

    def _sort(self, order: Qt.SortOrder) -> None:
        self.view.horizontalHeader().setSortIndicatorShown(True)
        self.view.sortByColumn(self.col, order)
        self.close()

    def _find(self, text: str) -> None:
        t = text.lower().strip()
        for i in range(self.values.count()):
            item = self.values.item(i)
            item.setHidden(bool(t) and t not in (item.data(Qt.ItemDataRole.UserRole) or "(blank)").lower())
        self._check()

    def _shown_items(self) -> list[QListWidgetItem]:
        return [self.values.item(i) for i in range(self.values.count()) if not self.values.item(i).isHidden()]

    def _toggle_all(self) -> None:
        items = self._shown_items()
        state = Qt.CheckState.Unchecked if all(i.checkState() == Qt.CheckState.Checked for i in items) \
            else Qt.CheckState.Checked
        self.values.blockSignals(True)
        for i in items:
            i.setCheckState(state)
        self.values.blockSignals(False)
        self._check()

    def _filter(self) -> ColumnFilter:
        values = None
        if not self.too_many and self.values.count():
            checked = {self.values.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.values.count())
                       if self.values.item(i).checkState() == Qt.CheckState.Checked}
            if len(checked) < self.values.count():
                values = frozenset(checked)
        return ColumnFilter(self.op.currentData() or "", self.text.text(), values)

    def _check(self) -> None:
        op = self.op.currentData() or ""
        self.text.setEnabled(bool(op) and op not in NO_TEXT)
        flt = self._filter()
        err = flt.error(self.kind)
        self.error.setText(err)
        self.error.setVisible(bool(err))
        self.text.setProperty("invalid", "true" if err else "false")
        repolish(self.text)
        nothing = flt.values is not None and not flt.values
        self.apply_btn.setEnabled(not err and not nothing)
        items = self._shown_items()
        self.all_box.setText("Clear all" if items and all(i.checkState() == Qt.CheckState.Checked for i in items)
                             else "Select all")

    def _apply(self) -> None:
        flt = self._filter()
        if flt.error(self.kind) or (flt.values is not None and not flt.values):
            return
        self.model.set_column_filter(self.col, flt)
        self.close()

    def _clear(self) -> None:
        self.model.set_column_filter(self.col, None)
        self.close()


def open_filter(view: QTableView, col: int) -> None:
    if _model(view) is None:
        return
    pop = FilterPopup(view, col)
    header = view.horizontalHeader()
    right = header.sectionViewportPosition(col) + header.sectionSize(col)
    at = header.viewport().mapToGlobal(QPoint(right, header.height()))
    pop.adjustSize()
    x = at.x() - pop.width()
    screen = QGuiApplication.screenAt(at) or QGuiApplication.primaryScreen()
    area = screen.availableGeometry()
    x = max(area.left() + 4, min(x, area.right() - pop.width() - 4))
    y = min(at.y() + 2, area.bottom() - pop.height() - 4)
    pop.move(x, y)
    pop.show()
    if pop.text.isEnabled():
        pop.text.setFocus()
    else:
        pop.op.setFocus()


class FilterBar(QWidget):
    """The column filters in force on a table, as chips with a remove button, and "Clear all"."""

    def __init__(self, view: QTableView):
        super().__init__()
        self.view = view
        self.flow = FlowLayout(spacing=6)
        self.setLayout(self.flow)
        self.refresh()

    def refresh(self) -> None:
        while self.flow.count():
            item = self.flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        m = _model(self.view)
        filters = m.column_filters() if m else {}
        self.setVisible(bool(filters))
        if not filters:
            return
        for col, flt in sorted(filters.items()):
            c = m.columns[col]
            b = QPushButton(f"{c.label[:1].upper() + c.label[1:]}: {flt.describe(c.kind)}")
            b.setObjectName("FilterChip")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip("Change this filter")
            THEME.bind_icon(b, "ph.funnel-simple-fill", "accent", 13)
            b.clicked.connect(lambda *_, k=col: open_filter(self.view, k))
            x = QPushButton()
            x.setObjectName("FilterChipX")
            x.setCursor(Qt.CursorShape.PointingHandCursor)
            x.setToolTip("Remove this filter")
            THEME.bind_icon(x, "ph.x", "muted", 12)
            x.clicked.connect(lambda *_, k=col: m.set_column_filter(k, None))
            pair = QWidget()
            pair.setLayout(hbox(b, x, spacing=0))
            self.flow.addWidget(pair)
        if len(filters) > 1:
            self.flow.addWidget(button("Clear all column filters", "Link", None, m.clear_column_filters))


def install_cell_menu(view: QTableView, extra: Callable[[QMenu, list[dict]], None] | None = None) -> None:
    """Right-click a cell: show only or hide its value, filter its column, copy it."""
    view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def menu_at(pos: QPoint) -> None:
        m = _model(view)
        index = view.indexAt(pos)
        if m is None or not index.isValid():
            return
        col, r = index.column(), m.row(index.row())
        c = m.columns[col]
        menu = QMenu(view)
        if c.filterable:
            v = c.filter_text(r)
            key = value_key(c.kind, v)
            shown = (key or "(blank)") if len(key) <= 40 else key[:39] + "…"
            title = c.label
            current = m.column_filters().get(col, ColumnFilter())

            def only() -> None:
                m.set_column_filter(col, ColumnFilter(current.op, current.text, frozenset({key})))

            def hide() -> None:
                keys = set(m.column_values(col)) - {key}
                if current.values is not None:
                    keys &= set(current.values)
                m.set_column_filter(col, ColumnFilter(current.op, current.text, frozenset(keys)))

            what = "on" if c.kind == "time" else "is"
            menu.addAction(icon("ph.funnel-simple", THEME.palette.text), f"Show only rows where {title} {what} "
                                                                          f"“{shown}”", only)
            menu.addAction(icon("ph.eye-slash", THEME.palette.text), f"Hide rows where {title} {what} “{shown}”",
                           hide)
            menu.addAction(f"Filter “{title}”…", lambda: open_filter(view, col))
            if col in m.column_filters():
                menu.addAction(f"Clear the filter on “{title}”", lambda: m.set_column_filter(col, None))
            if m.column_filters():
                menu.addAction("Clear all column filters", m.clear_column_filters)
            menu.addSeparator()
        text = c.text(r) if not c.chip else c.filter_text(r)
        if text:
            menu.addAction(icon("ph.copy", THEME.palette.text), "Copy this cell",
                           lambda: QGuiApplication.clipboard().setText(str(text)))
        rows = selected_rows(view) or [r]
        menu.addAction("Copy the selected row(s)", lambda: QGuiApplication.clipboard().setText(
            "\n".join("\t".join(cc.filter_text(x) for cc in m.columns if cc.title) for x in rows)))
        if extra is not None:
            extra(menu, rows)
        menu.exec(view.viewport().mapToGlobal(pos))

    view.customContextMenuRequested.connect(menu_at)


def selected_rows(view: QTableView) -> list[dict]:
    m = _model(view)
    if m is None or view.selectionModel() is None:
        return []
    rows = sorted({i.row() for i in view.selectionModel().selectedRows()})
    return [r for r in (m.row(i) for i in rows) if r is not None]
