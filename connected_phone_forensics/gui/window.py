"""The main window: a sidebar for navigation and a stack of pages."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QWidget,
)

from .. import __version__
from . import jobs, portable, theme
from .casedata import REQUIRED_FILES, CaseData
from .widgets import THEME, Toast, button, choose_folder, hbox, label, tool_button, vbox

ORG, APP = "ConnectedPhoneForensics", "Connected Phone Forensics"
CASE_PAGES = ("overview", "devices", "data", "timeline", "coverage", "issues")

NAV = [
    ("new", "New scan", "ph.plus-circle"),
    ("overview", "Overview", "ph.squares-four"),
    ("devices", "Devices", "ph.device-mobile"),
    ("data", "Recovered data", "ph.database"),
    ("timeline", "Timeline", "ph.clock-counter-clockwise"),
    ("coverage", "Coverage", "ph.files"),
    ("issues", "Issues", "ph.warning-circle"),
    ("guide", "Guide", "ph.book-open"),
]
NAV_SECTIONS = {"overview": "CASE", "guide": "HELP"}  # a heading before these entries


class NavButton(QPushButton):
    def __init__(self, text: str, icon_name: str):
        super().__init__(text)
        self.setObjectName("Nav")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(42)
        THEME.bind_icon(self, icon_name, "muted", 19)
        self.count = QLabel("")
        self.count.setObjectName("NavCount")
        self.count.setFixedHeight(20)
        self.count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count.hide()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.addStretch(1)
        lay.addWidget(self.count)

    def set_count(self, n: int | None, alert: bool = False) -> None:
        self.count.setVisible(bool(n))
        self.count.setText(f"{n:,}" if n else "")
        self.count.setProperty("alert", "true" if alert else "false")
        self.count.style().unpolish(self.count)
        self.count.style().polish(self.count)


class Sidebar(QFrame):
    def __init__(self, window: MainWindow):
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(250)
        self.buttons: dict[str, NavButton] = {}
        self.lockup = QLabel()
        self.lockup.setObjectName("Lockup")
        self._render_lockup()
        THEME.on_change(self._render_lockup, self)
        brand = hbox(self.lockup, "stretch")
        lay = vbox(spacing=3, margins=(14, 20, 14, 14))
        lay.addLayout(brand)
        lay.addSpacing(18)
        for key, text, icon_name in NAV:
            if key in NAV_SECTIONS:
                if key != "overview":
                    lay.addSpacing(6)
                lay.addWidget(label(NAV_SECTIONS[key], "NavSection"))
            b = NavButton(text, icon_name)
            b.clicked.connect(lambda *_, k=key: window.go(k))
            self.buttons[key] = b
            lay.addWidget(b)
        lay.addStretch(1)

        self.case_box = QFrame()
        self.case_box.setObjectName("CaseBox")
        self.case_title = label("", "H3", wrap=True)
        self.case_sub = label("", "Small", wrap=True)
        self.case_box.setLayout(vbox(label("Current case", "Small"), self.case_title, self.case_sub,
                                     spacing=2, margins=(12, 10, 12, 12)))
        self.case_box.hide()
        lay.addWidget(self.case_box)
        lay.addSpacing(10)
        self.theme_btn = tool_button("ph.moon", "Switch between light and dark", window.toggle_theme, 17)
        version = label(f"v{__version__}" + (" · portable" if portable.is_portable() else ""), "Faint")
        if portable.is_portable():
            version.setToolTip("Portable mode: settings are kept next to the program, not in this computer's "
                               "registry")
        lay.addLayout(hbox(version, "stretch", self.theme_btn))
        self.setLayout(lay)

    def _render_lockup(self) -> None:
        from .icons import svg_pixmap

        name = "lockup-two-line-dark.svg" if THEME.palette.dark else "lockup-two-line-light.svg"
        self.lockup.setPixmap(svg_pixmap(name, 214, ratio=max(2.0, self.devicePixelRatioF())))

    def set_case(self, case: CaseData | None) -> None:
        self.case_box.setVisible(case is not None)
        if case:
            self.case_title.setText(case.title)
            n = len(case.phones)
            self.case_sub.setText(f"{n} phone{'s' if n != 1 else ''} · {len(case.systems)} "
                                  f"computer{'s' if len(case.systems) != 1 else ''}")
        for key in CASE_PAGES:
            self.buttons[key].setEnabled(case is not None)
        if case:
            self.buttons["devices"].set_count(len(case.devices))
            self.buttons["timeline"].set_count(len(case.records))
            issues = len(case.warnings) + len(case.errors)
            self.buttons["issues"].set_count(issues, alert=issues > 0)
        else:
            for b in self.buttons.values():
                b.set_count(None)


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        from .pages.coverage import CoveragePage
        from .pages.data import DataPage
        from .pages.devices import DevicesPage
        from .pages.guide import GuidePage
        from .pages.home import HomePage
        from .pages.issues import IssuesPage
        from .pages.overview import OverviewPage
        from .pages.scan import ScanPage
        from .pages.timeline import TimelinePage

        self.settings = settings or portable.settings(ORG, "CPF")
        self.case: CaseData | None = None
        self.scan_job: jobs.Job | None = None
        self.setWindowTitle(APP)
        self.setMinimumSize(1120, 720)

        self.sidebar = Sidebar(self)
        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {
            "new": HomePage(self), "scan": ScanPage(self), "overview": OverviewPage(self),
            "devices": DevicesPage(self), "data": DataPage(self), "timeline": TimelinePage(self),
            "coverage": CoveragePage(self),
            "issues": IssuesPage(self), "guide": GuidePage(self),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
        root = QWidget()
        root.setObjectName("Root")
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.sidebar)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        for i, (key, _t, _i) in enumerate(NAV, 1):
            QShortcut(QKeySequence(f"Ctrl+{i}"), self, lambda k=key: self.go(k))
        QShortcut(QKeySequence.StandardKey.New, self, lambda: self.go("new"))
        QShortcut(QKeySequence.StandardKey.Open, self, self.open_results_dialog)
        QShortcut(QKeySequence.StandardKey.Find, self, self.focus_search)
        QShortcut(QKeySequence(Qt.Key.Key_F1), self, lambda: self.go("guide"))

        self.sidebar.set_case(None)
        self.go("new")
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            self.resize(1380, 880)

    # -- navigation ---------------------------------------------------------------------

    def go(self, key: str, arg=None) -> None:
        if key == "new" and self.scan_job is not None:
            key = "scan"
        if key in CASE_PAGES and self.case is None:
            key = "new"
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        nav_key = "new" if key == "scan" else key
        for k, b in self.sidebar.buttons.items():
            b.setChecked(k == nav_key)
        if arg is not None and hasattr(page, "focus_item"):
            page.focus_item(arg)

    def focus_search(self) -> None:
        page = self.stack.currentWidget()
        if hasattr(page, "search"):
            page.search.setFocus()
            page.search.selectAll()

    # -- theme --------------------------------------------------------------------------

    def apply_theme(self, name: str) -> None:
        p = theme.DARK if name == "dark" else theme.LIGHT
        THEME.palette = p
        theme.apply(QApplication.instance(), p)  # the style sheet first: nothing after it can stop it
        THEME.set(p)
        theme.style_title_bar(self, p)
        THEME.bind_icon(self.sidebar.theme_btn, "ph.sun" if p.dark else "ph.moon", "muted", 17)
        from .icons import app_icon

        self.setWindowIcon(app_icon())
        self.settings.setValue("theme", name)

    def toggle_theme(self) -> None:
        self.apply_theme("light" if THEME.palette.dark else "dark")

    # -- scanning -----------------------------------------------------------------------

    def start_scan(self, params: jobs.ScanParams) -> None:
        scan_page = self.pages["scan"]
        scan_page.begin(params)
        self.go("scan")
        self.scan_job = jobs.run(jobs.scan_job(params), done=self._scan_done, failed=self._scan_failed,
                                 progress=scan_page.on_event, cancelled=self._scan_cancelled)

    def cancel_scan(self) -> None:
        if self.scan_job is not None:
            self.scan_job.cancel()
            self.pages["scan"].cancelling()

    def _scan_done(self, case: CaseData) -> None:
        self.scan_job = None
        self.pages["scan"].finish()
        self.remember(case)
        self.show_case(case)
        took = case.duration
        from .casedata import fmt_duration

        Toast.show_for(self, f"Scan finished{' in ' + fmt_duration(took) if took else ''} · "
                             f"{len(case.phones)} phone(s), {len(case.records):,} records")

    def _scan_failed(self, message: str, details: str) -> None:
        self.scan_job = None
        self.pages["scan"].failed(message, details)

    def _scan_cancelled(self) -> None:
        self.scan_job = None
        self.pages["scan"].cancelled()

    # -- cases --------------------------------------------------------------------------

    def show_case(self, case: CaseData) -> None:
        self.case = case
        self.sidebar.set_case(case)
        data = self.pages["data"]
        data.set_case(case)  # first: the other pages use what it extracted
        for key in CASE_PAGES:
            if key != "data":
                self.pages[key].set_case(case)
        self.sidebar.buttons["data"].set_count(sum(len(v) for k, v in data.items.items() if k != "connections"))
        self.setWindowTitle(f"{case.title} — {APP}")
        self.go("overview")

    def open_results_dialog(self) -> None:
        start = self.settings.value("last_output_parent", str(Path.home()))
        chosen = choose_folder(self, "Open a scan output folder", start)
        if chosen:
            self.open_results(Path(chosen))

    def open_results(self, outdir: Path) -> None:
        missing = [f for f in REQUIRED_FILES if not (outdir / f).is_file()]
        if missing:
            QMessageBox.warning(self, "Not a scan output folder",
                                f"{outdir}\n\nThis folder has no scan results (missing {', '.join(missing)}). "
                                "Choose the folder a scan wrote its report to.")
            return
        Toast.show_for(self, "Opening case…", 1200)
        jobs.run(jobs.load_job(outdir), done=lambda case: (self.remember(case), self.show_case(case)),
                 failed=lambda msg, _d: QMessageBox.critical(self, "Could not open the case", msg))

    def remember(self, case: CaseData) -> None:
        if not case.outdir:
            return
        recent = [r for r in self.recent() if r.get("path") != str(case.outdir)]
        recent.insert(0, {"path": str(case.outdir), "title": case.title, "phones": len(case.phones),
                          "when": datetime.now().strftime("%Y-%m-%d %H:%M")})
        self.settings.setValue("recent", recent[:8])
        self.settings.setValue("last_output_parent", str(case.outdir.parent))
        self.pages["new"].refresh_recent()

    def recent(self) -> list[dict]:
        value = self.settings.value("recent") or []
        return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []

    def forget_recent(self, path: str) -> None:
        self.settings.setValue("recent", [r for r in self.recent() if r.get("path") != path])
        self.pages["new"].refresh_recent()

    def open_path(self, path: Path | None) -> None:
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_url(self, url: str | None) -> None:
        if url:
            QDesktopServices.openUrl(QUrl(url))

    # -- window -------------------------------------------------------------------------

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        theme.style_title_bar(self, THEME.palette)

    def closeEvent(self, e) -> None:  # noqa: N802
        if self.scan_job is not None:
            answer = QMessageBox.question(self, "A scan is running",
                                          "Stop the scan and close? Nothing from this scan will be saved.")
            if answer != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            self.scan_job.cancel()
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(e)


def header_actions(window: MainWindow) -> list[QPushButton]:
    """Buttons that open the current case's reports and its folder."""
    report = button("Report", None, "ph.arrow-square-out",
                    lambda: window.open_path(window.case.report if window.case else None),
                    "Open the detailed HTML report in your browser")
    pdf = button("PDF summary", None, "ph.file-pdf", lambda: window.pages["data"]._open_output("summary.pdf"),
                 "Open the PDF summary (written first for a case scanned by an older version)")
    xlsx = button("Workbook", None, "ph.table", lambda: window.pages["data"]._open_output("workbook.xlsx"),
                  "Open the Excel workbook: a summary sheet and one sheet per phone")
    folder = tool_button("ph.folder-open", "Show the case folder (report, workbook, timeline, hashes)",
                         lambda: window.open_path(window.case.outdir if window.case else None), 18)
    return [report, pdf, xlsx, folder]
