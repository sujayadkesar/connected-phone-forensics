"""Table models for record lists (timeline, coverage, issues).

Filtering and sorting run over a plain list of row indexes rather than through
QSortFilterProxyModel's per-row callbacks, so a timeline of 100,000 events
filters in a few milliseconds and only the visible rows are ever formatted.
Each column can carry a filter of its own (see :class:`ColumnFilter` and
:mod:`connected_phone_forensics.gui.colfilter`), as in a spreadsheet.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont

from .widgets import THEME, ColorFn

CHIP_ROLE = Qt.ItemDataRole.UserRole + 1
_ROOT = QModelIndex()
ROW_ROLE = Qt.ItemDataRole.UserRole + 2
PICTURE_ROLE = Qt.ItemDataRole.UserRole + 3


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
    picture: Callable[[dict], Any] | None = None  # a QPixmap drawn in the cell (contact photo, app icon)
    kind: str = "text"                # text | number | time: the conditions its filter offers
    value: Callable[[dict], Any] | None = None    # what the filter tests, when not the text shown
    filterable: bool = True
    name: str = ""                    # what filters call the column when its title is blank

    @property
    def label(self) -> str:
        return self.title or self.name or "this column"

    def filter_text(self, r: dict) -> str:
        v = (self.value or self.text)(r)
        v = "" if v is None else " ".join(str(v).split())
        return "" if v in ("—", "-") else v


# -- column filters --------------------------------------------------------------------

TEXT_OPS = (("contains", "contains"), ("not_contains", "does not contain"), ("equals", "equals"),
            ("not_equals", "does not equal"), ("begins", "begins with"), ("ends", "ends with"),
            ("regex", "matches the pattern (regular expression)"), ("empty", "is empty"),
            ("not_empty", "is not empty"))
NUMBER_OPS = (("eq", "="), ("ne", "≠"), ("gt", ">"), ("ge", "≥"), ("lt", "<"), ("le", "≤"),
              ("empty", "is empty"), ("not_empty", "is not empty"))
TIME_OPS = (("after", "on or after"), ("before", "on or before"), ("on", "on (a day, hour or minute)"),
            ("contains", "contains"), ("empty", "has no time"), ("not_empty", "has a time"))
NO_TEXT = {"empty", "not_empty"}


def ops_for(kind: str) -> tuple[tuple[str, str], ...]:
    return {"number": NUMBER_OPS, "time": TIME_OPS}.get(kind, TEXT_OPS)


def value_key(kind: str, text: str) -> str:
    """The value a column's value list groups by: the day for times, else the text itself."""
    return text[:10] if kind == "time" else text


def to_number(text: str) -> float | None:
    try:
        return float(str(text).replace(",", "").replace(" ", ""))
    except ValueError:
        return None


@dataclass(frozen=True)
class ColumnFilter:
    """One column's filter: a condition (``op`` with ``text``) and/or the values to keep."""

    op: str = ""
    text: str = ""
    values: frozenset[str] | None = None

    def active(self) -> bool:
        return bool(self.op and (self.op in NO_TEXT or self.text.strip())) or self.values is not None

    def error(self, kind: str) -> str:
        """Why the condition cannot be used ("" when it can)."""
        t = self.text.strip()
        if not self.op or self.op in NO_TEXT or not t:
            return ""
        if self.op == "regex":
            try:
                re.compile(t)
            except re.error as exc:
                return f"Not a valid pattern: {exc}"
        if kind == "number" and self.op in dict(NUMBER_OPS) and to_number(t) is None:
            return "Type a number"
        return ""

    def describe(self, kind: str) -> str:
        bits = []
        if self.op and (self.op in NO_TEXT or self.text.strip()):
            name = dict(ops_for(kind)).get(self.op, self.op)
            bits.append(name if self.op in NO_TEXT else f"{name} “{self.text.strip()}”")
        if self.values is not None:
            vals = sorted(self.values)
            shown = ", ".join(v or "(blank)" for v in vals[:2])
            bits.append(f"is {shown}" if len(vals) <= 2 else f"one of {len(vals):,} values")
        return " and ".join(bits)

    def predicate(self, col: Column) -> Callable[[dict], bool]:
        get, kind, values = col.filter_text, col.kind, self.values
        cond = self._condition(kind)

        def keep(r: dict) -> bool:
            v = get(r)
            if values is not None and value_key(kind, v) not in values:
                return False
            return cond is None or cond(v)

        return keep

    def _condition(self, kind: str) -> Callable[[str], bool] | None:
        op, t = self.op, self.text.strip()
        if not op or (op not in NO_TEXT and not t) or self.error(kind):
            return None
        tl = t.lower()
        if op == "empty":
            return lambda v: not v
        if op == "not_empty":
            return lambda v: bool(v)
        if kind == "number" and op in dict(NUMBER_OPS):
            n = to_number(t)
            cmp = {"eq": lambda a: a == n, "ne": lambda a: a != n, "gt": lambda a: a > n, "ge": lambda a: a >= n,
                   "lt": lambda a: a < n, "le": lambda a: a <= n}[op]

            def number(v: str) -> bool:
                a = to_number(v)
                return a is not None and cmp(a)

            return number
        if kind == "time" and op in ("after", "before", "on"):
            t = t.replace("T", " ")
            if op == "after":
                return lambda v: bool(v) and v >= t
            if op == "before":
                return lambda v: bool(v) and v[:len(t)] <= t
            return lambda v: v.startswith(t)
        if op == "regex":
            rx = re.compile(t, re.IGNORECASE)
            return lambda v: rx.search(v) is not None
        return {
            "contains": lambda v: tl in v.lower(), "not_contains": lambda v: tl not in v.lower(),
            "equals": lambda v: v.lower() == tl, "not_equals": lambda v: v.lower() != tl,
            "begins": lambda v: v.lower().startswith(tl), "ends": lambda v: v.lower().endswith(tl),
        }.get(op)


class RecordModel(QAbstractTableModel):
    filters_changed = Signal()  # a column filter was set or cleared

    def __init__(self, columns: list[Column], row_tint: Callable[[dict], str | None] | None = None):
        super().__init__()
        self.columns = columns
        self.row_tint = row_tint  # palette role to tint a row with (e.g. "warn" for recovered items)
        self.rows: list[dict] = []
        self.visible: list[int] = []
        self._filter: Callable[[dict], bool] | None = None
        self._col_filters: dict[int, ColumnFilter] = {}
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

    # -- column filters -----------------------------------------------------------------

    def column_filters(self) -> dict[int, ColumnFilter]:
        return dict(self._col_filters)

    def set_column_filter(self, col: int, flt: ColumnFilter | None) -> None:
        self.beginResetModel()
        if flt is None or not flt.active() or flt.error(self.columns[col].kind):
            self._col_filters.pop(col, None)
        else:
            self._col_filters[col] = flt
        self.visible = self._compute()
        self.endResetModel()
        self.filters_changed.emit()

    def clear_column_filters(self) -> None:
        if not self._col_filters:
            return
        self.beginResetModel()
        self._col_filters.clear()
        self.visible = self._compute()
        self.endResetModel()
        self.filters_changed.emit()

    def column_values(self, col: int) -> Counter:
        """How often each value of a column occurs in the rows every other filter keeps (a spreadsheet's
        value list); times are grouped by day."""
        c = self.columns[col]
        keep = self._predicate(skip=col)
        out: Counter = Counter()
        for r in self.rows:
            if keep is None or keep(r):
                out[value_key(c.kind, c.filter_text(r))] += 1
        return out

    def _predicate(self, skip: int = -1) -> Callable[[dict], bool] | None:
        fn = self._filter
        preds = [f.predicate(self.columns[c]) for c, f in self._col_filters.items() if c != skip]
        if not preds:
            return fn

        def keep(r: dict) -> bool:
            if fn is not None and not fn(r):
                return False
            for p in preds:  # noqa: SIM110 - a plain loop is faster than all() over many rows
                if not p(r):
                    return False
            return True

        return keep

    def _compute(self) -> list[int]:
        fn = self._predicate()
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
        if role == PICTURE_ROLE:
            return c.picture(r) if c.picture else None
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


def _picture_delegate(parent):
    """Draws a cell's picture (PICTURE_ROLE) centred, at its own size, over the row's tint."""
    from PySide6.QtCore import QRectF
    from PySide6.QtWidgets import QStyle, QStyledItemDelegate

    class PictureDelegate(QStyledItemDelegate):
        def paint(self, painter, option, index) -> None:
            tint = index.data(Qt.ItemDataRole.BackgroundRole)
            if isinstance(tint, QColor) and not option.state & QStyle.StateFlag.State_Selected:
                painter.fillRect(option.rect, tint)
            super().paint(painter, option, index)
            pm = index.data(PICTURE_ROLE)
            if pm is None or pm.isNull():
                return
            dpr = pm.devicePixelRatio() or 1.0
            w, h = pm.width() / dpr, pm.height() / dpr
            r = option.rect
            painter.drawPixmap(QRectF(r.x() + (r.width() - w) / 2, r.y() + (r.height() - h) / 2, w, h), pm,
                               QRectF(pm.rect()))

    return PictureDelegate(parent)


def make_table(model: RecordModel, sortable: bool = True, filters: bool = False):
    """A QTableView set up the same way everywhere: row selection, chip cells, fixed column widths;
    with ``filters``, a filter button on each column header (see :mod:`.colfilter`)."""
    from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

    from .widgets import ChipDelegate

    view = QTableView()
    if filters:
        from .colfilter import FilterHeader

        view.setHorizontalHeader(FilterHeader(view))
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
    pictures = None
    for i, c in enumerate(model.columns):
        if c.chip:
            view.setItemDelegateForColumn(i, delegate)
        if c.picture:
            pictures = pictures or _picture_delegate(view)
            view.setItemDelegateForColumn(i, pictures)
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
    view._chip_delegate = delegate  # keep references
    view._picture_delegate = pictures
    return view
