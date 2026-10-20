"""Live acquisition: hives through the registry API, files with their times, and the same results.

The acquirer runs against a fake "running computer" (the demo's Windows laptop, with its hives
locked so they can only come through the registry save path), then the acquired copy is scanned
and must give exactly the records the original gives. A real acquisition of this computer runs
only when the tests run as administrator on Windows (as on GitHub's Windows runners).
"""

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from connected_phone_forensics.demo import build_demo_evidence
from connected_phone_forensics.engine import scan
from connected_phone_forensics.evidence import long_path
from connected_phone_forensics.live.acquire import FILETIME_EPOCH, Acquirer, Entry

SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"


class FakeComputer:
    """A folder standing in for C:\\; SYSTEM, SOFTWARE and the signed-in user's NTUSER.DAT are locked."""

    def __init__(self, root: Path):
        self.root = str(root)
        self.base = root
        self.locked = {root / "Windows" / "System32" / "config" / "SYSTEM",
                       root / "Windows" / "System32" / "config" / "SOFTWARE",
                       root / "Users" / "alex" / "NTUSER.DAT"}

    def join(self, rel: str) -> str:
        return str(self.base.joinpath(*rel.split("/")))

    def relative(self, folder: str):
        try:
            return Path(folder).relative_to(self.base).as_posix()
        except ValueError:
            return None

    # Paths go through long_path, as on the live system: below pytest's temporary folder the Phone
    # Link databases are deeper than Windows' 260-character limit.
    def list_dir(self, path: str):
        with os.scandir(long_path(path)) as it:
            return [Entry(e.name, e.is_dir(follow_symlinks=False), e.is_symlink()) for e in it]

    def open(self, path: str):
        if Path(path) in self.locked:
            raise PermissionError(13, "The process cannot access the file because it is being used", path)
        return open(long_path(path), "rb")

    def times(self, path: str):
        st = os.stat(long_path(path))
        tick = lambda ns: ns // 100 + FILETIME_EPOCH  # noqa: E731
        return tick(st.st_mtime_ns), tick(st.st_atime_ns), tick(st.st_mtime_ns), st.st_size

    def set_times(self, path: str, created: int, accessed: int, modified: int) -> None:
        ns = lambda t: (t - FILETIME_EPOCH) * 100  # noqa: E731
        os.utime(long_path(path), ns=(ns(accessed), ns(modified)))

    def save_hive(self, hive: str, dest: Path) -> str:
        src = {"SYSTEM": "Windows/System32/config/SYSTEM", "SOFTWARE": "Windows/System32/config/SOFTWARE",
               f"HKU\\{SID}": "Users/alex/NTUSER.DAT"}[hive]
        shutil.copyfile(long_path(self.join(src)), long_path(dest))
        return "RegSaveKeyEx (test)"

    def profiles(self):
        return [(SID, self.join("Users/alex"))]

    def loaded_users(self):
        return {SID}


@pytest.fixture
def computer(tmp_path):
    evidence = build_demo_evidence(tmp_path / "demo")
    root = evidence / "ALEX-LAPTOP" / "C"
    # A folder time the Phone Link parser reports: it must survive the copy.
    profile = next((root / "Users" / "alex" / "AppData" / "Local" / "Packages").rglob("Indexed"))
    os.utime(profile, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    # KDE Connect for Windows: the parser lists the (empty) per-device plugin folders.
    shutil.copytree(next(p for p in evidence.rglob("kdeconnect") if p.parent.name == ".config"),
                    root / "Users" / "alex" / "AppData" / "Local" / "kdeconnect")
    return FakeComputer(root)


def test_acquisition_copies_evidence_with_hashes_and_times(computer, tmp_path):
    dest = tmp_path / "usb" / "case"
    acq = Acquirer(computer, dest, computer="ALEX-LAPTOP", windows="Windows 11 Pro").run()
    items = {i.path: i for i in acq.items}
    assert not acq.errors, [i.note for i in acq.errors]
    for hive in ("Windows/System32/config/SYSTEM", "Windows/System32/config/SOFTWARE", "Users/alex/NTUSER.DAT"):
        assert items[hive].method == "RegSaveKeyEx (test)", hive  # locked files came through the registry
    assert "Windows/INF/setupapi.dev.log" in items
    assert any(p.endswith("phone.db-wal") for p in items)  # SQLite sidecars come along
    collection = Path(acq.collection)
    for item in acq.items:
        data = long_path(collection / item.path).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item.sha256 and len(data) == item.size
    base, copies = long_path(computer.base), long_path(collection)
    src_db = next(base.rglob("phone.db"))
    assert (copies / src_db.relative_to(base)).stat().st_mtime_ns == src_db.stat().st_mtime_ns
    src_dir = next(base.rglob("Indexed"))
    assert (copies / src_dir.relative_to(base)).stat().st_mtime_ns == src_dir.stat().st_mtime_ns
    record = json.loads((dest / "acquisition.json").read_text(encoding="utf-8"))
    assert record["summary"]["files_acquired"] == len(acq.items) and record["summary"]["errors"] == 0
    sums = (dest / "acquisition.sha256").read_text(encoding="utf-8").splitlines()
    assert len(sums) == len(acq.items) and sums[0].split("  ")[1].startswith("collection/ALEX-LAPTOP/")
    assert "live acquisition of ALEX-LAPTOP" in (dest / "acquisition.log").read_text(encoding="utf-8")


def test_scanning_the_acquired_copy_gives_the_same_records(computer, tmp_path):
    original = scan(computer.base.parent, plugin_ids=None)
    acq = Acquirer(computer, tmp_path / "case", computer="ALEX-LAPTOP").run()
    copy = scan(Path(acq.collection).parent.parent)
    assert copy.systems[0].label == "ALEX-LAPTOP"  # computer name read from the saved SYSTEM hive

    def records(result):
        return sorted((o.artifact, o.event, o.message, str(o.timestamp), o.provenance.source_path,
                       o.provenance.locator, json.dumps(o.data, sort_keys=True, default=str))
                      for o in result.observations)

    assert any(o.artifact == "win_kdeconnect" and o.data.get("plugin_folders") for o in copy.observations)
    assert records(copy) == records(original)


def test_cancelled_acquisition_stops(computer, tmp_path):
    import threading

    from connected_phone_forensics.engine import ScanCancelled

    stop = threading.Event()
    stop.set()
    with pytest.raises(ScanCancelled):
        Acquirer(computer, tmp_path / "case", computer="X", cancel=stop).run()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drives")
def test_output_on_the_examined_drive_is_refused(tmp_path):
    from connected_phone_forensics.live.acquire import AcquisitionError, check_destination

    system_drive = os.environ.get("SYSTEMDRIVE", "C:") + "\\"
    on_system = Path(system_drive) / "cpf-test-never-created" / "case"
    with pytest.raises(AcquisitionError, match="drive being examined"):
        check_destination(on_system, system_drive)
    assert check_destination(on_system, system_drive, allow_same_drive=True)["root"].upper().startswith(
        system_drive[:2].upper())


def _admin() -> bool:
    if sys.platform != "win32":
        return False
    from connected_phone_forensics.live import winapi

    return winapi.is_admin()


@pytest.mark.skipif(not _admin(), reason="needs Windows and administrator rights (GitHub's runners have them)")
def test_real_live_acquisition_of_this_computer(tmp_path):
    from connected_phone_forensics.live.acquire import acquire_live
    from connected_phone_forensics.readers.registry import open_hive

    acq = acquire_live(tmp_path / "case", allow_same_drive=True)
    hives = {i.path: i for i in acq.items if i.method.startswith("RegSaveKeyEx")}
    assert "Windows/System32/config/SYSTEM" in hives and "Windows/System32/config/SOFTWARE" in hives
    assert all(i.status == "acquired" and i.sha256 for i in hives.values())
    system = Path(acq.collection) / "Windows" / "System32" / "config" / "SYSTEM"
    with open_hive(system) as h:
        assert h.key("\\Select") is not None
    result = scan(Path(acq.collection).parent.parent)
    assert result.systems and result.systems[0].label.upper() == acq.computer.upper()
