"""Regenerate the README screenshots from the real app and the synthetic demo case.

    pip install -e .
    python scripts/take_screenshots.py

Writes docs/images/screenshots/*.png. The demo case is scanned for real; only the
folder paths shown on screen are replaced with neutral ones (D:\\Cases\\...), and the live
acquisition screen shows a made-up computer and USB drive, so screenshots never carry the
local computer's or user's name. The progress screen replays the events of a real scan,
stopped part-way. pdf-summary.png shows two pages of the demo case's PDF summary.
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
FIELD_PC = {"computer": "FIELD-PC-07", "windows": "Windows 11 Pro 24H2 build 26100", "user": "examiner",
            "admin": True, "system_drive": "C:\\", "profiles": ["C:\\Users\\rkumar", "C:\\Users\\examiner"]}
USB_DRIVE = {"root": "E:\\", "kind": "removable", "label": "EVIDENCE", "filesystem": "exFAT", "serial": "5A1C22F0",
             "free_bytes": 61_500_000_000, "program_drive": True}


def main() -> int:
    from PySide6.QtCore import QRectF, QSettings, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from connected_phone_forensics import live
    from connected_phone_forensics.demo import build_demo_evidence
    from connected_phone_forensics.engine import scan
    from connected_phone_forensics.gui import jobs, theme
    from connected_phone_forensics.gui.casedata import CaseData
    from connected_phone_forensics.gui.widgets import THEME
    from connected_phone_forensics.gui.window import MainWindow
    from connected_phone_forensics.output import write_all
    from connected_phone_forensics.output.pdf import preview_pages

    THEME.fonts = theme.load_fonts()
    app.setFont(THEME.fonts.ui)
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="cpf-shots-"))
    evidence = build_demo_evidence(tmp / "demo")
    events: list[dict] = []
    result = scan(evidence, case={"case": "2026-0142", "examiner": "A. Examiner"},
                  on_event=events.append, progress=lambda m: events.append({"type": "log", "message": m}),
                  export_dir=tmp / "report" / "exports")
    write_all(result, tmp / "report", pdf=False, xlsx=False)
    case = CaseData.from_result(result, tmp / "report")
    case.input_path = SHOWN_EVIDENCE
    live.machine = lambda: dict(FIELD_PC)
    live.output_drives = lambda: [dict(USB_DRIVE)]

    def pump(seconds: float = 0.15) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    def save(window, name: str) -> None:
        pump(0.4)
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

    def phone(label_start: str) -> str:
        return next(d["id"] for d in case.phones if d["label"].startswith(label_start)
                    and (label_start != "Alex" or any(i["kind"] == "udid" for i in d["identifiers"])))

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

    home.set_source("live")
    save(w, "live")
    home.set_source("folder")
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
    w.go("devices", phone("Alex"))
    save(w, "devices")
    data = w.pages["data"]
    w.go("data", "messages")
    rows = data.models["messages"].visible_rows()
    deleted = next((i for i, r in enumerate(rows) if r.get("status")), 0)
    data.tables["messages"].selectRow(deleted)
    save(w, "recovered-data")
    w.go("timeline")
    tl = w.pages["timeline"]
    usb = next((i for i, r in enumerate(tl.model.visible_rows()) if r.get("artifact") == "win_usb_phones"), 0)
    tl.table.selectRow(usb)
    save(w, "timeline")
    w.go("coverage", "unrecognised")
    save(w, "coverage")
    w.close()

    w = window_for("light")
    w.show_case(case)
    w.go("devices", phone("Phone line +14155550199"))
    save(w, "devices-light")
    data = w.pages["data"]
    w.go("data", "media")
    data.layout_toggle.select("grid")
    data._apply()
    pump(1.5)  # thumbnails load in the background
    save(w, "recovered-photos-light")
    w.close()

    # Two pages of the PDF summary side by side: the cover and a phone page.
    pages = preview_pages(case, dpi=96)
    first_phone = next(i for i, img in enumerate(pages) if i > 0)
    pair = [pages[0], pages[first_phone + 1] if first_phone + 1 < len(pages) else pages[first_phone]]
    phone_page = next((i for i in range(1, len(pages)) if _has_phone(pages[i])), 2)
    pair[1] = pages[phone_page]
    gap, pad = 36, 40
    w0, h0 = pair[0].width(), pair[0].height()
    sheet = QImage(w0 * 2 + gap + pad * 2, h0 + pad * 2, QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#E9EEF6"))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for n, img in enumerate(pair):
        x = pad + n * (w0 + gap)
        for k in range(10, 0, -1):  # soft shadow
            p.fillRect(QRectF(x - k + 6, pad - k + 10, w0 + 2 * k, h0 + 2 * k), QColor(11, 22, 48, 5))
        p.drawImage(x, pad, img)
    p.end()
    sheet.save(str(OUT / "pdf-summary.png"))
    print("saved pdf-summary")
    return 0


def _has_phone(img) -> bool:
    """A phone page: its top-left area is mostly the drawn phone's dark frame."""
    from PySide6.QtGui import QColor

    dark = 0
    for y in range(int(img.height() * 0.18), int(img.height() * 0.5), 12):
        c = QColor(img.pixel(int(img.width() * 0.12), y))
        dark += c.lightness() < 90
    return dark > 8


if __name__ == "__main__":
    raise SystemExit(main())
