"""Coverage: every file a parser looked at, and what came of it, with the full note of the selected one."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QWidget

from ..casedata import STATUS_LABEL, CaseData
from ..models import Column, RecordModel, make_table
from ..widgets import (
    Card,
    Chip,
    ChipRow,
    EmptyState,
    KeyValueGrid,
    SearchBox,
    Segmented,
    breakable,
    button,
    clear_layout,
    copy_text,
    hbox,
    label,
    overline,
    panel_scroll,
    tone_fn,
    tool_button,
    vbox,
)
from .timeline import FilterCombo

STATUS_TONE = {"parsed": ("ok", "ph.check-circle"), "empty": ("faint", "ph.circle-dashed"),
               "unrecognised": ("warn", "ph.question"), "error": ("bad", "ph.x-circle")}
STATUS_ORDER = ["parsed", "empty", "unrecognised", "error"]
STATUS_MEANING = {
    "parsed": "The file was read and records were taken from it.",
    "empty": "The file was read and held nothing relevant.",
    "unrecognised": "The file exists, but its format or schema is not one the parser knows: it was not parsed "
                    "and should be examined by hand.",
    "error": "The parser failed on this file; the note says why. Nothing from it is in the results.",
}


def status_chip(status: str):
    tone, icon_name = STATUS_TONE.get(status, ("faint", "ph.circle"))
    return STATUS_LABEL.get(status, status), tone_fn(tone), icon_name


class CoveragePage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.case: CaseData | None = None
        self.scope = Segmented([("all", "All")] + [(s, STATUS_LABEL[s]) for s in STATUS_ORDER])
        self.scope.changed.connect(lambda *_: self._apply())
        self.search = SearchBox("Search files and notes")
        self.search.search.connect(lambda *_: self._apply())
        self.f_parser = FilterCombo("All parsers")
        self.f_system = FilterCombo("All computers")
        for f in (self.f_parser, self.f_system):
            f.currentIndexChanged.connect(lambda *_: self._apply())
        self.count = label("", "Muted")

        self.model = RecordModel([
            Column("Status", lambda r: STATUS_LABEL.get(r["status"], r["status"]), 140,
                   chip=lambda r: status_chip(r["status"]), sort_key=lambda r: STATUS_ORDER.index(r["status"])
                   if r["status"] in STATUS_ORDER else 9),
            Column("Parser", lambda r: r.get("plugin") or "", 180, mono=True, tooltip=True),
            Column("File", lambda r: (r.get("source_path") or "(whole parser)").replace("\\", "/").rsplit("/", 1)[-1],
                   220, mono=True, tip=lambda r: r.get("source_path") or None),
            Column("Records", lambda r: f"{r.get('records', 0):,}", 84, align_right=True,
                   sort_key=lambda r: r.get("records", 0)),
            Column("Note", lambda r: " ".join((r.get("note") or "").split()), None, muted=True, tooltip=True),
        ])
        self.table = make_table(self.model)
        self.table.selectionModel().currentRowChanged.connect(lambda cur, _p: self._show(self.model.row(cur.row())))
        table_card = Card(padding=0, spacing=0)
        table_card.body.addWidget(self.table)
        table_card.layout().setContentsMargins(1, 1, 1, 1)

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

        lay = vbox(spacing=12, margins=(28, 24, 28, 24))
        lay.addLayout(hbox((vbox(label("Coverage", "H1"),
                                label("Every file a parser looked at. Empty means the file was read and held nothing "
                                      "relevant; unrecognised means its format was not one the parser knows.",
                                      "Muted", wrap=True), spacing=4), 1), self.count, spacing=16))
        lay.addLayout(hbox(self.scope, self.search, self.f_parser, self.f_system, spacing=8))
        lay.addWidget(split, 1)
        self.setLayout(lay)
        self._show(None)

    def set_case(self, case: CaseData) -> None:
        self.case = case
        counts = {s: sum(1 for c in case.coverage if c.get("status") == s) for s in STATUS_ORDER}
        self.scope.set_count("all", len(case.coverage))
        for s, n in counts.items():
            self.scope.set_count(s, n)
        self.scope.select("all")
        self.f_parser.set_items([(p, p) for p in sorted({c.get("plugin") or "" for c in case.coverage} - {""})])
        self.f_system.set_items([(s, s) for s in sorted({c.get("system") or "" for c in case.coverage} - {""})])
        self.model.set_rows(case.coverage)
        self._apply()

    def focus_item(self, status: str) -> None:
        self.scope.select(status)
        self._apply()

    def _apply(self) -> None:
        if not self.case:
            return
        scope = self.scope.current()
        terms = self.search.text().lower().split()
        parser, system = self.f_parser.value(), self.f_system.value()

        def keep(r: dict) -> bool:
            if scope != "all" and r.get("status") != scope:
                return False
            if parser and r.get("plugin") != parser:
                return False
            if system and r.get("system") != system:
                return False
            hay = f"{r.get('source_path', '')} {r.get('note', '')} {r.get('plugin', '')}".lower()
            return all(t in hay for t in terms)

        active = scope != "all" or bool(terms or parser or system)
        self.model.set_filter(keep if active else None)
        n, total = len(self.model.visible), len(self.model.rows)
        self.count.setText(f"{n:,} of {total:,} files" if active else f"{total:,} files")
        if n:
            self.table.selectRow(0)
        else:
            self._show(None)

    def _show(self, r: dict | None) -> None:
        clear_layout(self.detail_lay)
        if r is None:
            self.detail_lay.addWidget(EmptyState("ph.hand-pointing", "Nothing selected",
                                                 "Select a file to see its full note and where it is."))
            return
        status = r.get("status") or ""
        name = (r.get("source_path") or "(whole parser)").replace("\\", "/").rsplit("/", 1)[-1]
        self.detail_lay.addWidget(label(name, "H3", wrap=True, select=True))
        text, color, icon_name = status_chip(status)
        self.detail_lay.addWidget(ChipRow([Chip(text, color, icon_name)]))
        if status in STATUS_MEANING:
            self.detail_lay.addWidget(label(STATUS_MEANING[status], "Small", wrap=True))
        kv = KeyValueGrid()
        kv.add("Parser", r.get("plugin") or "—", mono=True)
        kv.add("Computer", r.get("system") or "—")
        kv.add("Records", f"{r.get('records', 0):,}")
        self.detail_lay.addWidget(kv)
        self.detail_lay.addWidget(overline("File"))
        path = r.get("source_path") or ""
        row = hbox(label(breakable(path or "(the parser as a whole)"), "Mono", wrap=True, select=True, mono=True),
                   spacing=4)
        row.setStretch(0, 1)
        if path:
            row.addWidget(tool_button("ph.copy", "Copy the path", lambda: copy_text(path, self, "Copied the path"), 14),
                          0, Qt.AlignmentFlag.AlignTop)
        self.detail_lay.addLayout(row)
        note = (r.get("note") or "").strip()
        if note:
            self.detail_lay.addWidget(overline("Note"))
            self.detail_lay.addWidget(label(breakable(note), None, wrap=True, select=True))
            self.detail_lay.addWidget(button("Copy the note", "Ghost", "ph.copy",
                                             lambda: copy_text(note, self, "Copied the note")))
        if path and r.get("records"):
            self.detail_lay.addWidget(button("Show its records in the timeline", None, "ph.clock-counter-clockwise",
                                             lambda: self.window_.go("timeline", {"search": path})))
        self.detail_lay.addStretch(1)
