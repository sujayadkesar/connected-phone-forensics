"""Recovered data: what the computers held of each phone's own data, to search and export.

Tiles count messages, calls, contacts, photos and files, notifications, apps and connection
records for the phone and connection chosen; the table below lists the chosen kind, sortable
by any column, and the panel at the right shows one item in full with its source. The rows
shown export to Excel or CSV; the case workbook and PDF summary open from the same menu.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHeaderView,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSplitter,
    QStackedWidget,
    QWidget,
)

from ... import content
from ...content import CATEGORIES
from .. import jobs
from ..casedata import CaseData
from ..icons import icon
from ..models import Column, RecordModel, make_table
from ..widgets import (
    THEME,
    Card,
    Chip,
    ChipRow,
    EmptyState,
    KeyValueGrid,
    SearchBox,
    Segmented,
    Toast,
    breakable,
    button,
    channel_color_fn,
    choose_save_file,
    clear_layout,
    copy_text,
    hbox,
    label,
    overline,
    panel_scroll,
    repolish,
    tone_fn,
    tool_button,
    vbox,
)
from .timeline import FilterCombo

SERIES = {"messages": 0, "calls": 2, "contacts": 6, "media": 1, "notifications": 3, "apps": 4, "connections": 5}
IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
THUMB = 148


def _color(key: str) -> str:
    return THEME.palette.series[SERIES.get(key, 0) % len(THEME.palette.series)]


class CategoryTile(QFrame):
    """A kind of data with its count; click to show it."""

    clicked = Signal(str)

    def __init__(self, key: str, title: str, icon_name: str):
        super().__init__()
        self.key, self.icon_name = key, icon_name
        self.setObjectName("Tile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.badge = QWidget()
        self.badge.setFixedSize(30, 30)
        self.badge.paintEvent = self._paint_badge
        self.count = label("0", "TileValue")
        self.title = label(title, "Small", wrap=True)
        self.sub = label("", "Faint", wrap=True)  # wraps rather than widening the tile
        self.setLayout(vbox(hbox(self.badge, "stretch"), self.count, self.title, self.sub, "stretch", spacing=1,
                            margins=(14, 12, 14, 12)))
        self.setMinimumWidth(104)
        THEME.on_change(self.badge.update)

    def _paint_badge(self, _e) -> None:
        p = QPainter(self.badge)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(_color(self.key))
        bg = QColor(c)
        bg.setAlpha(46)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(self.badge.rect(), 8, 8)
        icon(self.icon_name, c.name()).paint(p, self.badge.rect().adjusted(7, 7, -7, -7))

    def set(self, n: int, deleted: int, total: int) -> None:
        self.count.setText(f"{n:,}")
        self.sub.setText(f"{deleted:,} deleted, recovered" if deleted else (f"of {total:,}" if n != total else ""))
        self.setToolTip(f"{n:,} item(s) shown with the current filters; {total:,} in the case")

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", "true" if on else "false")
        repolish(self)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mouseReleaseEvent(e)


# The table shows the columns that tell items apart; the detail panel and the exports have them all.
# Deleted-and-recovered rows are tinted amber (with a warning icon); direction is an icon.
TABLE_KEYS = {
    "messages": ("time", "dir", "party", "text", "service", "phone"),
    "calls": ("time", "dir", "party", "duration", "service", "phone"),
    "contacts": ("name", "numbers", "emails", "organisation", "phone"),
    "media": ("time", "name", "kind", "party", "size", "mime", "phone"),
    "notifications": ("time", "app", "title", "text", "phone"),
    "apps": ("name", "package", "version", "listed_by", "phone"),
    "connections": ("time", "channel", "what", "text", "phone"),
}
_NARROW = {"service": 84, "duration": 96, "size": 96, "version": 110, "mime": 110, "kind": 140, "phone": 170,
           "party": 210}
_DIRECTION_ICON = {"received": ("ph.arrow-down-left", "ok"), "incoming": ("ph.arrow-down-left", "ok"),
                   "sent": ("ph.arrow-up-right", "accent"), "outgoing": ("ph.arrow-up-right", "accent"),
                   "missed": ("ph.phone-x", "bad"), "rejected": ("ph.phone-x", "bad"),
                   "blocked": ("ph.phone-x", "bad")}


def _recovered_icon(r: dict):
    return ("ph.warning", "warn") if r.get("status") else None


def _time_width() -> int:
    from PySide6.QtGui import QFontMetrics

    from ..widgets import mono_font

    return QFontMetrics(mono_font()).horizontalAdvance("2025-10-11 19:31:00") + 30


def _table_columns(cat) -> list[Column]:
    cols: list[Column] = []
    by_key = {c.key: c for c in cat.columns}
    first = True
    for key in TABLE_KEYS[cat.key]:
        mark = _recovered_icon if first else None  # the first column carries the recovered mark
        first = False
        if key == "dir":
            cols.append(Column("", lambda r: "", 40, sort_key=lambda r: r.get("direction") or "",
                               icon=lambda r: _DIRECTION_ICON.get(r.get("direction") or ""),
                               tip=lambda r: (r.get("direction") or "").capitalize() or None))
            continue
        c = by_key[key]
        tip = (lambda r: r.get("status") or None) if mark else None
        if c.kind == "time":
            cols.append(Column(c.title, lambda r: content.format_time(r.get("time")) or "—", _time_width() + 22,
                               mono=True, sort_key=lambda r: r.get("time") or "", icon=mark, tip=tip))
        elif c.key == "status":
            cols.append(Column(c.title, lambda r: r.get("status") or "", 150,
                               chip=lambda r: (r["status"], tone_fn("warn"), "ph.warning") if r.get("status")
                               else None, sort_key=lambda r: r.get("status") or ""))
        elif c.key == "channel":
            cols.append(Column(c.title, lambda r: r.get("channel") or "", 138,
                               chip=lambda r: (r.get("channel") or "", channel_color_fn(_channel_key(r)), None)))
        elif c.kind == "number":
            cols.append(Column(c.title, lambda r, k=c.key: "" if r.get(k) in (None, "") else f"{r[k]:,}"
                               if isinstance(r.get(k), int) else str(r[k]), 104, align_right=True,
                               sort_key=lambda r, k=c.key: _num(r.get(k))))
        elif c.kind == "long":
            cols.append(Column(c.title, lambda r, k=c.key: " ".join(str(r.get(k) or "").split()), None,
                               tooltip=True))
        else:
            w = _NARROW.get(c.key) or max(110, min(230, c.width * 7))
            cols.append(Column(c.title, lambda r, k=c.key: str(r.get(k) or ""), w + (22 if mark else 0),
                               tooltip=not mark, mono=c.kind == "path", icon=mark, tip=tip))
    return cols


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_CHANNEL_KEYS = {v: k for k, v in content.CHANNEL_LABEL.items()}


def _channel_key(r: dict) -> str:
    return _CHANNEL_KEYS.get(r.get("channel") or "", r.get("channel") or "other")


class DataPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.case: CaseData | None = None
        self.items: dict[str, list[dict]] = {c.key: [] for c in CATEGORIES}
        self.current = "messages"
        self._thumbs: dict[str, QIcon] = {}
        self._thumb_token = 0

        self.search = SearchBox("Search messages, names, numbers, apps, files…")
        self.search.search.connect(lambda *_: self._apply())
        self.f_phone = FilterCombo("All phones", 220)
        self.f_phone.setMaximumWidth(340)
        self.f_channel = FilterCombo("All connections", 170)
        for f in (self.f_phone, self.f_channel):
            f.currentIndexChanged.connect(lambda *_: self._apply())
        self.deleted_only = QCheckBox("Deleted, recovered only")
        self.deleted_only.toggled.connect(lambda *_: self._apply())
        self.clear_btn = button("Clear", "Ghost", "ph.x", self._clear)
        self.export_btn = button("Export", None, "ph.export")
        menu = QMenu(self.export_btn)
        menu.addAction("Rows shown as an Excel workbook…", self._export_xlsx)
        menu.addAction("Rows shown as CSV…", self._export_csv)
        menu.addSeparator()
        menu.addAction("Open the case workbook (every phone)", lambda: self._open_output("workbook.xlsx"))
        menu.addAction("Open the PDF summary", lambda: self._open_output("summary.pdf"))
        self.export_btn.setMenu(menu)

        self.tiles: dict[str, CategoryTile] = {}
        tiles_row = hbox(spacing=10)
        for c in CATEGORIES:
            t = CategoryTile(c.key, c.title, c.icon)
            t.clicked.connect(self.show_category)
            self.tiles[c.key] = t
            tiles_row.addWidget(t, 1)

        # table / grid
        self.models = {c.key: RecordModel(_table_columns(c), row_tint=lambda r: "warn" if r.get("status") else None)
                       for c in CATEGORIES}
        self.tables = {}
        self.views = QStackedWidget()
        for c in CATEGORIES:
            tv = make_table(self.models[c.key])
            header = tv.horizontalHeader()
            for i, col in enumerate(self.models[c.key].columns):
                if col.width is None:  # long text: a generous width; the last column takes what is left
                    header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                    tv.setColumnWidth(i, 360)
            header.setStretchLastSection(True)
            tv.selectionModel().currentRowChanged.connect(
                lambda cur, _p, k=c.key: self._show(self.models[k].row(cur.row())))
            self.tables[c.key] = tv
            self.views.addWidget(tv)
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setIconSize(QSize(THUMB, THUMB))
        self.grid.setGridSize(QSize(THUMB + 26, THUMB + 46))
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setWordWrap(True)
        self.grid.setUniformItemSizes(True)
        self.grid.currentRowChanged.connect(self._grid_selected)
        self.views.addWidget(self.grid)
        self.layout_toggle = Segmented([("list", "List"), ("grid", "Grid")])
        self.layout_toggle.changed.connect(lambda *_: self._apply())
        self.table_title = label("", "H3")
        self.table_note = label("", "Small")
        self.empty = EmptyState("ph.tray", "Nothing to show", "")
        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.views)
        self.view_stack.addWidget(self.empty)
        table_card = Card(padding=0, spacing=0)
        head = hbox(self.table_title, self.table_note, "stretch", self.layout_toggle, spacing=10,
                    margins=(16, 12, 12, 10))
        table_card.body.addLayout(head)
        table_card.body.addWidget(self.view_stack, 1)
        table_card.layout().setContentsMargins(1, 1, 1, 1)

        # detail
        self.detail = QWidget()
        self.detail_lay = vbox(spacing=12, margins=(18, 16, 18, 18))
        self.detail.setLayout(self.detail_lay)
        detail_card = Card(padding=0, spacing=0)
        detail_card.body.addWidget(panel_scroll(self.detail))
        detail_card.layout().setContentsMargins(1, 1, 1, 1)
        detail_card.setMinimumWidth(340)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(14)
        split.setChildrenCollapsible(False)
        split.addWidget(table_card)
        split.addWidget(detail_card)
        split.setStretchFactor(0, 1)
        split.setSizes([1000, 380])
        split.setStyleSheet("QSplitter::handle { background: transparent; }")

        self.search.setMinimumWidth(240)
        lay = vbox(spacing=12, margins=(28, 24, 28, 24))
        intro = label("What the computers held of each phone's own data: messages, calls, contacts, photos and "
                      "files, notifications, apps and connection records.", "Muted", wrap=True)
        lay.addLayout(hbox((vbox(label("Recovered data", "H1"), intro, spacing=2), 1), self.export_btn, spacing=16))
        lay.addLayout(hbox(self.search, self.f_phone, self.f_channel, self.deleted_only, self.clear_btn, spacing=8))
        lay.addLayout(tiles_row)
        lay.addWidget(split, 1)
        self.setLayout(lay)
        self._show(None)

    # -- data ------------------------------------------------------------------------

    def set_case(self, case: CaseData) -> None:
        self.case = case
        self.items = content.extract(case.records, case.devices)
        self._thumbs.clear()
        for rows in self.items.values():
            for r in rows:
                r["_search"] = " ".join(str(v) for k, v in r.items()
                                        if not k.startswith("_") and isinstance(v, (str, int)) and v != "").lower()
        counts = content.counts(self.items)
        phones = [(d["id"], f"{d.get('label') or d['id']}  ·  {d['id']}  ({sum(counts.get(d['id'], {}).values()):,})")
                  for d in case.phones]
        extra = [("__none__", f"{content.UNLINKED} ({sum(counts[None].values()):,})")] if counts.get(None) else []
        self.f_phone.set_items(phones + extra)
        channels = sorted({r["channel"] for rows in self.items.values() for r in rows if r.get("channel")})
        self.f_channel.set_items([(c, c) for c in channels])
        for k, m in self.models.items():
            m.set_rows(self.items[k])
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        first = next((c.key for c in CATEGORIES if self.items[c.key]), "connections")
        self.current = first
        self._apply()

    def focus_item(self, arg) -> None:
        """A category key, or ``{"device": id, "category": key}`` from the devices page."""
        if isinstance(arg, str) and arg in self.tiles:
            arg = {"category": arg}
        if not isinstance(arg, dict):
            return
        self._clear(apply=False)
        if arg.get("device"):
            self.f_phone.select(arg["device"])
        if arg.get("category") in self.tiles:
            self.current = arg["category"]
        self._apply()

    def show_category(self, key: str) -> None:
        self.current = key
        self._apply()

    def _clear(self, apply: bool = True) -> None:
        for f in (self.f_phone, self.f_channel):
            f.blockSignals(True)
            f.setCurrentIndex(0)
            f.blockSignals(False)
        self.deleted_only.blockSignals(True)
        self.deleted_only.setChecked(False)
        self.deleted_only.blockSignals(False)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        if apply:
            self._apply()

    def _filter(self):
        terms = self.search.text().lower().split()
        phone, chan, deleted = self.f_phone.value(), self.f_channel.value(), self.deleted_only.isChecked()
        want_dev = None if phone == "__none__" else phone

        def keep(r: dict) -> bool:
            if phone and r["device"] != want_dev:
                return False
            if chan and r.get("channel") != chan:
                return False
            if deleted and not r.get("status"):
                return False
            blob = r["_search"]
            return all(t in blob for t in terms)

        active = bool(terms or phone or chan or deleted)
        return (keep if active else None), active

    def _apply(self) -> None:
        if self.case is None:
            return
        keep, active = self._filter()
        for c in CATEGORIES:
            rows = self.items[c.key]
            shown = rows if keep is None else [r for r in rows if keep(r)]
            self.tiles[c.key].set(len(shown), sum(1 for r in shown if r.get("status")), len(rows))
            self.tiles[c.key].set_selected(c.key == self.current)
        cat = content.BY_KEY[self.current]
        model = self.models[self.current]
        model.set_filter(keep)
        n, total = len(model.visible), len(model.rows)
        self.table_title.setText(cat.title)
        self.table_note.setText((f"{n:,} of {total:,}" if active else f"{total:,}") + f"  ·  {cat.description}")
        table = self.tables[self.current]
        phone_col = TABLE_KEYS[self.current].index("phone")
        table.setColumnHidden(phone_col, bool(self.f_phone.value()))
        self.clear_btn.setVisible(active)
        is_media = self.current == "media"
        self.layout_toggle.setVisible(is_media)
        grid = is_media and self.layout_toggle.current() == "grid"
        if n == 0:
            self.empty.title.setText(f"No {cat.title.lower()}" if total == 0 else "Nothing matches")
            self.empty.text.setText("None were recovered in this case." if total == 0 else
                                    "No item matches the filters; clear them to see everything.")
            self.view_stack.setCurrentWidget(self.empty)
            self._show(None)
        else:
            self.view_stack.setCurrentWidget(self.views)
            if grid:
                self._fill_grid(model.visible_rows())
                self.views.setCurrentWidget(self.grid)
                self.grid.setCurrentRow(0)
            else:
                self.views.setCurrentWidget(table)
                table.selectRow(0)
                self._show(model.row(0))
        self.export_btn.setEnabled(True)

    # -- photo grid --------------------------------------------------------------------

    def _file(self, r: dict) -> Path | None:
        if not r.get("file") or self.case is None or self.case.outdir is None:
            return None
        from ...evidence import long_path

        p = Path(self.case.outdir) / "exports" / r["file"]
        return p if long_path(p).is_file() else None  # case folders can be deeper than 260 characters

    def _fill_grid(self, rows: list[dict]) -> None:
        self.grid.clear()
        self._grid_rows = rows
        placeholder = icon("ph.image-square", THEME.palette.faint)
        wanted = []
        for r in rows:
            item = QListWidgetItem(placeholder, f"{r.get('name') or '(no name)'}\n{r.get('kind') or ''}")
            item.setToolTip(f"{r.get('name')}\n{r.get('phone')}\n{content.format_time(r.get('time'))}")
            f = self._file(r)
            if f is not None and f.suffix.lower() in IMAGE_TYPES:
                if str(f) in self._thumbs:
                    item.setIcon(self._thumbs[str(f)])
                else:
                    wanted.append(str(f))
            self.grid.addItem(item)
        if wanted:
            self._thumb_token += 1
            token = self._thumb_token

            def work(_job):
                out = {}
                for path in wanted:
                    img = QImage(path)
                    if not img.isNull():
                        out[path] = img.scaled(THUMB * 2, THUMB * 2, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation)
                return out

            jobs.run(work, done=lambda images: self._thumbs_ready(token, images))

    def _thumbs_ready(self, token: int, images: dict) -> None:
        for path, img in images.items():
            canvas = QPixmap(THUMB * 2, THUMB * 2)
            canvas.fill(Qt.GlobalColor.transparent)
            p = QPainter(canvas)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage((canvas.width() - img.width()) // 2, (canvas.height() - img.height()) // 2, img)
            p.end()
            canvas.setDevicePixelRatio(2.0)
            self._thumbs[path] = QIcon(canvas)
        if token != self._thumb_token:
            return
        for i, r in enumerate(getattr(self, "_grid_rows", [])):
            f = self._file(r)
            if f is not None and str(f) in self._thumbs and self.grid.item(i):
                self.grid.item(i).setIcon(self._thumbs[str(f)])

    def _grid_selected(self, row: int) -> None:
        rows = getattr(self, "_grid_rows", [])
        self._show(rows[row] if 0 <= row < len(rows) else None)

    # -- detail ----------------------------------------------------------------------

    def _show(self, r: dict | None) -> None:
        clear_layout(self.detail_lay)
        if r is None:
            self.detail_lay.addWidget(EmptyState("ph.hand-pointing", "Nothing selected",
                                                 "Select an item to see it in full, with the file and record it "
                                                 "came from."))
            return
        cat = content.BY_KEY[r["category"]]
        title = {"messages": r.get("text") or "(no text)",
                 "calls": f"{(r.get('direction') or '').capitalize()} call · {r.get('party') or 'unknown'}",
                 "contacts": r.get("name") or "(no name)", "media": r.get("name") or "(file)",
                 "notifications": " — ".join(x for x in (r.get("app"), r.get("title")) if x) or "(notification)",
                 "apps": r.get("name") or r.get("package") or "(app)",
                 "connections": r.get("what") or ""}[r["category"]]
        self.detail_lay.addWidget(label(title, "H3", wrap=True, select=True))
        chips = [Chip(cat.title, lambda _p, k=r["category"]: _color(k))]
        if r.get("channel"):
            chips.append(Chip.channel(_channel_key(r), r["channel"]))
        if r.get("status"):
            chips.append(Chip.status(r["status"], "warn", "ph.warning",
                                     "Recovered from a deleted or overwritten database record"))
        self.detail_lay.addWidget(ChipRow(chips))

        f = self._file(r)
        if f is not None and f.suffix.lower() in IMAGE_TYPES:
            img = QImage(str(f))
            if not img.isNull():
                pic = label("")
                pm = QPixmap.fromImage(img.scaled(640, 640, Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation))
                pm.setDevicePixelRatio(2.0)
                pic.setPixmap(pm)
                pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
                pic.setObjectName("Preview")
                self.detail_lay.addWidget(pic)

        kv = KeyValueGrid()
        for c in cat.columns:
            if c.key == "source":
                continue
            v = content.cell(r, c)
            if v in ("", None):
                continue
            kv.add(c.title, breakable(str(v)) if c.kind == "path" else str(v), mono=c.kind in ("time", "path"))
        if r.get("device"):
            kv.add("Phone record", button(f"{r['phone']}  ({r['device']})", "Link", None,
                                          lambda d=r["device"]: self.window_.go("devices", d)))
        self.detail_lay.addWidget(kv)

        actions = hbox(spacing=8)
        actions.addWidget(button("Show in timeline", None, "ph.clock-counter-clockwise",
                                 lambda i=r["record"]: self.window_.go("timeline", {"record": i})))
        if f is not None:
            actions.addWidget(button("Open file", None, "ph.arrow-square-out", lambda p=f: self.window_.open_path(p)))
            actions.addWidget(button("Folder", "Ghost", "ph.folder-open",
                                     lambda p=f: self.window_.open_path(p.parent)))
        actions.addStretch(1)
        self.detail_lay.addLayout(actions)

        self.detail_lay.addSpacing(4)
        self.detail_lay.addWidget(overline("Source"))
        src = KeyValueGrid()
        src.add("File", self._copyable(r.get("source_path") or "—", "Copied the path"))
        src.add("Record", self._copyable(r.get("locator") or "—", "Copied the locator"))
        src.add("SHA-256", self._copyable(r.get("sha256") or "—", "Copied the hash"))
        src.add("Parser", r.get("artifact") or "—", mono=True)
        self.detail_lay.addWidget(src)
        self.detail_lay.addStretch(1)

    def _copyable(self, text: str, toast: str) -> QWidget:
        w = QWidget()
        lb = label(breakable(text), "Mono", wrap=True, select=True, mono=True)
        lb.setMinimumWidth(10)
        lay = hbox(lb, spacing=4)
        lay.setStretch(0, 1)
        if text != "—":
            lay.addWidget(tool_button("ph.copy", "Copy", lambda: copy_text(text, self, toast), 14),
                          0, Qt.AlignmentFlag.AlignTop)
        w.setLayout(lay)
        return w

    # -- export ------------------------------------------------------------------------

    def _view_title(self) -> str:
        cat = content.BY_KEY[self.current]
        bits = [cat.title]
        if self.f_phone.value():
            bits.append(self.f_phone.currentText().split("  ·  ")[0])
        if self.f_channel.value():
            bits.append(self.f_channel.currentText())
        if self.deleted_only.isChecked():
            bits.append("deleted, recovered")
        if self.search.text().strip():
            bits.append(f"“{self.search.text().strip()}”")
        return " · ".join(bits)

    def _export(self, suffix: str, filters: str, writer) -> None:
        if not self.case:
            return
        rows = self.models[self.current].visible_rows()
        name = "".join(ch if ch.isalnum() or ch in " -_" else "-" for ch in self._view_title())[:80].strip()
        start = str((self.case.outdir or Path.home()) / f"{name}{suffix}")
        path = choose_save_file(self, "Export the rows shown", start, filters)
        if not path:
            return
        cat, title, case = content.BY_KEY[self.current], self._view_title(), self.case

        def work(_job):
            writer(Path(path), cat, rows, title, case)
            return len(rows)

        jobs.run(work, done=lambda n: Toast.show_for(self, f"Exported {n:,} rows to {Path(path).name}"),
                 failed=lambda msg, _d: Toast.show_for(self, f"Export failed: {msg}", 4000))

    def _export_xlsx(self) -> None:
        from ...output.excel import write_view

        self._export(".xlsx", "Excel workbook (*.xlsx)",
                     lambda p, cat, rows, title, case: write_view(p, cat, rows, title=title, case=case))

    def _export_csv(self) -> None:
        from ...output.excel import write_view_csv

        self._export(".csv", "CSV files (*.csv)", lambda p, cat, rows, _t, _c: write_view_csv(p, cat, rows))

    def _open_output(self, name: str) -> None:
        """Open the case workbook or PDF summary, writing it first for a case scanned by an older version."""
        if not self.case or not self.case.outdir:
            return
        path = Path(self.case.outdir) / name
        if path.is_file():
            self.window_.open_path(path)
            return
        case, items = self.case, self.items
        Toast.show_for(self, f"Writing {name}…", 1500)

        def work(_job):
            if name == "workbook.xlsx":
                from ...output.excel import write_workbook

                write_workbook(case, path, items)
            else:
                from ...output.pdf import write_pdf

                write_pdf(case, path, items)
            return path

        jobs.run(work, done=self.window_.open_path,
                 failed=lambda msg, _d: Toast.show_for(self, f"Could not write {name}: {msg}", 4000))
