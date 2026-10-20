"""Desktop app: a full demo scan through the GUI's background jobs, reopening, filtering, cancelling.

Skipped when PySide6 or qtawesome cannot be imported.
"""

import os
import time

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("qtawesome")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from connected_phone_forensics.gui import theme
    from connected_phone_forensics.gui.widgets import THEME

    qt_app = QApplication.instance() or QApplication([])
    THEME.fonts = theme.load_fonts()
    return qt_app


@pytest.fixture
def window(app, tmp_path):
    from connected_phone_forensics.gui.window import MainWindow

    w = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    w.apply_theme("dark")
    yield w
    w.scan_job = None
    w.close()


def _wait(app, condition, timeout=120.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def _demo(window, tmp_path, monkeypatch):
    import connected_phone_forensics.gui.pages.home as home

    base = tmp_path / "demo"
    base.mkdir()
    # Only the demo folder: the scan's own temporary copies must still get folders of their own.
    monkeypatch.setattr(home, "demo_folder", lambda: base)
    window.pages["new"]._demo()
    return base


def test_demo_scan_end_to_end(app, window, tmp_path, monkeypatch):
    from connected_phone_forensics.gui.casedata import CaseData

    base = _demo(window, tmp_path, monkeypatch)
    assert window.stack.currentWidget() is window.pages["scan"]
    assert _wait(app, lambda: window.case is not None), "scan did not finish"
    case = window.case
    assert window.stack.currentWidget() is window.pages["overview"]
    assert case.outdir == base / "report" and (case.outdir / "report.html").is_file()
    assert case.phones and case.records and window.scan_job is None
    assert window.recent()[0]["path"] == str(case.outdir)

    # Reopening the saved folder gives the same case.
    again = CaseData.load(case.outdir)
    assert [d["id"] for d in again.devices] == [d["id"] for d in case.devices]
    assert [r["message"] for r in again.records] == [r["message"] for r in case.records]
    assert len(again.coverage) == len(case.coverage) and again.warnings == case.warnings

    # Timeline search narrows the rows; a device filter comes from the devices page.
    tl = window.pages["timeline"]
    tl.search.setText("pixel")
    tl._apply()
    assert 0 < len(tl.model.visible) < len(tl.model.rows)
    phone = case.phones[0]["id"]
    window.go("timeline", {"device": phone})
    assert tl.search.text() == "" and tl.f_device.value() == phone
    assert {r["device"] for r in tl.model.visible_rows()} == {phone}

    window.go("devices", phone)
    assert window.pages["devices"].current == phone
    window.go("coverage")
    window.pages["coverage"].focus_item("unrecognised")
    assert all(r["status"] == "unrecognised" for r in window.pages["coverage"].model.visible_rows())


def test_cancelled_scan_writes_nothing(app, window, tmp_path, monkeypatch):
    base = _demo(window, tmp_path, monkeypatch)
    window.cancel_scan()
    assert _wait(app, lambda: window.scan_job is None)
    assert window.case is None
    assert window.pages["scan"].title.text() == "Scan cancelled"
    assert not (base / "report" / "manifest.json").exists()


def test_open_rejects_a_folder_without_results(app, window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **_k: shown.append(a[1]))
    window.open_results(tmp_path)
    assert shown == ["Not a scan output folder"] and window.case is None


def test_record_model_filters_and_sorts():
    from PySide6.QtCore import Qt

    from connected_phone_forensics.gui.models import Column, RecordModel

    model = RecordModel([Column("n", lambda r: str(r["n"]), sort_key=lambda r: r["n"])])
    model.set_rows([{"n": n} for n in (3, 1, 2)])
    assert [r["n"] for r in model.visible_rows()] == [3, 1, 2]  # the given order until a sort is asked for
    model.sort(0, Qt.SortOrder.DescendingOrder)
    assert [r["n"] for r in model.visible_rows()] == [3, 2, 1]
    model.set_filter(lambda r: r["n"] > 1)
    assert [r["n"] for r in model.visible_rows()] == [3, 2]
    model.sort(-1)
    assert [r["n"] for r in model.visible_rows()] == [3, 2]


def test_background_results_survive_garbage_collection(app, window, tmp_path, monkeypatch):
    # Nothing holds the preview and open-results jobs; they must stay alive until their
    # result has reached the UI thread, or the result is silently lost.
    import gc

    from connected_phone_forensics.demo import build_demo_evidence
    from connected_phone_forensics.engine import scan
    from connected_phone_forensics.output import write_all

    evidence = build_demo_evidence(tmp_path / "case")
    home = window.pages["new"]
    home.drop.set_path(evidence)
    assert _wait(app, lambda: gc.collect() is not None and home._systems is not None, 60)
    assert len(home._systems) >= 3 and home.start_btn.isEnabled()

    write_all(scan(evidence), tmp_path / "out")
    window.open_results(tmp_path / "out")
    assert _wait(app, lambda: gc.collect() is not None and window.case is not None, 60)
    assert window.case.outdir == tmp_path / "out"


@pytest.mark.skipif(os.name != "nt", reason="live acquisition is Windows-only")
def test_live_acquisition_through_the_app(app, window, tmp_path, monkeypatch):
    # The app's live mode end to end, with the demo laptop standing in for this computer.
    import connected_phone_forensics.live as live
    from connected_phone_forensics.demo import build_demo_evidence
    from connected_phone_forensics.live.acquire import Acquirer
    from tests.test_live import FakeComputer

    root = build_demo_evidence(tmp_path / "demo") / "ALEX-LAPTOP" / "C"
    usb = tmp_path / "usb"
    usb.mkdir()
    machine = {"computer": "ALEX-LAPTOP", "windows": "Windows 11 Pro", "user": "alex", "admin": False,
               "system_drive": "Z:\\", "profiles": ["Z:\\Users\\alex"]}
    monkeypatch.setattr(live, "machine", lambda: machine)
    monkeypatch.setattr(live, "system_drive", lambda: "Z:\\")
    monkeypatch.setattr(live, "is_admin", lambda: True)
    monkeypatch.setattr(live, "output_drives", lambda: [
        {"root": str(usb), "kind": "removable", "label": "EVIDENCE", "filesystem": "exFAT", "serial": "1234",
         "free_bytes": 32 * 1024**3, "program_drive": False}])
    monkeypatch.setattr(live, "check_destination", lambda *a, **k: {})
    monkeypatch.setattr(live, "acquire_live", lambda dest, **k: Acquirer(
        FakeComputer(root), dest, computer="ALEX-LAPTOP", progress=k.get("progress"), cancel=k.get("cancel")).run())

    home = window.pages["new"]
    home.set_source("live")
    assert not home.live_panel.isHidden() and home.drop.isHidden()
    assert home.problem.text() == "Restart as administrator to acquire this computer."
    assert not home.live_panel.restart_btn.isHidden()
    machine["admin"] = True
    home._machine = None
    home.set_source("live")
    assert home.start_btn.isEnabled(), home.problem.text()
    assert "EVIDENCE" in home.live_output.drive.currentText()
    out = home.live_output.path()
    assert out.parent == usb / "CPF cases" and out.name.startswith("ALEX-LAPTOP ")

    home._start()
    assert _wait(app, lambda: window.case is not None), window.pages["scan"].error.text()
    case = window.case
    assert case.outdir == out / "report" and (out / "acquisition.json").is_file()
    assert case.phones and int(window.pages["scan"].c_acquired.value.text().replace(",", "")) > 20
    assert not (out / "_work").exists()  # temporary copies stayed in the output folder, then went


def test_running_system_drive_is_recognised(tmp_path):
    from pathlib import Path

    from connected_phone_forensics.live import is_system_drive_root

    if os.name == "nt":
        assert is_system_drive_root(Path(os.environ.get("SYSTEMDRIVE", "C:") + "\\"))
    assert not is_system_drive_root(tmp_path)
