"""New scan: choose the evidence (a folder, or this computer live), name the case, pick parsers, start."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from ... import engine, live
from ...plugin import load_plugins
from .. import jobs, portable
from ..casedata import OS_LABEL, VALIDATION_LABEL
from ..hero import Banner, HeroArt
from ..icons import OS_ICON
from ..widgets import (
    THEME,
    Card,
    Chip,
    ChipRow,
    DropZone,
    IconBadge,
    PageScroll,
    PathField,
    Segmented,
    Spinner,
    Toast,
    button,
    clear_layout,
    hbox,
    label,
    overline,
    repolish,
    tone_fn,
    tool_button,
    vbox,
)

OS_ORDER = ("windows", "macos", "linux")
FOLDER_SUBTITLE = ("Read only: nothing in this folder is changed. Each computer inside it is found "
                   "automatically.")
LIVE_SUBTITLE = "Acquire the evidence from the computer this program is running on, then scan the copy."
LIVE_HOW = ("How it works: the registry hives are saved through the Windows registry API (as reg save does), "
            "and every file the parsers read is copied with the backup privilege, hashed (SHA-256) and given "
            "its original times. The copy is then scanned like any collection. Everything is written to the "
            "output folder: put it on a USB drive or external disk, not on the drive being examined.")
ONE_FILE_TIP = ("Tip: this single-file program unpacks itself into the temporary folder on the system drive "
                "to run. The portable ZIP build runs straight from the USB drive.")


def demo_folder() -> Path:
    """A new folder for the demo case's evidence and report."""
    return Path(tempfile.mkdtemp(prefix="cpf-demo-"))


def _safe_name(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in " -_.()" else "-" for ch in text).strip() or "case"


def _default_output(evidence: Path | None, case_ref: str) -> Path:
    base = Path.home() / "Documents" / "Connected Phone Forensics"
    name = _safe_name(case_ref.strip() or (evidence.name if evidence else "") or "case")
    return base / f"{name} {datetime.now():%Y-%m-%d %H%M}"


def fmt_bytes(n: int | None) -> str:
    if not n:
        return "0 B"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


class LivePanel(QFrame):
    """This computer: what it is, and whether the program runs as administrator."""

    restart = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("LivePanel")
        self.badge = IconBadge("ph.desktop-tower", "accent", 44)
        self.name = label("", "H2", select=True)
        self.details = label("", "Small", wrap=True, select=True)
        self.status_badge = IconBadge("ph.shield-check", "ok", 30)
        self.status_title = label("", "H3")
        self.status_text = label("", "Small", wrap=True)
        self.restart_btn = button("Restart as administrator", "Primary", "mdi6.shield-account", self.restart.emit,
                                  "Windows asks for permission, then the program opens again in live mode")
        self.how = label(LIVE_HOW, "Small", wrap=True)
        self.tip = label(ONE_FILE_TIP, "Small", wrap=True)
        self.tip.setVisible(portable.is_one_file())
        status = QFrame()
        status.setObjectName("LiveStatus")
        status.setLayout(hbox(self.status_badge, (vbox(self.status_title, self.status_text, spacing=1), 1),
                              self.restart_btn, spacing=12, margins=(12, 10, 12, 10)))
        self.status_frame = status
        lay = vbox(spacing=12, margins=(20, 18, 20, 18))
        lay.addLayout(hbox(self.badge, (vbox(self.name, self.details, spacing=2), 1), spacing=14))
        lay.addWidget(status)
        lay.addWidget(self.how)
        lay.addWidget(self.tip)
        self.setLayout(lay)

    def set_machine(self, m: dict | None, error: str = "") -> None:
        if m is None:
            self.name.setText("This computer")
            self.details.setText(error or "Live acquisition is available on Windows only.")
            self._status(False, "Not available", error)
            self.restart_btn.hide()
            return
        self.name.setText(m["computer"] or "This computer")
        profiles = ", ".join(Path(p).name for p in m["profiles"]) or "none"
        self.details.setText(f"{m['windows'] or 'Windows'} · signed in as {m['user']} · system drive "
                             f"{m['system_drive']} · user profiles: {profiles}")
        if m["admin"]:
            self._status(True, "Running as administrator",
                         "The registry hives can be saved and protected folders read.")
        else:
            self._status(False, "Administrator rights needed",
                         "Without them the registry hives are locked and some folders cannot be read.")
        self.restart_btn.setVisible(not m["admin"])

    def _status(self, ok: bool, title: str, text: str) -> None:
        self.status_badge.icon_name = "ph.shield-check" if ok else "ph.shield-warning"
        self.status_badge.set_tone("ok" if ok else "warn")
        self.status_title.setText(title)
        self.status_text.setText(text)
        self.status_text.setVisible(bool(text))
        self.status_frame.setProperty("tone", "ok" if ok else "warn")
        repolish(self.status_frame)


class LiveOutput(QWidget):
    """The output for a live acquisition: a drive (USB or external disk) and a folder on it."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.drive = QComboBox()
        self.drive.setMinimumWidth(300)
        self.drive.currentIndexChanged.connect(lambda *_: self.changed.emit())
        self.folder = QLineEdit()
        self.folder.setPlaceholderText("Folder on that drive")
        self.folder.textChanged.connect(lambda *_: self.changed.emit())
        self.refresh = tool_button("ph.arrows-clockwise", "Look for drives again", self.reload, 16)
        self.include_system = False
        self.setLayout(hbox(self.drive, self.refresh, (self.folder, 1), spacing=8))

    def reload(self) -> None:
        current = self.drive.currentData()
        self.drive.blockSignals(True)
        self.drive.clear()
        try:
            drives = live.output_drives()
            if self.include_system:
                from ...live import winapi

                drives.append({**vars(winapi.drive_info(live.system_drive())), "program_drive": False,
                               "system": True})
        except OSError:
            drives = []
        for d in drives:
            name = d.get("label") or ("Local disk" if d["kind"] == "fixed" else "USB drive")
            note = "examined drive, not recommended" if d.get("system") else d["kind"]
            here = " · this program's drive" if d.get("program_drive") else ""
            self.drive.addItem(f"{d['root']}  {name} · {note} · {fmt_bytes(d['free_bytes'])} free{here}", d["root"])
        if not drives:
            self.drive.addItem("No USB drive or external disk found: connect one, then refresh", None)
        index = self.drive.findData(current) if current else -1
        self.drive.setCurrentIndex(index if index >= 0 else 0)
        self.drive.blockSignals(False)
        self.changed.emit()

    def path(self) -> Path | None:
        root = self.drive.currentData()
        folder = self.folder.text().strip().strip('"').lstrip("\\/")
        return Path(root) / folder if root and folder else None


class HomePage(QWidget):
    def _hero(self) -> Banner:
        """The page's banner: what the tool does, with a phone and a computer exchanging records."""
        hero = Banner()
        n = len(self.plugins)
        chips = ChipRow([
            Chip("Read-only", tone_fn("ok"), "ph.shield-check", "Nothing in the evidence is changed"),
            Chip(f"{n} parsers", tone_fn("accent"), "ph.puzzle-piece", "Choose them below, under Parsers"),
            Chip("Windows · macOS · Linux", tone_fn("muted"), "ph.desktop"),
            Chip("The source of every record", tone_fn("muted"), "ph.fingerprint",
                 "Every record keeps the file, the record inside it and the file's SHA-256"),
        ])
        title = label("Find every phone that touched these computers", "HeroTitle", wrap=True)
        sub = label("USB, Bluetooth, hotspots, Phone Link, iPhone pairing and backups: read from a disk image, a "
                    "triage collection or this computer, with the messages, contacts and photos the computers "
                    "kept.", "Muted", wrap=True)
        self.hero_art = HeroArt()
        text = vbox("stretch", overline("New scan"), title, sub, 6, chips, "stretch", spacing=8)
        hero.setLayout(hbox((text, 6), (self.hero_art, 5), spacing=16, margins=(30, 26, 22, 22)))
        return hero

    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self._discover_token = 0
        self._systems: list[dict] | None = None
        self._auto_output = True
        self._auto_live_folder = True
        self._machine: dict | None = None
        self.plugins = load_plugins()

        content = QWidget()
        lay = vbox(spacing=20)
        content.setLayout(lay)
        lay.addWidget(self._hero())
        columns = hbox(spacing=20)
        lay.addLayout(columns)
        left = vbox(spacing=16)
        right = vbox(spacing=16)
        columns.addLayout(left, 5)
        columns.addLayout(right, 3)

        # evidence
        self.ev_card = ev = Card("Evidence", FOLDER_SUBTITLE)
        self.source: Segmented | None = None
        if live.is_supported():
            self.source = Segmented([("folder", "Evidence folder"), ("live", "This computer (live)")])
            self.source.changed.connect(self.set_source)
            ev.add_action(self.source)
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
        self.live_hint = QFrame()
        self.live_hint.setObjectName("Notice")
        self.live_hint_text = label("", "Small", wrap=True)
        self.live_hint.setLayout(hbox(IconBadge("ph.warning", "warn", 26), (self.live_hint_text, 1),
                                      button("Acquire it live", None, "ph.desktop-tower",
                                             lambda: self.set_source("live")),
                                      spacing=10, margins=(12, 10, 12, 10)))
        self.live_hint.hide()
        ev.body.addWidget(self.live_hint)
        self.live_panel = LivePanel()
        self.live_panel.restart.connect(self._restart_as_admin)
        self.live_panel.hide()
        ev.body.addWidget(self.live_panel)
        left.addWidget(ev)

        # case details
        details = Card("Case details", "Shown in the report and recorded in the manifest.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        self.case_ref = QLineEdit()
        self.case_ref.setPlaceholderText("e.g. ACME-Incident-EmpID39323")
        self.case_ref.setToolTip("A clear name for the case. It titles the reports, the workbook and the case "
                                 "dashboard, and names the output folder.")
        self.examiner = QLineEdit()
        self.examiner.setPlaceholderText("Your name")
        self.examiner.setText(str(window.settings.value("examiner", "")))
        self.output = PathField("Where the report and results are written", "Choose the output folder")
        self.live_output = LiveOutput()
        self.live_output.hide()
        self.live_output.changed.connect(self._validate)
        self.live_output.folder.textEdited.connect(self._live_folder_edited)
        self.allow_same = QCheckBox("Allow the output on the drive being examined (not recommended: it overwrites "
                                    "free space that may hold deleted evidence; recorded in the report)")
        self.allow_same.hide()
        self.allow_same.toggled.connect(self._allow_same_changed)
        self.output_note = label("", "Small", wrap=True)
        grid.addWidget(label("Case name", "Small"), 0, 0)
        grid.addWidget(label("Examiner", "Small"), 0, 1)
        grid.addWidget(self.case_ref, 1, 0)
        grid.addWidget(self.examiner, 1, 1)
        self.output_title = label("Output folder", "Small")
        grid.addWidget(self.output_title, 2, 0, 1, 2)
        grid.addWidget(self.output, 3, 0, 1, 2)
        grid.addWidget(self.live_output, 4, 0, 1, 2)
        grid.addWidget(self.allow_same, 5, 0, 1, 2)
        grid.addWidget(self.output_note, 6, 0, 1, 2)
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
        self.workdir_title = label("Temporary copies (databases and hives are copied before reading)", "Small")
        self.workdir_live = label("Temporary copies are kept in the output folder and removed at the end.",
                                  "Small", wrap=True)
        self.workdir_live.hide()
        ogrid = QGridLayout()
        ogrid.setHorizontalSpacing(14)
        ogrid.setVerticalSpacing(6)
        ogrid.addWidget(self.export_files, 0, 0, 1, 2)
        ogrid.addWidget(label("Parser threads", "Small"), 1, 0)
        ogrid.addWidget(self.workdir_title, 1, 1)
        ogrid.addWidget(self.workers, 2, 0)
        ogrid.addWidget(self.workdir, 2, 1)
        ogrid.addWidget(self.workdir_live, 2, 1)
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

    # -- source: a folder or this computer ------------------------------------------------

    @property
    def is_live(self) -> bool:
        return self.source is not None and self.source.current() == "live"

    def set_source(self, key: str) -> None:
        if self.source is None:
            return
        self.source.select(key)
        on = key == "live"
        if on and self._machine is None:
            try:
                self._machine = live.machine()
            except OSError as exc:
                self.live_panel.set_machine(None, f"This computer's details could not be read: {exc}")
            else:
                self.live_panel.set_machine(self._machine)
            self.live_output.reload()
        self.ev_card.subtitle_label.setText(LIVE_SUBTITLE if on else FOLDER_SUBTITLE)
        self.drop.setVisible(not on)
        self.found_row.setVisible(not on and self.drop.path is not None)
        self.found_chips.setVisible(not on and bool(self._systems))
        self.live_hint.setVisible(not on and self.drop.path is not None and live.is_system_drive_root(self.drop.path))
        self.live_panel.setVisible(on)
        self.output.setVisible(not on)
        self.live_output.setVisible(on)
        self.allow_same.setVisible(on)
        self.output_title.setText("Output drive and folder" if on else "Output folder")
        self.workdir.setVisible(not on)
        self.workdir_title.setVisible(not on)
        self.workdir_live.setVisible(on)
        self.start_btn.setText("Start live acquisition" if on else "Start scan")
        self._parsers_changed()
        self._refresh_output()

    def _restart_as_admin(self) -> None:
        from ...live import winapi

        if winapi.relaunch_elevated(["--live"]):
            self.window_.settings.setValue("examiner", self.examiner.text().strip())
            self.window_.settings.sync()
            QApplication.instance().quit()
        else:
            Toast.show_for(self, "Windows did not start the program as administrator (the prompt was declined)")

    def _allow_same_changed(self, on: bool) -> None:
        self.live_output.include_system = on
        self.live_output.reload()

    def _live_folder_edited(self, *_):
        self._auto_live_folder = False

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
        if live.is_system_drive_root(path):
            self.live_hint_text.setText(
                f"{path} is this computer's running system: Windows keeps its registry hives locked and "
                "protects some folders, so a folder scan misses evidence. A live acquisition (as "
                "administrator) reads them.")
            self.live_hint.show()
        else:
            self.live_hint.hide()
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
            self.found_chips.setVisible(not self.is_live)
        repolish(self.found_label)
        self._validate()

    def _discover_failed(self, token: int, msg: str) -> None:
        if token != self._discover_token:
            return
        self.found_spinner.stop()
        self._systems = []
        self.found_label.setText(f"Could not read this folder: {msg}")
        self.found_label.setObjectName("Error")
        repolish(self.found_label)
        self._validate()

    # -- output folder -------------------------------------------------------------------

    def _output_edited(self, *_):
        self._auto_output = False

    def _refresh_output(self, *_):
        if self._auto_output:
            self.output.edit.blockSignals(True)
            self.output.set_path(_default_output(self.drop.path, self.case_ref.text()))
            self.output.edit.blockSignals(False)
        if self._auto_live_folder:
            name = _safe_name(self.case_ref.text().strip() or (self._machine or {}).get("computer") or "case")
            self.live_output.folder.blockSignals(True)
            self.live_output.folder.setText(f"CPF cases\\{name} {datetime.now():%Y-%m-%d %H%M}")
            self.live_output.folder.blockSignals(False)
        self._validate()

    def output_path(self) -> Path | None:
        return self.live_output.path() if self.is_live else self.output.path()

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
        if self.is_live:
            note = f"A live acquisition runs the Windows parsers ({per_os['windows']} selected)."
        else:
            note = " · ".join(f"{OS_LABEL[o]} {per_os[o]}" for o in OS_ORDER)
        self.parser_note.setText(note + "    Parsers marked synthetic have been tested only against generated data.")
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

    def _live_problem(self) -> str:
        m = self._machine
        if m is None:
            return "This computer's details could not be read."
        if not m["admin"]:
            return "Restart as administrator to acquire this computer."
        if not any(self.plugins[p].os == "windows" for p in self.selected_plugins()):
            return "Select at least one Windows parser."
        out = self.live_output.path()
        if out is None:
            return ("Connect a USB drive or external disk for the output." if self.live_output.drive.currentData()
                    is None else "Name the output folder.")
        try:
            if out.exists() and (not out.is_dir() or any(out.iterdir())):
                return "The output folder must be new or empty."
        except OSError as exc:
            return f"Cannot use that output folder: {exc}"
        if Path(out.anchor).resolve() == Path(live.system_drive()).resolve() and not self.allow_same.isChecked():
            return (f"The output folder is on {live.system_drive()}, the drive being examined. Choose a USB "
                    "drive or external disk.")
        return ""

    def _problem(self) -> str:
        if self.is_live:
            return self._live_problem()
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
        out_bad = problem.startswith(("The output", "Write the output", "Cannot use", "Name the output"))
        self.output.set_invalid(out_bad and not self.is_live)
        self.live_output.folder.setProperty("invalid", "true" if out_bad and self.is_live else "false")
        repolish(self.live_output.folder)
        if self.is_live:
            self.output_note.setText("Written there: the copies (collection), acquisition.json and "
                                     "acquisition.sha256 with every file's SHA-256, a timestamped "
                                     "acquisition.log, and the report.")
        else:
            self.output_note.setText("A new folder: report.html, timeline, devices.json and SHA-256 hashes of "
                                     "every evidence file read.")

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
        if self.is_live:
            params = self._params(Path(live.system_drive()), self.live_output.path())
            params.live, params.workdir = True, None
            params.allow_same_drive = self.allow_same.isChecked()
            self.window_.start_scan(params)
            self._auto_live_folder = True
        else:
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
