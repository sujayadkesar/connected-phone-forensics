"""Timeline: every record, filterable, with the full provenance of the selected one."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QPlainTextEdit, QSplitter, QWidget

from ...output.structured import csv_safe
from .. import jobs
from ..casedata import CaseData, channel_label, fmt_time
from ..colfilter import FilterBar, install_cell_menu
from ..models import Column, RecordModel, make_table
from ..widgets import (
    Card,
    Chip,
    ChipRow,
    EmptyState,
    KeyValueGrid,
    SearchBox,
    Toast,
    breakable,
    button,
    channel_color_fn,
    choose_save_file,
    clear_layout,
    copy_text,
    hbox,
    label,
    mono_font,
    overline,
    panel_scroll,
    tool_button,
    vbox,
)

EXPORT_COLS = ["datetime", "timestamp_desc", "time_source", "device", "device_label", "channel", "artifact",
               "event", "message", "user", "system", "source_path", "locator", "sha256"]


class FilterCombo(QComboBox):
    def __init__(self, all_text: str, width: int = 170):
        super().__init__()
        self.all_text = all_text
        self.setMinimumWidth(width)
        self.setMaximumWidth(260)

    def set_items(self, items: list[tuple[str, str]]) -> None:
        self.blockSignals(True)
        self.clear()
        self.addItem(self.all_text, "")
        for value, text in items:
            self.addItem(text, value)
        self.blockSignals(False)

    def value(self) -> str:
        return self.currentData() or ""

    def select(self, value: str) -> None:
        i = self.findData(value)
        self.setCurrentIndex(i if i >= 0 else 0)


class TimelinePage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.case: CaseData | None = None

        self.search = SearchBox("Search messages, identifiers, paths, data…")
        self.search.search.connect(lambda *_: self._apply())
        self.f_device = FilterCombo("All devices", 190)
        self.f_channel = FilterCombo("All connections")
        self.f_system = FilterCombo("All computers")
        self.f_parser = FilterCombo("All parsers")
        for f in (self.f_device, self.f_channel, self.f_system, self.f_parser):
            f.currentIndexChanged.connect(lambda *_: self._apply())
        self.clear_btn = button("Clear", "Ghost", "ph.x", self._clear)
        self.count = label("", "Muted")
        self.export_btn = button("Export CSV", None, "ph.download-simple", self._export,
                                 "Save the events shown (with the current filters) as CSV")

        self.model = RecordModel([
            Column("Time (UTC)", lambda r: fmt_time(r.get("datetime")), 184, mono=True,
                   sort_key=lambda r: r.get("timestamp"), kind="time"),
            Column("Device", lambda r: r.get("device_label") or "—", 150, tooltip=True),
            Column("Connection", lambda r: channel_label(r.get("channel")), 138,
                   chip=lambda r: (channel_label(r.get("channel")), channel_color_fn(r.get("channel") or ""), None)),
            Column("What the time means", lambda r: r.get("timestamp_desc") or "", 170, muted=True, tooltip=True),
            Column("Details", lambda r: r.get("message") or "", None, tooltip=True),
        ])
        self.table = make_table(self.model, filters=True)
        self.table.selectionModel().currentRowChanged.connect(lambda cur, _p: self._show(self.model.row(cur.row())))
        install_cell_menu(self.table)
        self.model.filters_changed.connect(self._apply)
        self.filter_bar = FilterBar(self.table)

        table_card = Card(padding=0, spacing=0)
        bar_box = QWidget()
        bar_box.setLayout(vbox(self.filter_bar, margins=(14, 10, 14, 2)))
        table_card.body.addWidget(bar_box)
        table_card.body.addWidget(self.table)
        table_card.layout().setContentsMargins(1, 1, 1, 1)

        # detail panel
        self.detail = QWidget()
        self.detail_lay = vbox(spacing=12, margins=(18, 16, 18, 18))
        self.detail.setLayout(self.detail_lay)
        detail_card = Card(padding=0, spacing=0)
        self.detail_scroll = panel_scroll(self.detail)
        detail_card.body.addWidget(self.detail_scroll)
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

        toolbar = hbox(self.search, self.f_device, self.f_channel, self.f_system, self.f_parser, self.clear_btn,
                       spacing=8)
        self.search.setMinimumWidth(240)
        lay = vbox(spacing=12, margins=(28, 24, 28, 24))
        lay.addLayout(hbox(label("Timeline", "H1"), "stretch", self.count, self.export_btn, spacing=12))
        lay.addLayout(toolbar)
        lay.addWidget(split, 1)
        self.setLayout(lay)
        self._show(None)

    # -- data ------------------------------------------------------------------------

    def set_case(self, case: CaseData) -> None:
        self.case = case
        devices = sorted({(r["device"], r.get("device_label") or r["device"]) for r in case.records if r.get("device")},
                         key=lambda kv: (kv[1].lower(), kv[0]))
        self.f_device.set_items([(d, f"{lbl}  ·  {d}") for d, lbl in devices] + [("__none__", "(no device)")])
        self.f_channel.set_items([(c, channel_label(c)) for c, _n in case.channel_counts()])
        self.f_system.set_items([(s, s) for s in sorted({r.get("system") or "" for r in case.records} - {""})])
        self.f_parser.set_items([(a, a) for a in sorted({r.get("artifact") or "" for r in case.records} - {""})])
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.model.set_rows(case.records)
        self._clear()

    def focus_item(self, arg) -> None:
        """``{"device": id}`` filters to a device, ``{"search": text}`` searches, ``{"record": index}``
        selects one record."""
        if isinstance(arg, dict) and arg.get("search"):
            self._clear(apply=False)
            self.search.blockSignals(True)
            self.search.setText(arg["search"])
            self.search.blockSignals(False)
            self._apply()
        elif isinstance(arg, dict) and arg.get("device"):
            self._clear(apply=False)
            self.f_device.select(arg["device"])
            self._apply()
        elif isinstance(arg, dict) and isinstance(arg.get("record"), int) and self.case:
            self._clear(apply=False)
            self._apply()
            target = self.case.records[arg["record"]] if 0 <= arg["record"] < len(self.case.records) else None
            row = next((i for i in range(len(self.model.visible)) if self.model.row(i) is target), -1)
            if row >= 0:
                self.table.selectRow(row)
                self.table.scrollTo(self.model.index(row, 0), self.table.ScrollHint.PositionAtCenter)

    def _clear(self, apply: bool = True) -> None:
        for f in (self.f_device, self.f_channel, self.f_system, self.f_parser):
            f.blockSignals(True)
            f.setCurrentIndex(0)
            f.blockSignals(False)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.model.filters_changed.disconnect(self._apply)
        self.model.clear_column_filters()
        self.model.filters_changed.connect(self._apply)
        if apply:
            self._apply()

    def _apply(self) -> None:
        if not self.case:
            return
        terms = self.search.text().lower().split()
        dev, chan, sys_, parser = (self.f_device.value(), self.f_channel.value(), self.f_system.value(),
                                   self.f_parser.value())

        # Only the active conditions are checked, so a plain search over 100,000 records stays fast.
        fields = [(k, v) for k, v in (("device", dev), ("channel", chan), ("system", sys_), ("artifact", parser)) if v]

        def keep(r: dict) -> bool:
            for key, want in fields:
                if (r.get(key) or "__none__") != want:
                    return False
            blob = r["_search"]
            for t in terms:  # noqa: SIM110 - a plain loop is faster than all() over 100k rows
                if t not in blob:
                    return False
            return True

        active = bool(terms or fields)
        self.model.set_filter(keep if active else None)
        active = active or bool(self.model.column_filters())
        n, total = len(self.model.visible), len(self.model.rows)
        self.count.setText(f"{n:,} of {total:,} records" if active else f"{total:,} records")
        self.clear_btn.setVisible(active)
        self.filter_bar.refresh()
        self.export_btn.setEnabled(n > 0)
        if n:
            self.table.selectRow(0)
        else:
            self._show(None)

    # -- detail ----------------------------------------------------------------------

    def _show(self, r: dict | None) -> None:
        clear_layout(self.detail_lay)
        if r is None:
            self.detail_lay.addWidget(EmptyState("ph.hand-pointing", "Nothing selected",
                                                 "Select a record to see where it came from: the file, the exact "
                                                 "record inside it and the file's SHA-256."))
            return
        msg = label(r.get("message") or "", "H3", wrap=True, select=True)
        self.detail_lay.addWidget(msg)
        chips = [Chip.channel(r.get("channel") or "other")]
        if r.get("time_source") == "filesystem":
            chips.append(Chip.status("File time", "warn", "ph.warning",
                                     "This time is filesystem metadata, which depends on how the evidence was "
                                     "copied. It is not used for a device's first or last seen."))
        if r.get("phone_os"):
            chips.append(Chip(r["phone_os"]))
        self.detail_lay.addWidget(ChipRow(chips))

        kv = KeyValueGrid()
        kv.add("Time (UTC)", fmt_time(r.get("datetime")), mono=True)
        kv.add("Meaning", r.get("timestamp_desc") or "—")
        if r.get("device"):
            dev_btn = button(f"{r.get('device_label') or r['device']}  ({r['device']})", "Link", None,
                             lambda d=r["device"]: self.window_.go("devices", d))
            kv.add("Device", dev_btn)
        else:
            kv.add("Device", "Not linked to a device")
        kv.add("Computer", r.get("system") or "—")
        kv.add("User", r.get("user") or "—")
        kv.add("Event", r.get("event") or "—", mono=True)
        kv.add("Parser", r.get("artifact") or "—", mono=True)
        self.detail_lay.addWidget(kv)

        self.detail_lay.addSpacing(4)
        self.detail_lay.addWidget(overline("Source"))
        src = KeyValueGrid()
        src.add("File", self._copyable(r.get("source_path") or "—", "Copied the path"))
        src.add("Record", self._copyable(r.get("locator") or "—", "Copied the locator"))
        src.add("SHA-256", self._copyable(r.get("sha256") or "—", "Copied the hash"))
        self.detail_lay.addWidget(src)

        ids = r.get("device_ids") or []
        if ids:
            self.detail_lay.addSpacing(4)
            self.detail_lay.addWidget(overline("Identifiers in this record"))
            for i in ids:
                self.detail_lay.addWidget(label(i, "Mono", wrap=True, select=True, mono=True))

        data = r.get("data")
        if data:
            self.detail_lay.addSpacing(4)
            self.detail_lay.addWidget(overline("Parsed data"))
            box = QPlainTextEdit(json.dumps(data, indent=2, ensure_ascii=False, default=str))
            box.setReadOnly(True)
            box.setFont(mono_font())
            box.setMinimumHeight(140)
            box.setMaximumHeight(320)
            self.detail_lay.addWidget(box)
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

    # -- export ----------------------------------------------------------------------

    def _export(self) -> None:
        if not self.case:
            return
        start = str((self.case.outdir or Path.home()) / "timeline-filtered.csv")
        path = choose_save_file(self, "Export the records shown", start, "CSV files (*.csv)")
        if not path:
            return
        rows = self.model.visible_rows()

        def work(_job):
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(EXPORT_COLS + ["device_ids", "data"])
                for r in rows:
                    w.writerow([csv_safe(r.get(c)) for c in EXPORT_COLS]
                               + [csv_safe(";".join(r.get("device_ids") or [])),
                                  csv_safe(json.dumps(r.get("data") or {}, ensure_ascii=False, default=str))])
            return len(rows)

        jobs.run(work, done=lambda n: Toast.show_for(self, f"Exported {n:,} records to {Path(path).name}"),
                 failed=lambda msg, _d: Toast.show_for(self, f"Export failed: {msg}", 4000))
