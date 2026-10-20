"""Desktop app: a full demo scan through the GUI's background jobs, reopening, filtering, cancelling.

Skipped when PySide6 or qtawesome cannot be imported.
"""

import os
import sys
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


def _open_demo_case(app, window, tmp_path):
    from connected_phone_forensics.demo import build_demo_evidence
    from connected_phone_forensics.engine import scan
    from connected_phone_forensics.output import write_all

    evidence = build_demo_evidence(tmp_path / "case")
    write_all(scan(evidence, export_dir=tmp_path / "out" / "exports"), tmp_path / "out", pdf=False, xlsx=False)
    window.open_results(tmp_path / "out")
    assert _wait(app, lambda: window.case is not None, 60)
    return window.case


def test_recovered_data_page_filters_and_links(app, window, tmp_path):
    case = _open_demo_case(app, window, tmp_path)
    page = window.pages["data"]
    window.go("data")
    assert page.current == "messages" and int(page.tiles["messages"].count.text()) == 9
    page.search.setText("jordan")
    page._apply()
    shown = page.models["messages"].visible_rows()
    assert shown and all("jordan" in r["_search"] for r in shown)
    page.search.setText("")
    page.f_status.select("deleted")
    assert {r["text"] for r in page.models["messages"].visible_rows()} == {
        "Bring the documents. Delete this.", "Wipe the laptop tonight"}
    assert page.tiles["calls"].count.text() == "0"  # tiles count what the filters leave
    phone = next(d["id"] for d in case.phones if d["label"] == "Phone line +14155550199")
    window.go("data", {"device": phone, "category": "calls"})
    assert not page.f_status.value() and page.current == "calls"
    assert {r["device"] for r in page.models["calls"].visible_rows()} == {phone}
    record = page.models["calls"].visible_rows()[0]["record"]
    window.go("timeline", {"record": record})
    tl = window.pages["timeline"]
    assert tl.model.row(tl.table.currentIndex().row()) is case.records[record]


def test_device_page_draws_the_phone_and_lists_every_identifier(app, window, tmp_path):
    from connected_phone_forensics.gui.pages.devices import IdentifierRow, PhoneView

    case = _open_demo_case(app, window, tmp_path)
    phone = next(d for d in case.phones if any(i["kind"] == "udid" for i in d["identifiers"]))
    window.go("devices", phone["id"])
    page = window.pages["devices"]
    assert page.current == phone["id"]
    assert page.detail.findChildren(PhoneView)
    rows = [r for r in page.detail.findChildren(IdentifierRow) if r.isVisibleTo(page.detail)]  # not the old view
    assert len(rows) == len(phone["identifiers"])
    text = " ".join(lbl.text() for r in rows for lbl in r.findChildren(type(page.list_empty)))
    assert "A16 Bionic" in text  # the UDID decoded, not trimmed


def test_every_icon_name_exists():
    # An unknown name draws an empty circle; check every name the app uses against the icon fonts.
    import glob
    import json
    import re
    from pathlib import Path

    import qtawesome

    fonts = Path(qtawesome.__file__).parent / "fonts"
    maps = {}
    for prefix, pattern in (("ph", "phosphor*"), ("mdi6", "materialdesignicons6*")):
        found = glob.glob(str(fonts / f"{pattern}charmap*.json"))
        maps[prefix] = set(json.loads(Path(found[0]).read_text(encoding="utf-8"))) if found else None
    package = Path(__file__).resolve().parents[1] / "connected_phone_forensics"
    missing = []
    for source in package.rglob("*.py"):
        for m in re.finditer(r"""["'](ph|mdi6)\.([a-z0-9-]+)["']""", source.read_text(encoding="utf-8")):
            known = maps.get(m.group(1))
            if known is not None and m.group(2) not in known:
                missing.append(f"{source.name}: {m.group(0)}")
    assert not missing, missing


def test_column_filter_conditions_and_value_lists(app):
    from collections import Counter

    from connected_phone_forensics.gui.models import Column, ColumnFilter, RecordModel

    rows = [{"t": "2025-01-25 10:00:00", "n": 5, "s": "Bank of X"}, {"t": "2025-01-26 09:00:00", "n": 50, "s": "jordan"},
            {"t": "", "n": None, "s": ""}]
    m = RecordModel([Column("Time", lambda r: r["t"] or "—", kind="time"),
                     Column("Num", lambda r: "" if r["n"] is None else f"{r['n']:,}", kind="number"),
                     Column("Text", lambda r: r["s"])])
    m.set_rows(rows)

    def shown():
        return [r["s"] for r in m.visible_rows()]

    for flt, want in ((ColumnFilter("contains", "BANK"), ["Bank of X"]),
                      (ColumnFilter("not_contains", "bank"), ["jordan", ""]),
                      (ColumnFilter("begins", "jo"), ["jordan"]), (ColumnFilter("regex", r"^j.*n$"), ["jordan"]),
                      (ColumnFilter("empty"), [""]), (ColumnFilter("regex", "("), ["Bank of X", "jordan", ""])):
        m.set_column_filter(2, flt)
        assert shown() == want, flt
    assert ColumnFilter("regex", "(").error("text")  # an invalid pattern is reported, not applied
    m.set_column_filter(2, None)
    m.set_column_filter(1, ColumnFilter("ge", "10"))
    assert shown() == ["jordan"]
    m.set_column_filter(1, None)
    m.set_column_filter(0, ColumnFilter("before", "2025-01-25"))  # the whole day
    assert shown() == ["Bank of X"]
    m.set_column_filter(0, ColumnFilter("after", "2025-01-25 12:00"))
    assert shown() == ["jordan"]
    m.set_column_filter(0, None)
    assert m.column_values(0) == Counter({"2025-01-25": 1, "2025-01-26": 1, "": 1})  # days, as in Excel
    m.set_column_filter(0, ColumnFilter(values=frozenset({"2025-01-26"})))
    assert shown() == ["jordan"] and m.column_values(2) == Counter({"jordan": 1})
    m.clear_column_filters()
    assert len(m.visible) == 3


def test_recovered_data_pictures_filters_and_verified_saving(app, window, tmp_path, monkeypatch):
    import connected_phone_forensics.gui.pages.data as data_page
    from connected_phone_forensics.gui.colfilter import FilterPopup
    from connected_phone_forensics.gui.models import PICTURE_ROLE

    _open_demo_case(app, window, tmp_path)
    page = window.pages["data"]
    window.go("data", "contacts")
    m = page.models["contacts"]
    rows = m.visible_rows()
    photo = next(i for i, r in enumerate(rows) if r.get("file"))
    pm = m.index(photo, 0).data(PICTURE_ROLE)  # the saved photo, drawn round
    assert pm is not None and not pm.isNull() and pm.devicePixelRatio() == 2.0
    assert not data_page.initials("Jordan Lee", 26, "#3987e5", circle=True).isNull()  # no photo: initials

    name_col = [c.title for c in m.columns].index("Name")
    pop = FilterPopup(page.tables["contacts"], name_col)
    pop.op.setCurrentIndex(pop.op.findData("equals"))
    pop.text.setText(rows[photo]["name"].upper())
    pop._apply()
    assert [r["name"] for r in m.visible_rows()] == [rows[photo]["name"]]
    assert page.filter_bar.isVisibleTo(page) and page.clear_btn.isVisibleTo(page)
    page._clear()
    assert not m.column_filters() and len(m.visible) == len(rows)

    page.f_status.select("files")
    with_files = m.visible_rows()
    assert with_files and all(r["file"] for r in with_files)
    (tmp_path / "saved").mkdir()
    monkeypatch.setattr(data_page, "choose_folder", lambda *_a: str(tmp_path / "saved"))
    page._save(with_files, "contact photos")
    assert _wait(app, lambda: page.last_saved is not None, 60)
    res = page.last_saved
    assert len(res.saved) == len({r["file"] for r in with_files}) and not res.mismatched and not res.skipped
    assert (res.folder / "SHA256SUMS.txt").is_file() and res.folder.parent == tmp_path / "saved"


def test_theme_switch_survives_widgets_the_pages_threw_away(app, window, tmp_path):
    # Moving around rebuilds parts of pages; the widgets they drop must not stop a later theme change
    # (it once left the light theme applied only to a few icons).
    from PySide6.QtCore import QCoreApplication, QEvent

    from connected_phone_forensics.gui import theme
    from connected_phone_forensics.gui.widgets import THEME

    case = _open_demo_case(app, window, tmp_path)
    for d in case.devices[:6]:
        window.go("devices", d["id"])
        app.processEvents()
    window.go("data", "contacts")
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)  # as the running app would
    window.apply_theme("light")
    assert THEME.palette is theme.LIGHT and theme.LIGHT.bg in app.styleSheet()
    for listener in list(THEME._listeners):  # what is left belongs to live widgets
        listener()
    window.apply_theme("dark")
    assert theme.DARK.bg in app.styleSheet()


@pytest.mark.skipif(sys.version_info >= (3, 12), reason="None is immortal from Python 3.12")
def test_painting_keeps_its_references_to_none(app):
    # PySide6 6.12.0's Linux wheels dropped a reference to None in every QPainter.setPen/setBrush/
    # setFont, so Python before 3.12 aborted ("deallocating None") after enough drawing (see the
    # PySide6 requirement in pyproject.toml).
    from PySide6.QtGui import QColor, QFont, QImage, QPainter

    img = QImage(8, 8, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    before = sys.getrefcount(None)
    for _ in range(500):
        p.setPen(QColor("#102030"))
        p.setBrush(QColor("#405060"))
        p.setFont(QFont())
    p.end()
    assert sys.getrefcount(None) >= before - 50, "this PySide6 build loses references to None"
