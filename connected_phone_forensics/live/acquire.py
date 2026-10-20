"""Live acquisition: copy the evidence the Windows parsers need from the running computer.

Run as administrator, with the output on another drive (a USB drive), this:

1. enables the backup privilege, so ACLs cannot hide files or folders;
2. saves the registry hives Windows keeps locked (SYSTEM, SOFTWARE, the NTUSER.DAT of every
   signed-in user) with RegSaveKeyEx, the API behind "reg save": a consistent copy of the hive
   as the system sees it at that moment;
3. copies every other file the parsers read (from their declared paths, with SQLite -wal/-shm
   and registry .LOG sidecars), reading with backup semantics and hashing each file as it is
   read;
4. gives every copy and copied folder the original creation, access and modification times
   (parsers use folder and file times), and records each item (source, method, size, SHA-256,
   times, errors) in acquisition.json, acquisition.sha256 and a timestamped acquisition.log.

The copies form a normal evidence tree (``collection/<computer>/C/...``) that the regular scan
then reads. Nothing is written to the computer being examined; see :func:`check_destination`.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path, PureWindowsPath
from typing import BinaryIO, Protocol

from .. import __version__
from ..evidence import long_path

FILETIME_EPOCH = 116444736000000000  # 1601-01-01 to 1970-01-01, in 100 ns ticks
SIDECARS = ("-wal", "-shm", "-journal", ".LOG1", ".LOG2", ".LOG")
HIVE_FILES = {"Windows/System32/config/SYSTEM": "SYSTEM", "Windows/System32/config/SOFTWARE": "SOFTWARE"}
CHUNK = 1024 * 1024


class AcquisitionError(Exception):
    """The acquisition cannot start (not administrator, output on the evidence drive...)."""


@dataclass
class Entry:
    name: str
    is_dir: bool
    is_link: bool = False


class Backend(Protocol):
    """What acquisition needs from the system: the live Windows API or, in tests, a fake."""

    root: str

    def join(self, rel: str) -> str: ...
    def relative(self, folder: str) -> str | None: ...
    def list_dir(self, path: str) -> list[Entry]: ...
    def open(self, path: str) -> BinaryIO: ...
    def times(self, path: str) -> tuple[int, int, int, int]: ...
    def set_times(self, path: str, created: int, accessed: int, modified: int) -> None: ...
    def save_hive(self, hive: str, dest: Path) -> str: ...
    def profiles(self) -> list[tuple[str, str]]: ...
    def loaded_users(self) -> set[str]: ...


@dataclass
class Item:
    source: str                 # path on the examined computer
    path: str                   # path inside the collection, '/'-separated
    method: str                 # "RegSaveKeyEx (uncompressed)", "backup-semantics read", ...
    status: str                 # "acquired" | "error"
    size: int | None = None
    sha256: str | None = None
    created: str | None = None
    modified: str | None = None
    accessed: str | None = None
    note: str = ""


@dataclass
class Acquisition:
    computer: str
    windows: str
    user: str
    elevated: bool
    backup_privilege: bool
    source_root: str
    destination: str
    destination_drive: dict
    collection: str             # the evidence tree to scan
    started: str
    finished: str = ""
    items: list[Item] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Item]:
        return [i for i in self.items if i.status == "error"]

    def summary(self) -> dict:
        ok = [i for i in self.items if i.status == "acquired"]
        return {"mode": "live", "computer": self.computer, "windows": self.windows, "user": self.user,
                "elevated": self.elevated, "backup_privilege": self.backup_privilege,
                "started": self.started, "finished": self.finished, "source_root": self.source_root,
                "destination": self.destination, "destination_drive": self.destination_drive,
                "files_acquired": len(ok), "bytes_acquired": sum(i.size or 0 for i in ok),
                "hives_saved": sum(1 for i in ok if i.method.startswith("RegSaveKeyEx")),
                "errors": len(self.errors), "warnings": self.warnings,
                "record": "acquisition.json"}


def filetime_iso(ticks: int | None) -> str | None:
    if not ticks:
        return None
    try:
        return datetime.fromtimestamp((ticks - FILETIME_EPOCH) / 1e7, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_destination(dest: Path, source_root: str, allow_same_drive: bool = False) -> dict:
    """Refuse an output folder on the drive being examined: writing there overwrites free space
    that may still hold deleted evidence. Returns the destination drive's details."""
    from . import winapi

    probe = Path(dest)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    info = winapi.drive_info(str(probe))
    src = winapi.drive_info(source_root)
    same = info.root.rstrip("\\").lower() == src.root.rstrip("\\").lower()
    if same and not allow_same_drive:
        raise AcquisitionError(
            f"The output folder is on {src.root}, the drive being examined. Choose a folder on another "
            "drive (a USB drive or external disk): writing to the examined drive overwrites free space "
            "that may hold deleted evidence.")
    return {**asdict(info), "same_as_source": same}


class Acquirer:
    def __init__(self, backend: Backend, dest: Path, *, computer: str, windows: str = "",
                 elevated: bool = True, backup_privilege: bool = True, drive: dict | None = None,
                 patterns: Iterable[str] | None = None, progress: Callable[[dict], None] | None = None,
                 cancel: threading.Event | None = None):
        self.b = backend
        self.dest = Path(dest)
        self.say = progress or (lambda _e: None)
        self.cancel = cancel or threading.Event()
        drive_letter = (PureWindowsPath(backend.root).drive.rstrip(":") or "root").upper()
        safe_host = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in computer) or "computer"
        self.collection = self.dest / "collection" / safe_host / drive_letter
        self.patterns = list(patterns if patterns is not None else windows_patterns())
        self.acq = Acquisition(
            computer=computer, windows=windows, user=os.environ.get("USERNAME", ""), elevated=elevated,
            backup_privilege=backup_privilege, source_root=backend.root, destination=str(self.dest),
            destination_drive=drive or {}, collection=str(self.collection), started=_now())
        self._dirs: dict[str, str] = {}  # collection dir (rel) -> source dir, for folder times
        self._tree_dirs: set[str] = set()  # folders below a '**' pattern, recreated even when empty
        self._seen: set[str] = set()
        self._log = None

    # -- helpers -------------------------------------------------------------------

    def _source(self, rel: str) -> str:
        return self.b.join(rel) if rel else self.b.root

    def _log_line(self, text: str) -> None:
        line = f"{_now()}  {text}"
        if self._log:
            self._log.write(line + "\n")
            self._log.flush()

    def _note_dirs(self, rel: str) -> None:
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i])
            self._dirs.setdefault(d, self._source(d))

    def _record(self, item: Item) -> None:
        self.acq.items.append(item)
        self._log_line(f"{item.status.upper():8} {item.method:28} {item.source}"
                       + (f"  sha256={item.sha256}" if item.sha256 else "") + (f"  ({item.note})" if item.note else ""))
        self.say({"type": "acquire_item", "path": item.path, "status": item.status, "method": item.method,
                  "count": len(self.acq.items)})

    def _hash_file(self, path: Path) -> tuple[int, str]:
        h, size = hashlib.sha256(), 0
        with open(long_path(path), "rb") as f:
            for chunk in iter(lambda: f.read(CHUNK), b""):
                h.update(chunk)
                size += len(chunk)
        return size, h.hexdigest()

    # -- steps -----------------------------------------------------------------------

    def save_hive(self, hive: str, rel: str) -> None:
        if rel.lower() in self._seen:
            return
        self._seen.add(rel.lower())
        dest = long_path(self.collection.joinpath(*rel.split("/")))
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._note_dirs(rel)
        source = self._source(rel)
        try:
            method = self.b.save_hive(hive, dest)
            size, digest = self._hash_file(dest)
            self._record(Item(source=f"{hive} (live registry)", path=rel, method=method, status="acquired",
                              size=size, sha256=digest,
                              note="consistent copy of the live hive; no transaction logs needed"))
        except OSError as exc:
            self._record(Item(source=source, path=rel, method="RegSaveKeyEx", status="error",
                              note=_describe(exc)))

    def copy_file(self, rel: str) -> None:
        key = rel.lower()
        if key in self._seen:
            return
        self._seen.add(key)
        source = self._source(rel)
        dest = long_path(self.collection.joinpath(*rel.split("/")))
        try:
            created, accessed, modified, _size = self.b.times(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            self._note_dirs(rel)
            h, size = hashlib.sha256(), 0
            with self.b.open(source) as fin, open(dest, "wb") as fout:
                for chunk in iter(lambda: fin.read(CHUNK), b""):
                    h.update(chunk)
                    fout.write(chunk)
                    size += len(chunk)
            self.b.set_times(str(dest), created, accessed, modified)
            self._record(Item(source=source, path=rel, method="backup-semantics read", status="acquired",
                              size=size, sha256=h.hexdigest(), created=filetime_iso(created),
                              modified=filetime_iso(modified), accessed=filetime_iso(accessed)))
        except OSError as exc:
            self._record(Item(source=source, path=rel, method="backup-semantics read", status="error",
                              note=_describe(exc)))

    def _list(self, rel_dir: str) -> list[Entry]:
        try:
            return self.b.list_dir(self._source(rel_dir) if rel_dir else self.b.root)
        except FileNotFoundError as exc:
            if getattr(exc, "winerror", None) not in (206,):  # 206: path too long, not missing
                return []
            self.acq.warnings.append(f"folder {self._source(rel_dir)} could not be listed: {_describe(exc)}")
            return []
        except OSError as exc:
            self.acq.warnings.append(f"folder {self._source(rel_dir)} could not be listed: {_describe(exc)}")
            return []

    def expand(self, pattern: str) -> list[str]:
        """Files matching a parser path pattern on the live system (case-insensitive, like EvidenceFS)."""
        parts = [p for p in pattern.replace("\\", "/").split("/") if p]
        current = [""]
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            nxt: list[str] = []
            for d in current:
                if part == "**":
                    nxt.extend(self._walk_files(d) if last else self._walk_dirs(d))
                    continue
                for e in self._list(d):
                    if e.is_link:
                        continue  # junctions and symlinks ("All Users", "Application Data") would loop
                    if (last and e.is_dir) or (not last and not e.is_dir):
                        continue
                    if fnmatchcase(e.name.lower(), part.lower()):
                        nxt.append(f"{d}/{e.name}" if d else e.name)
            current = nxt
            if not current:
                return []
        return current

    def _walk_dirs(self, d: str) -> list[str]:
        out = [d]
        for e in self._list(d):
            if e.is_dir and not e.is_link:
                out.extend(self._walk_dirs(f"{d}/{e.name}" if d else e.name))
        return out

    def _walk_files(self, d: str) -> list[str]:
        out = []
        for e in self._list(d):
            rel = f"{d}/{e.name}" if d else e.name
            if e.is_link:
                continue
            if e.is_dir:
                self._tree_dirs.add(rel)
                out.extend(self._walk_files(rel))
            else:
                out.append(rel)
        return out

    def _with_sidecars(self, rel: str) -> list[str]:
        folder, _, name = rel.rpartition("/")
        names = {e.name.lower(): e.name for e in self._list(folder) if not e.is_dir}
        out = [rel]
        for suffix in SIDECARS:
            hit = names.get((name + suffix).lower())
            if hit:
                out.append(f"{folder}/{hit}" if folder else hit)
        return out

    # -- run -----------------------------------------------------------------------------

    def run(self) -> Acquisition:
        self.dest.mkdir(parents=True, exist_ok=True)
        self.collection.mkdir(parents=True, exist_ok=True)
        with open(self.dest / "acquisition.log", "a", encoding="utf-8") as self._log:
            self._log_line(f"Connected Phone Forensics {__version__}: live acquisition of {self.acq.computer} "
                           f"({self.acq.windows}) by {self.acq.user}; elevated={self.acq.elevated}, "
                           f"backup privilege={self.acq.backup_privilege}")
            self._log_line(f"output: {self.dest} ({self.acq.destination_drive})")
            if self.acq.destination_drive.get("same_as_source"):
                self.acq.warnings.append(
                    f"the output was written to {self.b.root}, the drive being examined, at the examiner's "
                    "choice: this overwrote free space on it")
            self.say({"type": "acquire_stage", "stage": "hives", "message": "Saving registry hives"})
            for rel, hive in HIVE_FILES.items():
                self._check_cancel()
                self.save_hive(hive, rel)
            self.say({"type": "acquire_stage", "stage": "users", "message": "Saving user registry hives"})
            loaded = self.b.loaded_users()
            for sid, folder in self.b.profiles():
                self._check_cancel()
                rel_home = self.b.relative(folder)
                if rel_home is None:
                    self.acq.warnings.append(f"profile {folder} ({sid}) is not on {self.b.root}; not acquired")
                    continue
                ntuser = f"{rel_home}/NTUSER.DAT"
                if sid in loaded:
                    self.save_hive(f"HKU\\{sid}", ntuser)
                else:
                    for rel in self._with_sidecars(ntuser):
                        self.copy_file(rel)
            self.say({"type": "acquire_stage", "stage": "files", "message": "Copying evidence files"})
            for pattern in self.patterns:
                for rel in self.expand(pattern):
                    self._check_cancel()
                    if rel.lower() in (h.lower() for h in HIVE_FILES) or rel.lower().endswith("/ntuser.dat"):
                        continue  # saved through the registry above
                    for f in self._with_sidecars(rel):
                        self.copy_file(f)
            for rel in sorted(self._tree_dirs):
                long_path(self.collection.joinpath(*rel.split("/"))).mkdir(parents=True, exist_ok=True)
                self._note_dirs(f"{rel}/-")
            self._restore_folder_times()
            self.acq.finished = _now()
            self._log_line(f"done: {sum(1 for i in self.acq.items if i.status == 'acquired')} item(s) acquired, "
                           f"{len(self.acq.errors)} error(s), {len(self.acq.warnings)} warning(s)")
        self._write_records()
        return self.acq

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            from ..engine import ScanCancelled

            raise ScanCancelled("acquisition cancelled")

    def _restore_folder_times(self) -> None:
        for rel in sorted(self._dirs, key=lambda r: r.count("/"), reverse=True):  # deepest first
            try:
                created, accessed, modified, _ = self.b.times(self._dirs[rel])
                self.b.set_times(str(long_path(self.collection.joinpath(*rel.split("/")))), created, accessed,
                                 modified)
            except OSError as exc:
                self.acq.warnings.append(f"times of folder {self._dirs[rel]} not copied: {_describe(exc)}")

    def _write_records(self) -> None:
        record = asdict(self.acq)
        record.update(tool="connected-phone-forensics", tool_version=__version__, summary=self.acq.summary())
        (self.dest / "acquisition.json").write_text(json.dumps(record, indent=2, ensure_ascii=False),
                                                    encoding="utf-8")
        lines = [f"{i.sha256}  collection/{self.collection.relative_to(self.dest / 'collection').as_posix()}/"
                 f"{i.path}" for i in self.acq.items if i.sha256]
        (self.dest / "acquisition.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def windows_patterns() -> list[str]:
    """Every path the Windows parsers read, as declared by the parsers themselves."""
    from ..plugin import load_plugins

    seen: dict[str, None] = {}
    for cls in load_plugins().values():
        if cls.os == "windows":
            for p in [*cls.paths, *cls.acquire]:
                seen.setdefault(p, None)
    return list(seen)


def _describe(exc: OSError) -> str:
    from ..evidence import describe_error

    return describe_error(exc)


class LiveBackend:
    """The running Windows computer, read with the backup privilege."""

    def __init__(self, root: str = ""):
        from . import winapi

        self.w = winapi
        self.root = root or (os.environ.get("SYSTEMDRIVE", "C:") + "\\")

    def join(self, rel: str) -> str:
        return str(PureWindowsPath(self.root, *rel.split("/")))

    def relative(self, folder: str) -> str | None:
        f, r = PureWindowsPath(folder), PureWindowsPath(self.root)
        try:
            return f.relative_to(r).as_posix()
        except ValueError:
            return None

    def list_dir(self, path: str) -> list[Entry]:
        try:
            out = []
            with os.scandir(long_path(path)) as it:
                for e in it:
                    attrs = e.stat(follow_symlinks=False).st_file_attributes
                    out.append(Entry(e.name, e.is_dir(follow_symlinks=False), bool(attrs & 0x400)))
            return out
        except PermissionError:
            return [Entry(e.name, e.is_dir, e.is_link) for e in self.w.list_dir_backup(str(long_path(path)))]

    def open(self, path: str) -> BinaryIO:
        return self.w.open_backup(str(long_path(path)))

    def times(self, path: str) -> tuple[int, int, int, int]:
        return self.w.file_times(str(long_path(path)))

    def set_times(self, path: str, created: int, accessed: int, modified: int) -> None:
        self.w.set_times(str(long_path(path)), created, accessed, modified)

    def save_hive(self, hive: str, dest: Path) -> str:
        import winreg

        if hive.startswith("HKU\\"):
            return self.w.reg_save(winreg.HKEY_USERS, hive[4:], dest)
        return self.w.reg_save(winreg.HKEY_LOCAL_MACHINE, hive, dest)

    def profiles(self) -> list[tuple[str, str]]:
        return self.w.user_profiles()

    def loaded_users(self) -> set[str]:
        return self.w.loaded_user_hives()


def acquire_live(dest: Path, *, allow_same_drive: bool = False, progress: Callable[[dict], None] | None = None,
                 cancel: threading.Event | None = None) -> Acquisition:
    """Acquire this computer's evidence into ``dest`` (Windows, as administrator)."""
    if sys.platform != "win32":
        raise AcquisitionError("Live acquisition is available on Windows only.")
    from . import winapi

    if not winapi.is_admin():
        raise AcquisitionError("Live acquisition needs administrator rights: the registry hives and protected "
                               "folders cannot be read otherwise. Run the program as administrator.")
    backend = LiveBackend()
    drive = check_destination(dest, backend.root, allow_same_drive)
    backup = winapi.enable_privilege("SeBackupPrivilege")
    if not backup:
        raise AcquisitionError("The backup privilege (SeBackupPrivilege) could not be enabled; run as an "
                               "administrator account.")
    return Acquirer(backend, dest, computer=winapi.computer_name(), windows=winapi.windows_version(),
                    elevated=True, backup_privilege=backup, drive=drive, progress=progress, cancel=cancel).run()
