"""Live forensics: acquire this computer's phone evidence, then scan the copy.

Use from a USB drive, as administrator::

    cpf live -o E:\\case-0142 --case 0142 --examiner "A. Examiner"

Everything is written under the output folder, which must be on another drive than the one being
examined: the copies (``collection/``), the acquisition record (``acquisition.json``,
``acquisition.sha256``, ``acquisition.log``), the report (``report/``) and the temporary copies the
scan makes (``_work/``, removed at the end).
"""

from __future__ import annotations

import shutil
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from .acquire import Acquisition, AcquisitionError, acquire_live, check_destination  # noqa: F401


def is_supported() -> bool:
    import sys

    return sys.platform == "win32"


def is_admin() -> bool:
    if not is_supported():
        return False
    from . import winapi

    return winapi.is_admin()


def system_drive() -> str:
    import os

    return os.environ.get("SYSTEMDRIVE", "C:") + "\\"


def is_system_drive_root(path: Path) -> bool:
    """True for the running system's drive (C:), which a folder scan cannot read fully."""
    if not is_supported():
        return False
    try:
        return Path(path).resolve() == Path(system_drive()).resolve()
    except OSError:
        return False


def machine() -> dict:
    """This computer, for the live-acquisition screen."""
    import getpass

    from . import winapi

    return {"computer": winapi.computer_name(), "windows": winapi.windows_version(), "user": getpass.getuser(),
            "admin": winapi.is_admin(), "system_drive": system_drive(),
            "profiles": [folder for _sid, folder in winapi.user_profiles()]}


def output_drives() -> list[dict]:
    """Drives an acquisition can be written to: every drive but the system drive, the drive this
    program runs from first, then removable drives."""
    import sys

    from . import winapi

    here = Path(sys.executable if getattr(sys, "frozen", False) else __file__).anchor.lower()
    sysd = system_drive().lower()
    out = [d for d in (vars(i) for i in winapi.drives()) if d["root"].lower() != sysd]
    for d in out:
        d["program_drive"] = d["root"].lower() == here
    out.sort(key=lambda d: (not d["program_drive"], d["kind"] != "removable", d["root"]))
    return out


def suggest_output(computer: str, when: str) -> Path | None:
    """A new folder on the drive this program runs from or on a removable drive, or None."""
    drives = [d for d in output_drives() if d["program_drive"] or d["kind"] == "removable"]
    if not drives:
        return None
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in computer) or "computer"
    return Path(drives[0]["root"]) / "CPF cases" / f"{safe} {when}"


def run_live(dest: Path, *, case: dict | None = None, workers: int | None = None, allow_same_drive: bool = False,
             progress: Callable[[str], None] | None = None, on_event: Callable[[dict], None] | None = None,
             cancel: threading.Event | None = None, export_files: bool = True, html: bool = True,
             xlsx: bool = True, pdf: bool = True, plugin_ids: list[str] | None = None):
    """Acquire, scan and report. Returns (ScanResult, Acquisition)."""
    from ..engine import scan
    from ..output import write_all
    from ..plugin import Warning_

    dest = Path(dest)
    say = progress or (lambda _m: None)
    emit = on_event or (lambda _e: None)
    if not is_supported():
        raise AcquisitionError("Live acquisition is available on Windows only.")
    if not is_admin():
        raise AcquisitionError("Live acquisition needs administrator rights: the registry hives and protected "
                               "folders cannot be read otherwise. Run the program as administrator.")
    check_destination(dest, system_drive(), allow_same_drive)  # before anything is written
    if dest.exists() and any(dest.iterdir()):
        raise AcquisitionError(f"{dest} is not empty: choose a new or empty output folder.")
    work = dest / "_work"
    old_tempdir = tempfile.tempdir
    try:
        work.mkdir(parents=True, exist_ok=True)
        tempfile.tempdir = str(work)  # every temporary file stays on the output drive
        say("acquiring this computer's evidence (live)")
        acq = acquire_live(dest, allow_same_drive=allow_same_drive, progress=emit, cancel=cancel)
        say(f"acquired {sum(1 for i in acq.items if i.status == 'acquired')} item(s) "
            f"({len(acq.errors)} error(s)) into {acq.collection}")
        result = scan(Path(acq.collection).parent.parent, plugin_ids=plugin_ids, case=case, workdir=work,
                      progress=progress, export_dir=dest / "report" / "exports" if export_files else None,
                      workers=workers, on_event=on_event, cancel=cancel)
        result.acquisition = acq.summary()
        for item in acq.errors:
            result.warnings.append(Warning_("live_acquisition", acq.computer, f"not acquired: {item.note}",
                                            item.path))
        for w in acq.warnings:
            result.warnings.append(Warning_("live_acquisition", acq.computer, w, None))
        emit({"type": "writing"})
        write_all(result, dest / "report", html=html, xlsx=xlsx, pdf=pdf)
        return result, acq
    finally:
        tempfile.tempdir = old_tempdir
        shutil.rmtree(work, ignore_errors=True)
