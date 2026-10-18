"""Read-only access to evidence: mounted images and triage collections.

Connected Phone Forensics never writes inside the evidence. Paths are resolved case-insensitively
because Windows images are often mounted or extracted on case-sensitive Linux
filesystems. A single input directory can hold several systems (e.g. a KAPE
collection of C: next to a macOS Data volume); :func:`discover_systems` finds
each one.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import plistlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

WINDOWS = "windows"
MACOS = "macos"
LINUX = "linux"

_HASH_CHUNK = 1024 * 1024

_WIN_EXTENDED = "\\\\?\\"
_WIN_EXTENDED_UNC = "\\\\?\\UNC\\"


def long_path(path: Path | str) -> Path:
    """``path`` made absolute and, on Windows, in extended-length form (``\\\\?\\C:\\...``).

    Without LongPathsEnabled, Windows refuses paths over 260 characters, and a
    Phone Link database below a case folder is easily that deep: the file then
    looks missing. Paths joined below an extended-length root keep the prefix,
    so every later open, listing and copy of them works.
    """
    p = Path(path).resolve()
    if os.name != "nt":
        return p
    s = str(p)
    if s.startswith((_WIN_EXTENDED, "\\\\.\\")):
        return p
    if s.startswith("\\\\"):  # \\server\share\... -> \\?\UNC\server\share\...
        return Path(_WIN_EXTENDED_UNC + s[2:])
    return Path(_WIN_EXTENDED + s)


def display_path(path: Path | str) -> str:
    """``path`` as text for reports, without the Windows extended-length prefix."""
    s = str(path)
    if s.startswith(_WIN_EXTENDED_UNC):
        return "\\\\" + s[len(_WIN_EXTENDED_UNC):]
    if s.startswith(_WIN_EXTENDED):
        return s[len(_WIN_EXTENDED):]
    return s


class EvidenceFS:
    """A read-only, case-insensitive view of a directory tree."""

    def __init__(self, root: Path | str):
        self.root = long_path(root)
        if not self.root.is_dir():
            raise NotADirectoryError(self.root)
        self._hashes: dict[Path, str] = {}
        self._rel: dict[Path, str] = {}

    # -- listing ---------------------------------------------------------

    @lru_cache(maxsize=65536)  # noqa: B019 - one EvidenceFS per run, cache lifetime is the run
    def _listdir(self, directory: Path) -> tuple[tuple[str, bool], ...]:
        try:
            with os.scandir(directory) as it:
                out = []
                for entry in it:
                    try:
                        out.append((entry.name, entry.is_dir(follow_symlinks=False)))
                    except OSError:
                        continue
                return tuple(sorted(out))
        except OSError:
            return ()

    def _match_component(self, directory: Path, pattern: str, dirs_only: bool) -> list[Path]:
        pat = pattern.lower()
        wildcard = any(ch in pattern for ch in "*?[")
        out = []
        for name, is_dir in self._listdir(directory):
            if dirs_only and not is_dir:
                continue
            low = name.lower()
            if (wildcard and fnmatch.fnmatchcase(low, pat)) or low == pat:
                out.append(directory / name)
        return out

    def _walk_dirs(self, directory: Path) -> Iterator[Path]:
        yield directory
        for name, is_dir in self._listdir(directory):
            if is_dir:  # symlinked directories are not followed (is_dir(follow_symlinks=False))
                yield from self._walk_dirs(directory / name)

    def glob(self, pattern: str) -> list[Path]:
        """Match a '/'-separated pattern relative to the root, case-insensitively.

        Supports ``*``, ``?``, ``[...]`` within a component and ``**`` for any
        number of directories.
        """
        parts = [p for p in pattern.replace("\\", "/").split("/") if p and p != "."]
        current = [self.root]
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            nxt: list[Path] = []
            if part == "**":
                for d in current:
                    nxt.extend(self._walk_dirs(d))
            else:
                for d in current:
                    nxt.extend(self._match_component(d, part, dirs_only=not last))
            current = nxt
            if not current:
                return []
        seen = set()
        result = []
        for p in current:
            if p not in seen:
                seen.add(p)
                result.append(p)
        return result

    def find(self, pattern: str) -> Path | None:
        """First match for a pattern, or None."""
        hits = self.glob(pattern)
        return hits[0] if hits else None

    def child(self, directory: Path, *names: str) -> Path | None:
        """Case-insensitive lookup of a literal relative path below ``directory`` (no globbing)."""
        cur = Path(directory)
        for name in names:
            low = name.lower()
            hit = next((n for n, _d in self._listdir(cur) if n.lower() == low), None)
            if hit is None:
                return None
            cur = cur / hit
        return cur

    def files(self, pattern: str) -> list[Path]:
        return [p for p in self.glob(pattern) if p.is_file()]

    def dirs(self, pattern: str) -> list[Path]:
        return [p for p in self.glob(pattern) if p.is_dir()]

    # -- reading ---------------------------------------------------------

    def rel(self, path: Path) -> str:
        """Path relative to the root, '/'-separated, as it appears in the evidence."""
        p = Path(path)
        hit = self._rel.get(p)
        if hit is None:
            try:
                hit = long_path(p).relative_to(self.root).as_posix()
            except ValueError:
                hit = p.as_posix()
            self._rel[p] = hit
        return hit

    def sha256(self, path: Path) -> str | None:
        path = Path(path)
        if path in self._hashes:
            return self._hashes[path]
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
                    h.update(chunk)
        except OSError:
            return None
        digest = h.hexdigest()
        self._hashes[path] = digest
        return digest

    def hashed_files(self) -> dict[str, str]:
        """Every file hashed so far, keyed by relative path."""
        return {self.rel(p): d for p, d in sorted(self._hashes.items())}

    def read_bytes(self, path: Path, limit: int | None = None) -> bytes:
        with open(path, "rb") as f:
            return f.read() if limit is None else f.read(limit)


@dataclass
class UserProfile:
    name: str
    home: Path


@dataclass
class SystemRoot:
    """One computer's filesystem found inside the evidence."""

    os: str
    fs: EvidenceFS
    label: str
    users: list[UserProfile] = field(default_factory=list)
    details: dict[str, str] = field(default_factory=dict)
    _homes: list[tuple[str, str]] | None = field(default=None, repr=False)

    def user_for(self, rel_path: str) -> str | None:
        """Name of the user profile containing a path relative to the root, if any."""
        if self._homes is None:
            self._homes = [(self.fs.rel(u.home).rstrip("/") + "/", u.name) for u in self.users]
        for prefix, name in self._homes:
            if rel_path.startswith(prefix):
                return name
        return None


# -- system discovery ---------------------------------------------------------

_WIN_SKIP_USERS = {"public", "default", "default user", "all users", "defaultapppool"}
_MAC_SKIP_USERS = {"shared", "guest", ".localized"}


def _esc(name: str) -> str:
    """Escape glob metacharacters in a literal path component."""
    return re.sub(r"([*?\[])", r"[\1]", name)


def _windows_users(fs: EvidenceFS) -> list[UserProfile]:
    users = []
    for d in fs.dirs("Users/*"):
        if d.name.lower() in _WIN_SKIP_USERS:
            continue
        if fs.find(f"Users/{_esc(d.name)}/NTUSER.DAT") or fs.find(f"Users/{_esc(d.name)}/AppData"):
            users.append(UserProfile(d.name, d))
    return users


def _mac_users(fs: EvidenceFS) -> list[UserProfile]:
    users = []
    for d in fs.dirs("Users/*"):
        if d.name.lower() in _MAC_SKIP_USERS or d.name.startswith("."):
            continue
        if fs.find(f"Users/{_esc(d.name)}/Library"):
            users.append(UserProfile(d.name, d))
    root_home = fs.find("private/var/root")
    if root_home and root_home.is_dir():
        users.append(UserProfile("root", root_home))
    return users


def _linux_users(fs: EvidenceFS) -> list[UserProfile]:
    users = [UserProfile(d.name, d) for d in fs.dirs("home/*") if not d.name.startswith(".")]
    root_home = fs.find("root")
    if root_home and root_home.is_dir():
        users.append(UserProfile("root", root_home))
    return users


def _classify(fs: EvidenceFS) -> str | None:
    if fs.find("Windows/System32/config") or fs.find("Users/*/NTUSER.DAT"):
        return WINDOWS
    if (
        fs.find("System/Library/CoreServices/SystemVersion.plist")
        or fs.find("private/var/db")
        or (fs.find("Users/*/Library") and fs.find("Library/Preferences"))
    ):
        return MACOS
    if fs.find("etc/os-release") or (fs.find("home") and fs.find("var/lib")):
        return LINUX
    return None


def _windows_details(fs: EvidenceFS) -> dict[str, str]:
    """Computer name (SYSTEM) and Windows version (SOFTWARE), best effort."""
    import logging

    from .readers.registry import control_set_paths, open_hive

    details: dict[str, str] = {}
    logging.getLogger("regipy").setLevel(logging.CRITICAL)
    system = fs.find("Windows/System32/config/SYSTEM")
    if system and system.is_file():
        try:
            with open_hive(system) as h:
                for cs in control_set_paths(h):
                    k = h.key(f"{cs}\\Control\\ComputerName\\ComputerName")
                    if k and k.get("ComputerName"):
                        details["computer_name"] = str(k.get("ComputerName"))
                        break
        except Exception:  # noqa: BLE001 - details are best effort
            pass
    software = fs.find("Windows/System32/config/SOFTWARE")
    if software and software.is_file():
        try:
            with open_hive(software) as h:
                k = h.key("\\Microsoft\\Windows NT\\CurrentVersion")
                if k:
                    product = str(k.get("ProductName") or "").strip()
                    build = k.get("CurrentBuild") or k.get("CurrentBuildNumber")
                    # Windows 11 still reports "Windows 10" in ProductName; the build tells them apart.
                    if product and build and str(build).isdigit() and int(build) >= 22000:
                        product = product.replace("Windows 10", "Windows 11")
                    parts = [product, str(k.get("DisplayVersion") or ""), f"build {build}" if build else ""]
                    details["product"] = " ".join(p for p in parts if p).strip()
        except Exception:  # noqa: BLE001
            pass
    return details


def _system_details(os_name: str, fs: EvidenceFS) -> dict[str, str]:
    details: dict[str, str] = {}
    if os_name == WINDOWS:
        details.update(_windows_details(fs))
    elif os_name == MACOS:
        prefs = fs.find("Library/Preferences/SystemConfiguration/preferences.plist")
        if prefs:
            try:
                with open(prefs, "rb") as f:
                    v = plistlib.load(f)
                sysinfo = v.get("System", {})
                name = (sysinfo.get("System", {}).get("ComputerName")
                        or sysinfo.get("Network", {}).get("HostNames", {}).get("LocalHostName"))
                if name:
                    details["computer_name"] = str(name)
            except Exception:  # noqa: BLE001
                pass
        p = fs.find("System/Library/CoreServices/SystemVersion.plist")
        if p:
            try:
                with open(p, "rb") as f:
                    v = plistlib.load(f)
                details["product_version"] = str(v.get("ProductVersion", ""))
                details["build"] = str(v.get("ProductBuildVersion", ""))
            except Exception:  # noqa: BLE001 - details are best effort
                pass
    elif os_name == LINUX:
        hn = fs.find("etc/hostname")
        if hn and hn.is_file():
            try:
                name = hn.read_text(errors="replace").strip().splitlines()[0].strip()
                if name:
                    details["computer_name"] = name
            except (OSError, IndexError):
                pass
        p = fs.find("etc/os-release")
        if p:
            try:
                for line in p.read_text(errors="replace").splitlines():
                    if line.startswith("PRETTY_NAME="):
                        details["pretty_name"] = line.split("=", 1)[1].strip().strip('"')
            except OSError:
                pass
    return details


def discover_systems(path: Path | str, max_depth: int = 5) -> list[SystemRoot]:
    """Find every Windows, macOS or Linux root under ``path``.

    Handles a mounted image (root is the volume), KAPE output (``<out>/C``),
    Velociraptor collections (``uploads/auto/C%3A``) and UAC (``[root]``) by
    searching a few levels down for something that looks like a system root.
    """
    base = long_path(path)
    found: list[SystemRoot] = []

    def visit(d: Path, depth: int) -> None:
        fs = EvidenceFS(d)
        kind = _classify(fs)
        if kind:
            users = {WINDOWS: _windows_users, MACOS: _mac_users, LINUX: _linux_users}[kind](fs)
            details = _system_details(kind, fs)
            folder = d.name if d != base else (base.name or display_path(base))
            # Collections often name the root after a drive ("C", "C%3A"); prefer the
            # computer name recorded in the evidence, then the parent folder's name.
            if re.fullmatch(r"[A-Za-z](%3A|:)?", folder) and d != base:
                folder = f"{d.parent.name}/{folder}"
            label = details.get("computer_name") or folder
            details["root_folder"] = folder
            found.append(SystemRoot(kind, fs, label, users, details))
            return
        if depth >= max_depth:
            return
        try:
            children = sorted(p for p in d.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError:
            return
        for child in children:
            visit(child, depth + 1)

    visit(base, 0)
    # Two images of the same computer share a name; keep labels unique.
    seen: dict[str, int] = {}
    for sysroot in found:
        n = seen.get(sysroot.label, 0)
        seen[sysroot.label] = n + 1
        if n:
            sysroot.label = f"{sysroot.label} ({sysroot.details.get('root_folder', n + 1)})"
    return found
