"""Start the desktop application: ``cpf-gui`` or ``python -m connected_phone_forensics.gui``."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("The desktop app needs PySide6: pip install -e \".[gui]\"", file=sys.stderr)
        return 2

    if sys.platform == "win32":  # group the taskbar entry under our own icon, not python.exe's
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ConnectedPhoneForensics.Desktop")
        except Exception:  # noqa: BLE001 - cosmetic only
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("Connected Phone Forensics")
    app.setOrganizationName("ConnectedPhoneForensics")
    app.setStyle("Fusion")

    from . import theme
    from .widgets import THEME
    from .window import MainWindow

    THEME.fonts = theme.load_fonts()
    app.setFont(THEME.fonts.ui)
    window = MainWindow()
    saved = window.settings.value("theme")
    window.apply_theme(saved if saved in ("light", "dark") else ("dark" if theme.system_prefers_dark() else "light"))
    window.show()
    args = app.arguments()[1:]
    if args:  # a results folder given on the command line opens straight away
        from pathlib import Path

        window.open_results(Path(args[0]))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
