"""Save recovered files for the examiner: byte for byte, verified, with a manifest.

The files a scan recovered (photos, attachments, contact pictures, app icons,
wallpapers) sit in the case's ``exports`` folder exactly as the evidence held
them. Saving them elsewhere:

* copies the bytes unchanged into a new folder; nothing is ever overwritten,
  and the copies are marked read-only;
* hashes each copy (SHA-256 and MD5) as it is written, reads it back from the
  disk to check the write, and compares it with the hash recorded when the
  scan extracted the file;
* sets each copy's modified time to the time its record gives (when the photo
  was taken, the message sent); the time the copy was made is in the manifest;
* writes ``manifest.csv`` and ``manifest.json`` (every file with what the case
  knows about it: phone, time, deleted or present, source database and record,
  hashes) and ``SHA256SUMS.txt``, which standard tools check
  (``sha256sum -c SHA256SUMS.txt``).
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import json
import os
import re
import stat
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .evidence import display_path, long_path

FOLDER_BY_CATEGORY = {"media": "Photos and files", "contacts": "Contact photos", "apps": "App icons",
                      "messages": "Message attachments", "calls": "Calls", "notifications": "Notifications",
                      "connections": "Connections"}
MANIFEST_FIELDS = (
    "saved_as", "category", "phone", "device_id", "item", "status", "record_time_utc", "modified_time_set_to",
    "size_bytes", "sha256", "md5", "sha256_at_scan", "hash_check", "case_copy", "source_path", "record",
    "source_sha256", "computer", "user", "parser",
)
_CHUNK = 1 << 20


@dataclass
class SaveResult:
    folder: Path
    saved: list[dict] = field(default_factory=list)      # manifest rows
    skipped: list[dict] = field(default_factory=list)    # {"item", "reason"}
    mismatched: list[dict] = field(default_factory=list)  # rows whose hash differs from the scan's
    cancelled: bool = False

    @property
    def total_bytes(self) -> int:
        return sum(int(r["size_bytes"] or 0) for r in self.saved)


def with_files(items: Iterable[dict]) -> list[dict]:
    """The items that have a saved file (the others are records only)."""
    return [r for r in items if r.get("file")]


def safe_name(text: str, limit: int = 80) -> str:
    """A file or folder name from evidence-controlled text: no separators, reserved characters or
    trailing dots, never empty."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(text or "")).strip(" .")
    name = re.sub(r"\s+", " ", name)[:limit].strip(" .")
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                     *(f"LPT{i}" for i in range(1, 10))}:
        name = f"_{name}"
    return name or "_"


def _new_folder(parent: Path, name: str) -> Path:
    base = Path(parent) / safe_name(name, 100)
    for n in range(1, 1000):
        folder = base if n == 1 else base.with_name(f"{base.name} ({n})")
        try:
            long_path(folder).mkdir(parents=True, exist_ok=False)
            return folder
        except FileExistsError:
            continue
    raise FileExistsError(f"no free folder name next to {base}")


def _unique(folder: Path, rel: Path) -> Path:
    """``rel`` under ``folder`` (an extended-length path), renamed "name (2).ext" while the name is taken."""
    target = folder / rel
    n = 2
    while target.exists():
        target = folder / rel.parent / f"{rel.stem} ({n}){rel.suffix}"
        n += 1
    return target


def _record_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _item_name(r: dict) -> str:
    return str(r.get("name") or r.get("package") or r.get("party") or r.get("title") or r.get("what") or "")


def _copy(src: Path, dst: Path) -> tuple[str, str, int]:
    """Copy ``src`` to a new file ``dst`` (never replacing one; both extended-length paths); returns
    SHA-256, MD5 and size of what was written, after reading the copy back from the disk."""
    sha, md5, size = hashlib.sha256(), hashlib.md5(), 0  # noqa: S324 - MD5 for tools that expect it
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as fi, open(dst, "xb") as fo:
        for chunk in iter(lambda: fi.read(_CHUNK), b""):
            sha.update(chunk)
            md5.update(chunk)
            size += len(chunk)
            fo.write(chunk)
    check = hashlib.sha256()
    with open(dst, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            check.update(chunk)
    if check.hexdigest() != sha.hexdigest():
        raise OSError(f"the copy read back from {dst} differs from what was written")
    return sha.hexdigest(), md5.hexdigest(), size


def _read_only(path: Path) -> None:
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


def save_files(case_dir: Path, items: Iterable[dict], parent: Path, *, title: str = "Recovered files",
               case: dict | None = None, set_times: bool = True,
               progress: Callable[[int, int], None] | None = None,
               cancel: threading.Event | None = None) -> SaveResult:
    """Copy the saved files of ``items`` (content items, see :func:`connected_phone_forensics.content.extract`)
    from ``case_dir/exports`` into a new folder below ``parent``. Returns what was saved and skipped."""
    started = datetime.now(timezone.utc)
    wanted = with_files(items)
    folder = _new_folder(Path(parent), f"{safe_name(title, 72)} {started:%Y-%m-%d %H%M%S}")  # time always shown
    result = SaveResult(folder)
    # Extended-length roots once: paths joined below them keep the prefix (and resolving each is slow).
    root = long_path(folder)
    exports = long_path(Path(case_dir) / "exports")
    done: dict[str, dict] = {}  # case copy -> manifest row (an icon can belong to several items)

    for n, r in enumerate(wanted, 1):
        if cancel is not None and cancel.is_set():
            result.cancelled = True
            break
        if progress:
            progress(n, len(wanted))
        rel_copy = str(r["file"])
        if rel_copy in done:
            continue
        src = exports.joinpath(*[p for p in rel_copy.split("/") if p not in ("", ".", "..")])
        if not src.is_file():
            result.skipped.append({"item": _item_name(r), "file": rel_copy,
                                   "reason": "the case's saved copy is missing"})
            continue
        category = r.get("category") or "media"
        phone = safe_name(r.get("phone") or r.get("device") or "Not linked to a phone", 60)
        rel = Path(FOLDER_BY_CATEGORY.get(category, safe_name(category)), phone, safe_name(src.name, 120))
        dst = _unique(root, rel)
        try:
            sha, md5, size = _copy(src, dst)
        except OSError as exc:
            result.skipped.append({"item": _item_name(r), "file": rel_copy, "reason": str(exc)})
            continue
        at_scan = str(r.get("file_sha256") or "").lower()
        check = ("matches the hash recorded at the scan" if at_scan == sha else
                 "DIFFERS from the hash recorded at the scan" if at_scan else "no hash recorded at the scan")
        when = _record_time(r.get("time"))
        time_set = ""
        if set_times and when is not None:
            try:
                os.utime(dst, (when.timestamp(), when.timestamp()))
                time_set = when.strftime("%Y-%m-%d %H:%M:%S UTC")
            except (OSError, OverflowError, ValueError):
                time_set = ""
        _read_only(dst)
        row = {
            "saved_as": dst.relative_to(root).as_posix(), "category": category, "phone": r.get("phone") or "",
            "device_id": r.get("device") or "", "item": _item_name(r),
            "status": r.get("status") or "Present (live record)",
            "record_time_utc": when.strftime("%Y-%m-%d %H:%M:%S") if when else "",
            "modified_time_set_to": time_set, "size_bytes": size, "sha256": sha, "md5": md5,
            "sha256_at_scan": at_scan, "hash_check": check, "case_copy": f"exports/{rel_copy}",
            "source_path": r.get("source_path") or "", "record": r.get("locator") or "",
            "source_sha256": r.get("sha256") or "", "computer": r.get("system") or "", "user": r.get("user") or "",
            "parser": r.get("artifact") or "",
        }
        done[rel_copy] = row
        result.saved.append(row)
        if at_scan and at_scan != sha:
            result.mismatched.append(row)

    _write_manifest(result, started, title, case or {}, Path(case_dir), len(wanted))
    return result


def _write_manifest(result: SaveResult, started: datetime, title: str, case: dict, case_dir: Path,
                    asked: int) -> None:
    folder = result.folder
    finished = datetime.now(timezone.utc)
    with open(long_path(folder / "manifest.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        w.writerows(result.saved)
    meta = {
        "tool": "Connected Phone Forensics", "version": __version__, "title": title,
        "case": case.get("case") or "", "examiner": case.get("examiner") or "",
        "case_folder": display_path(case_dir), "started_utc": started.isoformat(), "finished_utc": finished.isoformat(),
        "items_with_files": asked, "saved": len(result.saved), "skipped": result.skipped,
        "hash_mismatches": len(result.mismatched), "cancelled": result.cancelled,
        "bytes": result.total_bytes,
        "method": "Bytes copied unchanged from the case's saved copies; each copy hashed while written and "
                  "again read back from disk; compared with the SHA-256 recorded when the scan extracted it. "
                  "Copies are read-only; each copy's modified time is set to its record's time, where it has one.",
        "files": result.saved,
    }
    with open(long_path(folder / "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    with open(long_path(folder / "SHA256SUMS.txt"), "w", encoding="utf-8", newline="\n") as f:
        for row in result.saved:
            f.write(f"{row['sha256']} *{row['saved_as']}\n")
    ok = len(result.saved) - len(result.mismatched)
    lines = [
        f"{title}",
        f"Saved by Connected Phone Forensics {__version__} on {finished:%Y-%m-%d %H:%M:%S} UTC.",
        f"Case: {case.get('case') or '(none)'}    Examiner: {case.get('examiner') or '(none)'}",
        f"From the case folder: {display_path(case_dir)}",
        "",
        f"{len(result.saved)} file(s), {result.total_bytes:,} bytes. {ok} match the hash recorded when the scan "
        f"extracted them" + (f"; {len(result.mismatched)} DO NOT (see manifest.csv, hash_check)." if
                             result.mismatched else "."),
    ]
    if result.skipped:
        lines.append(f"{len(result.skipped)} item(s) not saved; manifest.json lists them with the reason.")
    if result.cancelled:
        lines.append("The save was cancelled before every file was copied.")
    lines += [
        "",
        "How the files were saved",
        "- The bytes are exactly those the scan extracted from the evidence: nothing is converted or re-encoded.",
        "- Each copy was hashed (SHA-256, MD5) as it was written and read back from the disk to check the write.",
        "- Each copy's modified time is set to the time its record gives (photo taken, contact updated), where",
        "  the record has one; the created time is when the copy was made. Both are in manifest.csv.",
        "- The copies are marked read-only.",
        "",
        "To check the files later:",
        "  sha256sum -c SHA256SUMS.txt                      (Linux, macOS, Git Bash)",
        "  Get-FileHash -Algorithm SHA256 <file>            (Windows PowerShell)",
        "",
        "manifest.csv / manifest.json: every file with its phone, record time, deleted or present, the database",
        "and record it came from (with that database's SHA-256), and the hashes.",
    ]
    with open(long_path(folder / "Read me.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    for name in ("manifest.csv", "manifest.json", "SHA256SUMS.txt", "Read me.txt"):
        _read_only(folder / name)
