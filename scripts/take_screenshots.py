"""Regenerate the README screenshots from the real app and the synthetic demo case.

    pip install -e .
    python scripts/take_screenshots.py

Writes docs/images/screenshots/*.png. The demo case is scanned for real; only the
folder paths shown on screen are replaced with neutral ones (D:\\Cases\\...), so
screenshots never carry the local user name. The progress screen replays the
events of a real scan, stopped part-way.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images" / "screenshots"
SIZE = (1600, 1000)
SHOWN_EVIDENCE = r"D:\Cases\2026-0142\Evidence"
SHOWN_OUTPUT = r"D:\Cases\2026-0142\Results"


def main() -> int:
    from PySide6.QtCore import QRectF, QSettings, Qt
    from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from connected_phone_forensics.demo import build_demo_evidence
    from connected_phone_forensics.engine import scan
    from connected_phone_forensics.gui import jobs, theme
    from connected_phone_forensics.gui.casedata import CaseData
    from connected_phone_forensics.gui.widgets import THEME
    from connected_phone_forensics.gui.window import MainWindow
    from connected_phone_forensics.output import write_all

    THEME.fonts = theme.load_fonts()
    app.setFont(THEME.fonts.ui)
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="cpf-shots-"))
    evidence = build_demo_evidence(tmp / "demo")
    events: list[dict] = []
    result = scan(evidence, case={"case": "Demo case (synthetic data)", "examiner": "A. Examiner"},
                  on_event=events.append, progress=lambda m: events.append({"type": "log", "message": m}))
    write_all(result, tmp / "report")
    case = CaseData.from_result(result, tmp / "report")
    case.input_path = SHOWN_EVIDENCE

    def pump(seconds: float = 0.15) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    def save(window, name: str) -> None:
        pump()
        shot = window.grab()
        framed = QPixmap(shot.size())
        framed.setDevicePixelRatio(shot.devicePixelRatio())
        framed.fill(Qt.GlobalColor.transparent)
        p = QPainter(framed)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.5, 0.5, shot.width() / shot.devicePixelRatio() - 1, shot.height() / shot.devicePixelRatio() - 1)
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        p.setClipPath(path)
        p.drawPixmap(0, 0, shot)
        p.setClipping(False)
        p.setPen(QPen(QColor(THEME.palette.border), 1))
        p.drawPath(path)
        p.end()
        framed.save(str(OUT / f"{name}.png"), "PNG")
        print("saved", name)

    def window_for(mode: str) -> MainWindow:
        w = MainWindow(QSettings(str(tmp / f"{mode}.ini"), QSettings.Format.IniFormat))
        w.apply_theme(mode)
        w.resize(*SIZE)
        w.show()
        return w

    w = window_for("dark")
    home = w.pages["new"]
    home.drop.set_path(evidence)
    home.case_ref.setText("2026-0142")
    home.examiner.setText("A. Examiner")
    for _ in range(300):
        pump(0.02)
        if home._systems is not None:
            break
    home.drop.title.setText("Evidence")
    home.drop.path_label.setText(SHOWN_EVIDENCE)
    home.output.edit.setText(SHOWN_OUTPUT)
    home._validate()
    w.resize(SIZE[0], SIZE[1] + 80)  # room for the Start button
    save(w, "new-scan")
    w.resize(*SIZE)

    page = w.pages["scan"]
    page.begin(jobs.ScanParams(evidence=Path(SHOWN_EVIDENCE), output=Path(SHOWN_OUTPUT)))
    w.go("scan")
    page.log.clear()
    done = 0
    for ev in events:
        if ev["type"] == "task_done":
            done += 1
            if done > 14:
                break
        if ev["type"] == "log":
            ev = {**ev, "message": ev["message"].replace(str(evidence), SHOWN_EVIDENCE)}
        if ev["type"] in ("log", "discovered", "task_done"):
            page.on_event(ev)
    page.elapsed.setText("1.4 s")
    page._timer.stop()
    w.resize(SIZE[0], 680)
    save(w, "scan-progress")
    w.resize(*SIZE)

    w.show_case(case)
    w.pages["overview"].case_kv.clear()
    for k, v in (("Case", "2026-0142 · Demo case (synthetic data)"), ("Examiner", "A. Examiner"),
                 ("Evidence", SHOWN_EVIDENCE), ("Output", SHOWN_OUTPUT), ("Tool", f"version {case.tool_version}")):
        w.pages["overview"].case_kv.add(k, v, mono=k in ("Evidence", "Output"))
    save(w, "overview")
    w.go("devices")
    save(w, "devices")
    w.go("timeline")
    tl = w.pages["timeline"]
    usb = next((i for i, r in enumerate(tl.model.visible_rows()) if r.get("artifact") == "win_usb_phones"), 0)
    tl.table.selectRow(usb)
    save(w, "timeline")
    w.go("coverage")
    save(w, "coverage")
    w.close()

    w = window_for("light")
    w.show_case(case)
    w.go("devices")
    phones = case.phones
    if len(phones) > 2:
        w.pages["devices"].focus_item(phones[2]["id"])
    save(w, "devices-light")
    w.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
