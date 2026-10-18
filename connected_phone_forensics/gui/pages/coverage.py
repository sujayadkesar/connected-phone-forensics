"""Coverage: every file a parser looked at, and what came of it."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ..casedata import STATUS_LABEL, CaseData
from ..models import Column, RecordModel, make_table
from ..widgets import Card, SearchBox, Segmented, hbox, label, tone_fn, vbox
from .timeline import FilterCombo

STATUS_TONE = {"parsed": ("ok", "ph.check-circle"), "empty": ("faint", "ph.circle-dashed"),
               "unrecognised": ("warn", "ph.question"), "error": ("bad", "ph.x-circle")}
STATUS_ORDER = ["parsed", "empty", "unrecognised", "error"]


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
            Column("Parser", lambda r: r.get("plugin") or "", 190, mono=True),
            Column("Computer", lambda r: r.get("system") or "", 150, muted=True, tooltip=True),
            Column("File", lambda r: r.get("source_path") or "(whole parser)", None, mono=True, tooltip=True),
            Column("Records", lambda r: f"{r.get('records', 0):,}", 90, align_right=True,
                   sort_key=lambda r: r.get("records", 0)),
            Column("Note", lambda r: r.get("note") or "", 320, muted=True, tooltip=True),
        ])
        self.table = make_table(self.model)
        table_card = Card(padding=0, spacing=0)
        table_card.body.addWidget(self.table)
        table_card.layout().setContentsMargins(1, 1, 1, 1)

        lay = vbox(spacing=12, margins=(28, 24, 28, 24))
        lay.addLayout(hbox((vbox(label("Coverage", "H1"),
                                label("Every file a parser looked at. Empty means the file was read and held nothing "
                                      "relevant; unrecognised means its format was not one the parser knows.",
                                      "Muted", wrap=True), spacing=4), 1), self.count, spacing=16))
        lay.addLayout(hbox(self.scope, self.search, self.f_parser, self.f_system, spacing=8))
        lay.addWidget(table_card, 1)
        self.setLayout(lay)

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
