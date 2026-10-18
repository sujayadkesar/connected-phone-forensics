"""Start the desktop application: ``cpf-gui`` or ``python -m connected_phone_forensics.gui``.

``cpf-gui <results folder>`` opens a saved case. ``cpf-gui --smoke-test <empty folder>`` runs
the demo case through the app without showing a window and exits 0 on success (used to check
release builds).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    smoke: Path | None = None
    if len(argv) >= 3 and argv[1] == "--smoke-test":
        smoke = Path(argv[2])
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        argv = argv[:1]
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("The desktop app needs PySide6: pip install --upgrade connected-phone-forensics", file=sys.stderr)
        return 2

    if sys.platform == "win32":  # group the taskbar entry under our own icon, not python.exe's
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ConnectedPhoneForensics.Desktop")
        except Exception:  # noqa: BLE001 - cosmetic only
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    app.setApplicationName("Connected Phone Forensics")
    app.setOrganizationName("ConnectedPhoneForensics")
    app.setStyle("Fusion")

    from . import theme
    from .icons import app_icon
    from .widgets import THEME
    from .window import MainWindow

    THEME.fonts = theme.load_fonts()
    app.setFont(THEME.fonts.ui)
    app.setWindowIcon(app_icon())
    if smoke is not None:
        return _smoke_test(app, smoke)
    window = MainWindow()
    saved = window.settings.value("theme")
    window.apply_theme(saved if saved in ("light", "dark") else ("dark" if theme.system_prefers_dark() else "light"))
    window.show()
    args = app.arguments()[1:]
    if args:  # a results folder given on the command line opens straight away
        window.open_results(Path(args[0]))
    return app.exec()


def _smoke_test(app, folder: Path) -> int:
    """Run the demo case through the window's own scan path; report in ``folder/smoke-test.txt``."""
    from PySide6.QtCore import QSettings, QTimer

    from . import jobs
    from .window import MainWindow

    folder.mkdir(parents=True, exist_ok=True)
    report = folder / "smoke-test.txt"
    window = MainWindow(QSettings(str(folder / "settings.ini"), QSettings.Format.IniFormat))
    window.apply_theme("dark")
    outcome: dict[str, object] = {}

    def check() -> None:
        if window.case is not None:
            case = window.case
            ok = bool(case.phones and case.records and case.report)
            outcome.update(code=0 if ok else 1, text=f"{'ok' if ok else 'FAILED'}: {len(case.phones)} phones, "
                                                     f"{len(case.records)} records, report {case.report}")
        elif window.scan_job is None and window.pages["scan"].title.text() != "Scanning the evidence" \
                and window.pages["scan"].title.text() != "Building the demo case":
            outcome.update(code=1, text=f"FAILED: {window.pages['scan'].error.text() or 'scan did not finish'}")
        if outcome:
            report.write_text(str(outcome["text"]) + "\n", encoding="utf-8")
            app.exit(int(outcome["code"]))

    window.start_scan(jobs.ScanParams(evidence=folder / "evidence-root", output=folder / "report", demo=True,
                                      case={"case": "Smoke test (synthetic)", "examiner": ""}))
    timer = QTimer(interval=100)
    timer.timeout.connect(check)
    timer.start()
    QTimer.singleShot(180_000, lambda: (report.write_text("FAILED: timed out\n", encoding="utf-8"), app.exit(3)))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
