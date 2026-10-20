"""Table models for record lists (timeline, coverage, issues).

Filtering and sorting run over a plain list of row indexes rather than through
QSortFilterProxyModel's per-row callbacks, so a timeline of 100,000 events
filters in a few milliseconds and only the visible rows are ever formatted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFont

from .widgets import THEME, ColorFn

CHIP_ROLE = Qt.ItemDataRole.UserRole + 1
_ROOT = QModelIndex()
ROW_ROLE = Qt.ItemDataRole.UserRole + 2


@dataclass
class Column:
    title: str
    text: Callable[[dict], str]
    width: int | None = None          # None: stretch
    mono: bool = False
    muted: bool = False
    align_right: bool = False
    sort_key: Callable[[dict], Any] | None = None
    chip: Callable[[dict], tuple[str, ColorFn | None, str | None] | None] | None = None
    tooltip: bool = False             # show the full text on hover (for elided cells)
    icon: Callable[[dict], tuple[str, str] | None] | None = None  # (icon name, palette role) before the text
    tip: Callable[[dict], str | None] | None = None               # a tooltip of its own


class RecordModel(QAbstractTableModel):
    def __init__(self, columns: list[Column], row_tint: Callable[[dict], str | None] | None = None):
        super().__init__()
        self.columns = columns
        self.row_tint = row_tint  # palette role to tint a row with (e.g. "warn" for recovered items)
        self.rows: list[dict] = []
        self.visible: list[int] = []
        self._filter: Callable[[dict], bool] | None = None
        self._sort: tuple[int, Qt.SortOrder] | None = None

    # -- data -------------------------------------------------------------------------

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.visible = self._compute()
        self.endResetModel()

    def set_filter(self, fn: Callable[[dict], bool] | None) -> None:
        self.beginResetModel()
        self._filter = fn
        self.visible = self._compute()
        self.endResetModel()

    def _compute(self) -> list[int]:
        fn = self._filter
        idx = list(range(len(self.rows))) if fn is None else [i for i, r in enumerate(self.rows) if fn(r)]
        if self._sort:
            col, order = self._sort
            c = self.columns[col]
            key = c.sort_key or c.text
            rows = self.rows
            idx.sort(key=lambda i: _sortable(key(rows[i])), reverse=order == Qt.SortOrder.DescendingOrder)
        return idx

    def row(self, view_row: int) -> dict | None:
        return self.rows[self.visible[view_row]] if 0 <= view_row < len(self.visible) else None

    def visible_rows(self) -> list[dict]:
        return [self.rows[i] for i in self.visible]

    # -- Qt model API ----------------------------------------------------------------

    def rowCount(self, parent=_ROOT) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.visible)

    def columnCount(self, parent=_ROOT) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return self.columns[section].title
            if role == Qt.ItemDataRole.TextAlignmentRole:
                c = self.columns[section]
                return int((Qt.AlignmentFlag.AlignRight if c.align_right else Qt.AlignmentFlag.AlignLeft)
                           | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[self.visible[index.row()]]
        c = self.columns[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return "" if c.chip else c.text(r)
        if role == CHIP_ROLE:
            return c.chip(r) if c.chip else None
        if role == ROW_ROLE:
            return r
        if role == Qt.ItemDataRole.ToolTipRole and c.tip:
            return c.tip(r)
        if role == Qt.ItemDataRole.ToolTipRole and c.tooltip:
            text = c.text(r)
            return text if len(text) > 40 else None
        if role == Qt.ItemDataRole.DecorationRole and c.icon:
            spec = c.icon(r)
            if spec:
                from .icons import icon

                return icon(spec[0], getattr(THEME.palette, spec[1]))
            return None
        if role == Qt.ItemDataRole.BackgroundRole and self.row_tint:
            tone = self.row_tint(r)
            if tone:
                from .theme import mix

                p = THEME.palette
                return QColor(mix(p.surface, getattr(p, tone), 0.14))
            return None
        if role == Qt.ItemDataRole.FontRole and c.mono:
            return _mono()
        if role == Qt.ItemDataRole.ForegroundRole and c.muted:
            return QColor(THEME.palette.muted)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int((Qt.AlignmentFlag.AlignRight if c.align_right else Qt.AlignmentFlag.AlignLeft)
                       | Qt.AlignmentFlag.AlignVCenter)
        return None

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder) -> None:
        self.beginResetModel()
        self._sort = (column, order) if 0 <= column < len(self.columns) else None
        self.visible = self._compute()
        self.endResetModel()


def _sortable(v: Any):
    if v is None:
        return (1, "")
    return (0, v.lower() if isinstance(v, str) else v)


_MONO: QFont | None = None


def _mono() -> QFont:
    global _MONO
    if _MONO is None:
        from .widgets import mono_font

        _MONO = QFont(mono_font())
        _MONO.setPointSizeF(_MONO.pointSizeF() - 0.25)
    return _MONO


def _tint_delegate(parent):
    """Paints the model's row tint (BackgroundRole), which style-sheet item rendering ignores."""
    from PySide6.QtWidgets import QStyle, QStyledItemDelegate

    class TintDelegate(QStyledItemDelegate):
        def paint(self, painter, option, index) -> None:
            tint = index.data(Qt.ItemDataRole.BackgroundRole)
            if isinstance(tint, QColor) and not option.state & QStyle.StateFlag.State_Selected:
                painter.fillRect(option.rect, tint)
            super().paint(painter, option, index)

    return TintDelegate(parent)


def make_table(model: RecordModel, sortable: bool = True):
    """A QTableView set up the same way everywhere: row selection, chip cells, fixed column widths."""
    from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

    from .widgets import ChipDelegate

    view = QTableView()
    view.setModel(model)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.setShowGrid(False)
    view.setWordWrap(False)
    view.setAlternatingRowColors(True)
    view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.setTextElideMode(Qt.TextElideMode.ElideRight)
    view.verticalHeader().hide()
    view.verticalHeader().setDefaultSectionSize(36)
    view.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    header = view.horizontalHeader()
    header.setHighlightSections(False)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setMinimumSectionSize(60)
    delegate = ChipDelegate(CHIP_ROLE, view)
    if model.row_tint is not None:
        view.setItemDelegate(_tint_delegate(view))
    stretch = None
    for i, c in enumerate(model.columns):
        if c.chip:
            view.setItemDelegateForColumn(i, delegate)
        if c.width is None:
            stretch = i
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        else:
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            view.setColumnWidth(i, c.width)
    if stretch is None:
        header.setStretchLastSection(True)
    if sortable:
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)  # keep the model's own order until a click
        view.setSortingEnabled(True)
        header.setSortIndicatorShown(False)
        header.sectionClicked.connect(lambda *_: header.setSortIndicatorShown(True))
    view._chip_delegate = delegate  # keep a reference
    return view
