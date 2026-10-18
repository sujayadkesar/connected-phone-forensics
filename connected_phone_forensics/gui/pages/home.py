"""New scan: choose the evidence, name the case, pick parsers, start."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QGridLayout, QLineEdit, QSpinBox, QWidget

from ... import engine
from ...plugin import load_plugins
from .. import jobs
from ..casedata import OS_LABEL, VALIDATION_LABEL
from ..icons import OS_ICON
from ..widgets import (
    THEME,
    Card,
    Chip,
    ChipRow,
    DropZone,
    IconBadge,
    PageHeader,
    PageScroll,
    PathField,
    Spinner,
    Toast,
    button,
    clear_layout,
    hbox,
    label,
    overline,
    tone_fn,
    vbox,
)

OS_ORDER = ("windows", "macos", "linux")


def demo_folder() -> Path:
    """A new folder for the demo case's evidence and report."""
    return Path(tempfile.mkdtemp(prefix="cpf-demo-"))


def _default_output(evidence: Path | None, case_ref: str) -> Path:
    base = Path.home() / "Documents" / "Connected Phone Forensics"
    name = (case_ref.strip() or (evidence.name if evidence else "") or "case").replace("/", "-").replace("\\", "-")
    return base / f"{name} {datetime.now():%Y-%m-%d %H%M}"


class HomePage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self._discover_token = 0
        self._systems: list[dict] | None = None
        self._auto_output = True
        self.plugins = load_plugins()

        content = QWidget()
        lay = vbox(spacing=20)
        content.setLayout(lay)
        lay.addWidget(PageHeader("New scan", "Find the phones that were connected to the computers in a disk "
                                              "image or triage collection."))
        columns = hbox(spacing=20)
        lay.addLayout(columns)
        left = vbox(spacing=16)
        right = vbox(spacing=16)
        columns.addLayout(left, 5)
        columns.addLayout(right, 3)

        # evidence
        ev = Card("Evidence", "Read only: nothing in this folder is changed. Each computer inside it is found "
                              "automatically.")
        self.drop = DropZone()
        self.drop.chosen.connect(self._evidence_chosen)
        self.found_spinner = Spinner(14)
        self.found_spinner.hide()
        self.found_label = label("", "Small", wrap=True)
        self.found_chips = ChipRow()
        self.found_chips.hide()
        ev.body.addWidget(self.drop)
        self.found_row = QWidget()
        self.found_row.setLayout(hbox(self.found_spinner, (self.found_label, 1), spacing=8))
        self.found_row.hide()
        ev.body.addWidget(self.found_row)
        ev.body.addWidget(self.found_chips)
        left.addWidget(ev)

        # case details
        details = Card("Case details", "Shown in the report and recorded in the manifest.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        self.case_ref = QLineEdit()
        self.case_ref.setPlaceholderText("e.g. 2026-0142")
        self.examiner = QLineEdit()
        self.examiner.setPlaceholderText("Your name")
        self.examiner.setText(str(window.settings.value("examiner", "")))
        self.output = PathField("Where the report and results are written", "Choose the output folder")
        self.output_note = label("A new folder: report.html, timeline, devices.json and SHA-256 hashes of every "
                                 "evidence file read.", "Small", wrap=True)
        grid.addWidget(label("Case reference", "Small"), 0, 0)
        grid.addWidget(label("Examiner", "Small"), 0, 1)
        grid.addWidget(self.case_ref, 1, 0)
        grid.addWidget(self.examiner, 1, 1)
        grid.addWidget(label("Output folder", "Small"), 2, 0, 1, 2)
        grid.addWidget(self.output, 3, 0, 1, 2)
        grid.addWidget(self.output_note, 4, 0, 1, 2)
        grid.setRowMinimumHeight(2, 26)
        details.body.addLayout(grid)
        left.addWidget(details)
        self.case_ref.textChanged.connect(self._refresh_output)
        self.output.edit.textEdited.connect(self._output_edited)
        self.output.changed.connect(lambda *_: self._validate())

        # parsers
        self.parsers_card = Card("Parsers", "")
        self.parser_summary = self.parsers_card.title_label
        self.toggle_parsers = button("Choose…", "Ghost", "ph.caret-down", self._toggle_parsers)
        self.parsers_card.add_action(self.toggle_parsers)
        self.parser_panel = QWidget()
        pgrid = hbox(spacing=18)
        self.parser_panel.setLayout(pgrid)
        self.checks: dict[str, QCheckBox] = {}
        for os_name in OS_ORDER:
            col = vbox(spacing=7)
            items = [c for c in self.plugins.values() if c.os == os_name]
            col.addLayout(hbox(overline(OS_LABEL[os_name]), "stretch",
                               button("All", "Link", None, lambda o=os_name: self._set_os(o, True)),
                               button("None", "Link", None, lambda o=os_name: self._set_os(o, False)),
                               spacing=10))
            for cls in sorted(items, key=lambda c: c.name):
                cb = QCheckBox(cls.name)
                cb.setChecked(True)
                cb.setToolTip(f"{cls.id} · {VALIDATION_LABEL.get(cls.validation.value, cls.validation.value)}"
                              f"\n{cls.description}")
                cb.toggled.connect(lambda *_: self._parsers_changed())
                self.checks[cls.id] = cb
                col.addWidget(cb)
            col.addStretch(1)
            pgrid.addLayout(col, 1)
        self.parser_panel.hide()
        self.parsers_card.body.addWidget(self.parser_panel)
        self.parser_note = label("", "Small", wrap=True)
        self.parsers_card.body.addWidget(self.parser_note)
        left.addWidget(self.parsers_card)

        # options
        opts = Card("Options")
        self.export_files = QCheckBox("Save recovered files (cached photos, attachments) to the output folder")
        self.export_files.setChecked(True)
        self.workers = QSpinBox()
        self.workers.setRange(0, 32)
        self.workers.setSpecialValueText(f"Auto ({engine.default_workers()})")
        self.workers.setValue(0)
        self.workers.setFixedWidth(130)
        self.workdir = PathField("System temporary folder", "Choose a folder for temporary copies")
        ogrid = QGridLayout()
        ogrid.setHorizontalSpacing(14)
        ogrid.setVerticalSpacing(6)
        ogrid.addWidget(self.export_files, 0, 0, 1, 2)
        ogrid.addWidget(label("Parser threads", "Small"), 1, 0)
        ogrid.addWidget(label("Temporary copies (databases and hives are copied before reading)", "Small"), 1, 1)
        ogrid.addWidget(self.workers, 2, 0)
        ogrid.addWidget(self.workdir, 2, 1)
        ogrid.setColumnStretch(1, 1)
        ogrid.setRowMinimumHeight(1, 26)
        opts.body.addLayout(ogrid)
        left.addWidget(opts)

        # start
        self.problem = label("", "Muted", wrap=True)
        self.start_btn = button("Start scan", "Primary", "ph.play-fill", self._start)
        self.start_btn.setMinimumHeight(40)
        left.addLayout(hbox((self.problem, 1), self.start_btn, spacing=16))
        left.addStretch(1)

        # right column: open, recent, demo
        open_card = Card("Open a case", "Reopen the results of an earlier scan.")
        open_card.body.addWidget(button("Open results folder…", None, "ph.folder-open",
                                        window.open_results_dialog))
        self.recent_box = vbox(spacing=2)
        open_card.body.addLayout(self.recent_box)
        right.addWidget(open_card)

        demo = Card("Try it out", "A synthetic case with three computers, an iPhone and a Pixel. Everything is "
                                  "generated; no real data.")
        demo.body.addWidget(button("Run the demo case", None, "ph.flask", self._demo))
        right.addWidget(demo)

        inputs = Card("What it reads")
        for icon_name, os_name, text in (
                ("ph.windows-logo", "Windows", "USB and Bluetooth history, hotspots, Phone Link, iPhone pairing "
                                               "and backups, Microsoft-account phones"),
                ("ph.apple-logo", "macOS", "iPhone pairing and backups, Messages, calls, AirDrop, "
                                          "notifications, iPhone Mirroring, hotspots"),
                ("mdi6.linux", "Linux", "BlueZ pairings, KDE Connect")):
            inputs.body.addLayout(hbox(IconBadge(icon_name, "muted", 30),
                                       vbox(label(os_name, "H3"), label(text, "Small", wrap=True), spacing=1),
                                       spacing=12))
        right.addWidget(inputs)
        right.addStretch(1)

        page = vbox()
        page.addWidget(PageScroll(content, max_width=1240))
        self.setLayout(page)
        self.refresh_recent()
        self._parsers_changed()
        self._refresh_output()
        self._validate()

    # -- evidence ------------------------------------------------------------------------

    def _evidence_chosen(self, path: Path) -> None:
        self._refresh_output()
        self._systems = None
        self._discover_token += 1
        token = self._discover_token
        self.found_chips.hide()
        self.found_row.show()
        self.found_spinner.start()
        self.found_label.setText("Looking for computers in this folder…")
        self.found_label.setObjectName("Small")
        self._validate()
        jobs.run(jobs.discover_job(path),
                 done=lambda systems: self._discovered(token, systems),
                 failed=lambda msg, _d: self._discover_failed(token, msg))

    def _discovered(self, token: int, systems: list[dict]) -> None:
        if token != self._discover_token:
            return
        self._systems = systems
        self.found_spinner.stop()
        if not systems:
            self.found_label.setText("No Windows, macOS or Linux system was found in this folder (searched five "
                                     "levels down). Choose the folder that holds the drive or collection.")
            self.found_label.setObjectName("Error")
        else:
            n = len(systems)
            self.found_label.setText(f"{n} computer{'s' if n != 1 else ''} found")
            self.found_label.setObjectName("Small")
            chips = []
            for s in systems:
                product = s["details"].get("product") or s["details"].get("pretty_name") or \
                    (f"macOS {s['details']['product_version']}" if s["details"].get("product_version") else "")
                users = ", ".join(s["users"]) or "no user profiles"
                chips.append(Chip(s["label"], tone_fn("muted"), OS_ICON.get(s["os"], "ph.desktop"),
                                  tip=f"{OS_LABEL.get(s['os'], s['os'])} {product}\nUsers: {users}"))
            self.found_chips.set(chips)
            self.found_chips.show()
        self.found_label.style().unpolish(self.found_label)
        self.found_label.style().polish(self.found_label)
        self._validate()

    def _discover_failed(self, token: int, msg: str) -> None:
        if token != self._discover_token:
            return
        self.found_spinner.stop()
        self._systems = []
        self.found_label.setText(f"Could not read this folder: {msg}")
        self.found_label.setObjectName("Error")
        self.found_label.style().polish(self.found_label)
        self._validate()

    # -- output folder -------------------------------------------------------------------

    def _output_edited(self, *_):
        self._auto_output = False

    def _refresh_output(self, *_):
        if self._auto_output:
            self.output.edit.blockSignals(True)
            self.output.set_path(_default_output(self.drop.path, self.case_ref.text()))
            self.output.edit.blockSignals(False)
        self._validate()

    # -- parsers -------------------------------------------------------------------------

    def _toggle_parsers(self) -> None:
        show = not self.parser_panel.isVisible()
        self.parser_panel.setVisible(show)
        self.toggle_parsers.setText("Hide" if show else "Choose…")
        THEME.bind_icon(self.toggle_parsers, "ph.caret-up" if show else "ph.caret-down", "text", 15)

    def _set_os(self, os_name: str, on: bool) -> None:
        for pid, cb in self.checks.items():
            if self.plugins[pid].os == os_name:
                cb.setChecked(on)

    def selected_plugins(self) -> list[str]:
        return [pid for pid, cb in self.checks.items() if cb.isChecked()]

    def _parsers_changed(self) -> None:
        chosen = self.selected_plugins()
        total = len(self.checks)
        per_os = {o: sum(1 for p in chosen if self.plugins[p].os == o) for o in OS_ORDER}
        self.parser_summary.setText(f"Parsers · {len(chosen)} of {total}")
        self.parser_note.setText(" · ".join(f"{OS_LABEL[o]} {per_os[o]}" for o in OS_ORDER)
                                 + "    Parsers marked synthetic have been tested only against generated data.")
        self._validate()

    # -- recent --------------------------------------------------------------------------

    def refresh_recent(self) -> None:
        clear_layout(self.recent_box)
        recent = self.window_.recent()
        if not recent:
            return
        self.recent_box.addSpacing(6)
        self.recent_box.addWidget(overline("Recent"))
        for r in recent[:6]:
            path = Path(r.get("path", ""))
            b = button("", "Ghost")
            b.setText(f"{r.get('title') or path.name}")
            b.setToolTip(f"{path}\nScanned or opened {r.get('when', '')}")
            b.setStyleSheet("text-align: left; padding: 6px 8px;")
            THEME.bind_icon(b, "ph.clock-counter-clockwise", "faint", 14)
            b.clicked.connect(lambda *_, p=path: self._open_recent(p))
            self.recent_box.addWidget(b)

    def _open_recent(self, path: Path) -> None:
        if not (path / "manifest.json").is_file():
            self.window_.forget_recent(str(path))
            Toast.show_for(self, "That case folder no longer exists; removed it from the list")
            return
        self.window_.open_results(path)

    # -- start ---------------------------------------------------------------------------

    def _problem(self) -> str:
        ev = self.drop.path
        if ev is None:
            return "Choose the evidence folder to begin."
        if not ev.is_dir():
            return "The evidence folder does not exist."
        if self._systems is None:
            return "Looking for computers in the evidence…"
        if not self._systems:
            return "No computer was found in the evidence folder."
        if not self.selected_plugins():
            return "Select at least one parser."
        out = self.output.path()
        if out is None:
            return "Choose an output folder."
        try:
            if out.exists() and (not out.is_dir() or any(out.iterdir())):
                return "The output folder must be new or empty."
            if out.resolve() == ev.resolve() or ev.resolve() in out.resolve().parents:
                return "Write the output outside the evidence folder."
        except OSError as exc:
            return f"Cannot use that output folder: {exc}"
        return ""

    def _validate(self) -> None:
        problem = self._problem()
        self.problem.setText(problem)
        self.start_btn.setEnabled(not problem)
        out_bad = problem.startswith(("The output", "Write the output", "Cannot use"))
        self.output.set_invalid(out_bad)

    def _params(self, evidence: Path, output: Path, demo: bool = False) -> jobs.ScanParams:
        chosen = self.selected_plugins()
        return jobs.ScanParams(
            evidence=evidence, output=output, demo=demo,
            case={"case": self.case_ref.text().strip(), "examiner": self.examiner.text().strip()},
            plugin_ids=None if len(chosen) == len(self.checks) else chosen,
            export_files=self.export_files.isChecked(), workers=self.workers.value() or None,
            workdir=self.workdir.path(),
        )

    def _start(self) -> None:
        self._validate()
        if not self.start_btn.isEnabled():
            return
        self.window_.settings.setValue("examiner", self.examiner.text().strip())
        self.window_.start_scan(self._params(self.drop.path, self.output.path()))
        self._auto_output = True
        self._refresh_output()

    def _demo(self) -> None:
        base = demo_folder()
        params = self._params(base, base / "report", demo=True)
        params.case = {"case": "DEMO (synthetic data)", "examiner": self.examiner.text().strip()}
        params.plugin_ids = None
        self.window_.start_scan(params)

    def focus_item(self, _arg) -> None:
        pass

    @property
    def search(self):  # Ctrl+F on this page goes to the case reference
        return self.case_ref

