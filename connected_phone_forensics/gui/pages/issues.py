"""Issues: parser warnings and errors, with the full message of the selected one."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPlainTextEdit, QSplitter, QStackedWidget, QWidget

from ..casedata import CaseData
from ..models import Column, RecordModel, make_table
from ..widgets import Card, EmptyState, KeyValueGrid, button, hbox, label, mono_font, tone_fn, vbox


class IssuesPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.model = RecordModel([
            Column("Kind", lambda r: r["kind"], 120,
                   chip=lambda r: (r["kind"], tone_fn("bad" if r["kind"] == "Error" else "warn"),
                                   "ph.x-circle" if r["kind"] == "Error" else "ph.warning")),
            Column("Parser", lambda r: r.get("plugin") or "", 180, mono=True),
            Column("Computer", lambda r: r.get("system") or "—", 150, muted=True),
            Column("Message", lambda r: (r.get("message") or "").splitlines()[0] if r.get("message") else "", None,
                   tooltip=True),
            Column("File", lambda r: r.get("source_path") or "", 260, mono=True, muted=True, tooltip=True),
        ])
        self.table = make_table(self.model)
        self.table.selectionModel().currentRowChanged.connect(lambda cur, _p: self._show(self.model.row(cur.row())))
        self.count = label("", "Muted")

        self.kv = KeyValueGrid()
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(mono_font())
        detail = Card("Details")
        detail.body.addWidget(self.kv)
        detail.body.addWidget(self.text, 1)
        detail.setMinimumWidth(380)
        table_card = Card(padding=0, spacing=0)
        table_card.body.addWidget(self.table)
        table_card.layout().setContentsMargins(1, 1, 1, 1)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(14)
        split.setStyleSheet("QSplitter::handle { background: transparent; }")
        split.setChildrenCollapsible(False)
        split.addWidget(table_card)
        split.addWidget(detail)
        split.setStretchFactor(0, 1)
        split.setSizes([860, 420])

        self.empty = EmptyState("ph.check-circle", "No issues", "Every parser ran without warnings or errors.",
                                button("Back to overview", None, "ph.arrow-left", lambda: window.go("overview")),
                                tone="ok")
        self.stack = QStackedWidget()
        self.stack.addWidget(split)
        self.stack.addWidget(self.empty)

        lay = vbox(spacing=12, margins=(28, 24, 28, 24))
        lay.addLayout(hbox((vbox(label("Issues", "H1"),
                                label("Warnings and errors from the parsers. A warning means something may be "
                                      "missing or uncertain; read it before relying on the result.", "Muted",
                                      wrap=True), spacing=4), 1), self.count, spacing=16))
        lay.addWidget(self.stack, 1)
        self.setLayout(lay)

    def set_case(self, case: CaseData) -> None:
        rows = [{"kind": "Error", "plugin": c.get("plugin"), "system": c.get("system"),
                 "source_path": c.get("source_path"), "message": c.get("note") or "parser error"}
                for c in case.errors]
        rows += [{"kind": "Warning", **w} for w in case.warnings]
        self.model.set_rows(rows)
        self.count.setText(f"{len(rows):,} issue{'s' if len(rows) != 1 else ''}")
        self.stack.setCurrentIndex(0 if rows else 1)
        if rows:
            self.table.selectRow(0)

    def _show(self, r: dict | None) -> None:
        self.kv.clear()
        self.text.setPlainText("")
        if not r:
            return
        self.kv.add("Parser", r.get("plugin") or "—", mono=True)
        self.kv.add("Computer", r.get("system") or "—")
        self.kv.add("File", r.get("source_path") or "—", mono=True)
        self.text.setPlainText(r.get("message") or "")
